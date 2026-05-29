"""Agent 基类（Phase 2：skill-driven 调用 + 优雅降级）。

每个 agent 从 SkillRegistry 取自己的 skill（用户要求：工作流由 skill 控制）。
`llm_structured()` 用 skill 的 system_prompt + model_tier 调 LLM 并解析为指定 schema；
无 key / 解析失败时返回 None，由各 agent 回退到自己的 stub，保证链路永远通。
"""

from __future__ import annotations

from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from ..contracts.enums import AgentId
from ..contracts.message import TokenUsage
from ..llm.client import LLM, LLMStructuredError, LLMUnavailable
from ..skills.base import Skill
from ..skills.bootstrap import ensure_skills_registered
from ..skills.registry import SKILL_REGISTRY

T = TypeVar("T", bound=BaseModel)


class BaseAgent:
    agent_id: AgentId

    def __init__(self, agent_id: Optional[AgentId] = None) -> None:
        if agent_id is not None:
            self.agent_id = agent_id
        ensure_skills_registered()
        self.skill: Optional[Skill] = SKILL_REGISTRY.primary(self.agent_id)
        self.last_cost_usd: float = 0.0
        self.last_tokens: TokenUsage = TokenUsage()

    def llm_structured(self, prompt: str, schema: Type[T], max_tokens: int = 1024) -> Optional[T]:
        """skill-driven 结构化调用；不可用/失败返回 None（调用方回退 stub）。"""
        if self.skill is None or not LLM.available:
            return None
        try:
            obj, result = LLM.complete_structured(
                prompt=prompt,
                schema=schema,
                model_tier=self.skill.spec.model_tier,
                system=self.skill.spec.system_prompt,
                max_tokens=max_tokens,
            )
            self.last_cost_usd = result.cost_usd
            self.last_tokens = result.tokens
            return obj
        except (LLMUnavailable, LLMStructuredError):
            return None
