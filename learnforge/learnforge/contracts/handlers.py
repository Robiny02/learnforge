"""可插拔 Handler 协议（轻量 DDD 重构 Phase 2，纯加法、未接线）。

定义三类 handler 的**接口契约**（typing.Protocol），让后续新增能力 / 证据源 / 工具时
不必改 Manager 主循环——只需实现对应协议并注册。本阶段只给接口与空注册表，**不接入**任何
现有 Agent（行为零变化）。设计文档见 `docs/architecture/refactor-plan.md`。

三类协议：
- `CapabilityHandler`     —— QA / Diagnosis / Planning / Mock / EvidenceResearch 等对外能力。
- `EvidenceProviderHandler` —— resume / repo / file / attachment / retrieval 等证据来源。
- `ToolHandler`          —— file.read/write/edit、mcp.*、retrieval 等底层工具。

Python 3.9 兼容：用 typing.Optional/List/Dict，不用 `X | None`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Protocol, runtime_checkable

from .agents.evidence import ProviderResult
from .enums import EvidenceSourceType
from .intent import Capability
from .result import AgentResult

if TYPE_CHECKING:
    # ToolResult 暂住 tools/base.py。Phase 6 会把它提升进 domain；届时删掉此 TYPE_CHECKING import。
    # 仅类型注解引用，运行期不导入 → 不产生 contracts→tools 的运行期依赖/循环。
    from ..tools.base import ToolResult


@runtime_checkable
class CapabilityHandler(Protocol):
    """一个对外能力的统一执行接口（Manager 经注册表调度，不再硬编码 if/elif）。

    实现者声明 `capability`，并在 `run` 内完成该能力的全部工作，回一个统一的 `AgentResult`
    （status / payload / evidence_refs / artifact_refs / next_request / reason）。Manager 只读
    `AgentResult` 决定下一步，不需要知道底下是哪种能力。
    """

    capability: Capability

    def run(self, request: Any, context: Dict[str, Any]) -> AgentResult:
        """执行该能力。request = 能力专属输入；context = Manager 提供的有界上下文。"""
        ...


@runtime_checkable
class EvidenceProviderHandler(Protocol):
    """一个证据来源的只读读取接口（由 EvidenceResearchAgent 统一编排）。

    实现者声明它负责的 `source_type`，在 `fetch` 内**只读**地拉取材料并回 `EvidenceRef` 列表
    （指针 + 截断片段，**非全文**——保证大内容不进 Conversation State）。绝不写状态、不执行命令。
    """

    source_type: EvidenceSourceType

    def fetch(self, query: str, targets: List[str], context: Dict[str, Any]) -> ProviderResult:
        """按 query/targets 只读采集证据，回 ProviderResult（evidence_refs + source_refs）。

        evidence_refs 只放指针 + 截断片段（**非全文**——大内容不进 Conversation State）；
        即便没采到也应在 source_refs 记录 empty/error，便于透明展示。绝不写状态、不执行命令。
        """
        ...


@runtime_checkable
class ToolHandler(Protocol):
    """一个底层工具的统一调用接口（file/mcp/local 同一注册 + 权限闸 + 结果清理）。

    实现者声明 `name` 与副作用等级 `effect`（read/write/delegate/compute/external），`__call__`
    接受结构化参数并回 `ToolResult`。权限仍由 skill.allowed_tools + require_tool 把守（注册≠授权）。
    """

    name: str
    effect: str

    def __call__(self, arguments: Dict[str, Any]) -> "ToolResult":
        """执行工具。参数与返回应可被统一清理/限大小（Phase 6 的 sanitize_tool_result）。"""
        ...
