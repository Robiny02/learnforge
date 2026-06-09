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

# 简历意图关键词（用户文本里出现即认为想诊断简历，而非弱点）。
_RESUME_CUES = ("简历", "履历", "resume", "cv", "résumé")
# 文件名像简历的线索（自动从附件挑出简历文档）。
_RESUME_FILENAME_CUES = ("简历", "履历", "resume", "cv", "résumé")


def looks_like_resume_request(text: Optional[str]) -> bool:
    """用户文本是否在要求诊断简历（区别于学习弱点诊断）。"""
    low = (text or "").lower()
    return any(cue in low for cue in _RESUME_CUES)


_INTENT_RE = re.compile(r"(?:求职意向|意向岗位|求职方向|目标岗位|应聘岗位)\s*[:：]\s*(.+)")


def extract_job_intent(resume_text: str) -> Optional[str]:
    """从简历抽『求职意向：X』作为默认目标岗位（未提供 JD 时用它评估，避免 jd_fit=unknown）。"""
    for line in (resume_text or "").splitlines():
        m = _INTENT_RE.search(line.strip())
        if m:
            return m.group(1).strip()[:60] or None
    return None


def detect_resume_language(resume_text: str) -> str:
    """检测简历主语言：CJK 字符占比 ≥15% 视为中文('zh')，否则英文('en')。

    用于锁定输出语言——不能因为挖到的 README/CLAUDE.md 是英文就把中文简历的诊断切成英文。
    """
    s = resume_text or ""
    cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
    alpha = sum(1 for ch in s if ch.isalpha())
    if alpha == 0:
        return "zh" if cjk else "en"
    return "zh" if cjk / max(1, cjk + alpha) >= 0.15 else "en"

# 风险标签 → (问题分类, 严重度)。夸大(overclaim)才是高风险；"描述了工作但缺内联指标"属中风险。
_FLAG_TO_CATEGORY = {
    "overclaim": (ResumeIssueCategory.RISKY_LANGUAGE, IssueSeverity.HIGH),
    "no_evidence": (ResumeIssueCategory.EVIDENCE_GAP, IssueSeverity.MEDIUM),
    "vague": (ResumeIssueCategory.WEAK_PHRASING, IssueSeverity.MEDIUM),
}
_MIN_CLAIM_LEN = 6   # 过短的行（标题/分隔）不当 claim
_MAX_ISSUES = 8      # 规则兜底别刷屏；LLM 路径自行决定条数


# 简历条目常见的「动词引导词」——用于在换行被压成空格后重新切条（附件入库会归一化空白）。
_LEADER_VERBS = ("主导", "负责", "参与", "实现", "完成", "优化", "搭建", "设计", "开发",
                 "独立", "基于", "使用", "采用", "通过", "构建", "研究", "训练", "部署", "提升")
_LEADER_RE = re.compile(r"\s+(?=(?:" + "|".join(_LEADER_VERBS) + r"))")
_SENT_RE = re.compile(r"[。！？；;!?]+")


def split_claims(resume_text: str) -> List[str]:
    """把简历正文拆成逐条 claim。

    对附件入库后**换行被压成空格**的文本鲁棒：先按句末标点切，再在「空格 + 简历动词引导词」
    处补切，避免多条经历并成一行后、某条的证据把另一条的夸大"洗白"。再去标题/空行/过短行。
    """
    text = _SENT_RE.sub("\n", resume_text or "")
    text = _LEADER_RE.sub("\n", text)
    claims: List[str] = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*([-*•·▪◦]|\d+[.)、])\s*", "", raw).strip()
        # 跳过疑似栏目标题（无句意的短词、纯大写段名）。
        if len(line) < _MIN_CLAIM_LEN:
            continue
        if line.endswith(("：", ":")) and len(line) < 16:
            continue
        claims.append(line)
    return claims


