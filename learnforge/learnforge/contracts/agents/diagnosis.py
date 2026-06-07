"""DiagnosisAgent input/output（Design §3.13 / §5.5）。

严格只读：聚合 Atom 掌握度 + interaction_events，产出弱点结论与行动建议，不写任何 state。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from ..enums import (
    DiagnosisTrigger,
    IssueSeverity,
    JDFitVerdict,
    ResumeIssueCategory,
    TimeWindow,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WeakAtom(BaseModel):
    atom_id: str
    topic: str
    mastery: float = Field(ge=0.0, le=1.0)
    evidence_count: int = 0


class Cluster(BaseModel):
    topic: str
    severity: float = Field(ge=0.0, le=1.0, description="弱点簇严重度。")


class DiagnosisInput(BaseModel):
    time_window: TimeWindow = TimeWindow.D30
    focus_topics: List[str] = Field(default_factory=list)
    trigger: DiagnosisTrigger = DiagnosisTrigger.USER


class DiagnosisResult(BaseModel):
    """诊断结论（只读输出；写操作必经 Manager→Planning，Proposal §5 决策 5）。"""

    weak_atoms: List[WeakAtom] = Field(default_factory=list)
    clusters: List[Cluster] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# --------------------------------------------------------------------------- #
# 简历诊断（resume review，蒸馏自 llm-intern-skill；只读产出，详细可召回）
# --------------------------------------------------------------------------- #
class ResumeIssue(BaseModel):
    """单条简历问题——尽量详细，便于召回后直接复用为改写/拷打依据。"""

    category: ResumeIssueCategory
    severity: IssueSeverity = IssueSeverity.MEDIUM
    excerpt: str = Field(description="问题所在的简历原句/片段（定位用）。")
    problem: str = Field(description="为什么这是个问题（会被怎么追问 / 哪里站不住）。")
    suggestion: str = Field(default="", description="如何修：更安全的降级写法或该补的证据。")
    evidence_needed: List[str] = Field(
        default_factory=list, description="要让这条 claim 站得住，需要补的证据（指标/产物/对比）。"
    )
    expected_question: str = Field(
        default="", description="这条 claim 最可能招致的面试追问（供提前准备）。"
    )
    risk_flags: List[str] = Field(
        default_factory=list, description="命中的真实性风险标签：overclaim/no_evidence/vague。"
    )


class ResumeDimensions(BaseModel):
    """简历五维评分（0-5；llm-intern-skill 的评审轴）。"""

    truth_boundary: int = Field(default=0, ge=0, le=5, description="真实性边界（不夸大、可自证）。")
    evidence_contract: int = Field(default=0, ge=0, le=5, description="证据契约（claim 是否有佐证）。")
    technical_expression: int = Field(default=0, ge=0, le=5, description="技术表达清晰度/精确度。")
    interviewability: int = Field(default=0, ge=0, le=5, description="可被追问的抗压程度。")
    jd_alignment: int = Field(default=0, ge=0, le=5, description="与目标岗位硬要求的对齐度。")


class ResumeDiagnosis(BaseModel):
    """一次简历诊断的完整结论（只读产出 + 可整条召回）。

    设计为「自包含、可整体序列化」：保存时整条进记忆库的 chunk metadata，召回时按原样重建，
    因此字段尽量详细——召回方拿到的就是完整诊断，而非片段。
    """

    diagnosis_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=_now_iso)
    target_role: Optional[str] = None
    role_type: Optional[str] = None
    jd_fit: JDFitVerdict = JDFitVerdict.UNKNOWN
    summary: str = ""
    issues: List[ResumeIssue] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    dimensions: ResumeDimensions = Field(default_factory=ResumeDimensions)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    resume_digest: str = Field(
        default="", description="被诊断简历的摘录/指纹（便于召回时确认对应哪份简历，不存全文）。"
    )

    def search_text(self) -> str:
        """供 FTS/向量检索命中的可读正文（标题 + 摘要 + 各问题片段/分类）。"""
        lines = [f"简历诊断：{self.target_role or '通用'}（{self.role_type or '未定向'}）",
                 f"JD 匹配：{self.jd_fit.value}", self.summary]
        for it in self.issues:
            lines.append(f"[{it.category.value}/{it.severity.value}] {it.excerpt} —— {it.problem}")
        return "\n".join(x for x in lines if x)
