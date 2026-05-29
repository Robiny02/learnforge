"""skills —— 预留：agent 工作流的可控能力单元（由 SkillRegistry 驱动）。"""

from .base import Skill, SkillSpec
from .registry import SKILL_REGISTRY, SkillRegistry

__all__ = ["Skill", "SkillSpec", "SkillRegistry", "SKILL_REGISTRY"]
