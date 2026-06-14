"""Notion 集成（包内 canonical）：把学习计划渲染成美观 Notion 页 + 注册成 notion.sync 工具。

- 凭据从 .env 的 NOTION_TOKEN 读；父页面取 NOTION_PARENT_PAGE（默认集成可访问的页）。
- create_learning_note(...) 直接建页；notion_sync_handler(args) 是给 ReactRunner 的 tool handler。
- 离线/无 token/失败 → 返回 {"error":...}，不抛出（调用方据此降级）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _parent() -> str:
    return os.environ.get("NOTION_PARENT_PAGE", "372638f6-24c0-8052-85e0-fac925dcbbe5")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ.get('NOTION_TOKEN','')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def available() -> bool:
    return bool(os.environ.get("NOTION_TOKEN")) and httpx is not None


def _rt(text: str) -> List[dict]:
    return [{"type": "text", "text": {"content": str(text)[:1900]}}]


def _block(btype: str, **payload) -> dict:
    return {"object": "block", "type": btype, btype: payload}


def _callout(text: str, emoji: str = "💡", color: str = "blue_background") -> dict:
    return _block("callout", rich_text=_rt(text), icon={"emoji": emoji}, color=color)


def _normalize_days(days: Any) -> Dict[int, List[str]]:
    """容忍模型给的多种结构：{day:[...]}, [{day,items}], [[...]], 或扁平 list。"""
    out: Dict[int, List[str]] = {}
    if isinstance(days, dict):
        for k, v in days.items():
            try:
                idx = int(k)
            except Exception:
                idx = len(out)
            out[idx] = [str(x) for x in (v or [])]
    elif isinstance(days, list):
        for i, v in enumerate(days):
            if isinstance(v, dict) and "items" in v:
                idx = int(v.get("day", i))
                out[idx] = [str(x) for x in (v.get("items") or [])]
            elif isinstance(v, list):
                out[i] = [str(x) for x in v]
            else:
                out.setdefault(0, []).append(str(v))
    return out


def create_learning_note(
    title: str,
    summary: str,
    days: Any,
    tips: Optional[List[str]] = None,
    parent_id: Optional[str] = None,
) -> Dict[str, str]:
    if not available():
        return {"error": "NOTION_TOKEN 未配置或 httpx 不可用"}
    parent_id = parent_id or _parent()
    day_map = _normalize_days(days)
    children: List[dict] = [
        _callout(summary or "学习计划", emoji="🎯", color="green_background"),
        _block("divider"),
    ]
    for day in sorted(day_map):
        children.append(_block("heading_2", rich_text=_rt(f"📅 Day {day + 1}")))
        for item in day_map[day]:
            children.append(_block("to_do", rich_text=_rt(item), checked=False))
    if tips:
        children.append(_block("divider"))
        children.append(_callout("下一步建议", emoji="🚀", color="yellow_background"))
        for t in tips:
            children.append(_block("paragraph", rich_text=_rt("• " + str(t))))

    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "icon": {"type": "emoji", "emoji": "📘"},
        "properties": {"title": {"title": _rt(title or "学习计划")}},
        "children": children[:95],  # Notion children 上限保护
    }
    try:
        r = httpx.post(f"{NOTION_API}/pages", headers=_headers(), json=payload, timeout=60.0)
        if r.status_code >= 300:
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        d = r.json()
        return {"url": d.get("url", ""), "id": d.get("id", "")}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# --- tool handler（给 ReactRunner / TOOL_RUNTIME 用）---
def notion_sync_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    return create_learning_note(
        title=args.get("title") or "学习计划",
        summary=args.get("summary") or "",
        days=args.get("days") or {},
        tips=args.get("tips") or [],
    )


_NOTION_PARAMS = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Notion 页面标题"},
        "summary": {"type": "string", "description": "一句话计划摘要（放进顶部 callout）"},
        "days": {
            "type": "object",
            "description": "按天分桶的条目：键是天序号(从0)，值是该天的字符串条目列表。",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
        "tips": {"type": "array", "items": {"type": "string"}, "description": "下一步建议"},
    },
    "required": ["title", "summary", "days"],
}


def register() -> None:
    """把 notion.sync 注册进运行时工具表（幂等）。"""
    from ..tools import openai_bridge as toolmod

    if not toolmod.has_handler("notion.sync"):
        toolmod.register_tool(
            "notion.sync", notion_sync_handler, parameters=_NOTION_PARAMS,
            description="把学习计划发布成一篇美观的 Notion 笔记（图标+callout+按天 checkbox）。",
        )
