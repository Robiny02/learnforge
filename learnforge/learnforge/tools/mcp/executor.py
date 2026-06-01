"""McpToolExecutor —— 连接外部 MCP server、发现其工具、注册进统一 ToolCollection + MCP_REGISTRY。

注册后这些工具就和本地工具同构：名字 `mcp.<server>.<tool>`，ReactRunner/agent 可调用
（前提是某 agent 的 skill 声明了它，过 require_tool）。未配置/连不上 → 返回 []，不报错。
"""
from __future__ import annotations
from typing import List


def bootstrap_mcp() -> List[str]:
    from ..collection import TOOLS
    from ..registry import MCP_REGISTRY
    from ..spec import ToolEffect, ToolSpec
    from .registry import MCP_CLIENT_REGISTRY

    registered: List[str] = []
    try:
        clients = MCP_CLIENT_REGISTRY.connect_all()
    except Exception:
        return registered
    for server_name, client in clients.items():
        try:
            tools = client.list_tools()
        except Exception:
            tools = []
        for tool in tools:
            tname = tool.get("name")
            if not tname:
                continue
            full = f"mcp.{server_name}.{tname}"
            params = tool.get("inputSchema") or {"type": "object", "properties": {}}
            desc = tool.get("description") or full

            def _make(c, n):
                def handler(args):
                    return c.call_tool(n, args)
                return handler

            TOOLS.register(full, _make(client, tname), params, desc)
            if MCP_REGISTRY.get(full) is None:
                MCP_REGISTRY.register(ToolSpec(
                    name=full, description=desc, effect=ToolEffect.EXTERNAL,
                    owner_agents=["research", "qa"], audit_required=True,
                    server=server_name,
                ))
            registered.append(full)
    return registered
