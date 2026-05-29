"""Manager 复合 workflow 5.6 + 写边界 + trace/event 埋点（Design §5.6 / §2a / §9a / §4c）。"""

from learnforge.agents.manager import ManagerAgent
from learnforge.contracts.enums import Status
from learnforge.mastery import update_mastery
from learnforge.storage.repositories import (
    AtomRepository,
    EventRepository,
    TraceRepository,
)


def test_composite_empty_diagnosis_skips_modify_suggests_mock(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    agg = mgr.handle("快面试了帮我准备一下", trace_id="c-empty")
    assert [t["agent"] for t in agg["plan"]] == ["diagnosis", "planning"]
    # 诊断为空 → 跳过 modify、建议先 mock，控制权交回（不自动启）。
    assert agg["next_actions"], "应建议进行 mock"
    assert "跳过" in agg["reply_text"] or "模拟面试" in agg["reply_text"]
    assert agg["status"] == Status.OK.value


def test_composite_with_signals_runs_modify(seeded_db):
    mgr = ManagerAgent(db_path=seeded_db)
    agg = mgr.handle("快面试了帮我准备一下", trace_id="c-full")
    assert [t["agent"] for t in agg["plan"]] == ["diagnosis", "planning"]
    # 有弱点信号 → 不跳过 modify，且建议 mock（§5.6 P5）。
    assert "跳过" not in agg["reply_text"]
    assert agg["next_actions"]
    assert agg["replan_count"] <= 2


def test_no_pointless_replan_on_honest_low_confidence(tmp_db):
    # 空库诊断诚实低置信，不应触发无意义 replan 循环。
    mgr = ManagerAgent(db_path=tmp_db)
    agg = mgr.handle("诊断我的弱点", trace_id="c-diag")
    assert agg["replan_count"] == 0


def test_trace_written_per_dispatch(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    mgr.handle("乐观锁还是悲观锁?", trace_id="c-trace")
    traces = TraceRepository(db_path=tmp_db).list_by_trace("c-trace")
    assert len(traces) >= 1
    t = traces[0]
    assert t["sender"] == "manager" and t["recipient"] == "qa"
    assert t["status"] == "ok"


def test_qa_emits_qa_signal_event(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    mgr.handle("乐观锁还是悲观锁?", trace_id="c-qaev")
    events = EventRepository(db_path=tmp_db).list_window("all")
    qa_events = [e for e in events if e.event_type.value == "qa_signal"]
    assert qa_events, "QA 路径应 emit qa_signal 事件"


def test_commit_mastery_is_manager_only_write(seeded_db):
    mgr = ManagerAgent(db_path=seeded_db)
    repo = AtomRepository(db_path=seeded_db)
    before = repo.get("a0").mastery_score
    n = mgr.commit_mastery([{"atom_id": "a0", "signal": 0.9, "source": "mock"}])
    assert n == 1
    after = repo.get("a0")
    assert abs(after.mastery_score - update_mastery(before, 0.9, "mock")) < 1e-9
    assert after.review_count >= 1
