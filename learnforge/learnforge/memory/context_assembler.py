"""Prompt 拼装（预留）——Design §6c 优先级与 KV-cache prefix。

拼装顺序（从稳定到易变，KV-cache 友好 prefix）：
  System(身份/安全) → Skill(agent 专属能力) → Project Constitution(格式/反模式)
  → Memory(UserProfile 摘要) → Retrieved(检索片段) → Session(handoff_summary) → User Input

冲突解决：越靠后优先级越高，但安全/格式规范不可被 User Input 覆盖；检索证据优先于模型先验。
前 4 段对同一 agent 稳定，作为可缓存 prefix。

这一层把 skill（system_prompt）、memory（作用域切片 + handoff_summary）、检索片段组装到一起，
是"agent 工作流由 skill 控制"的拼装点。Phase 1：返回结构化分段，不真正调用 LLM。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..skills.base import Skill


@dataclass
class AssembledPrompt:
    """分段保存，便于 KV-cache prefix 命中观测（Design §6c）。"""

    system: str = ""              # 稳定 prefix 段 1
    skill: str = ""               # 稳定 prefix 段 2
    constitution: str = ""        # 稳定 prefix 段 3
    memory: str = ""              # 稳定 prefix 段 4（UserProfile 摘要）
    retrieved: str = ""           # 易变尾部
    session: str = ""             # 易变尾部（handoff_summary）
    user_input: str = ""          # 易变尾部

    def cacheable_prefix(self) -> str:
        return "\n\n".join(p for p in [self.system, self.skill, self.constitution, self.memory] if p)

    def volatile_suffix(self) -> str:
        return "\n\n".join(p for p in [self.retrieved, self.session, self.user_input] if p)

    def full(self) -> str:
        prefix, suffix = self.cacheable_prefix(), self.volatile_suffix()
        return "\n\n".join(p for p in [prefix, suffix] if p)


def assemble_prompt(
    skill: Optional[Skill] = None,
    constitution: str = "",
    memory_summary: str = "",
    retrieved: str = "",
    handoff_summary: str = "",
    user_input: str = "",
    system_override: Optional[str] = None,
) -> AssembledPrompt:
    """按 Design §6c 顺序拼装。Phase 1：纯字符串拼接，不做 token 计数与裁剪。"""
    return AssembledPrompt(
        system=system_override or "You are a LearnForge agent. Follow safety and format rules.",
        skill=(skill.spec.system_prompt if skill else ""),
        constitution=constitution,
        memory=memory_summary,
        retrieved=retrieved,
        session=handoff_summary,
        user_input=user_input,
    )
