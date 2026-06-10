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


# --------------------------------------------------------------------------- #
# 外部链接深挖（link extraction / 分类 / 受控按 claim 取证 / 来源透明）
# --------------------------------------------------------------------------- #
def test_extract_links_classify():
    from learnforge.agents.diagnosis.evidence import extract_links
    from learnforge.contracts.enums import ExternalSourceKind as K
    srcs = extract_links("repo https://github.com/a/b 文件 https://github.com/a/b/blob/main/x.py "
                          "博客 https://juejin.cn/post/1 文档 https://x.gitbook.io/y")
    kinds = {s.url: s.kind for s in srcs}
    assert kinds["https://github.com/a/b"] == K.GITHUB_REPO
    assert kinds["https://github.com/a/b/blob/main/x.py"] == K.GITHUB_FILE
    assert kinds["https://juejin.cn/post/1"] == K.TECH_BLOG
    assert kinds["https://x.gitbook.io/y"] == K.DOCS_PAGE


def test_should_deep_mine_trigger():
    from learnforge.agents.diagnosis.evidence import should_deep_mine
    assert should_deep_mine("见 https://github.com/a/b")          # 有外链 → 深挖
    assert not should_deep_mine("纯文本简历，无链接")              # 无链接 → fast
    assert should_deep_mine("无链接", deep_flag=True)             # deep=true 强制
    assert not should_deep_mine("有 https://github.com/a/b", deep_flag=False)  # 强制 fast


def test_claim_tokens_dynamic_no_hardcoding():
    from learnforge.agents.diagnosis.repo_map import claim_tokens
    toks = claim_tokens("基于 LangGraph 的 ManagerAgent，用 RocketMQ + JWT 做鉴权")
    assert "manager" in toks and "agent" in toks   # CamelCase 拆分
    assert "rocketmq" in toks and "jwt" in toks     # 任意技术词，无需预设
    assert "design" not in toks and "the" not in toks  # 停用词剔除


# --------------------------------------------------------------------------- #
# Repo Map：构建 / 角色识别 / 动态选择（不依赖项目专属路径）
# --------------------------------------------------------------------------- #
def test_infer_role_structural():
    from learnforge.agents.diagnosis.repo_map import infer_role
    assert infer_role("learnforge/orchestration/manager.py") == "source"
    assert infer_role("tests/test_manager.py") == "test"
    assert infer_role("docs/overview.md") == "doc"
    assert infer_role("pyproject.toml") == "config"
    assert infer_role("examples/demo.py") == "example"


def _fake_tree(*paths_sizes):
    return {"tree": [{"path": p, "type": "blob", "size": s} for p, s in paths_sizes]}


def test_build_repo_map_classifies_roles():
    from learnforge.agents.diagnosis.repo_map import build_repo_map
    rm = build_repo_map("a/b",
                        {"repo": "a/b", "description": "d", "languages": {"Python": 1}},
                        _fake_tree(("README.md", 1000), ("src/core.py", 2000),
                                   ("tests/test_core.py", 500), ("config.yaml", 100),
                                   ("node_modules/x.js", 9)))
    roles = rm.summary()
    assert roles.get("doc") == 1 and roles.get("source") == 1
    assert roles.get("test") == 1 and roles.get("config") == 1
    assert all("node_modules" not in f.path for f in rm.files)  # 噪声目录排除


def test_select_files_generalizes_to_non_agent_project():
    # 秒杀项目：按候选人自己的 token(redis/rocketmq) 选源码，不依赖任何 LearnForge 专属规则。
    from learnforge.agents.diagnosis.repo_map import build_repo_map, select_files, claim_tokens
    rm = build_repo_map("a/seckill", {"repo": "a/seckill", "languages": {"Java": 1}},
                        _fake_tree(("README.md", 3000), ("docs/design.md", 5000),
                                   ("src/RedisStockService.java", 2000),
                                   ("src/RocketMQProducer.java", 1500),
                                   ("src/UserController.java", 1000),
                                   ("application.yml", 300)))
    sel = select_files(rm, claim_tokens("用 Redis 扣库存，RocketMQ 异步下单"), budget=5)
    paths = [s.path for s in sel]
    assert "src/RedisStockService.java" in paths      # 命中 redis
    assert "src/RocketMQProducer.java" in paths        # 命中 rocketmq
    assert "src/UserController.java" not in paths       # 无关 claim → 不选
    # 每条都可解释
    redis = [s for s in sel if "Redis" in s.path][0]
    assert redis.selected_reason and "redis" in redis.expected_claims


