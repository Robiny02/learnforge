"""SkillRegistry（预留）——按 agent 注册/查找 skill。

agent 实现不直接 new skill，而是 `SKILL_REGISTRY.get(AgentId.X)`，从而让 skill 可替换、
可灰度、可按场景切换（如 QA 的轻路径 vs 全路径）。Phase 1 仅登记 SkillSpec，不绑 handler。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..contracts.enums import AgentId
from ..mcp.registry import MCP_REGISTRY
from .base import Skill, SkillSpec


class UnknownToolError(ValueError):
    """Skill 声明了不在 canonical 工具清单内的 allowed_tool（多为 typo）。"""


class ToolNotAllowedError(ValueError):
    """Skill 为某 agent 声明了不归其所有的 capability。"""


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._by_agent: Dict[AgentId, List[str]] = {}

    def register(self, skill: Skill) -> None:
        name = skill.spec.name
        self._skills[name] = skill
        self._by_agent.setdefault(skill.spec.agent_id, []).append(name)

    def register_spec(self, spec: SkillSpec) -> Skill:
        self._validate_tools(spec)
        skill = Skill(spec)
        self.register(skill)
        return skill

    @staticmethod
    def _validate_tools(spec: SkillSpec) -> None:
        """注册期校验：allowed_tools 必须已知，且该 agent 有权声明。"""
        unknown = [t for t in spec.allowed_tools if not MCP_REGISTRY.is_known(t, for_declaration=True)]
        if unknown:
            raise UnknownToolError(
                f"Skill '{spec.name}' ({spec.agent_id}) declares unknown tools: {unknown}"
            )
        denied = [t for t in spec.allowed_tools if not MCP_REGISTRY.may_declare(t, spec.agent_id.value)]
        if denied:
            raise ToolNotAllowedError(
                f"Skill '{spec.name}' ({spec.agent_id}) may not declare tools: {denied}"
            )
        strategy_tools = []
        for rule in spec.strategy_rules.values():
            strategy_tools.extend(rule.get("tools", []))
        missing = [t for t in strategy_tools if t not in spec.allowed_tools]
        if missing:
            raise ToolNotAllowedError(
                f"Skill '{spec.name}' strategy references tools not in allowed_tools: {missing}"
            )

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def for_agent(self, agent_id: AgentId) -> List[Skill]:
        return [self._skills[n] for n in self._by_agent.get(agent_id, [])]

    def primary(self, agent_id: AgentId) -> Optional[Skill]:
        """返回该 agent 的首选 skill（Phase 1 取第一个注册的）。"""
        skills = self.for_agent(agent_id)
        return skills[0] if skills else None

    def select(
        self,
        agent_id: AgentId,
        intent: Optional[str] = None,
        event: Optional[str] = None,
        text: str = "",
    ) -> List[Skill]:
        """Progressive skill trigger selection.

        Only metadata is inspected here. Full SKILL/SOP sections stay unloaded
        until an agent actually needs instructions.
        """
        candidates = self.for_agent(agent_id)
        if not candidates:
            return []
        selected: List[Skill] = []
        normalized = (text or "").lower()
        for skill in candidates:
            triggers = skill.spec.triggers or {}
            intents = triggers.get("intents", [])
            events = triggers.get("events", []) + triggers.get("state_events", [])
            keywords = triggers.get("keywords", [])
            if intent and intent in intents:
                selected.append(skill)
                continue
            if event and event in events:
                selected.append(skill)
                continue
            if normalized and any(k.lower() in normalized for k in keywords):
                selected.append(skill)
        return selected or [candidates[0]]


# 全局单例（Phase 1 空表；Phase 2/3 在 skills/definitions/ 中填充并注册）。
SKILL_REGISTRY = SkillRegistry()
