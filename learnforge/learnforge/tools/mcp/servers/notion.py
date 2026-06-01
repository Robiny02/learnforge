"""Built-in Notion MCP server.

Tools:
- search_pages: find pages/databases visible to the integration.
- read_page: render a page's child blocks as compact markdown text.
- create_learning_note: publish a LearnForge learning note.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ._common import json_response, schema, serve

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _token() -> str:
    return os.environ.get("NOTION_TOKEN", "")


def _parent() -> str:
    return os.environ.get("NOTION_PARENT_PAGE", "")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not _token():
        return {"error": "NOTION_TOKEN is not configured"}
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{NOTION_API}{path}", data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        return {"error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _plain_text(rich: List[Dict[str, Any]]) -> str:
    return "".join((x.get("plain_text") or "") for x in rich or [])


def _title(page: Dict[str, Any]) -> str:
    props = page.get("properties") or {}
    for prop in props.values():
        if prop.get("type") == "title":
            return _plain_text(prop.get("title") or []) or "Untitled"
    return "Untitled"


def search_pages(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query") or "")
    page_size = max(1, min(int(args.get("page_size") or 10), 25))
    payload = {"query": query, "page_size": page_size, "sort": {"direction": "descending", "timestamp": "last_edited_time"}}
    data = _request("POST", "/search", payload)
    if data.get("error"):
        return json_response(data, is_error=True)
    results = []
    for item in data.get("results") or []:
        results.append({
            "id": item.get("id"),
            "object": item.get("object"),
            "title": _title(item) if item.get("object") == "page" else item.get("title", [{}])[0].get("plain_text", "Untitled"),
            "url": item.get("url"),
            "last_edited_time": item.get("last_edited_time"),
        })
    return json_response({"results": results})


def _render_block(block: Dict[str, Any]) -> str:
    typ = block.get("type")
    body = block.get(typ) or {}
    text = _plain_text(body.get("rich_text") or [])
    if not text:
        return ""
    if typ == "heading_1":
        return f"# {text}"
    if typ == "heading_2":
        return f"## {text}"
    if typ == "heading_3":
        return f"### {text}"
    if typ in {"bulleted_list_item", "to_do"}:
        return f"- {text}"
    if typ == "numbered_list_item":
        return f"1. {text}"
    if typ == "quote":
        return f"> {text}"
    if typ == "code":
        lang = body.get("language") or ""
        return f"```{lang}\n{text}\n```"
    return text


def read_page(args: Dict[str, Any]) -> Dict[str, Any]:
    page_id = str(args.get("page_id") or "")
    if not page_id:
        return json_response({"error": "page_id is required"}, is_error=True)
    max_blocks = max(1, min(int(args.get("max_blocks") or 80), 200))
    blocks: List[str] = []
    cursor: Optional[str] = None
    while len(blocks) < max_blocks:
        suffix = f"?page_size={min(100, max_blocks - len(blocks))}"
        if cursor:
            suffix += f"&start_cursor={cursor}"
        data = _request("GET", f"/blocks/{page_id}/children{suffix}")
        if data.get("error"):
            return json_response(data, is_error=True)
        for block in data.get("results") or []:
            rendered = _render_block(block)
            if rendered:
                blocks.append(rendered)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return json_response({"page_id": page_id, "markdown": "\n\n".join(blocks), "block_count": len(blocks)})


def _rt(text: str) -> List[dict]:
    return [{"type": "text", "text": {"content": str(text)[:1900]}}]


def _block(kind: str, **payload) -> Dict[str, Any]:
    return {"object": "block", "type": kind, kind: payload}


def _normalize_days(value: Any) -> Dict[int, List[str]]:
    out: Dict[int, List[str]] = {}
    if isinstance(value, dict):
        for key, items in value.items():
            try:
                idx = int(key)
            except Exception:
                idx = len(out)
            out[idx] = [str(x) for x in (items or [])]
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if isinstance(item, dict) and "items" in item:
                out[int(item.get("day", i))] = [str(x) for x in (item.get("items") or [])]
            elif isinstance(item, list):
                out[i] = [str(x) for x in item]
            else:
                out.setdefault(0, []).append(str(item))
    return out


def create_learning_note(args: Dict[str, Any]) -> Dict[str, Any]:
    parent_id = str(args.get("parent_id") or _parent())
    if not parent_id:
        return json_response({"error": "NOTION_PARENT_PAGE or parent_id is required"}, is_error=True)
    title = str(args.get("title") or "LearnForge 学习计划")
    summary = str(args.get("summary") or "")
    tips = [str(x) for x in (args.get("tips") or [])]
    children: List[dict] = []
    if summary:
        children.append(_block("callout", rich_text=_rt(summary), icon={"emoji": "🎯"}, color="green_background"))
    for day, items in sorted(_normalize_days(args.get("days") or {}).items()):
        children.append(_block("heading_2", rich_text=_rt(f"Day {day + 1}")))
        for item in items:
            children.append(_block("to_do", rich_text=_rt(item), checked=False))
    if tips:
        children.append(_block("heading_2", rich_text=_rt("下一步建议")))
        for tip in tips:
            children.append(_block("bulleted_list_item", rich_text=_rt(tip)))
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "icon": {"type": "emoji", "emoji": "📘"},
        "properties": {"title": {"title": _rt(title)}},
        "children": children[:95],
    }
    data = _request("POST", "/pages", payload)
    return json_response(data, is_error=bool(data.get("error")))


TOOLS = [
    {
        "name": "search_pages",
        "description": "Search Notion pages/databases visible to the integration.",
        "inputSchema": schema({"query": {"type": "string"}, "page_size": {"type": "integer", "default": 10}}, ["query"]),
    },
    {
        "name": "read_page",
        "description": "Read a Notion page's child blocks as markdown-like text.",
        "inputSchema": schema({"page_id": {"type": "string"}, "max_blocks": {"type": "integer", "default": 80}}, ["page_id"]),
    },
    {
        "name": "create_learning_note",
        "description": "Create a Notion page for a LearnForge learning plan.",
        "inputSchema": schema({
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "days": {"type": "object"},
            "tips": {"type": "array", "items": {"type": "string"}},
            "parent_id": {"type": "string"},
        }, ["title", "summary", "days"]),
    },
]

HANDLERS = {
    "search_pages": search_pages,
    "read_page": read_page,
    "create_learning_note": create_learning_note,
}


if __name__ == "__main__":
    serve("learnforge-notion", TOOLS, HANDLERS)
