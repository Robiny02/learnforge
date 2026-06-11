"""Prompt 拼装（预留）——Design §6c 优先级与 KV-cache prefix。

拼装顺序（从稳定到易变，KV-cache 友好 prefix）：
  System(身份/安全) → Skill(agent 专属能力) → Project Constitution(格式/反模式)
  → Memory(UserProfile 摘要) → Retrieved(检索片段) → Session(handoff_summary) → User Input

冲突解决：越靠后优先级越高，但安全/格式规范不可被 User Input 覆盖；检索证据优先于模型先验。
前 4 段对同一 agent 稳定，作为可缓存 prefix。

> 上下文体量控制**不在这一层**：采用 Claude Code 式**会话级 compaction**——每个 agent turn
> 结束后由 `ManagerAgent.record_turn` 统计 session context tokens，超 `SESSION_COMPACTION_
> THRESHOLD_TOKENS` 才把较旧轮折叠成摘要（pinned 重要结果永不折叠）。这里只负责按序拼装，
> 不做单次 prompt 的 token 计数/裁剪。
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
    """按 Design §6c 顺序拼装。纯字符串拼接；体量由会话级 compaction 控制（见模块 docstring）。"""
    return AssembledPrompt(
        system=system_override or "You are a LearnForge agent. Follow safety and format rules.",
        skill=(skill.spec.system_prompt if skill else ""),
        constitution=constitution,
        memory=memory_summary,
        retrieved=retrieved,
        session=handoff_summary,
        user_input=user_input,
    )
