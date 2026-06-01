"""兼容 shim：实现已迁至 `learnforge.tools.spec`（工具层已统一到 tools/ 包）。

保留此模块仅为不破坏历史 import（如 `from learnforge.mcp.base import ToolEffect`）。
新代码请直接用 `from ..tools.spec import ToolEffect, ToolSpec`。
"""

from ..tools.spec import MCPClient, ToolEffect, ToolSpec  # noqa: F401