def test_select_files_different_tokens_pick_different_files():
    from learnforge.agents.diagnosis.repo_map import build_repo_map, select_files, claim_tokens
    rm = build_repo_map("a/b", {"repo": "a/b"},
                        _fake_tree(("src/auth_jwt.py", 1000), ("src/payment_stripe.py", 1000)))
    a = {s.path for s in select_files(rm, claim_tokens("JWT 鉴权"), budget=3)}
    b = {s.path for s in select_files(rm, claim_tokens("Stripe 支付"), budget=3)}
    assert "src/auth_jwt.py" in a and "src/auth_jwt.py" not in b
    assert "src/payment_stripe.py" in b and "src/payment_stripe.py" not in a


def test_deep_mining_reads_claim_files(monkeypatch):
    # 离线模拟 github：深挖应读 README + 按 claim 找的源码/测试，并记录 external_sources。
    import learnforge.agents.diagnosis.evidence as EV
    from learnforge.tools.mcp.servers import github

    monkeypatch.setattr(EV, "_in_pytest", lambda: False)  # 放开网络门（下面已把 github 打桩）

    def _resp(d):
        import json
        return {"content": [{"type": "text", "text": json.dumps(d)}], "isError": False}

    monkeypatch.setattr(github, "repo_summary", lambda a: _resp(
        {"repo": "a/b", "languages": {"Python": 1}, "readme_excerpt": "README mentions manager"}))
    monkeypatch.setattr(github, "list_tree", lambda a: _resp(_fake_tree(
        ("learnforge/orchestration/manager.py", 2000),
        ("tests/test_manager.py", 500), ("CLAUDE.md", 1000))))
    monkeypatch.setattr(github, "read_file", lambda a: _resp(
        {"path": a["path"], "content": f"class Manager: pass  # {a['path']}"}))

    ev = EV.mine_project_evidence("Manager 编排唯一写者 https://github.com/a/b", deep=True)
    src = [s for s in ev["external_sources"] if s.kind.value == "github_repo"][0]
    assert "README.md" in src.items_read
    assert "learnforge/orchestration/manager.py" in src.items_read  # 按 claim 读到源码
    assert src.evidence_kind in ("code", "test")  # 证据类型升级（读到源码）
    # repo map 选择被记录（含可解释依据 + 读后内容匹配）
    paths = {f.path: f for f in src.selected_files}
    mgr = paths["learnforge/orchestration/manager.py"]
    assert mgr.read_success and mgr.selected_reason
    assert "manager" in mgr.matched_claims          # 内容级匹配（不只文件名）
    assert mgr.evidence_kind == "code"


def test_selected_file_read_but_no_match():
    # 读到文件但内容不含 claim token → read_success_but_no_match=True（不据此判支持）。
    from learnforge.agents.diagnosis.evidence import _content_match
    from learnforge.contracts.agents.diagnosis import SelectedFile
    matched, facts = _content_match("def helper(): return 1", ["manager", "redis"])
    assert matched == [] and facts == []
    f = SelectedFile(path="x.py", read_success=True)
    f.matched_claims = matched
    f.read_success_but_no_match = not matched
    assert f.read_success_but_no_match


def test_render_shows_selected_files_with_reasons():
    from learnforge.agents.diagnosis.resume import render_resume_diagnosis
    from learnforge.contracts.agents.diagnosis import ExternalSource, SelectedFile
    from learnforge.contracts.enums import ExternalSourceKind
    d = ResumeDiagnosis(
        jd_fit=JDFitVerdict.STRONG, overall_verdict="ok",
        external_sources=[
            ExternalSource(url="https://github.com/a/b", kind=ExternalSourceKind.GITHUB_REPO,
                           status="read", evidence_kind="code", items_read=["manager.py"],
                           selected_files=[
                               SelectedFile(path="orchestration/manager.py", role="source",
                                            evidence_kind="code", selected_reason="源码：命中 claim manager",
                                            read_success=True, matched_claims=["manager"]),
                               SelectedFile(path="tests/test_x.py", role="test", evidence_kind="test",
                                            selected_reason="覆盖测试类型（多样性）", read_success=True,
                                            read_success_but_no_match=True)]),
            ExternalSource(url="https://blog.x/p", kind=ExternalSourceKind.TECH_BLOG,
                           status="failed", reason="页面不可达"),
        ],
    )
    md = render_resume_diagnosis(d)
    assert "repo map 动态选择" in md
    assert "orchestration/manager.py" in md and "源码：命中 claim manager" in md  # 选择理由可见
    assert "读到但未命中 claim" in md         # 区分读到 vs 支持
    assert "未读取" in md and "页面不可达" in md  # 失败链接透明展示


