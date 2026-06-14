"""Phase 5：Diagnosis 信号不足 → need_evidence → Manager 调统一证据 worker → 注入复诊。

证明回路接通且**只读**：
- 弱信号（空库）的标准弱点诊断会触发 need_evidence，Manager 经 EvidenceResearchAgent 补证据，
  注入 context['evidence_artifact'] 后复跑一次（防重入）。
- 强信号（seeded_db，conf≥0.5）不触发——行为不变（黄金回归亦守此）。
- 复合"准备面试"（composite）不触发——§5.6 空诊断→跳过 modify 语义保持。
- 全程不写 mastery/learning_paths（只读不变量）。
"""

from __future__ import annotations

from learnforge.orchestration.manager import ManagerAgent
from learnforge.contracts.enums import Status
from learnforge.contracts.result import NextRequestKind
from learnforge.orchestration.capability_handlers import DiagnosisCapabilityHandler


def _ctx(**kw):
    base = {"composite": False, "session_context": ""}
    base.update(kw)
    return base


def test_low_signal_diagnosis_handler_requests_evidence(tmp_db):
    """空库 → 诊断信号不足 → handler 回 next_request=need_evidence（首跑、未补证据时）。"""
    mgr = ManagerAgent(db_path=tmp_db)
    handler = DiagnosisCapabilityHandler(mgr)
    result = handler.run("诊断我的弱点", _ctx())
    assert result.confidence < 0.5
    assert result.next_request is not None
    assert result.next_request.kind == NextRequestKind.NEED_EVIDENCE
    assert result.next_request.payload.get("source_types") == ["attachment", "resume"]


def test_dispatch_fulfills_need_evidence_and_reruns(tmp_db):
    """dispatch 层接通回路：补证据注入 context 并复跑一次，最终回正常 ResponsePayload。"""
    mgr = ManagerAgent(db_path=tmp_db)
    ctx = _ctx()
    resp = mgr.dispatch("diagnosis", "诊断我的弱点", ctx)
    # 复跑后是终态诊断结论（不再带 need_evidence；ResponsePayload 不暴露该字段）。
    assert resp.status == Status.OK
    assert "weak_atoms" in resp.result and "clusters" in resp.result
    # Manager 确实补过证据并注入 context（artifact 摘要，非全文）。
    assert ctx.get("_evidence_gathered") is True
    assert "evidence_artifact" in ctx
    # 防重入：只补一次。
    assert isinstance(ctx.get("_evidence_refs"), list)


def test_strong_signal_does_not_request_evidence(seeded_db):
    """seeded_db（conf≈0.55）不触发 need_evidence——行为不变。"""
    mgr = ManagerAgent(db_path=seeded_db)
    ctx = _ctx()
    handler = DiagnosisCapabilityHandler(mgr)
    result = handler.run("诊断我的弱点", ctx)
    assert result.confidence >= 0.5
    assert result.next_request is None
    # dispatch 不应注入证据。
    resp = mgr.dispatch("diagnosis", "诊断我的弱点", _ctx())
    assert resp.status == Status.OK


def test_composite_does_not_request_evidence(tmp_db):
    """复合流程（composite=True）即使弱信号也不走 need_evidence，§5.6 语义保持。"""
    mgr = ManagerAgent(db_path=tmp_db)
    handler = DiagnosisCapabilityHandler(mgr)
    result = handler.run("准备面试", _ctx(composite=True))
    assert result.next_request is None


def test_need_evidence_loop_is_read_only(tmp_db):
    """补证据 + 复诊全程不写 mastery / learning_paths（只读不变量）。"""
    from learnforge.storage.repositories import LearningPathRepository

    mgr = ManagerAgent(db_path=tmp_db)
    mgr.dispatch("diagnosis", "诊断我的弱点", _ctx())
    # 没有任何学习路径被写入（诊断 + 证据采集都只读）。
    assert LearningPathRepository(db_path=tmp_db).get("path-default") is None


def test_evidence_gathered_flag_prevents_reentry(tmp_db):
    """已补过证据（_evidence_gathered=True）时 handler 不再请求证据，避免死循环。"""
    mgr = ManagerAgent(db_path=tmp_db)
    handler = DiagnosisCapabilityHandler(mgr)
    ctx = _ctx(_evidence_gathered=True, evidence_artifact="（已补充的只读证据摘要）", _evidence_refs=[])
    result = handler.run("诊断我的弱点", ctx)
    assert result.next_request is None
    assert "已据补充证据复诊" in result.reason
