"""兼容 shim：实现已迁至 `learnforge.tools.openai_bridge`。

新代码请用 `from ..tools import openai_bridge as toolmod`。
"""

from ..tools.openai_bridge import (  # noqa: F401
    TOOL_DESC,
    TOOL_PARAMS,
    TOOL_RUNTIME,
    has_handler,
    register_schema,
    register_tool,
    run_tool,
    safe_name,
    to_openai_tools,
    unsafe_name,
)
