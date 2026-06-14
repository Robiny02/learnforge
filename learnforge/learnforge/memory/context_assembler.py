"""固定 Prompt Stack —— 所有子 agent 走同一套有序、分层的 prompt 装配（架构收敛 Step B）。

8 层（从稳定到易变，KV-cache 友好 prefix；每层职责单一、不互相混用）：

  稳定 prefix（每 agent 稳定，可缓存）：
    1. System / Constitution —— 身份 + 安全 + 格式不变量
    2. Project Guide         —— 项目级运行时约束（架构/硬不变量/输出规范，见 PROJECT_GUIDE.md）
    3. User Memory           —— 用户长期稳定偏好（MEMORY.md）
    4. Active Skill          —— 当前 agent 的 SOP（怎么做事，见 skills/sops/）
    5. Tool Registry         —— 当前 agent 能用什么工具 + 边界 + 输出 schema
  易变尾部（每轮变化）：
    6. Conversation State    —— 最近发生了什么 / 有哪些 artifact 可引用（compact summary + recent）
    7. Evidence              —— 这轮回答基于的具体材料（检索/附件召回/repo/工具结果）
    8. User Input            —— 当前用户输入

冲突解决：越靠后优先级越高，但安全/格式不可被 User Input 覆盖；Evidence 优先于模型先验。
前 5 段对同一 agent 稳定，作为可缓存 prefix；后 3 段进 volatile 尾部。

> 体量控制不在这一层：pinned/summary/recent 的折叠由会话级 compaction 负责（见 manager.record_turn），
> 注入预算由 session_context 装配器负责。这里只按固定层序拼装并标注层名（便于模型区分 + 可观测）。

兼容：保留旧参数名（skill/constitution/memory_summary/retrieved/handoff_summary/system_override）作别名，
映射到对应新层，老调用/测试不破。Python 3.9 兼容。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..skills.base import Skill

_DEFAULT_SYSTEM = "You are a LearnForge agent. Follow safety and format rules."

# (字段名, 层标题)。顺序即拼装顺序。
_PREFIX_LAYERS: List[Tuple[str, str]] = [
    ("system", "【System / Constitution】"),
    ("project_guide", "【Project Guide】"),
    ("user_memory", "【User Memory】"),
    ("active_skill", "【Active Skill】"),
    ("tool_registry", "【Tool Registry】"),
]
_SUFFIX_LAYERS: List[Tuple[str, str]] = [
    ("conversation_state", "【Conversation State】"),
    ("evidence", "【Evidence】"),
    ("user_input", "【User Input】"),
]


@dataclass
class AssembledPrompt:
    """8 层分段保存，便于 KV-cache prefix 命中观测与可观测面板逐层报 token。"""

    system: str = ""               # 稳定 1
    project_guide: str = ""        # 稳定 2
    user_memory: str = ""          # 稳定 3
    active_skill: str = ""         # 稳定 4
    tool_registry: str = ""        # 稳定 5
    conversation_state: str = ""   # 易变 6
    evidence: str = ""             # 易变 7
    user_input: str = ""           # 易变 8

    def _join(self, layers: List[Tuple[str, str]]) -> str:
        out = []
        for attr, label in layers:
            val = (getattr(self, attr) or "").strip()
            if val:
                out.append(f"{label}\n{val}")
        return "\n\n".join(out)

    def cacheable_prefix(self) -> str:
        return self._join(_PREFIX_LAYERS)

    def volatile_suffix(self) -> str:
        return self._join(_SUFFIX_LAYERS)

    def full(self) -> str:
        prefix, suffix = self.cacheable_prefix(), self.volatile_suffix()
        return "\n\n".join(p for p in [prefix, suffix] if p)

    def layer_sizes(self) -> List[Tuple[str, str, str]]:
        """[(attr, label, text)]（非空层），供可观测面板逐层报 token（Step G）。"""
        return [(a, lbl, getattr(self, a))
                for a, lbl in (_PREFIX_LAYERS + _SUFFIX_LAYERS)
                if (getattr(self, a) or "").strip()]


def assemble_prompt(
    *,
    system: Optional[str] = None,
    project_guide: str = "",
    user_memory: str = "",
    active_skill: str = "",
    tool_registry: str = "",
    conversation_state: str = "",
    evidence: str = "",
    user_input: str = "",
    # --- 兼容旧签名（Step B 之前的调用/测试）：映射到对应新层 ---
    skill: Optional[Skill] = None,
    constitution: str = "",
    memory_summary: str = "",
    retrieved: str = "",
    handoff_summary: str = "",
    system_override: Optional[str] = None,
) -> AssembledPrompt:
    """按固定 8 层拼装。新参数优先；未给则回退同义旧参数（兼容）。纯字符串拼接。"""
    return AssembledPrompt(
        system=system if system is not None else (system_override or _DEFAULT_SYSTEM),
        project_guide=project_guide,
        user_memory=user_memory or memory_summary,
        active_skill=active_skill or (skill.spec.system_prompt if skill else ""),
        tool_registry=tool_registry or constitution,
        conversation_state=conversation_state or handoff_summary,
        evidence=evidence or retrieved,
        user_input=user_input,
    )
