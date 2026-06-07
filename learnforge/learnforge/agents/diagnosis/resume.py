"""简历问题诊断的确定性规则引擎（蒸馏自 llm-intern-skill，离线可跑）。

复用 mock 的证据约束规则（`agents/mock/interview_skill.py`）：把面试「回答风险」那套
（overclaim / no_evidence / vague + 安全降级）迁移到简历 **claim** 上，逐句判定问题、给降级写法
与预期追问。是 DiagnosisAgent.diagnose_resume 的无 key 兜底，也为 LLM 路径提供锚点，
延续 "链路永远通"。纯函数、不写任何 state。
"""

from __future__ import annotations

import re
from typing import List, Optional

from ...contracts.agents.diagnosis import (
    ResumeDiagnosis,
    ResumeDimensions,
    ResumeIssue,
)
from ...contracts.agents.mock import InterviewContext
from ...contracts.enums import (
    IssueSeverity,
    JDFitVerdict,
    ResumeIssueCategory,
)
from ..mock import interview_skill as IS

# 风险标签 → (问题分类, 严重度)。
_FLAG_TO_CATEGORY = {
    "overclaim": (ResumeIssueCategory.RISKY_LANGUAGE, IssueSeverity.HIGH),
    "no_evidence": (ResumeIssueCategory.EVIDENCE_GAP, IssueSeverity.HIGH),
    "vague": (ResumeIssueCategory.WEAK_PHRASING, IssueSeverity.MEDIUM),
}
_MIN_CLAIM_LEN = 6   # 过短的行（标题/分隔）不当 claim
_MAX_ISSUES = 12


def split_claims(resume_text: str) -> List[str]:
    """把简历正文拆成逐条 claim：按行 + 项目符号切，去标题/空行/过短行。"""
    claims: List[str] = []
    for raw in (resume_text or "").splitlines():
        line = re.sub(r"^\s*([-*•·▪◦]|\d+[.)、])\s*", "", raw).strip()
        # 跳过疑似栏目标题（无句意的短词、纯大写段名）。
        if len(line) < _MIN_CLAIM_LEN:
            continue
        if line.endswith(("：", ":")) and len(line) < 16:
            continue
        claims.append(line)
    return claims


def _expected_question(claim: str, flags: List[str], role_type: Optional[str]) -> str:
    """这条 claim 最可能招致的面试追问（证据式拷打口径）。"""
    if "overclaim" in flags:
        return "你说的这个结果——提升前后具体数字多少？用什么方法/样例验证的？"
    if "no_evidence" in flags:
        return "这部分具体是你做的哪一块？有没有产物/数据/日志/代码可以佐证？"
    if "vague" in flags:
        return "能更具体讲讲吗：输入输出、用了什么、怎么衡量做得好不好？"
    if role_type:
        focus = IS.role_focus(role_type)
        if focus:
            return f"围绕该岗位重点（{focus[0]}），这条经历你能展开到什么深度？"
    return "这条经历里最有技术含量的决策是什么，为什么这么选？"


def analyze_resume_rules(
    resume_text: str, context: Optional[InterviewContext] = None
) -> ResumeDiagnosis:
    """确定性简历诊断：逐 claim 判风险 → 分类/降级/预期追问 → 维度评分 + JD 匹配判定。"""
    ctx = context or InterviewContext()
    role_type = ctx.role_type or IS.detect_role_type(ctx.jd_text, ctx.target_role)

    # claim 来源：优先用结构化 resume_claims/projects，否则从全文拆。
    structured = IS.extract_claims(ctx.resume_claims, ctx.projects)
    claims = structured or split_claims(resume_text)

    issues: List[ResumeIssue] = []
    strengths: List[str] = []
    for claim in claims:
        flags = IS.risk_flags_for_answer(claim)
        if not flags:
            # 无风险且够具体 → 视为亮点（有证据词的硬经历）。
            if len(claim) >= 12:
                strengths.append(claim)
            continue
        # 取最严重的一个 flag 作为主分类（overclaim > no_evidence > vague 顺序）。
        primary = next((f for f in ("overclaim", "no_evidence", "vague") if f in flags), flags[0])
        category, severity = _FLAG_TO_CATEGORY.get(
            primary, (ResumeIssueCategory.WEAK_PHRASING, IssueSeverity.LOW)
        )
        safer = IS.downgrade(claim)
        issues.append(ResumeIssue(
            category=category,
            severity=severity,
            excerpt=claim[:200],
            problem=_problem_for(primary),
            suggestion=safer or _suggestion_for(primary),
            evidence_needed=_evidence_for(primary, role_type),
            expected_question=_expected_question(claim, flags, role_type),
            risk_flags=flags,
        ))
        if len(issues) >= _MAX_ISSUES:
            break

    dims = _score_dimensions(issues, strengths, role_type, ctx)
    jd_fit = _verdict(issues, claims)
    summary = _summarize(issues, strengths, role_type, jd_fit)
    confidence = round(min(1.0, len(claims) / 8.0) * (0.5 if not claims else 1.0), 3)
    return ResumeDiagnosis(
        target_role=ctx.target_role,
        role_type=role_type,
        jd_fit=jd_fit,
        summary=summary,
        issues=issues,
        strengths=strengths[:5],
        dimensions=dims,
        confidence=confidence if claims else 0.0,
        resume_digest=(resume_text or "").strip()[:280],
    )


