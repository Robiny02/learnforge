"""McpRegistry —— 持有已连接的外部 MCP server 客户端。"""
from __future__ import annotations
from typing import Dict
from .config import load_descriptors
from .runtime import McpClientRuntime
from .runtime_factory import create_client


class McpRegistry:
    def __init__(self) -> None:
        self.clients: Dict[str, McpClientRuntime] = {}

    def connect_all(self) -> Dict[str, McpClientRuntime]:
        for d in load_descriptors():
            if d.name in self.clients:
                continue
            client = create_client(d)
            if client is not None and client.connect():
                self.clients[d.name] = client
        return self.clients

    def close_all(self) -> None:
        for c in self.clients.values():
            c.close()
        self.clients = {}


MCP_CLIENT_REGISTRY = McpRegistry()