# --------------------------------------------------------------------------- #
# Repo-RAG + Reranker + 受控 ReAct 证据循环
# --------------------------------------------------------------------------- #
def _mk_sel(path, role="source", hits=None):
    from learnforge.contracts.agents.diagnosis import SelectedFile
    return SelectedFile(path=path, role=role, evidence_kind="code",
                        expected_claims=hits or [], selected_reason="x")


def test_rerank_fallback_deterministic_without_llm(monkeypatch):
    import learnforge.agents.diagnosis.evidence as EV
    monkeypatch.setattr(EV, "_llm_pick", lambda *a, **k: None)  # 无 LLM → 退确定性
    cands = [_mk_sel(f"f{i}.py") for i in range(6)]
    out = EV.rerank_candidates("claims", cands, top_k=3)
    assert [s.path for s in out] == ["f0.py", "f1.py", "f2.py"]  # 保持确定性序前 3


def test_rerank_uses_llm_order(monkeypatch):
    import learnforge.agents.diagnosis.evidence as EV
    monkeypatch.setattr(EV, "_llm_pick", lambda prompt, n: [3, 1])  # LLM 选 3,1
    cands = [_mk_sel(f"f{i}.py") for i in range(5)]
    out = EV.rerank_candidates("claims", cands, top_k=3)
    assert out[0].path == "f3.py" and out[1].path == "f1.py"  # LLM 选的在前
    assert len(out) == 3  # 不足再用确定性补满


def test_react_next_files_targets_unmatched(monkeypatch):
    import learnforge.agents.diagnosis.evidence as EV
    monkeypatch.setattr(EV, "_llm_pick", lambda *a, **k: None)  # 退确定性
    remaining = [_mk_sel("src/auth/jwt_filter.py"), _mk_sel("src/order/pay.py")]
    out = EV.react_next_files("claims", remaining, unmatched=["jwt"], k=1)
    assert out and out[0].path == "src/auth/jwt_filter.py"  # 命中 unmatched 'jwt'


def test_important_unmatched_filters_noise():
    from learnforge.agents.diagnosis.evidence import _important_unmatched
    um = _important_unmatched({"manager", "github", "https", "jwt", "io"}, matched={"manager"})
    assert "jwt" in um and "manager" not in um  # 已命中的剔除
    assert "github" not in um and "https" not in um and "io" not in um  # 噪声剔除


def test_rag_loop_respects_budget(monkeypatch):
    # 受控：单 repo 读取数不超过预算，离线（无 LLM）也能跑确定性 RAG。
    import learnforge.agents.diagnosis.evidence as EV
    from learnforge.tools.mcp.servers import github
    monkeypatch.setattr(EV, "_in_pytest", lambda: False)

    def _resp(d):
        import json
        return {"content": [{"type": "text", "text": json.dumps(d)}], "isError": False}

    monkeypatch.setattr(github, "repo_summary", lambda a: _resp(
        {"repo": "a/b", "languages": {"Python": 1}, "readme_excerpt": "redis jwt"}))
    monkeypatch.setattr(github, "list_tree", lambda a: _resp(_fake_tree(
        *[(f"src/redis_{i}.py", 500) for i in range(10)])))
    monkeypatch.setattr(github, "read_file", lambda a: _resp(
        {"path": a["path"], "content": "redis jwt code"}))

    ev = EV.mine_project_evidence("用 Redis 做缓存，JWT 鉴权 https://github.com/a/b", deep=True)
    src = [s for s in ev["external_sources"] if s.kind.value == "github_repo"][0]
    read = [f for f in src.selected_files if f.read_success]
    assert len(read) <= 1 + EV._RAG_READ_PER_REPO + 2  # README + rerank + ≤1 轮 react×2，受控
    assert any(f.path == "README.md" for f in src.selected_files)  # SOP 入口先读


# --------------------------------------------------------------------------- #
# 增强：claim-level judge / preview rerank / re-search react / 输出分类
# --------------------------------------------------------------------------- #
def test_file_preview_extracts_signals():
    from learnforge.agents.diagnosis.repo_map import file_preview
    assert "class Manager:" in file_preview("m.py", "import os\nclass Manager:\n  def route(self):pass")
    assert "## Architecture" in file_preview("r.md", "# T\n## Architecture\nbody")
    assert "redis" in file_preview("c.yaml", "redis:\n  host: x\njwt_secret: y")


