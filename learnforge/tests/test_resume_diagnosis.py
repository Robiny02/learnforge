"""简历问题诊断 + 详细保存 + 完整召回（蒸馏自 llm-intern-skill）。

覆盖：
- 规则引擎逐 claim 判风险 → 分类/降级/预期追问/维度评分/JD 匹配（离线确定性）。
- DiagnosisAgent.diagnose_resume 端到端：产出 ResumeDiagnosis 并存记忆。
- 记忆「完整召回」：召回重建出整条诊断（含全部 issues），而非片段。
- 只读不变量：diagnose_resume 不写学习状态表（mastery/events/paths/diagnosis_reports）。

全部离线（无 key → 规则兜底），并用 monkeypatch 验证 LLM 路径的 prompt 与采纳。
"""

from __future__ import annotations

from learnforge.agents.diagnosis import DiagnosisAgent
from learnforge.agents.diagnosis.resume import (
    analyze_resume_rules,
    load_resume_text,
    looks_like_resume_request,
    split_claims,
)
from learnforge.knowledge.ingest import ingest_document
from learnforge.orchestration.manager import ManagerAgent
from learnforge.contracts.enums import Status
from learnforge.contracts.agents.diagnosis import ResumeDiagnosis, ResumeIssue
from learnforge.contracts.agents.mock import InterviewContext
from learnforge.contracts.enums import (
    IssueSeverity,
    JDFitVerdict,
    ResumeIssueCategory,
)
from learnforge.memory.resume import (
    latest_resume_diagnosis,
    recall_resume_diagnoses,
    save_resume_diagnosis,
)
from learnforge.storage.db import get_connection
import learnforge.llm.client as llm_client


_RESUME = """主导上线企业级 RAG 系统，准确率显著提升
负责文档问答模块，用 FAISS 做向量检索
做了点前端，感觉还行
基于固定 query 集对比 BM25 与 rerank 的 NDCG@10，并记录了 bad case 类型"""

_STATE_TABLES = ["knowledge_atoms", "interaction_events", "learning_paths",
                 "path_items", "diagnosis_reports"]


def _state_snapshot(db: str) -> dict:
    conn = get_connection(db)
    snap = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
            for t in _STATE_TABLES}
    conn.close()
    return snap


# --------------------------------------------------------------------------- #
# 规则引擎（离线确定性）
# --------------------------------------------------------------------------- #
def test_split_claims_drops_titles_and_short_lines():
    text = "项目经历：\n主导上线企业级 RAG 系统\n- 负责召回模块的实现\nAI"
    claims = split_claims(text)
    assert "主导上线企业级 RAG 系统" in claims
    assert "负责召回模块的实现" in claims        # 项目符号被剥掉
    assert "AI" not in claims                    # 过短行被丢
    assert "项目经历：" not in claims            # 栏目标题被丢


def test_rules_flag_overclaim_and_reward_evidence():
    ctx = InterviewContext(target_role="RAG 算法实习", jd_text="召回 rerank citation 评估")
    d = analyze_resume_rules(_RESUME, ctx)
    # 夸大句 → risky_language / high，带降级写法与预期追问
    risky = [i for i in d.issues if i.category == ResumeIssueCategory.RISKY_LANGUAGE]
    assert risky and risky[0].severity == IssueSeverity.HIGH
    assert risky[0].suggestion and risky[0].expected_question
    assert "主导上线企业级 RAG 系统" in risky[0].excerpt
    # 带 NDCG/bad case 证据的硬经历 → 不报问题，进 strengths
    assert any("NDCG" in s for s in d.strengths)
    # 角色推断 + JD 匹配判定（高风险多 → risky）
    assert d.role_type == "rag"
    assert d.jd_fit == JDFitVerdict.RISKY


