"""MCP / 外部工具接入抽象（预留接口）。

⚠️ MVP Non-Goal：Proposal §2 明确"本期不接外部 MCP / 第三方工具生态"。
此模块仅留**前向兼容接口**（做法同 Design §10a-G1 的云端检索后端）：
skill 的 allowed_tools 经此解析；MVP 注册表为空，调用即抛 NotImplementedError。

这样未来接入 MCP（如时效性查询通道）时，agent / skill 接口无需改动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel


@dataclass
class ToolSpec:
    """一个外部工具/MCP tool 的声明。"""

    name: str
    description: str = ""
    server: str = ""  # MCP server 标识（预留）
    input_schema: Optional[Type[BaseModel]] = None
    output_schema: Optional[Type[BaseModel]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class MCPClient:
    """MCP 客户端协议（预留）。MVP 不实现真实连接。"""

    def call(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        raise NotImplementedError(
            "MCP 接入为 MVP Non-Goal（Proposal §2）。此接口仅前向兼容预留。"
        )