# 非经历行（联系方式/学历事实/栏目标题/链接/日期）——不评估为"风险"。
_META_RE = re.compile(
    r"[\w.\-]+@[\w.\-]+\.\w+|电话|邮[箱件]|phone|e-?mail|tel|微信|wechat|地址|address|"
    r"github\.com|linkedin|gitee|https?://|"
    r"绩点|雅思|托福|ielts|toefl|cet|四级|六级|"
    r"求职意向|期望(岗位|薪资)|意向岗位|个人信息|联系方式|教育(背景|经历)?|荣誉|奖学金|证书",
    re.IGNORECASE,
)
# GPA 单独用词边界匹配，避免命中正文里别的字。
_GPA_RE = re.compile(r"\bgpa\b|绩点", re.IGNORECASE)
_DATE_ONLY_RE = re.compile(r"^[\s\d年月./~\-—至今present]+$", re.IGNORECASE)
# 技术栈背景行（语言/框架/中间件枚举）——不单独当风险点（否则刷一堆无意义 evidence_gap）。
_TECH_STACK_RE = re.compile(r"技术栈|tech\s*stack|skills?[:：]|熟悉|掌握|精通|了解|工具[:：]", re.IGNORECASE)
_DEGREE_CUES = ("大学", "学院", "university", "college", "硕士", "本科", "学士", "博士", "专业")
# 证据标记：量化数字/指标/产物词——有则视为有支撑。
_NUM_RE = re.compile(r"\d+(\.\d+)?\s*(%|倍|万|亿|qps|ms|k\b|w\b|条|次|人|天|周|月)", re.IGNORECASE)
# 注意：不要把"准确/召回"这类领域名词当证据——它们常出现在夸大句里（如"准确率显著提升"），
# 真正的证据是量化数字(_NUM_RE)或具体佐证词。
_EVIDENCE_MARKERS = ("指标", "数据", "日志", "对比", "实验", "ndcg", "mrr", "baseline",
                     "样例", "case", "代码", "复现", "配置", "记录", "report",
                     "压测", "基准", "p99", "p95", "覆盖率")


def _is_meta_line(line: str) -> bool:
    """是否为非经历行（联系方式/学历事实/栏目/日期/链接）——跳过，不当风险。"""
    low = line.lower().strip()
    if (_META_RE.search(low) or _GPA_RE.search(low) or _DATE_ONLY_RE.match(line.strip())
            or _TECH_STACK_RE.search(low)):
        return True
    # 纯学历事实行（含学校/学位且较短，无动作描述）。
    if any(c in low for c in _DEGREE_CUES) and len(line) < 30:
        return True
    return False


def _has_evidence(claim: str) -> bool:
    low = claim.lower()
    return bool(_NUM_RE.search(low)) or any(m in low for m in _EVIDENCE_MARKERS)


def _resume_claim_risk(claim: str) -> List[str]:
    """简历条目的风险（区别于面试回答）：只看夸大 + 行动声明无证据，不把"短"当含糊。"""
    flags: List[str] = []
    has_ev = _has_evidence(claim)
    if any(t in claim for t in IS.OVERCLAIM_TERMS) and not has_ev:
        flags.append("overclaim")
    # 行动型声明（有动词引导词）却无任何量化/证据 → 证据缺失。
    if any(v in claim for v in _LEADER_VERBS) and not has_ev and "overclaim" not in flags:
        flags.append("no_evidence")
    return flags


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
        # 跳过联系方式/学历事实/栏目/日期等非经历行——它们不是简历"风险"。
        if _is_meta_line(claim):
            continue
        # 只评估实质经历/项目条目（有动词引导词，或足够长的描述）。
        is_experience = any(v in claim for v in _LEADER_VERBS) or len(claim) >= 16
        if not is_experience:
            continue
        flags = _resume_claim_risk(claim)
        if not flags:
            # 实质经历 + 有量化/证据 → 硬亮点。
            if _has_evidence(claim):
                strengths.append(claim)
            continue
        # 取最严重的一个 flag 作为主分类（overclaim > no_evidence）。
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


