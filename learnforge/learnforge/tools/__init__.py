"""统一工具层（合并了原 mcp/ 与 tools/ 两套）。

Tools 是具体可调用能力；Skills 决定何时/为何用;本包负责在 agent 权限门后执行。

模块划分：
- spec.py          : ToolSpec / ToolEffect / MCPClient（工具元数据 + 副作用等级）
- registry.py      : CAPABILITY_REGISTRY（内部能力权限目录 + 校验；旧名 MCP_REGISTRY 为兼容别名）
- base.py          : ToolCall / ToolResult（程序化调用的规范观测）
- runtime.py       : ToolRuntime（agent 作用域执行器，含权限门）+ DEFAULT_TOOL_REGISTRY
- openai_bridge.py : TOOL_RUNTIME + to_openai_tools（LLM tool-calling 的 handler 表与 schema 转换）
- diagnosis.py     : 诊断类只读/计算工具的实现

历史 `learnforge.mcp.*` shim 已删除（importers 已直接指向 tools.*）。
"""

from .base import BaseTool, ToolCall, ToolResult
from .collection import TOOLS, ToolCollection
from .registry import (
    CAPABILITY_REGISTRY,
    CapabilityRegistry,
    MCP_REGISTRY,       # 兼容别名（= CAPABILITY_REGISTRY）
    MCPRegistry,        # 兼容别名（= CapabilityRegistry）
)
from .runtime import DEFAULT_TOOL_REGISTRY, ToolRuntime
from .spec import MCPClient, ToolEffect, ToolSpec

__all__ = [
    "ToolCall", "ToolResult", "BaseTool", "ToolRuntime", "DEFAULT_TOOL_REGISTRY",
    "ToolCollection", "TOOLS",
    "ToolSpec", "ToolEffect", "MCPClient",
    "CAPABILITY_REGISTRY", "CapabilityRegistry",
    "MCP_REGISTRY", "MCPRegistry",  # 兼容别名
]