def test_rules_skip_personal_info_and_education(tmp_db):
    # 联系方式/学历/日期/GPA 不应被当成简历风险；只评估真实经历/项目。
    resume = (
        "邮箱:273915397@qq.com\n电话:（+1）447-902-7594\n求职意向：后端开发+agent\n"
        "伊利诺伊大学厄巴纳香槟分校\nGPA: 4.0/4.0\n2025-2027\n"
        "主导上线企业级 RAG 系统，准确率显著提升\n"
        "基于固定 query 集对比 BM25 与 rerank 的 NDCG@10，记录了 bad case"
    )
    d = analyze_resume_rules(resume, InterviewContext(target_role="后端+agent"))
    flagged = " ".join(i.excerpt for i in d.issues)
    assert "qq.com" not in flagged and "GPA" not in flagged and "伊利诺伊" not in flagged
    assert "2025-2027" not in flagged
    # 真实风险仍被抓（夸大句）；带证据的硬经历进 strengths
    assert any(i.category == ResumeIssueCategory.RISKY_LANGUAGE for i in d.issues)
    assert any("NDCG" in s for s in d.strengths)


def test_rules_dimensions_and_verdict_scale_with_issues():
    clean = "基于固定样例集对比 baseline 的 NDCG@10 指标，记录了 bad case 与复盘"
    d = analyze_resume_rules(clean, InterviewContext(role_type="rag"))
    # 无风险 + 有证据 → strong，证据维度高
    assert d.jd_fit in (JDFitVerdict.STRONG, JDFitVerdict.MEDIUM)
    assert d.dimensions.evidence_contract >= 3


def test_rules_empty_resume_is_honest():
    d = analyze_resume_rules("", InterviewContext())
    assert d.jd_fit == JDFitVerdict.UNKNOWN
    assert d.confidence == 0.0
    assert not d.issues


# --------------------------------------------------------------------------- #
# 保存 + 完整召回
# --------------------------------------------------------------------------- #
def test_save_and_recall_full_content(tmp_db):
    d = analyze_resume_rules(_RESUME, InterviewContext(target_role="RAG 实习"))
    save_resume_diagnosis(d, db_path=tmp_db)
    got = recall_resume_diagnoses(query="RAG", db_path=tmp_db)
    assert len(got) == 1
    r = got[0]
    # 召回的是完整对象，而非片段：id/issues/dimensions/原始字段都在
    assert r.diagnosis_id == d.diagnosis_id
    assert len(r.issues) == len(d.issues)
    assert r.issues[0].expected_question == d.issues[0].expected_question
    assert r.dimensions.model_dump() == d.dimensions.model_dump()
    assert r.resume_digest == d.resume_digest


def test_recall_substring_fallback_for_cjk(tmp_db):
    d = analyze_resume_rules(_RESUME, InterviewContext(target_role="后端"))
    save_resume_diagnosis(d, db_path=tmp_db)
    # 中文子串召回（FTS 不切中文时靠 LIKE 兜底）
    got = recall_resume_diagnoses(query="向量检索", db_path=tmp_db)
    assert got and got[0].diagnosis_id == d.diagnosis_id


def test_recall_latest_and_ordering(tmp_db):
    d1 = ResumeDiagnosis(target_role="A", created_at="2026-01-01T00:00:00+00:00",
                         issues=[ResumeIssue(category=ResumeIssueCategory.WEAK_PHRASING,
                                             excerpt="x", problem="p")])
    d2 = ResumeDiagnosis(target_role="B", created_at="2026-06-01T00:00:00+00:00")
    save_resume_diagnosis(d1, db_path=tmp_db)
    save_resume_diagnosis(d2, db_path=tmp_db)
    alld = recall_resume_diagnoses(db_path=tmp_db, top_k=10)
    assert [r.target_role for r in alld] == ["B", "A"]          # 新→旧
    assert latest_resume_diagnosis(db_path=tmp_db).target_role == "B"


