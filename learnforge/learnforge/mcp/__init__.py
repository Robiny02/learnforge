"""mcp —— 预留：外部工具 / MCP 接入接口（MVP Non-Goal，仅前向兼容）。"""

from .base import MCPClient, ToolSpec
from .registry import MCP_REGISTRY, MCPRegistry

__all__ = ["MCPClient", "ToolSpec", "MCPRegistry", "MCP_REGISTRY"]
