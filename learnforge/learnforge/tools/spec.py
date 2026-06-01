"""MCP / 外部工具接入抽象（预留接口）。

⚠️ MVP Non-Goal：Proposal §2 明确"本期不接外部 MCP / 第三方工具生态"。
此模块仅留**前向兼容接口**（做法同 Design §10a-G1 的云端检索后端）：
skill 的 allowed_tools 经此解析；MVP 注册表为空，调用即抛 NotImplementedError。

这样未来接入 MCP（如时效性查询通道）时，agent / skill 接口无需改动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel


@dataclass
class ToolEffect(str, Enum):
    """工具/能力副作用等级。"""

    READ = "read"
    WRITE = "write"
    DELEGATE = "delegate"
    COMPUTE = "compute"
    EXTERNAL = "external"


@dataclass
class ToolSpec:
    """一个工具/能力声明。

    目前 registry 中登记的是内部 capability 名，不是真实外部 MCP server tool。
    字段按未来接入外部 MCP 的形态保留：effect/owner/audit/budget 可直接复用。
    """

    name: str
    description: str = ""
    effect: ToolEffect = field(default_factory=lambda: ToolEffect.COMPUTE)
    owner_agents: List[str] = field(default_factory=list)
    audit_required: bool = False
    timeout_ms: int = 0
    cost_budget_usd: float = 0.0
    is_namespace: bool = False
    allow_prefix: bool = False
    declarable: bool = True
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
