"""Phase 1 契约测试：AgentResult / ArtifactRef / NextRequest（纯加法，零行为改动）。

只断言契约结构与 ResponsePayload 的双向 adapter 等价性。不触碰 Manager / 子 Agent。
设计文档：docs/architecture/refactor-plan.md。
"""

from __future__ import annotations

from learnforge.contracts import (
    AgentResult,
    ArtifactKind,
    ArtifactRef,
    NextRequest,
    NextRequestKind,
)
from learnforge.contracts.agents.evidence import EvidenceRef
from learnforge.contracts.enums import EvidenceSourceType, Status
from learnforge.contracts.message import ErrorInfo, ResponsePayload, TokenUsage


def test_agent_result_defaults_are_empty():
    r = AgentResult(status=Status.OK, confidence=0.5)
    assert r.payload == {}
    assert r.evidence_refs == []
    assert r.artifact_refs == []
    assert r.next_request is None
    assert r.error is None


def test_from_response_payload_preserves_core_fields():
    resp = ResponsePayload(
        status=Status.OK,
        confidence=0.73,
        result={"answer": "x", "topic": "rag"},
        reason="conf=0.73；引用2条",
        cost_usd=0.012,
        tokens=TokenUsage(prompt=100, completion=20),
    )
    r = AgentResult.from_response_payload(resp)
    assert r.status == Status.OK
    assert r.confidence == 0.73
    assert r.payload == {"answer": "x", "topic": "rag"}
    assert r.reason == "conf=0.73；引用2条"
    assert r.cost_usd == 0.012
    assert r.tokens.prompt == 100 and r.tokens.completion == 20
    # 新字段在提升时为空（行为等价）。
    assert r.evidence_refs == [] and r.artifact_refs == [] and r.next_request is None


def test_to_response_payload_round_trips_core_fields():
    r = AgentResult(
        status=Status.ESCALATE,
        confidence=0.5,
        payload={"escalate_action": "diagnose"},
        reason="mock 升级",
        cost_usd=0.0,
        evidence_refs=[EvidenceRef(locator="a.py", snippet="def f()", score=0.6)],
        artifact_refs=[ArtifactRef(kind=ArtifactKind.NOTION, ref="https://n/abc")],
        next_request=NextRequest(kind=NextRequestKind.NEED_EVIDENCE, payload={"query": "x"}),
    )
    resp = r.to_response_payload()
    assert isinstance(resp, ResponsePayload)
    assert resp.status == Status.ESCALATE
    assert resp.confidence == 0.5
    assert resp.result == {"escalate_action": "diagnose"}
    assert resp.reason == "mock 升级"
    # 投影刻意丢弃附加字段——不影响 ResponsePayload 语义。
    assert not hasattr(resp, "evidence_refs")


def test_round_trip_payload_is_stable():
    resp = ResponsePayload(status=Status.PARTIAL, confidence=0.4,
                           result={"k": [1, 2, 3]}, reason="r", error=ErrorInfo(code="e", message="m"))
    back = AgentResult.from_response_payload(resp).to_response_payload()
    assert back.status == resp.status
    assert back.confidence == resp.confidence
    assert back.result == resp.result
    assert back.reason == resp.reason
    assert back.error is not None and back.error.code == "e"


def test_artifact_and_next_request_serialize():
    a = ArtifactRef(kind=ArtifactKind.COMMITTED_PATH, ref="path-default", label="已更新路径")
    assert a.model_dump()["kind"] == "committed_path"
    nr = NextRequest(
        kind=NextRequestKind.NEED_EVIDENCE,
        reason="diagnosis 信号不足",
        payload={"query": "rag", "source_types": [EvidenceSourceType.RESUME.value]},
    )
    d = nr.model_dump()
    assert d["kind"] == "need_evidence"
    assert d["payload"]["source_types"] == ["resume"]


def test_evidence_refs_carry_pointers_not_full_text():
    """证据边界：evidence_refs 只携带 locator + 截断 snippet，不是全文。"""
    ref = EvidenceRef(locator="resume.md", snippet="主导了 X 系统", source_type=EvidenceSourceType.RESUME)
    r = AgentResult(status=Status.OK, confidence=0.5, evidence_refs=[ref])
    assert r.evidence_refs[0].locator == "resume.md"
    assert r.evidence_refs[0].source_type == EvidenceSourceType.RESUME