# --------------------------------------------------------------------------- #
# Agent 端到端 + 只读不变量
# --------------------------------------------------------------------------- #
def test_diagnose_resume_offline_persists_and_is_state_read_only(tmp_db, monkeypatch):
    monkeypatch.setattr(llm_client.LLM, "available", False)  # 走规则兜底
    before = _state_snapshot(tmp_db)
    ag = DiagnosisAgent(db_path=tmp_db)
    d = ag.diagnose_resume(_RESUME, InterviewContext(target_role="RAG 实习"))
    after = _state_snapshot(tmp_db)
    # 学习状态表零改动（只读不变量），但记忆库已落一条可召回
    assert before == after, "diagnose_resume 不得写学习状态表"
    assert d.issues
    assert recall_resume_diagnoses(query="RAG", db_path=tmp_db)


def test_diagnose_resume_llm_path_uses_sop_and_adopts_output(tmp_db, monkeypatch):
    captured = {}

    def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        obj = ResumeDiagnosis(
            target_role="RAG 实习", role_type="rag", jd_fit=JDFitVerdict.MEDIUM,
            summary="LLM-SUMMARY",
            issues=[ResumeIssue(category=ResumeIssueCategory.EVIDENCE_GAP,
                                excerpt="负责文档问答模块", problem="无产物佐证",
                                suggestion="补 demo", expected_question="有无代码?")],
        )

        class _R:
            cost_usd = 0.0
            from learnforge.contracts.message import TokenUsage
            tokens = TokenUsage()
        return obj, _R()

    monkeypatch.setattr(llm_client.LLM, "available", True)
    monkeypatch.setattr(llm_client.LLM, "complete_structured", fake_complete_structured)

    ag = DiagnosisAgent(db_path=tmp_db)
    d = ag.diagnose_resume(_RESUME, InterviewContext(target_role="RAG 实习"))
    # 采纳 LLM 输出
    assert d.summary == "LLM-SUMMARY"
    assert d.issues[0].category == ResumeIssueCategory.EVIDENCE_GAP
    # 项目级 SOP + few-shot 进入 system；简历正文 + pipeline 指令进入 prompt
    system = str(captured.get("system", ""))
    prompt = str(captured.get("prompt", ""))
    assert "项目级简历诊断 SOP" in system and "few-shot" in system
    assert "claim_type" in system and "技术栈背景" in system  # claim 分类 + tech-stack 不当风险
    assert "项目级诊断" in prompt and "主导上线企业级 RAG 系统" in prompt
    assert "EvidencePacket" in prompt or "packets" in prompt  # 证据包 pipeline
    # 持久化后可完整召回
    assert recall_resume_diagnoses(query="RAG", db_path=tmp_db)[0].summary == "LLM-SUMMARY"


# --------------------------------------------------------------------------- #
# 路由：从自然语言/附件自动进入简历诊断
# --------------------------------------------------------------------------- #
def test_looks_like_resume_request():
    assert looks_like_resume_request("诊断一下我的简历有什么问题")
    assert looks_like_resume_request("review my resume / CV")
    assert not looks_like_resume_request("我哪里比较薄弱")


def _ingest_resume_attachment(db, text, filename="我的简历.pdf", session_id="s1"):
    ingest_document(
        text=text, source_type="doc", topic="resume", kb_scope="local",
        metadata={"origin": "attachment", "document_id": "doc-1", "filename": filename,
                  "attachment_kind": "pdf", "session_id": session_id},
        db_path=db, embed=False,
    )


def test_load_resume_text_reconstructs_from_attachment(tmp_db):
    _ingest_resume_attachment(tmp_db, _RESUME)
    # 再放一个非简历附件，确认按文件名优先挑出简历
    ingest_document(text="一些无关笔记内容用于干扰", source_type="doc", kb_scope="local",
                    metadata={"origin": "attachment", "document_id": "doc-2",
                              "filename": "notes.txt", "attachment_kind": "text"},
                    db_path=tmp_db, embed=False)
    text = load_resume_text(db_path=tmp_db, session_id="s1")
    assert "RAG" in text and "NDCG" in text
    assert "无关笔记" not in text       # 选了简历文档，不是干扰附件


