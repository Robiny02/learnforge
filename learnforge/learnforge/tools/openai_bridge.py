"""LLM tool-calling 调用面（OpenAI 兼容）——统一后是 `collection.TOOLS` 的薄 facade。

历史上这里持有 TOOL_RUNTIME(name->handler) + TOOL_PARAMS + TOOL_DESC；现在它们直接
别名到 TOOLS 的内部 dict（同一对象，保持向后兼容：mcp/tools.py shim 仍 import 这些名字）。
ReactRunner 用 has_handler / run_tool / to_openai_tools / unsafe_name。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .collection import TOOLS, safe_name, unsafe_name  # noqa: F401

# 兼容别名：与 TOOLS 内部 dict 同一对象（mutate 通过 register_* 落到 TOOLS）。
TOOL_RUNTIME = TOOLS.handlers
TOOL_PARAMS = TOOLS.params
TOOL_DESC = TOOLS.descs


def register_tool(name: str, handler, parameters: Optional[dict] = None, description: str = "") -> None:
    """登记可调用工具（真执行）。handler 可返回 dict 或 ToolResult。"""
    TOOLS.register(name, handler, parameters, description)


def register_schema(name: str, parameters: dict, description: str = "") -> None:
    """只登记参数 schema（真执行由 ReactRunner 的 per-call handler 提供）。"""
    TOOLS.register(name, None, parameters, description)


def has_handler(name: str) -> bool:
    return TOOLS.has_handler(name)


def run_tool(name: str, args: Optional[dict] = None) -> Dict[str, Any]:
    return TOOLS.call_dict(name, args)


def to_openai_tools(names: List[str]) -> List[dict]:
    return TOOLS.to_openai_tools(names)
