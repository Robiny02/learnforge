"""Skill 抽象（预留）——agent 工作流的可控单元。

设计动机（用户要求 + Design §6c）：
很多 agent 的工作流应由 **skill** 驱动，而非把 prompt / 模型档位 / 可用工具 / 步骤
硬编码进 agent 实现。一个 Skill 把以下东西打包成一个可注册、可替换的能力单元：

  - system_prompt：该 agent 的身份/边界/反模式（Design §3 各节"System Prompt 大纲"）
  - model_tier：Haiku / Sonnet（Design §3 各节 Bounds）
  - allowed_tools：可调用的 MCP/本地工具名（经 mcp.registry 解析，MVP 为空）
  - input/output schema：绑定 contracts 里的 Pydantic 模型，保证结构化输出
  - steps：可选的工作流步骤声明（供 agent 编排，如 QA 的 route→retrieve→synth→verify）

Phase 1：仅定义协议与数据结构，run() 为 stub。Phase 2/3 再填实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel

from ..contracts.enums import AgentId, ModelTier


@dataclass
class SkillSpec:
    """声明式 skill 定义（一个 agent 可绑定一个或多个 skill）。"""

    name: str
    agent_id: AgentId
    model_tier: ModelTier
    system_prompt: str = ""
    allowed_tools: List[str] = field(default_factory=list)  # MCP/工具名，经 mcp.registry 解析
    input_schema: Optional[Type[BaseModel]] = None
    output_schema: Optional[Type[BaseModel]] = None
    steps: List[str] = field(default_factory=list)  # 工作流步骤声明（可选）
    metadata: Dict[str, Any] = field(default_factory=dict)


class Skill:
    """运行期 skill 句柄。Phase 1 为 stub：不调用 LLM，直接回显占位。

    真实现（Phase 2+）将在此：组装 prompt（经 memory.context_assembler）、
    解析 allowed_tools（经 mcp.registry）、调用 llm.client、校验 output_schema。
    """

    def __init__(self, spec: SkillSpec, handler: Optional[Callable[[BaseModel], BaseModel]] = None):
        self.spec = spec
        self._handler = handler

    def run(self, payload: BaseModel) -> BaseModel:
        """执行 skill。Phase 1：若提供 handler 则调用，否则抛未实现。"""
        if self._handler is not None:
            return self._handler(payload)
        raise NotImplementedError(
            f"Skill '{self.spec.name}' ({self.spec.agent_id}) 尚未实现（Phase 1 stub）。"
        )
