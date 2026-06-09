"""DiagnosisAgent input/output（Design §3.13 / §5.5）。

严格只读：聚合 Atom 掌握度 + interaction_events，产出弱点结论与行动建议，不写任何 state。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from ..enums import (
    ClaimType,
    DiagnosisTrigger,
    EvidenceStrength,
    ExternalSourceKind,
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


class SelectedFile(BaseModel):
    """repo map 动态选中并读取的一个文件（可解释 + 为后续 evidence binding 打基础）。"""

    path: str
    role: str = Field(default="unknown", description="文件角色：doc/source/test/config/example/script/unknown。")
    evidence_kind: str = Field(default="", description="证据类型：doc/code/test/config。")
    score: float = Field(default=0.0, description="选择分数（越高越相关）。")
    selected_reason: str = Field(default="", description="为什么选它（可解释）。")
    expected_claims: List[str] = Field(
        default_factory=list, description="预期它能支持的 claim token（选择时的依据）。"
    )
    # --- 读取后填（区分『读到』与『支持』；为 evidence binding 打基础）---
    read_success: bool = Field(default=False, description="是否成功读到内容。")
    matched_claims: List[str] = Field(
        default_factory=list, description="文件内容里**实际**命中的 claim token（不只文件名）。"
    )
    extracted_facts: List[str] = Field(
        default_factory=list, description="从内容里抽出的、含命中 token 的短证据片段。"
    )
    read_success_but_no_match: bool = Field(
        default=False, description="读到了但内容未命中任何 claim → 不应据此判 support。"
    )


class ExternalSource(BaseModel):
    """一个被访问的外部链接及其读取结果（用于在诊断里透明展示读了什么/失败了什么）。"""

    url: str
    kind: ExternalSourceKind
    status: str = Field(default="read", description="read | failed | skipped。")
    reason: str = Field(default="", description="失败/跳过的原因（PAT 失效/私有仓库/超时/不可达）。")
    items_read: List[str] = Field(
        default_factory=list, description="实际读到的文件/页面（如 README.md / orchestration/manager.py）。"
    )
    evidence_kind: str = Field(
        default="", description="该来源提供的证据类型：doc / blog / code / test（博客≠源码）。"
    )
    selected_files: List[SelectedFile] = Field(
        default_factory=list, description="repo map 动态选中的文件及其可解释选择依据/读取结果。"
    )


class EvidencePacket(BaseModel):
    """单条 claim 的项目级证据包（项目拷打器核心）。

    诊断前先为每条 claim 绑定这份证据包：它属于什么类型、项目材料里有何证据、强度如何、
    缺什么证据（且该去哪找）、背后真正能打的技术亮点、面试官会怎么深挖、现在能安全怎么写、
    补证据后能更强怎么写。
    """

    claim: str = Field(description="简历原句/bullet。")
    claim_type: ClaimType
    technical_highlight: str = Field(
        default="", description="这条 claim 背后真正能打的工程亮点（项目特异，不是套话）。"
    )
    evidence_found: List[str] = Field(
        default_factory=list, description="项目材料里支持该 claim 的具体证据（引文件/测试/README/trace）。"
    )
    evidence_sources: List[str] = Field(
        default_factory=list, description="证据来源定位：如 CLAUDE.md / orchestration/manager.py / tests/。"
    )
    support_strength: EvidenceStrength = EvidenceStrength.NONE
    missing_evidence: List[str] = Field(
        default_factory=list, description="缺什么证据 + 该去哪个文件/测试/trace/日志里找（非泛泛『补证据』）。"
    )
    interview_questions: List[str] = Field(
        default_factory=list, description="面试官会顺着该 claim 深挖的真实追问。"
    )
    safe_now: str = Field(default="", description="以现有证据，现在能安全怎么写（不夸大）。")
    stronger_after_evidence: str = Field(
        default="", description="补齐缺的证据后，可以怎么写得更强。"
    )


class ResumeDiagnosis(BaseModel):
    """一次简历诊断的完整结论（只读产出 + 可整条召回）。

    设计为「自包含、可整体序列化」：保存时整条进记忆库的 chunk metadata，召回时按原样重建，
    因此字段尽量详细——召回方拿到的就是完整诊断，而非片段。

    v2 升级为「项目级诊断」：在 issues/strengths/dimensions 之外，给出 overall_verdict、
    top_highlights、most_dangerous、逐条 EvidencePacket、可直接替换进简历的 rewritten_bullets。
    """

    diagnosis_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=_now_iso)
    target_role: Optional[str] = None
    role_type: Optional[str] = None
    jd_fit: JDFitVerdict = JDFitVerdict.UNKNOWN
    summary: str = ""

    # --- 项目级输出（v2 核心） ---
    overall_verdict: str = Field(default="", description="总体判断：简历整体在目标岗位下站不站得住。")
    top_highlights: List[str] = Field(
        default_factory=list, description="这个项目/简历真正能打的亮点（项目特异的工程深度）。"
    )
    most_dangerous: List[str] = Field(
        default_factory=list, description="最危险的表述（最容易被问穿、扣分最狠的几条）。"
    )
    packets: List[EvidencePacket] = Field(
        default_factory=list, description="每条 bullet 的项目级诊断（证据包）。"
    )
    rewritten_bullets: List[str] = Field(
        default_factory=list, description="可直接替换进简历的改写版本（每条：原句→改写）。"
    )
    evidence_sources_used: List[str] = Field(
        default_factory=list, description="本次挖掘实际读到的项目材料来源（github/附件/本地材料）。"
    )
    external_sources: List[ExternalSource] = Field(
        default_factory=list, description="访问过的外部链接及读取结果（含失败原因），透明展示读了什么。"
    )

    # --- 兼容字段（v1，仍保留：弱点列表 / 亮点 / 五维 / 置信 / 指纹） ---
    issues: List[ResumeIssue] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    dimensions: ResumeDimensions = Field(default_factory=ResumeDimensions)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    resume_digest: str = Field(
        default="", description="被诊断简历的摘录/指纹（便于召回时确认对应哪份简历，不存全文）。"
    )

    def search_text(self) -> str:
        """供 FTS/向量检索命中的可读正文（标题 + 总体判断 + 亮点 + 各 claim 片段）。"""
        lines = [f"简历诊断：{self.target_role or '通用'}（{self.role_type or '未定向'}）",
                 f"JD 匹配：{self.jd_fit.value}", self.overall_verdict or self.summary]
        lines += [f"亮点：{h}" for h in self.top_highlights]
        for p in self.packets:
            lines.append(f"[{p.claim_type.value}/{p.support_strength.value}] {p.claim} —— "
                         f"{p.technical_highlight}")
        for it in self.issues:  # 兼容：v1 issues 也进检索正文
            lines.append(f"[{it.category.value}/{it.severity.value}] {it.excerpt} —— {it.problem}")
        return "\n".join(x for x in lines if x)
