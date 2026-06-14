"""Manager 复合 workflow 5.6 + 写边界 + trace/event 埋点（Design §5.6 / §2a / §9a / §4c）。"""

from learnforge.orchestration.manager import ManagerAgent
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


def _force_router_finish(monkeypatch):
    """模拟在线但"早退"的弱路由器：decide_next 的 LLM 调用每步都返回 finish。

    只打桩 NextStep（Manager 路由）这一种 schema，其它（worker 的结构化调用）抛 LLMUnavailable
    走各自确定性兜底，避免污染 worker。
    """
    from learnforge.llm import client as llm_client
    from learnforge.orchestration.manager import NextStep

    class _Res:
        cost_usd = 0.0

    def _fake(prompt, schema, **kwargs):
        if schema is NextStep:
            return NextStep(next_agent="finish"), _Res()
        raise llm_client.LLMUnavailable("stubbed offline for non-router calls")

    monkeypatch.setattr(llm_client.LLM, "available", True)
    monkeypatch.setattr(llm_client.LLM, "complete_structured", _fake)


def test_online_react_guardrail_blocks_finish_before_planning(seeded_db, monkeypatch):
    """在线 ReAct：即使路由器每步都想 finish，用户明确要计划时 planning 也必须先跑（不恢复固定 DAG）。"""
    _force_router_finish(monkeypatch)
    mgr = ManagerAgent(db_path=seeded_db)
    nonempty = {"diagnosis": {"weak_atoms": [{"atom_id": "a0"}]}}

    # 复合"准备面试"：首步路由器想 finish → 护栏先补 diagnosis；诊断（非空）后 → 续到 planning。
    assert mgr.decide_next("快面试了帮我准备一下", [], [], {}) == "diagnosis"
    assert mgr.decide_next("快面试了帮我准备一下", ["diagnosis"], [], nonempty) == "planning"
    # 纯"生成学习计划"（无诊断意图）：护栏直接保证 planning 先跑。
    assert mgr.decide_next("帮我生成学习计划", [], [], {}) == "planning"


def test_online_react_guardrail_does_not_overfire(seeded_db, monkeypatch):
    """护栏不误吞：非计划意图（纯诊断 / 概念问答）首步后 finish，不应被强行拉去 planning。"""
    _force_router_finish(monkeypatch)
    mgr = ManagerAgent(db_path=seeded_db)
    nonempty = {"diagnosis": {"weak_atoms": [{"atom_id": "a0"}]}}

    # 纯诊断：诊断已做后路由器 finish → 不应被护栏拉去 planning。
    assert mgr.decide_next("诊断我的弱点", ["diagnosis"], [], nonempty) == "finish"
    # 概念问答：qa 后 finish。
    assert mgr.decide_next("乐观锁还是悲观锁?", ["qa"], [], {}) == "finish"


def test_no_pointless_replan_on_honest_low_confidence(tmp_db):
    # 空库诊断诚实低置信，不应触发无意义 replan 循环。
    mgr = ManagerAgent(db_path=tmp_db)
    agg = mgr.handle("诊断我的弱点", trace_id="c-diag")
    assert agg["replan_count"] == 0


def test_qa_early_stops_without_extra_routing(tmp_db):
    """简单 QA 是叶子终态：成功答完即停，不再多花一次 decide_next 去确认 finish（省 ReAct 路由开销）。"""
    mgr = ManagerAgent(db_path=tmp_db)
    calls = []
    orig = mgr.decide_next
    mgr.decide_next = lambda *a: (calls.append(1), orig(*a))[1]
    agg = mgr.handle("乐观锁还是悲观锁?", trace_id="qa-early")
    assert [t["agent"] for t in agg["plan"]] == ["qa"]
    assert len(calls) == 1, f"qa 终态应只决策一次，实得 {len(calls)} 次"


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