def test_rerank_listing_includes_preview():
    import learnforge.agents.diagnosis.evidence as EV
    c = _mk_sel("src/auth.py", hits=["jwt"])
    c.preview = "class JwtFilter: | def verify(self): pass"
    assert "preview:class JwtFilter" in EV._candidate_listing([c])


def test_judge_claim_support_fallback_without_llm(monkeypatch):
    import learnforge.agents.diagnosis.evidence as EV
    monkeypatch.setattr(llm_client.LLM, "available", False)  # 无 LLM → token-level 兜底
    j = EV.judge_claim_support("用 Redis 缓存", [("x.py", "source", [], [])],
                               tokens={"redis", "cache", "github"}, matched={"cache"})
    assert "redis" in j["next_queries"]      # 未命中 → 进 next_queries
    assert "cache" not in j["next_queries"]   # 已命中剔除
    assert "github" not in j["next_queries"]  # 噪声剔除


def test_judge_claim_support_uses_llm(monkeypatch):
    import learnforge.agents.diagnosis.evidence as EV

    def fake_cs(**kw):
        schema = kw["schema"]
        obj = schema(supported=["用了 Redis"], missing=["高并发未证实"],
                     next_queries=["ratelimit", "lua"])

        class _R:
            cost_usd = 0.0
            from learnforge.contracts.message import TokenUsage
            tokens = TokenUsage()
        return obj, _R()

    monkeypatch.setattr(llm_client.LLM, "available", True)
    monkeypatch.setattr(llm_client.LLM, "complete_structured", fake_cs)
    j = EV.judge_claim_support("claims", [("x.py", "source", ["redis"], ["redis code"])],
                               tokens={"redis"}, matched={"redis"})
    assert j["missing"] == ["高并发未证实"] and "ratelimit" in j["next_queries"]


def test_search_repo_re_search_excludes_read():
    from learnforge.agents.diagnosis.repo_map import build_repo_map, search_repo, claim_tokens
    rm = build_repo_map("a/b", {"repo": "a/b"},
                        _fake_tree(("src/ratelimit.py", 800), ("src/lua_script.py", 600),
                                   ("src/auth.py", 500)))
    out = search_repo(rm, claim_tokens("ratelimit lua"),
                      exclude_paths={"src/auth.py"}, k=2)
    paths = [s.path for s in out]
    assert "src/ratelimit.py" in paths and "src/auth.py" not in paths  # 重搜 + 排除已读


def test_deep_loop_judge_and_suggested_next_reads(monkeypatch):
    # 端到端（离线 github + mocked judge）：emit 证据 + claim-level judge 给 suggested_next_reads。
    import learnforge.agents.diagnosis.evidence as EV
    from learnforge.tools.mcp.servers import github
    monkeypatch.setattr(EV, "_in_pytest", lambda: False)
    monkeypatch.setattr(EV, "judge_claim_support",
                        lambda *a, **k: {"supported": ["redis"], "missing": ["高并发"],
                                         "next_queries": ["ratelimit", "lua"]})

    def _resp(d):
        import json
        return {"content": [{"type": "text", "text": json.dumps(d)}], "isError": False}

    monkeypatch.setattr(github, "repo_summary", lambda a: _resp(
        {"repo": "a/b", "languages": {"Python": 1}, "readme_excerpt": "redis cache"}))
    monkeypatch.setattr(github, "list_tree", lambda a: _resp(_fake_tree(
        ("src/redis_cache.py", 800), ("src/ratelimit.py", 700), ("tests/test_cache.py", 300))))
    monkeypatch.setattr(github, "read_file", lambda a: _resp(
        {"path": a["path"], "content": "redis ratelimit code"}))

    ev = EV.mine_project_evidence("用 Redis 缓存，高并发限流 https://github.com/a/b", deep=True)
    src = [s for s in ev["external_sources"] if s.kind.value == "github_repo"][0]
    assert src.suggested_next_reads                      # judge 输出仍缺证据 → 建议继续
    assert any(f.read_success and f.matched_claims for f in src.selected_files)  # 有 supported
    # ReAct 据 next_queries 重搜读到 ratelimit
    assert any("ratelimit" in f.path for f in src.selected_files)


