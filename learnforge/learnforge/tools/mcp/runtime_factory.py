"""按 transport 创建 MCP 客户端。stdio 已实现；sse/http 为占位（返回 None → 降级跳过）。"""
from __future__ import annotations
from typing import Optional
from .config import McpServerDescriptor
from .runtime import McpClientRuntime


def create_client(descriptor: McpServerDescriptor) -> Optional[McpClientRuntime]:
    if descriptor.transport == "stdio":
        return McpClientRuntime(descriptor)
    # TODO: sse / streamable-http（需在 3.10+ 接官方 mcp SDK，或自行实现）。
    return None
