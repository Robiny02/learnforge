"""MCPRegistry（预留）——工具名 → ToolSpec 解析。

skill.spec.allowed_tools 中的工具名经此查找。MVP 为空注册表。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .base import ToolSpec


class MCPRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def resolve(self, names: List[str]) -> List[ToolSpec]:
        """解析 skill 的 allowed_tools；未注册的工具被忽略（MVP 全部忽略）。"""
        return [self._tools[n] for n in names if n in self._tools]


# 全局单例（MVP 空表，Non-Goal）。
MCP_REGISTRY = MCPRegistry()
