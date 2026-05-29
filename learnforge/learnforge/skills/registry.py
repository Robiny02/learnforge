"""SkillRegistry（预留）——按 agent 注册/查找 skill。

agent 实现不直接 new skill，而是 `SKILL_REGISTRY.get(AgentId.X)`，从而让 skill 可替换、
可灰度、可按场景切换（如 QA 的轻路径 vs 全路径）。Phase 1 仅登记 SkillSpec，不绑 handler。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..contracts.enums import AgentId
from .base import Skill, SkillSpec


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._by_agent: Dict[AgentId, List[str]] = {}

    def register(self, skill: Skill) -> None:
        name = skill.spec.name
        self._skills[name] = skill
        self._by_agent.setdefault(skill.spec.agent_id, []).append(name)

    def register_spec(self, spec: SkillSpec) -> Skill:
        skill = Skill(spec)
        self.register(skill)
        return skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def for_agent(self, agent_id: AgentId) -> List[Skill]:
        return [self._skills[n] for n in self._by_agent.get(agent_id, [])]

    def primary(self, agent_id: AgentId) -> Optional[Skill]:
        """返回该 agent 的首选 skill（Phase 1 取第一个注册的）。"""
        skills = self.for_agent(agent_id)
        return skills[0] if skills else None


# 全局单例（Phase 1 空表；Phase 2/3 在 skills/definitions/ 中填充并注册）。
SKILL_REGISTRY = SkillRegistry()
