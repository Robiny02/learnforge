"""记忆日志面板 —— 记忆系统的可观测入口（REQUIREMENTS 面板需求 §1-§4）。

不是普通 debug log，而是把"每轮对话的记忆读 / 注入 / 写 / 维护"过程显式记录下来，
让用户与开发者能看到：这轮有没有想起过去？想起了哪些？有没有记下新内容？为什么记 / 不记。

设计取舍（保持简洁）：
- 一个进程级单例 `MEMORY_LOG`，按轮(`reset()`)收集 `MemoryEvent`；本地单用户调试足够，
  并发场景非本阶段目标（与既有 LLM/EMBEDDINGS 单例同构）。
- 本模块零依赖（不 import 存储/agent），避免循环导入；overview 查询用惰性导入。
- 渲染只产出纯文本行，配色/面板留给 CLI 层包装（分层）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

# 日志四类（REQUIREMENTS §3）：读取 / 注入 / 写入 / 维护。
READ = "read"
INJECT = "inject"
WRITE = "write"
MAINTAIN = "maintain"

_CATEGORY_LABEL = {READ: "读取", INJECT: "注入", WRITE: "写入", MAINTAIN: "维护"}


@dataclass
class MemoryEvent:
    """一条记忆操作记录（REQUIREMENTS §2.3：类型 / 时间 / 结果 / 简短原因）。"""

    category: str            # read | inject | write | maintain
    action: str              # 操作类型，如"搜索长期记忆""保存记忆""跳过保存"
    result: str              # 操作结果，如"命中 3 条，采用 2 条""成功，类型 weak"
    reason: str = ""         # 简短原因（可选）
    count: Optional[int] = None  # 注入类记录的条数（用于"最终注入了几条"统计）
    ts: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))

    def render(self) -> str:
        line = f"{self.ts} {self.action}：{self.result}"
        if self.reason:
            line += f"（{self.reason}）"
        return line


class MemoryLog:
    """按轮收集记忆操作；`reset()` 清空、`record()` 追加、`render_lines()` 渲染。"""

    def __init__(self) -> None:
        self._events: List[MemoryEvent] = []
        self.enabled: bool = True

    def reset(self) -> None:
        self._events = []

    def record(self, category: str, action: str, result: str,
               reason: str = "", count: Optional[int] = None) -> None:
        if self.enabled:
            self._events.append(MemoryEvent(category, action, result, reason, count))

    @property
    def events(self) -> List[MemoryEvent]:
        return list(self._events)

    # ---------------- 统计（REQUIREMENTS §4 第一版最小字段）----------------
    def summary(self) -> dict:
        ev = self._events

        def has(action_kw: str) -> bool:
            return any(action_kw in e.action for e in ev)

        injected = sum(e.count or 0 for e in ev if e.category == INJECT)
        save = next((e for e in ev if e.category == WRITE and "保存记忆" in e.action), None)
        skip = next((e for e in ev if e.category == WRITE and "跳过保存" in e.action), None)
        return {
            "loaded_memory_md": any(
                e.category == READ and "稳定记忆" in e.action and "为空" not in e.result
                for e in ev
            ),
            "loaded_session": any(
                e.category == READ and "会话记忆" in e.action and "无历史" not in e.result
                for e in ev
            ),
            "searched_daily": has("搜索长期记忆"),
            "injected_count": injected,
            "extracted_candidate": has("提取候选记忆"),
            "saved": save is not None,
            "save_result": save.result if save else (skip.result if skip else "无写入"),
            "skip_reason": (skip.reason or skip.result) if skip and not save else "",
        }

    # ---------------- 渲染（纯文本，CLI 层再包面板）----------------
    def render_lines(self) -> List[str]:
        """按时间顺序的「最近操作」行（REQUIREMENTS §2.3）。空 → 一行提示。"""
        if not self._events:
            return ["（本轮无记忆操作）"]
        return [e.render() for e in self._events]

    def summary_lines(self) -> List[str]:
        """§4 第一版最小摘要的人读版。"""
        s = self.summary()
        yn = lambda b: "是" if b else "否"  # noqa: E731
        lines = [
            f"加载 MEMORY.md：{yn(s['loaded_memory_md'])}",
            f"加载会话记忆：{yn(s['loaded_session'])}",
            f"搜索长期记忆：{yn(s['searched_daily'])}",
            f"最终注入：{s['injected_count']} 条",
            f"提取候选记忆：{yn(s['extracted_candidate'])}",
        ]
        if s["saved"]:
            lines.append(f"保存记忆：{s['save_result']}")
        elif s["skip_reason"]:
            lines.append(f"未保存：{s['skip_reason']}")
        else:
            lines.append("保存记忆：本轮无写入")
        return lines


# 进程级单例（按轮 reset）。
MEMORY_LOG = MemoryLog()


# ---------------------------------------------------------------------------
# 概览（REQUIREMENTS §2.1 记忆文件 / §2.2 记忆索引）——惰性导入，best-effort。
# ---------------------------------------------------------------------------
def memory_files_overview(db_path: Optional[str] = None) -> List[str]:
    """当前系统存在哪些记忆来源（§2.1）。读失败的项标注，不抛异常。"""
    from .. import config
    from .files import daily_files, root_memory_path

    lines: List[str] = []
    try:
        md = root_memory_path()
        lines.append(f"MEMORY.md（稳定规则）：{'存在' if md.exists() else '缺失'}")
    except Exception:
        lines.append("MEMORY.md（稳定规则）：未知")
    try:
        n = len(daily_files())
        lines.append(f"daily memory：{n} 个日期文件")
    except Exception:
        lines.append("daily memory：未知")
    try:
        from ..storage.repositories import SessionStateRepository  # noqa: F401
        from ..storage.db import get_connection

        conn = get_connection(db_path)
        row = conn.execute("SELECT COUNT(*) AS c FROM session_state").fetchone()
        lines.append(f"session_memory：{row['c']} 个会话")
    except Exception:
        lines.append("session_memory：未知")
    try:
        from ..storage.db import get_connection

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM user_profile"
        ).fetchone()
        lines.append(f"用户偏好 / 项目规则：{row['c']} 份档案（权威来源为 MEMORY.md）")
    except Exception:
        lines.append("用户偏好 / 项目规则：未知")
    return lines


def estimate_tokens(text: str) -> int:
    """粗略 token 估算（前端展示用，非计费精度）：CJK 1 字≈1 token，其余按 len/4。"""
    if not text:
        return 0
    cjk = sum(
        1
        for ch in text
        if "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ" or "가" <= ch <= "힣"
    )
    other = len(text) - cjk
    return int(cjk + other / 4 + 0.999)


def _first_meaningful_line(text: str, limit: int = 48) -> str:
    """取首个非空、非引用(>)行作摘要；优先 markdown 标题正文。"""
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line or line.startswith(">"):
            continue
        return line[:limit]
    return ""


def prompt_load_overview(
    session_id: Optional[str] = None, db_path: Optional[str] = None
) -> List[dict]:
    """本轮注入 prompt 的「文件/来源」概览：[{kind, name, summary, tokens}]。

    只覆盖每轮稳定注入的部分（MEMORY.md + 会话短期记忆）；按需召回的 daily 片段
    属于 RetrievalAgent，作为事件出现在记忆日志里（READ/INJECT「搜索/注入长期记忆」）。
    全 best-effort，任意来源读失败即跳过，不抛异常。
    """
    out: List[dict] = []
    try:
        from .files import read_root_memory

        text = read_root_memory()
        if text:
            out.append({
                "kind": "稳定",
                "name": "MEMORY.md",
                "summary": _first_meaningful_line(text),
                "tokens": estimate_tokens(text),
            })
    except Exception:
        pass
    if session_id:
        try:
            from ..storage.repositories import SessionStateRepository

            st = SessionStateRepository(db_path=db_path).get(session_id)
            if st:
                parts: List[str] = []
                if st.get("summary"):
                    parts.append(str(st["summary"]))
                rounds = st.get("recent_messages") or []
                for r in rounds:
                    parts.append(f"用户：{r.get('user', '')}\n回复：{r.get('reply', '')}")
                joined = "\n".join(parts)
                if joined:
                    out.append({
                        "kind": "会话",
                        "name": f"session:{session_id}",
                        "summary": f"{len(rounds)} 轮原文 + {'早期摘要' if st.get('summary') else '无摘要'}",
                        "tokens": estimate_tokens(joined),
                    })
        except Exception:
            pass
    return out


def memory_panel_payload(
    session_id: Optional[str] = None, db_path: Optional[str] = None
) -> dict:
    """打包给前端的记忆面板：本轮事件 + 最小摘要 + 注入来源(含 token)。"""
    return {
        "events": [
            {
                "category": e.category,
                "action": e.action,
                "result": e.result,
                "reason": e.reason,
                "ts": e.ts,
            }
            for e in MEMORY_LOG.events
        ],
        "summary": MEMORY_LOG.summary(),
        "loaded": prompt_load_overview(session_id, db_path),
    }


def memory_index_overview(db_path: Optional[str] = None) -> List[str]:
    """长期记忆的分类概览（§2.2）：按 kind 统计本地 daily 记忆条数。"""
    from ..storage.db import get_connection

    label = {"qa": "QA 总结", "weak": "weak points", "mock": "mock 反馈",
             "progress": "学习进展", "decision": "项目决策", "note": "普通记录"}
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            "SELECT json_extract(metadata,'$.kind') AS kind, COUNT(*) AS c "
            "FROM chunks WHERE kb_scope='local' "
            "AND json_extract(metadata,'$.origin')='daily' GROUP BY kind"
        ).fetchall()
    except Exception:
        return ["（索引概览不可用）"]
    if not rows:
        return ["（长期记忆为空）"]
    return [f"{label.get(r['kind'], r['kind'] or '未分类')}：{r['c']} 条" for r in rows]
