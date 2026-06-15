"""统一 AgentResult 契约（轻量 DDD 重构 Phase 1，纯加法）。

把现状散落在三处的“子 Agent 回收结果”收成一个类型化值对象：
- `ResponsePayload.result: Dict`（不透明结论）；
- Manager 主循环里的 `context` grab-bag（`context["diagnosis"]` / `context["evidence_artifact"]`）；
- Manager 里的 `meta` grab-bag（`meta["notion_url"]` / `meta["path_committed"]` / `meta["suggest_mock"]`…）。

`AgentResult` 让 Manager 只读 `status / payload / evidence_refs / artifact_refs / next_request / reason`
即可决定下一步，而不必知道底下是 qa / diagnosis / planning / mock。

本阶段**不改任何调用方**：`ResponsePayload` 继续存在，本模块只提供新契约 + 与之的双向 adapter
（`from_response_payload` / `to_response_payload`），供后续 Phase 渐进迁移。设计文档见
`docs/architecture/refactor-plan.md`。

Python 3.9 兼容：用 typing.Optional/List/Dict，不用 `X | None`。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .agents.evidence import EvidenceRef
from .enums import Status
from .message import ErrorInfo, ResponsePayload, TokenUsage


# --------------------------------------------------------------------------- #
# Artifact —— 子 Agent 产出的“产物指针”（替代 Manager 里散落的 meta[...] 键）
# --------------------------------------------------------------------------- #
class ArtifactKind(str, Enum):
    """产物类型。值刻意对齐现有 `meta` 键，便于 Phase 6/7 平滑迁移。"""

    NOTION = "notion"                      # meta["notion_url"]
    REPORT = "report"                      # meta["report_path"]
    PLAN_IMAGE = "plan_image"              # meta["plan_image_path"] / ["plan_image_spec"]
    COMMITTED_PATH = "committed_path"      # meta["path_committed"]（Manager 落库后的 path_id）
    MOCK_SESSION = "mock_session"          # 进行中的 mock 会话引用
    DIAGNOSIS_REPORT = "diagnosis_report"  # 落库的诊断快照 report_id
    DOCUMENT = "document"                  # 上传文档引用（document_id）
    IMAGE = "image"                        # 通用图片资产
    OTHER = "other"


class ArtifactRef(BaseModel):
    """一个被产出/落库的产物引用（只放指针 + 标签，不放本体）。"""

    kind: ArtifactKind
    ref: str = Field(default="", description="URL / 文件路径 / id 等稳定指针。")
    label: str = Field(default="", description="可选的人类可读标签（供 UI/next_actions）。")
    meta: Dict[str, Any] = Field(default_factory=dict, description="附加结构化信息（如 image_spec）。")


# --------------------------------------------------------------------------- #
# NextRequest —— 子 Agent 回传给 Manager 的“下一步诉求”（不自己执行）
# --------------------------------------------------------------------------- #
class NextRequestKind(str, Enum):
    NEED_EVIDENCE = "need_evidence"            # 证据不足：请 Manager 调 EvidenceResearchAgent 采证据再回灌
    NEED_CLARIFICATION = "need_clarification"  # 缺必填槽位：请向用户追问
    SUGGEST_CAPABILITY = "suggest_capability"  # 建议下一步换某能力（如改完路径建议 mock，控制权仍交回用户）


class NextRequest(BaseModel):
    """子 Agent 对 Manager 的结构化“下一步诉求”。

    这是协作铁律的落点：Diagnosis 证据不足时返回 `need_evidence`，而**不是**自己读大量 source
    或 spawn subagent；Manager 看到后才去调统一只读证据 worker。
    """

    kind: NextRequestKind
    reason: str = Field(default="", description="为什么需要这一步（简短）。")
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="结构化诉求。need_evidence: {query, source_types, targets}；"
                    "need_clarification: {question}；suggest_capability: {capability}。",
    )


# --------------------------------------------------------------------------- #
# AgentResult —— 统一回收结果（泛化 ResponsePayload）
# --------------------------------------------------------------------------- #
class AgentResult(BaseModel):
    """子 Agent → Manager 的统一结构化结果（Design §4a 的类型化演进）。

    与 `ResponsePayload` 的关系：`status/confidence/payload(=result)/reason/cost_usd/tokens/error`
    一一对应；新增 `evidence_refs / artifact_refs / next_request` 收掉 Manager 的旁路 dict。
    """

    status: Status = Field(..., description="ok/error/partial/needs_input/escalate。")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度，<0.4 触发 replan。")
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="结构化结论（各 agent *Output 的序列化）。"
    )
    evidence_refs: List[EvidenceRef] = Field(
        default_factory=list, description="本结果所依据的证据指针（不含全文，§证据边界）。"
    )
    artifact_refs: List[ArtifactRef] = Field(
        default_factory=list, description="本步产出的产物指针（notion/report/committed_path…）。"
    )
    next_request: Optional[NextRequest] = Field(
        default=None, description="对 Manager 的下一步诉求（need_evidence/clarification/suggest）。"
    )
    reason: str = Field(default="", description="给 Manager 看的简短“为什么”（依据/缺口/降级），不外传用户。")
    cost_usd: float = Field(default=0.0, description="本次调用累计成本。")
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    error: Optional[ErrorInfo] = None

    # ------------------------------------------------------------- adapters
    def to_response_payload(self) -> ResponsePayload:
        """向后兼容投影：AgentResult → ResponsePayload（丢弃 evidence/artifact/next_request）。

        供 Phase 3 期间“handler 产 AgentResult，外层仍消费 ResponsePayload”的过渡。
        被丢弃的字段是**附加信息**，不影响现有 ResponsePayload 的语义。
        """
        return ResponsePayload(
            status=self.status,
            confidence=self.confidence,
            result=dict(self.payload),
            reason=self.reason,
            cost_usd=self.cost_usd,
            tokens=self.tokens,
            error=self.error,
        )

    @classmethod
    def from_response_payload(cls, resp: ResponsePayload) -> "AgentResult":
        """从现有 ResponsePayload 提升为 AgentResult（新字段留空，行为等价）。

        供 Phase 3 期间“外层产 ResponsePayload，新代码消费 AgentResult”的过渡。
        """
        return cls(
            status=resp.status,
            confidence=resp.confidence,
            payload=dict(resp.result),
            reason=resp.reason,
            cost_usd=resp.cost_usd,
            tokens=resp.tokens,
            error=resp.error,
        )
