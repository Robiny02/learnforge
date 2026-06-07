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
from learnforge.agents.diagnosis.resume import analyze_resume_rules, split_claims
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
    # SOP + few-shot 进入 system；规则锚点进入 prompt
    system = str(captured.get("system", ""))
    prompt = str(captured.get("prompt", ""))
    assert "简历诊断 SOP" in system and "few-shot" in system
    assert "风险锚点" in prompt and "主导上线企业级 RAG 系统" in prompt
    # 持久化后可完整召回
    assert recall_resume_diagnoses(query="RAG", db_path=tmp_db)[0].summary == "LLM-SUMMARY"
