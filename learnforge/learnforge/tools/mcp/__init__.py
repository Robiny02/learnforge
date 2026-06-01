"""真·MCP 客户端子系统（连外部 MCP server，把其工具注册进统一 ToolCollection）。

放在 tools/ 下：MCP 工具也是 tool。与旧的 `learnforge/mcp/`(指向 tools/ 的兼容 shim)不同——
那个是历史命名垫片，这里是**真客户端**。

约束：app 跑在 Python 3.9，官方 `mcp` SDK 需 3.10+，故自带极简 stdio JSON-RPC 客户端，
不依赖 mcp SDK；server 进程可用任意解释器（如 python3.11 跑 mcp_server_fetch）。
未配置/连不上 → 优雅降级（不注册、不报错）。
"""

from .config import McpServerDescriptor, load_descriptors  # noqa: F401
from .executor import bootstrap_mcp  # noqa: F401
from .registry import MCP_CLIENT_REGISTRY, McpRegistry  # noqa: F401
