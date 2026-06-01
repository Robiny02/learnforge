"""Notion 集成：把 LearnForge 学习计划渲染成美观的 Notion 页面。

- 读 .env 的 NOTION_TOKEN；父页面取 NOTION_PARENT_PAGE（默认用集成可访问的 "notion" 页）。
- create_learning_note(title, summary, days, tips) -> 创建子页面，返回 url。
- 既可被 planning agent 当 tool（notion.sync）调用，也可直接 CLI 运行做演示。
"""

from __future__ import annotations

import os
import pathlib
from typing import Dict, List, Optional

import httpx

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_PARENT = os.environ.get("NOTION_PARENT_PAGE", "372638f6-24c0-8052-85e0-fac925dcbbe5")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ.get('NOTION_TOKEN','')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rt(text: str) -> List[dict]:
    return [{"type": "text", "text": {"content": text}}]


def _callout(text: str, emoji: str = "💡", color: str = "blue_background") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": _rt(text), "icon": {"emoji": emoji}, "color": color}}


def _h2(text: str) -> dict:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rt(text)}}


def _todo(text: str) -> dict:
    return {"object": "block", "type": "to_do",
            "to_do": {"rich_text": _rt(text), "checked": False}}


def _para(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(text)}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def create_learning_note(
    title: str,
    summary: str,
    days: Dict[int, List[str]],
    tips: Optional[List[str]] = None,
    parent_id: Optional[str] = None,
) -> Dict[str, str]:
    """days: {day_index: [条目文本,...]}。返回 {url, id} 或 {error}。"""
    parent_id = parent_id or DEFAULT_PARENT
    children: List[dict] = [
        _callout(summary, emoji="🎯", color="green_background"),
        _divider(),
    ]
    for day in sorted(days):
        children.append(_h2(f"📅 Day {day + 1}"))
        for item in days[day]:
            children.append(_todo(item))
    if tips:
        children.append(_divider())
        children.append(_callout("下一步建议", emoji="🚀", color="yellow_background"))
        for t in tips:
            children.append(_para("• " + t))

    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "icon": {"type": "emoji", "emoji": "📘"},
        "properties": {"title": {"title": _rt(title)}},
        "children": children,
    }
    try:
        r = httpx.post(f"{NOTION_API}/pages", headers=_headers(), json=payload, timeout=60.0)
        if r.status_code >= 300:
            return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
        d = r.json()
        return {"id": d.get("id", ""), "url": d.get("url", "")}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _load_committed_plan(db_path: str) -> Dict[int, List[str]]:
    """从 learnforge.db 读已落库的学习计划，按 day_index 分组成可读条目。"""
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT pi.day_index, pi.order_idx, ka.topic, ka.title "
        "FROM path_items pi JOIN knowledge_atoms ka ON pi.atom_id=ka.atom_id "
        "ORDER BY pi.order_idx"
    ).fetchall()
    days: Dict[int, List[str]] = {}
    for r in rows:
        days.setdefault(r["day_index"], []).append(f"[{r['topic']}] {r['title']}")
    return days


if __name__ == "__main__":
    db = str(REPO_ROOT / "learnforge" / "learnforge.db")
    days = _load_committed_plan(db)
    if not days:
        days = {0: ["[concurrency] 乐观锁 vs 悲观锁", "[database] B+树索引"],
                1: ["[concurrency] 线程池核心参数"]}
    out = create_learning_note(
        title="字节面试复习计划 · 并发 & 数据库",
        summary="基于 LearnForge 弱点诊断生成的 2 天复习计划，覆盖并发与数据库薄弱知识点。",
        days=days,
        tips=["完成每日 checkbox 后做一场模拟面试巩固", "薄弱点会自动进入下次诊断"],
    )
    print(out)