def _problem_for(flag: str) -> str:
    return {
        "overclaim": "用了结果性/夸大措辞却无量化口径，面试官会直接要数据，答不上会显著扣分。",
        "no_evidence": "声称做过但没有任何证据词（数据/产物/对比），属于无支撑论断，容易被问穿。",
        "vague": "表达含糊或过短，撑不住技术追问，给不出可信细节。",
    }.get(flag, "表达存在风险，建议补充证据或降级。")


def _suggestion_for(flag: str) -> str:
    return {
        "overclaim": "去掉绝对化措辞，改写成你真实负责的范围 + 可验证的观察。",
        "no_evidence": "补一个具体产物/指标/对比（哪怕是固定样例上的改善），否则降级表达。",
        "vague": "补输入输出、所用方法与衡量方式，把含糊词替换成具体事实。",
    }.get(flag, "补证据或降级表达。")


def _evidence_for(flag: str, role_type: Optional[str]) -> List[str]:
    base = {
        "overclaim": ["提升前后的量化对比", "验证方法/样例集"],
        "no_evidence": ["可展示的产物（代码/demo/报告）", "你负责部分的明确边界"],
        "vague": ["输入输出与数据规模", "衡量好坏的指标口径"],
    }.get(flag, ["佐证材料"])
    if role_type:
        focus = IS.role_focus(role_type)
        if focus:
            base.append(f"该岗位关注点：{focus[0]}")
    return base


def _score_dimensions(
    issues: List[ResumeIssue], strengths: List[str],
    role_type: Optional[str], ctx: InterviewContext
) -> ResumeDimensions:
    """问题越多/越严重，相应维度分越低；亮点抬高表达分。"""
    def cnt(cat: ResumeIssueCategory) -> int:
        return sum(1 for i in issues if i.category == cat)

    def score(bad: int) -> int:
        return max(0, 5 - min(5, bad * 2))

    return ResumeDimensions(
        truth_boundary=score(cnt(ResumeIssueCategory.RISKY_LANGUAGE)),
        evidence_contract=score(cnt(ResumeIssueCategory.EVIDENCE_GAP)
                                + cnt(ResumeIssueCategory.UNSUPPORTED_CLAIM)),
        technical_expression=min(5, score(cnt(ResumeIssueCategory.WEAK_PHRASING)) + bool(strengths)),
        interviewability=score(sum(1 for i in issues if i.severity == IssueSeverity.HIGH)),
        jd_alignment=3 if role_type else 1,
    )


def _verdict(issues: List[ResumeIssue], claims: List[str]) -> JDFitVerdict:
    if not claims:
        return JDFitVerdict.UNKNOWN
    high = sum(1 for i in issues if i.severity == IssueSeverity.HIGH)
    ratio = len(issues) / max(1, len(claims))
    if high >= 3 or ratio >= 0.5:
        return JDFitVerdict.RISKY
    if high >= 1 or ratio >= 0.25:
        return JDFitVerdict.MEDIUM
    return JDFitVerdict.STRONG


def _summarize(
    issues: List[ResumeIssue], strengths: List[str],
    role_type: Optional[str], jd_fit: JDFitVerdict
) -> str:
    if not issues and not strengths:
        return "未能从简历中解析出可评估的经历条目，建议提供更完整的项目/经历描述。"
    high = sum(1 for i in issues if i.severity == IssueSeverity.HIGH)
    role = f"目标方向 {role_type}；" if role_type else ""
    return (f"{role}JD 匹配判定={jd_fit.value}。共 {len(issues)} 处风险"
            f"（其中高风险 {high} 处），{len(strengths)} 条可作为硬亮点。"
            "高风险多为夸大/无证据，建议先降级表达并补齐证据，再用 mock 检验抗追问。")