# --------------------------------------------------------------------------- #
# 子断言级证据 + generic 弱信号 + 跨项目隔离
# --------------------------------------------------------------------------- #
def test_content_match_ignores_generic_tokens():
    from learnforge.agents.diagnosis.evidence import _content_match, _GENERIC_TOKENS
    # 只命中泛化词 → 不算强证据（matched 空，facts 空）
    m, f = _content_match("class Agent: pass  # learn graph judge",
                          ["agent", "learn", "graph", "judge"], generic=_GENERIC_TOKENS)
    assert m == [] and f == []
    # 项目名也剔除
    m2, _ = _content_match("learnforge core", ["learnforge"], generic=_GENERIC_TOKENS | {"learnforge"})
    assert m2 == []
    # 具体 token 仍命中
    m3, _ = _content_match("redis lua ratelimit", ["redis", "lua"], generic=_GENERIC_TOKENS)
    assert set(m3) == {"redis", "lua"}


def test_enforce_subclaim_support_takes_weakest():
    from learnforge.contracts.agents.diagnosis import EvidencePacket, SubClaim
    from learnforge.contracts.enums import ClaimType, EvidenceStrength as ES
    pkt = EvidencePacket(claim="动态主Agent编排", claim_type=ClaimType.ARCHITECTURE,
                         support_strength=ES.CODE_SUPPORTED,
                         subclaims=[
                             SubClaim(text="Manager 唯一写者", support_strength=ES.CODE_SUPPORTED),
                             SubClaim(text="replan≤2", support_strength=ES.NONE),  # 无证据
                         ])
    d = ResumeDiagnosis(packets=[pkt])
    DiagnosisAgent._enforce_subclaim_support(d)
    # 某子点有代码 ≠ 整条 code_supported；取最弱 → none
    assert d.packets[0].support_strength == ES.NONE


def test_subclaim_schema_roundtrip_and_render():
    from learnforge.agents.diagnosis.resume import render_resume_diagnosis
    from learnforge.contracts.agents.diagnosis import EvidencePacket, SubClaim
    from learnforge.contracts.enums import ClaimType, EvidenceStrength as ES
    d = ResumeDiagnosis(
        jd_fit=JDFitVerdict.STRONG, overall_verdict="ok",
        packets=[EvidencePacket(
            claim="动态主Agent编排", claim_type=ClaimType.ARCHITECTURE, support_strength=ES.DOC_SUPPORTED,
            subclaims=[
                SubClaim(text="Manager 唯一写者", support_strength=ES.CODE_SUPPORTED,
                         evidence_sources=["orchestration/manager.py"]),
                SubClaim(text="replan≤2", support_strength=ES.NONE,
                         missing_evidence=["未读到 replan 相关源码"])])])
    # JSON 往返
    r = ResumeDiagnosis.model_validate_json(d.model_dump_json())
    assert r.packets[0].subclaims[1].text == "replan≤2"
    md = render_resume_diagnosis(d)
    assert "子断言[code_supported] Manager 唯一写者" in md
    assert "子断言[none] replan≤2" in md and "未读到 replan" in md


def test_resume_skill_encodes_subclaim_and_focus_rules():
    from learnforge.skills.bootstrap import ensure_skills_registered
    from learnforge.skills.registry import SKILL_REGISTRY
    ensure_skills_registered()
    sk = SKILL_REGISTRY.get("diagnosis.resume.v1")
    sop = sk.load_instructions()
    assert "子断言拆分" in sop and "取各 subclaim 最弱项" in sop
    assert "泛化词" in sop and "项目名" in sop  # generic 弱信号
    assert "orchestration/manager.py" in sop   # code_supported 需具体文件


def test_project_analysis_intent_routes_to_project_diagnosis():
    # 「分析我的项目 / github 链接」应判为简历级项目诊断，而非学习弱点诊断。
    from learnforge.agents.diagnosis.resume import looks_like_resume_request
    assert looks_like_resume_request("重新分析我的项目")
    assert looks_like_resume_request("分析我的项目")
    assert looks_like_resume_request("看看 https://github.com/a/b")
    assert looks_like_resume_request("诊断我的简历")
    assert not looks_like_resume_request("我哪里比较薄弱")   # 学习弱点诊断，不劫持
    assert not looks_like_resume_request("制定学习计划")


