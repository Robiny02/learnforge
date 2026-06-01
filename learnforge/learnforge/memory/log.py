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
