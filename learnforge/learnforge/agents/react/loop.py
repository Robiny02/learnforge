"""ReactRunner —— 真正的 think → tool_call → observe 循环（OpenAI 兼容 tool-calling）。

职责（与传输层 llm.client.complete_with_tools 分离）：
- 维护对话 messages；
- 每步把 assistant 的 tool_calls 取出，逐个：过 `agent.require_tool` 权限门（越权 → error tool_result，不崩溃）
  → 执行（优先 per-call handlers，否则 `mcp.tools.run_tool`）→ 结果作为 role=tool 消息回灌；
- 无 tool_calls 时返回最终文本（这就是"简单问题快答"路径：模型不调工具直接答）；步数超 `max_steps` → 安全收尾。

不变量：只暴露 `skill.allows(name)` ∧（有 per-call handler 或全局 handler）的工具；写工具不会出现（只读 agent 的 skill 不声明写工具）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ...llm.client import LLM, LLMUnavailable
from ...tools import openai_bridge as toolmod


@dataclass
class ReactResult:
    text: str = ""
    steps: int = 0
    trace: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)  # [{tool, result(dict)}]
    degraded: bool = False  # True 表示中途降级（无 LLM / 超预算 / 出错）


class ReactRunner:
    def __init__(self, max_steps: int = 5) -> None:
        self.max_steps = max_steps

    def run(
        self,
        agent: Any,
        user_prompt: str,
        tool_names: List[str],
        system: Optional[str] = None,
        model_tier=None,
        handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = None,
    ) -> ReactResult:
        """跑一条 ReAct 循环。

        handlers：按调用注入的工具实现（覆盖全局 TOOL_RUNTIME），用于把带 db_path 的
        retrieval/verify 等实例方法接进来。
        """
        if not LLM.available:
            return ReactResult(text="", degraded=True)

        handlers = handlers or {}
        if model_tier is None:
            model_tier = agent.skill.spec.model_tier if getattr(agent, "skill", None) else None

        # 暴露：skill 允许 ∧（有 per-call handler 或全局 handler）的工具。
        exposed = [
            n for n in tool_names
            if agent.has_tool(n) and (n in handlers or toolmod.has_handler(n))
        ]
        openai_tools = toolmod.to_openai_tools(exposed)

        messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        trace: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []

        for step in range(self.max_steps):
            try:
                msg, _ = LLM.complete_with_tools(
                    messages, openai_tools, model_tier=model_tier, system=system
                )
            except LLMUnavailable:
                return ReactResult(text="", steps=step, trace=trace,
                                   tool_results=tool_results, degraded=True)

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                # 没有工具调用 → 直接最终答复（简单问题快答路径）。
                return ReactResult(text=msg.get("content") or "", steps=step,
                                   trace=trace, tool_results=tool_results)

            messages.append(msg)  # 带 tool_calls 的 assistant 回合需原样回传
            for tc in tool_calls:
                fn = tc.get("function") or {}
                real = toolmod.unsafe_name(fn.get("name", ""))
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                try:
                    agent.require_tool(real)  # 权限门
                    handler = handlers.get(real)
                    result = handler(args) if handler is not None else toolmod.run_tool(real, args)
                    if not isinstance(result, dict):
                        result = {"result": result}
                    allowed = True
                except Exception as e:  # SkillPermissionError / handler 异常
                    result = {"error": f"permission denied or tool error: {e}"}
                    allowed = False
                tool_results.append({"tool": real, "result": result})
                trace.append({"step": step, "tool": real, "args": args,
                              "allowed": allowed, "result_preview": str(result)[:160]})
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "content": json.dumps(result, ensure_ascii=False)[:4000]})

        # 超步数预算：再要一次"基于已观察到的信息直接给最终答复"。
        try:
            msg, _ = LLM.complete_with_tools(
                messages + [{"role": "user", "content": "基于以上工具结果直接给出最终答复，不要再调用工具。"}],
                [], model_tier=model_tier, system=system,
            )
            return ReactResult(text=msg.get("content") or "", steps=self.max_steps,
                               trace=trace, tool_results=tool_results, degraded=True)
        except LLMUnavailable:
            return ReactResult(text="", steps=self.max_steps, trace=trace,
                               tool_results=tool_results, degraded=True)