def test_manager_routes_resume_request_with_attachment(tmp_db):
    _ingest_resume_attachment(tmp_db, _RESUME)
    mgr = ManagerAgent(db_path=tmp_db)
    resp = mgr._dispatch_impl("diagnosis", "诊断我的简历有什么问题", {"session_id": "s1"})
    assert resp.status == Status.OK
    assert resp.result["kind"] == "resume_diagnosis"
    # 夸大句被识别（换行被附件入库压成空格后仍能切条）
    cats = {i["category"] for i in resp.result["issues"]}
    assert ResumeIssueCategory.RISKY_LANGUAGE.value in cats
    # 已落记忆可召回
    assert recall_resume_diagnoses(query="RAG", db_path=tmp_db)


def test_manager_weakness_diagnosis_not_hijacked_by_resume_route(tmp_db):
    # 普通弱点诊断不应误入简历分支（无"简历"线索）
    mgr = ManagerAgent(db_path=tmp_db)
    resp = mgr._dispatch_impl("diagnosis", "我哪里比较薄弱", {})
    assert resp.result.get("kind") != "resume_diagnosis"


def test_manager_resume_request_without_resume_needs_input(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    resp = mgr._dispatch_impl("diagnosis", "诊断我的简历", {})
    assert resp.status == Status.NEEDS_INPUT
    assert "简历" in resp.result.get("message", "")


# --------------------------------------------------------------------------- #
# PDF 乱码检测（字体子集/无 ToUnicode）→ 诚实降级，不在垃圾上诊断
# --------------------------------------------------------------------------- #
def test_garbled_pdf_text_is_detected():
    from learnforge.multimodal.parse import _looks_garbled

    # 真实样本：pypdf 把子集字体中文映射成随机文字系统的字符
    garbled = ("Ⴏད:273915397@qq.com ߅׈:č+1Ď447-902-7594 ؿagent ऊაື၂ཿೆb "
               "ၫაഈ༯໓ᇍ৘ğݖ Ⴟ Redis LuaؓIPၩ ކࢲRocketMQᇅੲఖაॢ")
    assert _looks_garbled(garbled)
    # 正常中英文简历不应误判
    good = "主导上线企业级 RAG 系统，准确率显著提升\n负责文档问答模块，基于 NDCG@10 评估"
    assert not _looks_garbled(good)
    assert not _looks_garbled("short")  # 过短不判


def test_resume_ui_path_honest_when_text_unusable(tmp_db, monkeypatch):
    # 模拟 PDF 抽取乱码：附件 extracted_text 为空 + degraded → 诚实提示，不输出乱码诊断
    import learnforge.app.server as srv
    from learnforge.contracts.attachment import Attachment, AttachmentManifest

    monkeypatch.setattr(srv, "_mgr", lambda: ManagerAgent(db_path=tmp_db))
    att = Attachment(id="a1", kind="pdf", filename="简历.pdf",
                     degraded=True, note="PDF 文本提取为乱码(字体子集/无 ToUnicode 映射)")
    manifest = AttachmentManifest(documents=[])
    resp = srv._resume_diagnosis_response("诊断我的简历", "", "u1", manifest, [att])
    assert resp["status"] == Status.NEEDS_INPUT.value
    assert "乱码" in resp["reply_text"] or "提取到可用文本" in resp["reply_text"]
    assert ".md" in resp["reply_text"]  # 指引改用文本版


# --------------------------------------------------------------------------- #
# 项目级升级：证据挖掘 / claim 分类 / EvidencePacket 渲染
# --------------------------------------------------------------------------- #
def test_extract_repo_urls():
    from learnforge.agents.diagnosis.evidence import extract_repo_urls
    urls = extract_repo_urls("项目 LearnForge https://github.com/Robiny02/learnforge 2026.4")
    assert urls == ["Robiny02/learnforge"]
    assert extract_repo_urls("无链接") == []


def test_evidence_mining_offline_uses_recall(monkeypatch):
    # pytest 下 github 网络挖掘被跳过；本地 recall（上传材料）仍可用，离线安全。
    from learnforge.agents.diagnosis.evidence import mine_project_evidence
    ev = mine_project_evidence(
        "基于 https://github.com/Robiny02/learnforge 的 Agent 系统",
        recall_fn=lambda q: "【附件：项目说明】Manager 是唯一写者，QA/Diagnosis 是 agent-as-tool",
    )
    assert "uploaded-materials" in ev["sources"]
    assert "唯一写者" in ev["corpus"]
    assert ev["repos"] == ["Robiny02/learnforge"]  # 仍解析出 repo（只是不联网）


def test_rule_engine_skips_tech_stack(tmp_db):
    from learnforge.agents.diagnosis.resume import analyze_resume_rules
    resume = ("技术栈：Python, FastAPI, LangGraph, Redis, Elasticsearch\n"
              "主导上线企业级 RAG 系统，准确率显著提升")
    d = analyze_resume_rules(resume, InterviewContext())
    flagged = " ".join(i.excerpt for i in d.issues)
    assert "技术栈" not in flagged and "FastAPI" not in flagged   # 技术栈不当风险
    assert any(i.category == ResumeIssueCategory.RISKY_LANGUAGE for i in d.issues)  # 真夸大仍抓


def test_render_project_level_packets():
    from learnforge.app.server import _render_resume_diagnosis
    from learnforge.contracts.agents.diagnosis import EvidencePacket
    from learnforge.contracts.enums import ClaimType, EvidenceStrength
    d = ResumeDiagnosis(
        jd_fit=JDFitVerdict.STRONG,
        overall_verdict="整体不错",
        top_highlights=["Manager 唯一写者编排"],
        most_dangerous=["高并发无数据"],
        evidence_sources_used=["github:x/y/CLAUDE.md"],
        packets=[EvidencePacket(
            claim="分层 Agent 架构", claim_type=ClaimType.ARCHITECTURE,
            support_strength=EvidenceStrength.CODE_SUPPORTED,
            technical_highlight="唯一写者 + agent-as-tool",
            evidence_found=["manager.py 有 plan/execute"], evidence_sources=["CLAUDE.md"],
            missing_evidence=["编排成功率无数据 → 看 tests/"],
            interview_questions=["Manager 怎么路由？"],
            safe_now="设计分层编排", stronger_after_evidence="补数据后更强",
        )],
        rewritten_bullets=["原句→改写后的强 bullet"],
    )
    md = _render_resume_diagnosis(d)
    assert "总体判断" in md and "真正能打的亮点" in md and "最危险" in md
    assert "architecture" in md and "唯一写者 + agent-as-tool" in md
    assert "已读取项目材料" in md and "CLAUDE.md" in md
    assert "面试官会追问" in md and "Manager 怎么路由" in md
    assert "可直接替换进简历的改写" in md


def test_manager_aggregate_renders_resume_directly_no_llm(tmp_db, monkeypatch):
    # 无附件「我的简历有什么不足」走主图 → aggregate 应直接结构化渲染，不过 LLM 重写（会截断）。
    monkeypatch.setattr(llm_client.LLM, "available", False)
    _ingest_resume_attachment(tmp_db, _RESUME)
    mgr = ManagerAgent(db_path=tmp_db)
    resp = mgr._dispatch_impl("diagnosis", "我的简历有什么不足吗", {"session_id": "s1"})
    assert resp.result["kind"] == "resume_diagnosis"
    agg = mgr.aggregate([resp])
    rt = agg["reply_text"]
    # 结构化渲染（含标题 + 风险/逐条段），而不是被重写成一段叙述
    assert "## 简历诊断" in rt
    assert "风险点" in rt or "逐条项目级诊断" in rt


def test_jd_default_from_job_intent(tmp_db, monkeypatch):
    # 未提供目标岗位 → 按简历『求职意向』默认评估，target_role 被回填。
    from learnforge.agents.diagnosis.resume import extract_job_intent
    assert extract_job_intent("求职意向：后端开发+agent") == "后端开发+agent"
    monkeypatch.setattr(llm_client.LLM, "available", False)
    resume = "求职意向：后端开发+agent\n主导上线企业级 RAG 系统，准确率显著提升"
    ctx = InterviewContext()  # 不传 target_role
    DiagnosisAgent(db_path=tmp_db).diagnose_resume(resume, ctx)
    assert ctx.target_role == "后端开发+agent"  # 回填


def test_evidence_strength_is_source_based():
    from learnforge.contracts.enums import EvidenceStrength
    vals = {e.value for e in EvidenceStrength}
    assert {"doc_supported", "code_supported", "test_supported", "runtime_supported"} <= vals
    assert "strong" not in vals and "moderate" not in vals  # 不再用笼统强弱


def test_resume_skill_encodes_new_rules():
    from learnforge.skills.bootstrap import ensure_skills_registered
    from learnforge.skills.registry import SKILL_REGISTRY
    ensure_skills_registered()
    sk = SKILL_REGISTRY.get("diagnosis.resume.v1")
    sp, sop = sk.spec.system_prompt, sk.load_instructions()
    # 证据按来源 + 架构不强求指标 + 禁编造 X% + 改写保留关键词 + JD 默认
    assert "doc_supported" in sp and "runtime_supported" in sp
    assert "不要强求性能指标" in sp and "提升 X%" in sp
    assert "handoff summary" in sp and "fallback" in sp  # 改写需保留的关键实体
    assert "unknown" in sp  # JD 默认规则
    # 输出语言跟随简历 + 架构表述纠偏 + stronger 增强版
    assert "不要因为 README/CLAUDE.md 是英文就切英文" in sp
    assert "别写成 Manager 自己 ReAct 调所有工具" in sp
    assert "增强版 bullet" in sp
    # 追问攻具体设计
    assert "为什么唯一写者" in sop and "interrupt/resume" in sop


def test_detect_resume_language():
    from learnforge.agents.diagnosis.resume import detect_resume_language
    assert detect_resume_language("动态主Agent编排：设计以 Manager 为核心的分层架构") == "zh"
    assert detect_resume_language("Designed a layered Manager-centric agent orchestration") == "en"


def test_rewrite_coverage_backfill():
    # 模型只给了 2 条改写但有 3 条核心 claim → 用 packet 的 safe_now 兜底补齐到 3。
    from learnforge.contracts.agents.diagnosis import EvidencePacket
    from learnforge.contracts.enums import ClaimType, EvidenceStrength
    d = ResumeDiagnosis(
        packets=[
            EvidencePacket(claim="动态主Agent编排设计", claim_type=ClaimType.ARCHITECTURE,
                           safe_now="设计 Manager 唯一写者的分层 agent-as-tool 编排"),
            EvidencePacket(claim="记忆与上下文治理分层", claim_type=ClaimType.ARCHITECTURE,
                           safe_now="设计稳定记忆/会话摘要/handoff summary 的分层机制"),
            EvidencePacket(claim="技术栈 Python FastAPI", claim_type=ClaimType.TECH_STACK,
                           support_strength=EvidenceStrength.DOC_SUPPORTED),
        ],
        rewritten_bullets=["设计 Manager 唯一写者的分层 agent-as-tool 编排（plan-execute, replan≤2）"],
    )
    DiagnosisAgent._ensure_rewrite_coverage(d)
    core = 2  # 两条非技术栈 claim
    assert len(d.rewritten_bullets) >= core
    # 第二条核心 claim 的 safe_now 被补进改写
    assert any("handoff summary" in b for b in d.rewritten_bullets)
