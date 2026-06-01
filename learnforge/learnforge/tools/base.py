"""Core tool-call contracts used by ReAct agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class ToolCall:
    """A model- or workflow-selected action."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Canonical observation returned to the ReAct loop."""

    name: str
    ok: bool
    observation: str
    data: Any = None
    error: Optional[str] = None


ToolHandler = Callable[[Dict[str, Any]], ToolResult]


@dataclass
class BaseTool:
    """一个可调用工具的统一描述（本地工具 / MCP 工具 / skill 工具都用它）。

    handler 可返回 ToolResult 或 dict（ToolCollection 会归一化）；handler=None 表示
    "仅声明 schema"（真执行由 ReactRunner 的 per-call handler 提供，如 QA 的 retrieval.search）。
    """

    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    effect: str = "compute"
    handler: Optional[Callable[[Dict[str, Any]], Any]] = None
