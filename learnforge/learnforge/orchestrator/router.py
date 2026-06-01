"""意图路由器（Agent-level hybrid router）。

只回答一个问题：「这句话该交给哪个子 Agent」。在原 Phase 1 纯关键词路由之上，
升级为更稳定、可维护的混合路由，但**边界不变**——只决定 target_agent、抽取轻量
槽位、给出 route hints、识别多意图 handoff，**绝不**替子 Agent 决定内部执行步骤
（不产出 mock.answer / planning.modify 这类 workflow 字段；子 Agent 自己理解任务、
调工具、读记忆、做推理）。

能力（全部确定性、可离线、可回归）：
  1. 显式命令优先：开头 ``/plan`` ``/mock`` 等直接命中，最高优先级；
  2. 多轮状态感知：进行中的模拟面试默认续接（除非明确切换/否定）；
  3. 否定意图识别：「别给我做模拟」「don't mock me」会取消该意图；
  4. 多意图识别 + handoff：「先诊断再给计划」→ 主 agent + 有序 handoff 提示；
  5. 轻量槽位抽取：仅 topic / deadline，不做重型 NLU；
  6. retrieval policy：给出 ``use_retrieval`` 提示（是否建议走 RAG）；
  7. 可选一次性结构化 LLM 增强：仅在不确定时调用**至多一次**，无 key 自动跳过；
  8. deterministic fallback：无 LLM、无 context 时退化为关键词路由，行为稳定；
  9. signals：记录命中了哪些规则，供 trace / 调试。

向后兼容：``RouteResult`` 仍带 ``agent`` / ``reason``，``QARouter.route(user_input)``
旧签名照常可用，新增 ``context`` / ``llm`` 均为可选参数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

# 关键词表：dict 顺序即优先级（mock > diagnosis > planning）。
# 诊断是规划的上游，复合「诊断…并给计划」应先 seed 到 diagnosis，再由 next_actions
# 驱动 planning（Phase 3 串行协作）。单意图选择严格按此顺序，保证与旧路由完全一致。
_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "mock": ("面试", "mock", "模拟面试", "模拟", "刷题", "出题", "interview", "practice"),
    "diagnosis": ("诊断", "弱点", "薄弱", "复盘", "短板", "diagnose", "weakness", "assess"),
    "planning": ("计划", "规划", "学习路径", "路线", "安排", "路径", "plan", "roadmap", "schedule"),
}

# 显式命令表：开头的 /command 直接命中对应 agent，绕过关键词与上下文。
_COMMANDS: Dict[str, str] = {
    "qa": "qa",
    "ask": "qa",
    "plan": "planning",
    "planning": "planning",
    "diagnose": "diagnosis",
    "diagnosis": "diagnosis",
    "mock": "mock",
    "interview": "mock",
}

# 否定标记：出现在某意图关键词「附近」时取消该意图（而非命中）。
_NEGATIONS: Tuple[str, ...] = (
    "不要", "别", "不用", "不想", "无需", "不必", "停", "取消", "退出", "跳过",
    "don't", "do not", "dont", "no need", "not now", "stop", "cancel", "skip", "without",
)
# 子句分隔符：否定只在「同一子句、且在关键词之前」时生效，避免「先别面试了，帮我制定计划」
# 里的「别」误伤跨子句的「计划」。
_CLAUSE_SEP = re.compile(r"[，,。.!?！？；;、\n]")

# 轻量槽位：topic 命中表 + deadline 正则。刻意保持极小，不做重型抽取。
_TOPIC_HINTS: Tuple[str, ...] = (
    "python", "asyncio", "java", "golang", "go", "rust", "kubernetes", "k8s", "docker",
    "sql", "mysql", "redis", "react", "http", "tcp", "system design",
    "并发", "并发编程", "网络", "算法", "数据结构", "系统设计", "操作系统", "数据库",
)
_DEADLINE_RE = re.compile(
    r"(\d+)\s*(个月|周|天|月|days|day|weeks|week|months|month)", re.IGNORECASE
)

_EXPLICIT_RE = re.compile(r"^/(\w+)")

# 置信度低于此阈值才（可选地）请求一次 LLM 增强。
_LOW_CONFIDENCE = 0.5

# LLM 增强器：``(user_input) -> Optional[overrides]``，overrides 可含 "agent" 及槽位键。
# 用纯 callable 注入，路由器本身不依赖任何 LLM SDK，离线/测试都简单。
LLMEnhancer = Callable[[str], Optional[Dict[str, str]]]


@dataclass(frozen=True)
class RouteResult:
    """单次路由结论。

    ``agent`` / ``reason`` 为向后兼容字段；其余为 hybrid 路由新增的轻量增强信息。
    """

    agent: str  # qa | planning | diagnosis | mock
    reason: str
    confidence: float = 1.0
    slots: Dict[str, str] = field(default_factory=dict)
    handoffs: Tuple[str, ...] = ()        # 后续应转交的其它 agent（有序，仅提示）
    use_retrieval: bool = False            # 是否建议走 RAG（提示，非命令）
    signals: Tuple[str, ...] = ()          # 命中的规则，供 trace / 调试


def _extract_slots(text: str) -> Dict[str, str]:
    slots: Dict[str, str] = {}
    for hint in _TOPIC_HINTS:
        if hint in text:
            slots["topic"] = hint
            break
    m = _DEADLINE_RE.search(text)
    if m:
        slots["deadline"] = f"{m.group(1)}{m.group(2)}"
    return slots


def _negated_near(text: str, keyword: str) -> bool:
    """关键词所在子句内、且位于关键词之前，是否出现否定标记。"""
    idx = text.find(keyword)
    if idx < 0:
        return False
    # 关键词所在子句的起点 = 它之前最后一个子句分隔符之后。
    start = 0
    for m in _CLAUSE_SEP.finditer(text, 0, idx):
        start = m.end()
    clause_prefix = text[start:idx]
    return any(neg in clause_prefix for neg in _NEGATIONS)


def _use_retrieval(agent: str, slots: Dict[str, str]) -> bool:
    """retrieval policy：哪些意图建议走 RAG。"""
    if agent == "qa":
        return True
    if agent == "planning":
        # 规划在指明主题时可拉取资料；否则只排程，不必检索。
        return bool(slots.get("topic"))
    # mock 当场出题、diagnosis 只读用户事件，均不建议走共享知识库。
    return False


class QARouter:
    """Agent-level hybrid router：用户输入（+可选上下文）→ 目标 agent。

    ``route`` 为纯函数：相同 ``(user_input, context)`` 永远给出相同结论，便于回归。
    """

    def route(
        self,
        user_input: str,
        context: Optional[Dict] = None,
        llm: Optional[LLMEnhancer] = None,
    ) -> RouteResult:
        ctx = context or {}
        text = (user_input or "").strip()
        if not text:
            return RouteResult(agent="qa", reason="空输入，默认 qa", confidence=0.4,
                               signals=("empty",))

        lowered = text.lower()
        slots = _extract_slots(lowered)

        # 1) 显式命令优先：绕过关键词与上下文。
        m = _EXPLICIT_RE.match(lowered)
        if m and m.group(1) in _COMMANDS:
            agent = _COMMANDS[m.group(1)]
            return RouteResult(
                agent=agent,
                reason=f"显式命令 /{m.group(1)}",
                confidence=1.0,
                slots=slots,
                use_retrieval=_use_retrieval(agent, slots),
                signals=("explicit",),
            )

        # 2) 关键词命中（按位置记录），并剔除被否定的意图。
        matched: Dict[str, int] = {}
        negated: Tuple[str, ...] = ()
        for agent, kws in _KEYWORDS.items():
            present = [k for k in kws if k in lowered]
            if not present:
                continue
            if all(_negated_near(lowered, k) for k in present):
                negated = negated + (agent,)
                continue
            matched[agent] = min(lowered.find(k) for k in present)

        # 3) 多轮状态感知：进行中的模拟面试默认续接，
        #    除非用户显式切到 planning/diagnosis，或否定了 mock。
        if ctx.get("active_mock") and "mock" not in negated:
            if not ({"planning", "diagnosis"} & set(matched)):
                return RouteResult(
                    agent="mock",
                    reason="进行中的模拟面试，默认续接（多轮状态感知）",
                    confidence=0.7,
                    slots=slots,
                    use_retrieval=False,
                    signals=("multi_turn",),
                )

        # 4) 单意图 / 多意图。主 agent 按优先级（mock>diagnosis>planning）选，
        #    与旧路由完全一致；handoff 取其余命中意图，按出现位置排序（仅提示）。
        if matched:
            decision = self._decide_from_matched(matched, negated, slots)
        else:
            sig = ("fallback",) + (("negation",) if negated else ())
            decision = RouteResult(
                agent="qa",
                reason="默认 qa" if not negated else "意图被否定，回退 qa",
                confidence=0.4,
                slots=slots,
                use_retrieval=_use_retrieval("qa", slots),
                signals=sig,
            )

        # 5) 可选一次性 LLM 增强：仅在不确定时、且提供了 llm 时，调用至多一次。
        if llm is not None and decision.confidence < _LOW_CONFIDENCE:
            decision = self._maybe_enhance(decision, user_input, llm)

        return decision

    # -- 内部 ----------------------------------------------------------------

    def _decide_from_matched(
        self, matched: Dict[str, int], negated: Tuple[str, ...], slots: Dict[str, str]
    ) -> RouteResult:
        # 主 agent：严格按关键词表优先级，保证与 Phase 1 行为一致。
        primary = next(a for a in _KEYWORDS if a in matched)
        rest = [a for a in matched if a != primary]
        handoffs = tuple(sorted(rest, key=lambda a: matched[a]))

        signals = ("keyword",)
        if negated:
            signals = signals + ("negation",)
        if handoffs:
            signals = signals + ("multi_intent",)
            reason = f"多意图：主「{primary}」，建议转交 {list(handoffs)}"
            confidence = 0.7
        else:
            reason = f"命中「{primary}」关键词"
            confidence = 0.85

        return RouteResult(
            agent=primary,
            reason=reason,
            confidence=confidence,
            slots=slots,
            handoffs=handoffs,
            use_retrieval=_use_retrieval(primary, slots),
            signals=signals,
        )

    def _maybe_enhance(
        self, decision: RouteResult, user_input: str, llm: LLMEnhancer
    ) -> RouteResult:
        """对低置信度结论做一次结构化 LLM 兜底；任何异常/空结果都保持原结论。"""
        try:
            overrides = llm(user_input)
        except Exception:  # noqa: BLE001 - 增强绝不能打断路由
            return decision
        if not overrides:
            return decision

        agent = overrides.get("agent") or decision.agent
        if agent not in ("qa", "planning", "diagnosis", "mock"):
            agent = decision.agent

        slots = dict(decision.slots)
        for key in ("topic", "deadline"):
            if overrides.get(key):
                slots.setdefault(key, overrides[key])

        return RouteResult(
            agent=agent,
            reason=decision.reason + "（LLM 增强）",
            confidence=max(decision.confidence, 0.6),
            slots=slots,
            handoffs=decision.handoffs,
            use_retrieval=_use_retrieval(agent, slots),
            signals=decision.signals + ("llm",),
        )
