"""会话上下文装配（阶段一）——把已存储的会话记忆装配成一段**有界**的 session_context。

阶段一修复的核心问题：pinned/summary/最近原文这套会话记忆已实现，但产物从未进入子 agent
的 prompt（在 dispatch 前被丢弃）。这里提供单一真值的装配器：

  - `ManagerAgent.execute_dynamic` 注入 QA / planning 的 session 槽用它；
  - `memory.log.prompt_load_overview`（记忆面板）报告"本轮真实注入了哪些来源 + token"也用它。

两边共用同一个 `build_session_context`，面板不再各算一套、也不再谎报注入。

装配内容（来源 → 段）：
  - session_state.active_task.pinned         → 【重要结果·固定】
  - session_state.summary                    → 【早期会话摘要】
  - session_state.recent_messages            → 【最近对话】
  - dialogue_turns 最近轮的 tool_calls       → 【最近调用】(子 agent 投影，轻量)
  - dialogue_turns 最近轮的 document/image   → 【可引用附件】(文件名 + document_id)

预算：每段先各自按 `SESSION_CONTEXT_*_TOKENS` 裁断，再对整段加一道
`SESSION_CONTEXT_MAX_TOKENS` 全局天花板——绝不把 pinned 全文无上限塞进 prompt。

纯读、离线安全、不写任何状态（Manager 仍是唯一写者；本模块只读取已落库的会话记忆）。
Python 3.9 兼容：用 typing.Optional/List/Tuple，不用 `X | None`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .. import config
from .tokens import count_tokens, truncate_to_tokens


@dataclass
class ContextSection:
    """会话上下文的一段：kind 供面板分类，label 是注入 prompt 时的小标题。"""

    kind: str          # pinned | summary | recent | tools | attachments
    label: str
    text: str

    @property
    def tokens(self) -> int:
        return count_tokens(self.text)


@dataclass
class SessionContext:
    """装配结果：分段保存（便于面板逐源报 token），render() 拼成注入用的单串。"""

    sections: List[ContextSection] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(s.text.strip() for s in self.sections)

    def render(self) -> str:
        """拼成注入子 agent session 槽的单串；整体再加一道全局预算天花板。"""
        body = "\n\n".join(f"{s.label}\n{s.text}" for s in self.sections if s.text.strip())
        if not body:
            return ""
        return truncate_to_tokens(body, config.SESSION_CONTEXT_MAX_TOKENS)

    def total_tokens(self) -> int:
        return count_tokens(self.render())


def build_session_context(
    session_id: Optional[str], db_path: Optional[str] = None
) -> SessionContext:
    """装配有界 session_context。无 session_id / 无历史 → 空（is_empty()）。全 best-effort。"""
    ctx = SessionContext()
    if not session_id:
        return ctx

    st = _session_state(session_id, db_path)

    pinned = (st.get("active_task") or {}).get("pinned") or []
    if pinned:
        text = "\n".join(
            f"· 用户：{p.get('user', '')}\n  结论：{p.get('reply', '')}" for p in pinned
        )
        ctx.sections.append(ContextSection(
            "pinned", "【重要结果·固定】",
            truncate_to_tokens(text, config.SESSION_CONTEXT_PINNED_TOKENS)))

    if st.get("summary"):
        ctx.sections.append(ContextSection(
            "summary", "【早期会话摘要】",
            truncate_to_tokens(str(st["summary"]), config.SESSION_CONTEXT_SUMMARY_TOKENS)))

    rounds = st.get("recent_messages") or []
    if rounds:
        text = "\n".join(
            f"· 用户：{r.get('user', '')}\n  回复：{r.get('reply', '')}" for r in rounds
        )
        ctx.sections.append(ContextSection(
            "recent", "【最近对话】",
            truncate_to_tokens(text, config.SESSION_CONTEXT_RECENT_TOKENS)))

    proj, attach = _recent_projection(session_id, db_path)
    if proj:
        ctx.sections.append(ContextSection(
            "tools", "【最近调用】",
            truncate_to_tokens(proj, config.SESSION_CONTEXT_TOOLS_TOKENS)))
    if attach:
        ctx.sections.append(ContextSection(
            "attachments", "【可引用附件】",
            truncate_to_tokens(attach, config.SESSION_CONTEXT_ATTACH_TOKENS)))

    return ctx


def _session_state(session_id: str, db_path: Optional[str]) -> dict:
    try:
        from ..storage.repositories import SessionStateRepository

        return SessionStateRepository(db_path=db_path).get(session_id) or {}
    except Exception:
        return {}


def _recent_projection(
    session_id: str, db_path: Optional[str]
) -> Tuple[str, str]:
    """从 dialogue_turns 最近轮派生两段轻量信号：

    - 投影：每轮"做了什么 → 调了哪些子 agent"（让后续回答知道最近调用过谁）；
    - 附件：去重的可引用附件（文件名 + document_id），供"刚才那个文件"跨轮定位。

    刻意只取 agent 名 / 文件引用，不塞完整 trace 原文（§5：避免 trace 无脑进 prompt）。
    """
    try:
        from ..storage.repositories import DialogueTurnRepository

        turns = DialogueTurnRepository(db_path=db_path).recent(
            session_id, limit=config.SESSION_CONTEXT_RECENT_ROUNDS)
    except Exception:
        return "", ""

    lines: List[str] = []
    files: "dict[str, Tuple[str, str]]" = {}  # ref → (kind, filename)
    for t in turns:
        agents = [tc.agent for tc in (t.tool_calls or []) if getattr(tc, "agent", "")]
        if agents:
            lines.append(f"· {(t.text or '')[:30]} → {'/'.join(agents)}")
        for a in (t.artifacts or []):
            kind = a.get("kind")
            if kind in ("document", "image"):
                ref = a.get("ref")
                if ref and ref not in files:
                    files[ref] = (kind, a.get("filename") or ref)

    proj = "\n".join(lines)
    attach = "\n".join(
        f"· {fn}（{kind}，document_id={ref}）" for ref, (kind, fn) in files.items()
    )
    return proj, attach
