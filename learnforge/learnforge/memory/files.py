"""记忆文件层（OpenClaw 风格，真值 = 人类可读 markdown）。

- `MEMORY.md`：稳定规则/偏好，整段加载进可缓存 prefix（不索引、不衰减）。
- `memory/YYYY-MM-DD.md`：每日记录（QA 结论 / mock 反馈 / 薄弱点），每条是可解析的 note 块：

    ## <iso8601> · <kind> · <topic>
    importance: <0..1>
    <正文……>

这些文件是真值；SQLite 的 chunks(kb_scope='local') 是可重建的派生索引（见 memory/daily.py）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from .. import config

_DEFAULT_MEMORY_MD = """# MEMORY

> 稳定、高优先级、人工可读的长期规则与偏好。每轮整段加载，请保持简短。
> 不要在此堆历史对话或检索内容——那些进 memory/YYYY-MM-DD.md 并按需召回。

## 用户目标
- （示例）准备后端面试，主攻并发、Redis、JVM。

## 回答风格
- 简洁、给可核验要点；无证据时降低断言强度。

## 长期项目约定
- （示例）代码用 Python 3.9 兼容写法。
"""


def memory_root(root: Optional[Union[str, Path]] = None) -> Path:
    """记忆根目录（默认 config.MEMORY_DIR，运行时读取以便测试隔离）。"""
    return Path(root) if root is not None else Path(config.MEMORY_DIR)


def _ensure_root(root: Optional[Union[str, Path]] = None) -> Path:
    p = memory_root(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _normalize_dt(date: Union[str, datetime, None]) -> datetime:
    """None→现在(UTC)；'YYYY-MM-DD' / ISO8601 → datetime；naive 视为 UTC。"""
    if date is None:
        return datetime.now(timezone.utc)
    if isinstance(date, datetime):
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# MEMORY.md（稳定规则层）
# ---------------------------------------------------------------------------
def root_memory_path(root: Optional[Union[str, Path]] = None) -> Path:
    return memory_root(root) / "MEMORY.md"


def read_root_memory(root: Optional[Union[str, Path]] = None) -> str:
    """读 MEMORY.md；缺失则用内置模板创建并返回。读失败 → 空串。"""
    path = root_memory_path(root)
    try:
        if not path.exists():
            _ensure_root(root)
            path.write_text(_DEFAULT_MEMORY_MD, encoding="utf-8")
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_USER_RULES_SECTION = "## 用户补充偏好"


def append_root_memory(text: str, root: Optional[Union[str, Path]] = None) -> bool:
    """把一条稳定规则追加进 MEMORY.md 的「用户补充偏好」段（REQUIREMENTS R3.3 受控写入）。

    仅在用户明确要求记住时调用（稳定层不自动更新）。段不存在则创建；已存在同条目则跳过去重。
    返回是否写入。读写失败 → False（best-effort，不阻断主链路）。
    """
    line = (text or "").strip()
    if not line:
        return False
    bullet = f"- {line}"
    path = root_memory_path(root)
    try:
        read_root_memory(root)  # 确保文件按模板存在
        content = path.read_text(encoding="utf-8")
        if bullet in content:
            return False  # 已记录，避免稳定层重复堆积
        if _USER_RULES_SECTION in content:
            new = content.rstrip() + f"\n{bullet}\n"
        else:
            new = content.rstrip() + f"\n\n{_USER_RULES_SECTION}\n{bullet}\n"
        path.write_text(new, encoding="utf-8")
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# daily 记录（每日学习记录）
# ---------------------------------------------------------------------------
def daily_path(
    date: Union[str, datetime, None] = None, root: Optional[Union[str, Path]] = None
) -> Path:
    day = _normalize_dt(date).date().isoformat()
    return memory_root(root) / f"{day}.md"


def append_daily(
    kind: str,
    text: str,
    topic: Optional[str] = None,
    date: Union[str, datetime, None] = None,
    importance: Optional[float] = None,
    root: Optional[Union[str, Path]] = None,
) -> dict:
    """往当日文件追加一条 note 块（真值）。返回 {date(iso), kind, topic, text}。"""
    dt = _normalize_dt(date)
    iso = dt.isoformat()
    body = (text or "").strip()
    imp = importance if importance is not None else config.MEMORY_IMPORTANCE.get(
        kind, config.MEMORY_IMPORTANCE_DEFAULT
    )
    heading = f"## {iso} · {kind}" + (f" · {topic}" if topic else "")
    block = f"{heading}\nimportance: {imp:.2f}\n{body}\n\n"
    path = daily_path(dt, root)
    _ensure_root(root)
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return {"date": iso, "kind": kind, "topic": topic,
            "importance": float(imp), "text": body, "content": body}


def daily_files(root: Optional[Union[str, Path]] = None) -> List[Path]:
    p = memory_root(root)
    if not p.exists():
        return []
    return sorted(f for f in p.glob("*.md") if f.name != "MEMORY.md")


def iter_daily_notes(root: Optional[Union[str, Path]] = None) -> List[dict]:
    """扫描所有 daily 文件，把 note 块解析回 [{date, kind, topic, text}]（供 reindex）。"""
    notes: List[dict] = []

    def _finish_note(cur: dict, buf: List[str]) -> None:
        raw = "\n".join(buf).strip()
        lines = raw.splitlines()
        importance = None
        body_start = 0
        if lines and lines[0].lower().startswith("importance:"):
            try:
                importance = float(lines[0].split(":", 1)[1].strip())
            except ValueError:
                importance = None
            body_start = 1
        if body_start < len(lines) and lines[body_start].strip().lower() == "content:":
            body_start += 1
        text = "\n".join(lines[body_start:]).strip()
        cur["importance"] = (
            importance
            if importance is not None
            else config.MEMORY_IMPORTANCE.get(cur.get("kind"), config.MEMORY_IMPORTANCE_DEFAULT)
        )
        cur["text"] = text
        cur["content"] = text
        notes.append(cur)

    for path in daily_files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        cur: Optional[dict] = None
        buf: List[str] = []
        for line in lines:
            if line.startswith("## "):
                if cur is not None:
                    _finish_note(cur, buf)
                parts = [p.strip() for p in line[3:].split(" · ")]
                cur = {
                    "date": parts[0] if parts else "",
                    "kind": parts[1] if len(parts) > 1 else "note",
                    "topic": parts[2] if len(parts) > 2 else None,
                }
                buf = []
            elif cur is not None:
                buf.append(line)
        if cur is not None:
            _finish_note(cur, buf)
    return notes