def render_resume_diagnosis(diag) -> str:
    """把 ResumeDiagnosis 渲染成前端可读的项目级 Markdown（共享渲染器，两条路径共用）。

    直接结构化渲染，**不再过 LLM 重写**——避免 Manager 的 _llm_aggregate 把诊断重写并截断。
    """
    lines = [f"## 简历诊断（JD 匹配：{diag.jd_fit.value}）", ""]
    if diag.overall_verdict:
        lines += ["### 总体判断", diag.overall_verdict, ""]
    elif diag.summary:
        lines += [diag.summary, ""]
    if diag.evidence_sources_used:
        lines += [f"> 已读取项目材料：{('、'.join(diag.evidence_sources_used))}", ""]
    if diag.top_highlights:
        lines += ["### 真正能打的亮点"] + [f"- {h}" for h in diag.top_highlights] + [""]
    if diag.most_dangerous:
        lines += ["### ⚠️ 最危险的表述"] + [f"- {x}" for x in diag.most_dangerous] + [""]
    if diag.packets:
        lines.append("### 逐条项目级诊断")
        for i, p in enumerate(diag.packets, 1):
            lines.append(f"**{i}. [{p.claim_type.value}/证据{p.support_strength.value}]** {p.claim}")
            if p.technical_highlight:
                lines.append(f"   - 💡 亮点：{p.technical_highlight}")
            if p.evidence_found:
                src = f"（{('、'.join(p.evidence_sources))}）" if p.evidence_sources else ""
                lines.append(f"   - ✅ 已有证据{src}：{('；'.join(p.evidence_found))}")
            if p.missing_evidence:
                lines.append(f"   - 🔍 缺证据/去哪找：{('；'.join(p.missing_evidence))}")
            if p.interview_questions:
                lines.append(f"   - ❓ 面试官会追问：{('；'.join(p.interview_questions))}")
            if p.safe_now:
                lines.append(f"   - ✍️ 现在能安全写：{p.safe_now}")
            if p.stronger_after_evidence:
                lines.append(f"   - 🚀 补证据后更强：{p.stronger_after_evidence}")
        lines.append("")
    elif diag.issues:  # 规则兜底（无 packets 时）
        lines.append("### 风险点")
        for i, it in enumerate(diag.issues, 1):
            lines += [
                f"{i}. **[{it.category.value}/{it.severity.value}]** {it.excerpt}",
                f"   - 问题：{it.problem}",
                f"   - 改写：{it.suggestion}" if it.suggestion else "",
                f"   - 预期追问：{it.expected_question}" if it.expected_question else "",
            ]
        lines.append("")
    if diag.rewritten_bullets:
        lines += ["### 可直接替换进简历的改写"] + [f"- {b}" for b in diag.rewritten_bullets] + [""]
    if diag.strengths and not diag.top_highlights:
        lines += ["### 可作为硬亮点"] + [f"- {s}" for s in diag.strengths]
    return "\n".join(x for x in lines if x is not None)


def load_resume_text(
    db_path: Optional[str] = None, session_id: Optional[str] = None, max_chars: int = 8000
) -> str:
    """从上传附件（local chunks, origin=attachment）重建一份简历全文。

    优先文件名像简历的文档；否则取最近的文本附件。按 document_id 聚合、chunk_index 还原顺序，
    拼回整篇（不是片段）。无附件/失败 → 空串（调用方据此提示上传）。
    """
    from ...storage.repositories import ChunkRepository

    try:
        conn = ChunkRepository(db_path=db_path).conn
        rows = conn.execute(
            "SELECT chunk_id, text, "
            "json_extract(metadata,'$.document_id') AS doc, "
            "json_extract(metadata,'$.filename') AS fname, "
            "json_extract(metadata,'$.attachment_kind') AS akind, "
            "json_extract(metadata,'$.session_id') AS sess, "
            "COALESCE(json_extract(metadata,'$.chunk_index'),0) AS cidx, "
            "created_at "
            "FROM chunks WHERE kb_scope='local' "
            "AND json_extract(metadata,'$.origin')='attachment'",
        ).fetchall()
    except Exception:
        return ""
    # 只要文本类附件（排除图片）。
    docs: dict = {}
    for r in rows:
        if (r["akind"] or "") == "image":
            continue
        doc = r["doc"] or r["chunk_id"]
        d = docs.setdefault(doc, {"fname": r["fname"] or "", "sess": r["sess"],
                                  "created_at": r["created_at"] or "", "pieces": []})
        d["pieces"].append((r["cidx"], r["text"] or ""))
    if not docs:
        return ""

    def _score(item) -> tuple:
        doc_id, d = item
        fname_low = (d["fname"] or "").lower()
        name_hit = any(c in fname_low for c in _RESUME_FILENAME_CUES)
        sess_hit = bool(session_id) and d["sess"] == session_id
        return (name_hit, sess_hit, d["created_at"])  # 文件名像简历 > 同会话 > 最近

    best_id, best = max(docs.items(), key=_score)
    pieces = sorted(best["pieces"], key=lambda p: (p[0] if isinstance(p[0], int) else 0))
    return "\n".join(t for _, t in pieces).strip()[:max_chars]


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