def test_dispatch_shortcuts_project_analysis_to_diagnosis():
    from learnforge.intent.dispatch import Dispatcher
    d = Dispatcher()
    assert d.route("重新分析我的项目").capability == "diagnosis"
    assert d.route("分析我的项目 github.com/a/b").capability == "diagnosis"
    assert d.route("什么是乐观锁").capability == "qa"        # 概念问句不受影响


# --------------------------------------------------------------------------- #
# 项目 section 隔离 / no_match 一致性 / 具体 next_reads / mixed support 展示
# --------------------------------------------------------------------------- #
def test_extract_project_section_isolates_per_project():
    from learnforge.agents.diagnosis.repo_map import extract_project_section, claim_tokens
    resume = ("秒杀系统 https://github.com/me/seckill\n用 Redis 扣库存，RocketMQ 异步下单\n"
              "LearnForge https://github.com/me/learnforge\nManager 唯一写者，子 Agent ReAct 调工具")
    sec = extract_project_section(resume, "me/learnforge")
    t = claim_tokens(sec)
    assert "manager" in t and "react" in t        # 本项目词在
    assert "redis" not in t and "rocketmq" not in t  # 别项目词不混入（req1）


def test_reconcile_no_match_with_subclaim_sources():
    # req3：被某 subclaim 引用为证据的文件，不能再显示 read_success_but_no_match。
    from learnforge.contracts.agents.diagnosis import (
        EvidencePacket, SubClaim, ExternalSource, SelectedFile)
    from learnforge.contracts.enums import ClaimType, ExternalSourceKind, EvidenceStrength as ES
    d = ResumeDiagnosis(
        packets=[EvidencePacket(claim="编排", claim_type=ClaimType.ARCHITECTURE, subclaims=[
            SubClaim(text="Manager 唯一写者", support_strength=ES.CODE_SUPPORTED,
                     evidence_sources=["orchestration/manager.py"])])],
        external_sources=[ExternalSource(
            url="https://github.com/a/b", kind=ExternalSourceKind.GITHUB_REPO,
            selected_files=[SelectedFile(path="orchestration/manager.py", read_success=True,
                                         read_success_but_no_match=True)])])
    DiagnosisAgent._reconcile_no_match(d)
    assert not d.external_sources[0].selected_files[0].read_success_but_no_match  # 被引用 → 不再 no_match


def test_support_summary_shows_mix():
    from learnforge.contracts.agents.diagnosis import EvidencePacket, SubClaim
    from learnforge.contracts.enums import ClaimType, EvidenceStrength as ES
    d = ResumeDiagnosis(packets=[EvidencePacket(
        claim="编排", claim_type=ClaimType.ARCHITECTURE, subclaims=[
            SubClaim(text="a", support_strength=ES.CODE_SUPPORTED),
            SubClaim(text="b", support_strength=ES.DOC_SUPPORTED),
            SubClaim(text="c", support_strength=ES.NONE)])])
    DiagnosisAgent._enforce_subclaim_support(d)
    p = d.packets[0]
    assert p.support_strength == ES.NONE                 # 最弱
    assert "code_supported" in p.support_summary and "none" in p.support_summary  # 混合展示（req5）
    assert "最弱=none" in p.support_summary


def test_suggested_next_reads_are_concrete(monkeypatch):
    # req4：suggested_next_reads 是 search:<query>/read:<path>，不是抽象产品词。
    import learnforge.agents.diagnosis.evidence as EV
    from learnforge.tools.mcp.servers import github
    monkeypatch.setattr(EV, "_in_pytest", lambda: False)
    monkeypatch.setattr(EV, "judge_claim_support",
                        lambda *a, **k: {"supported": [], "missing": ["x"], "next_queries": ["ratelimit"]})

    def _resp(d):
        import json
        return {"content": [{"type": "text", "text": json.dumps(d)}], "isError": False}

    monkeypatch.setattr(github, "repo_summary", lambda a: _resp({"repo": "a/b", "readme_excerpt": ""}))
    monkeypatch.setattr(github, "list_tree", lambda a: _resp(_fake_tree(
        ("src/ratelimit.py", 800), ("src/redis_cache.py", 700))))
    monkeypatch.setattr(github, "read_file", lambda a: _resp({"path": a["path"], "content": "x"}))

    ev = EV.mine_project_evidence("用 Redis 缓存，限流 ratelimit https://github.com/a/b", deep=True)
    src = [s for s in ev["external_sources"] if s.kind.value == "github_repo"][0]
    assert src.suggested_next_reads
    assert all(x.startswith(("search:", "read:")) for x in src.suggested_next_reads)  # 具体对象
