"""Phase 3 黄金回归：锁住 Manager.dispatch() 对四能力的输出形状（离线 stub 模式）。

在“把 _dispatch_impl 的 if/elif 改成注册表分发”前后，本测试必须**逐字段一致**——证明接线是
行为保持的重构，而非改写。断言确定性字段（status/confidence/reason/result 关键键），不断言
LLM 文本质量（离线兜底已确定性）。
"""

from __future__ import annotations

import pytest

from learnforge.orchestration.manager import ManagerAgent
from learnforge.contracts.enums import Status, Verdict


@pytest.fixture
def mgr(seeded_db) -> ManagerAgent:
    return ManagerAgent(db_path=seeded_db)


def _ctx(**kw):
    base = {"composite": False, "session_context": ""}
    base.update(kw)
    return base


def test_golden_qa(mgr):
    resp = mgr.dispatch("qa", "什么是乐观锁", _ctx())
    assert resp.status == Status.OK
    assert resp.confidence == 0.5
    res = resp.result
    assert sorted(res.keys()) == ["answer", "citations", "confidence", "topic", "verdict"]
    assert res["verdict"] == Verdict.UNCERTAIN.value
    assert isinstance(res["answer"], str) and len(res["answer"]) > 0
    assert "引用" in resp.reason and "核验" in resp.reason


def test_golden_diagnosis(mgr):
    resp = mgr.dispatch("diagnosis", "诊断我的弱点", _ctx())
    assert resp.status == Status.OK
    assert resp.confidence == pytest.approx(0.55, abs=0.2)
    res = resp.result
    assert sorted(res.keys()) == ["clusters", "confidence", "recommendations", "weak_atoms"]
    assert len(res["clusters"]) >= 1
    assert "簇" in resp.reason


def test_golden_planning(mgr):
    resp = mgr.dispatch("planning", "生成学习计划", _ctx())
    assert resp.status == Status.OK
    assert resp.confidence == 0.6
    res = resp.result
    assert sorted(res.keys()) == ["diff", "status"]
    assert sorted(res["diff"].keys()) == ["add", "rationale", "remove", "reorder"]
    assert resp.reason.startswith("status=")


def test_golden_mock(mgr):
    resp = mgr.dispatch("mock", "模拟面试 并发", _ctx(mock_session_id="gold-sess"))
    assert resp.status == Status.OK
    assert resp.confidence == 0.5
    res = resp.result
    assert res["status"] == "active"
    assert res["question"]
    assert "轮次推进" in resp.reason


def test_golden_planning_modify_uses_diagnosis_context(mgr):
    """planning 在 context 带 diagnosis 时走 MODIFY（与 generate 同样产 PathDiff）。"""
    diag = mgr.dispatch("diagnosis", "诊断我的弱点", _ctx()).result
    resp = mgr.dispatch("planning", "据此改计划", _ctx(diagnosis=diag, composite=True))
    assert resp.status == Status.OK
    assert resp.confidence == 0.6
    assert sorted(resp.result.keys()) == ["diff", "status"]


def test_unknown_capability_is_not_registered():
    """注册表只认四能力；非法能力名 get 不到（dispatch 层 require_tool 也会先拦）。"""
    from learnforge.orchestration.capability_handlers import build_default_handlers

    reg = build_default_handlers(ManagerAgent.__new__(ManagerAgent))
    assert reg.get("qa") is not None
    assert reg.get("composite") is None
    assert reg.get("nope") is None
