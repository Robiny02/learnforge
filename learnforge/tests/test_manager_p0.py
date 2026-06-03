"""P0 闭环数据流接线测试（Design §2a/§3.7/§4c/§5.2/§5.4/§6b）。

覆盖四项此前"定义但未接线"的写/交接回路：
- commit_path 接线 + PATH_CHANGED 事件（apply_path_diff）
- needs_input 追问回路（aggregate 生成澄清问题）
- mock escalate → handoff 摘要注入
- diagnosis_reports 落库（dispatch / settle_mock 后由 Manager 单点写）
"""

from learnforge.agents.manager import ManagerAgent
from learnforge.contracts.agents.mock import MockOutput, Score
from learnforge.contracts.agents.planning import PathDiff, PathItem, ReorderOp
from learnforge.contracts.enums import Status
from learnforge.contracts.message import ResponsePayload
from learnforge.storage.repositories import (
    DiagnosisReportRepository,
    EventRepository,
    LearningPathRepository,
)


# ---------------- P0-1: commit_path 接线 + PATH_CHANGED ----------------
def test_apply_path_diff_commits_and_emits_event(seeded_db):
    mgr = ManagerAgent(db_path=seeded_db)  # seeded 有 a0/a1/a2
    diff = PathDiff(
        add=[
            PathItem(item_id="i0", atom_id="a0", order_idx=1, day_index=1),
            PathItem(item_id="i1", atom_id="a1", order_idx=0, day_index=1),
            PathItem(item_id="ibad", atom_id="not_exist", order_idx=2, day_index=2),
        ],
        rationale="加强并发",
    )
    path_id = mgr.apply_path_diff(diff, existing_path_ref="current", user_id="u1", trace_id="t1")
    assert path_id == "path-u1"

    path = LearningPathRepository(db_path=seeded_db).get(path_id)
    item_ids = [it.item_id for it in path.items]
    assert "ibad" not in item_ids  # 不存在的 atom 被过滤（保护外键）
    assert item_ids == ["i1", "i0"]  # 按 order_idx 排序

    events = EventRepository(db_path=seeded_db).list_window("all")
    pc = [e for e in events if e.event_type.value == "path_changed"]
    assert pc and pc[0].signal["added"] == 2


def test_empty_diff_is_noop(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    assert mgr.apply_path_diff(PathDiff()) is None
    events = EventRepository(db_path=tmp_db).list_window("all")
    assert not [e for e in events if e.event_type.value == "path_changed"]


def test_reorder_applies(seeded_db):
    mgr = ManagerAgent(db_path=seeded_db)
    mgr.apply_path_diff(
        PathDiff(add=[
            PathItem(item_id="i0", atom_id="a0", order_idx=0, day_index=1),
            PathItem(item_id="i1", atom_id="a1", order_idx=1, day_index=1),
        ]),
        user_id="u2",
    )
    mgr.apply_path_diff(PathDiff(reorder=[ReorderOp(item_id="i0", new_order=5)]), user_id="u2")
    path = LearningPathRepository(db_path=seeded_db).get("path-u2")
    assert [it.item_id for it in path.items] == ["i1", "i0"]


# ---------------- P0-2: needs_input 追问回路 ----------------
def test_needs_input_surfaces_clarification(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    # generate 模式空 goal → PlanningAgent 返回 needs_input。
    resp = mgr.dispatch("planning", "", {})
    assert resp.status == Status.NEEDS_INPUT
    agg = mgr.aggregate([resp])
    assert agg["status"] == Status.NEEDS_INPUT.value
    assert agg["next_actions"] and "补充" in agg["next_actions"][0]


def test_error_takes_priority_over_needs_input(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    err = ResponsePayload(status=Status.ERROR, confidence=0.0, result={})
    ni = ResponsePayload(status=Status.NEEDS_INPUT, confidence=0.3,
                         result={"diff": {"rationale": "缺目标"}})
    agg = mgr.aggregate([err, ni])
    assert agg["status"] == Status.PARTIAL.value


# ---------------- P0-3: escalate → handoff 注入 ----------------
def test_escalate_injects_handoff_summary(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    mgr.mock.run = lambda payload: MockOutput(
        status="escalate", escalate_action="change_plan", turn_index=3,
        turn_scores=[Score(), Score()],
    )
    # ReAct 轨迹：输入路由到 mock（关键词 "模拟面试"），mock escalate → Manager 接力生成 handoff。
    responses, meta, plan = mgr.execute_dynamic("模拟面试练习", trace_id="t-esc")
    assert responses[0].status == Status.ESCALATE
    assert "handoff_summary" in meta and meta["handoff_summary"]
    assert "change_plan" in meta["handoff_summary"]

    agg = mgr.aggregate(responses, plan, meta)
    assert agg["status"] == Status.ESCALATE.value
    assert any("交接" in a for a in agg["next_actions"])


# ---------------- P0-4: diagnosis_reports 落库 ----------------
def test_diagnosis_report_persisted_on_dispatch(seeded_db):
    mgr = ManagerAgent(db_path=seeded_db)
    mgr.handle("诊断我的弱点", trace_id="t-diag")
    reports = DiagnosisReportRepository(db_path=seeded_db).latest(5)
    assert reports, "诊断后应落一条 diagnosis_reports 快照"
    r = reports[0]
    assert r["trigger"] == "user"
    assert r["trace_id"] == "t-diag"
    assert "weak_atoms" in r["result"]


def test_settle_mock_persists_post_mock_report(seeded_db):
    mgr = ManagerAgent(db_path=seeded_db)
    out = mgr.settle_mock("nonexistent-session")
    assert out["diagnosis"] is not None  # 无会话也不影响只读诊断
    reports = DiagnosisReportRepository(db_path=seeded_db).latest(5)
    assert any(r["trigger"] == "post_mock" for r in reports)
