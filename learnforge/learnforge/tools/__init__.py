"""统一工具层（合并了原 mcp/ 与 tools/ 两套）。

Tools 是具体可调用能力；Skills 决定何时/为何用;本包负责在 agent 权限门后执行。

模块划分：
- spec.py          : ToolSpec / ToolEffect / MCPClient（工具元数据 + 副作用等级）
- registry.py      : MCP_REGISTRY（工具名 canonical 清单 + 权限校验，skill.allowed_tools 据此校验）
- base.py          : ToolCall / ToolResult（程序化调用的规范观测）
- runtime.py       : ToolRuntime（agent 作用域执行器，含权限门）+ DEFAULT_TOOL_REGISTRY
- openai_bridge.py : TOOL_RUNTIME + to_openai_tools（LLM tool-calling 的 handler 表与 schema 转换）
- diagnosis.py     : 诊断类只读/计算工具的实现

兼容：`learnforge.mcp.*` 仍作为 shim 指向这里（spec/registry/openai_bridge）。
"""

from .base import BaseTool, ToolCall, ToolResult
from .collection import TOOLS, ToolCollection
from .registry import MCP_REGISTRY, MCPRegistry
from .runtime import DEFAULT_TOOL_REGISTRY, ToolRuntime
from .spec import MCPClient, ToolEffect, ToolSpec

__all__ = [
    "ToolCall", "ToolResult", "BaseTool", "ToolRuntime", "DEFAULT_TOOL_REGISTRY",
    "ToolCollection", "TOOLS",
    "ToolSpec", "ToolEffect", "MCPClient", "MCP_REGISTRY", "MCPRegistry",
]
