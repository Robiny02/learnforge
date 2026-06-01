"""Agent 作用域工具执行器（程序化调用面）。

统一后：`ToolRegistry`/`DEFAULT_TOOL_REGISTRY` 只是 `collection.TOOLS` 的薄 facade，
不再各持一份 handler 表。agent 通过 `ToolRuntime.call` / `BaseAgent.call_tool` 调用，
权限门由 agent.require_tool 把守，执行落到 TOOLS。
"""

from __future__ import annotations

from typing import Any, Dict

from .base import ToolCall, ToolResult
from .collection import TOOLS


class UnknownToolError(ValueError):
    """没有为该工具名注册可执行 handler。"""


class ToolRegistry:
    """兼容 facade → collection.TOOLS（历史 API：register/has/call）。"""

    def register(self, name: str, handler) -> None:
        TOOLS.register(name, handler)

    def has(self, name: str) -> bool:
        return TOOLS.has_handler(name)

    def call(self, call: ToolCall) -> ToolResult:
        if not TOOLS.has_handler(call.name):
            raise UnknownToolError(f"Unknown tool: {call.name}")
        return TOOLS.call_result(call.name, call.arguments)


class ToolRuntime:
    """Agent 作用域执行器：policy 在 skill（agent.require_tool），这里只管执行。"""

    def __init__(self, agent: Any, registry: ToolRegistry = None) -> None:
        self.agent = agent
        self.registry = registry or DEFAULT_TOOL_REGISTRY

    def call(self, name: str, arguments: Dict[str, Any] = None) -> ToolResult:
        self.agent.require_tool(name)
        return self.registry.call(ToolCall(name=name, arguments=arguments or {}))


DEFAULT_TOOL_REGISTRY = ToolRegistry()


def _register_defaults() -> None:
    from .diagnosis import register_diagnosis_tools

    register_diagnosis_tools(DEFAULT_TOOL_REGISTRY)


_register_defaults()
