"""Trajectory 评测（T-EV-10 / Design §9b）——端到端编排的拓扑 + 数据回流 + 写边界。

与 diagnosis_eval（指标型）不同，本模块测的是**系统级不变量**（布尔断言，非可调指标）：
对真实 ManagerAgent 回放场景，断言 Design §9b 三条：
  (1) 消息链拓扑正确    —— Manager.PLAN 产出的 DAG 与场景预期一致。
  (2) 闭环数据回流      —— QA→event→Diagnosis 闭环；路径/掌握度写后可读回。
  (3) 无越权写          —— 只 Manager 写 learning_paths / knowledge_atoms.mastery；
                          Diagnosis 全程只读；复合任务部分失败不半写。

全程离线（无 ANTHROPIC_API_KEY）：Manager 走 _keyword_plan 与确定性兜底，断言可复现。

用法：
    python -m learnforge.eval.trajectory_eval
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from ..agents.diagnosis import DiagnosisAgent
from ..agents.manager import ManagerAgent
from ..contracts.agents.diagnosis import DiagnosisInput
from ..contracts.agents.planning import PathDiff, PathItem
from ..contracts.atom import KnowledgeAtom
from ..contracts.enums import EventType, TimeWindow
from ..contracts.message import EventPayload
from ..mastery import update_mastery
from ..storage.db import get_connection, init_db
from ..storage.repositories import (
    AtomRepository,
    EventRepository,
    LearningPathRepository,
    TraceRepository,
)

# 写边界表（Design §2a）：只有 Manager 可改这些。
_WRITE_TABLES = ["learning_paths", "path_items"]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class TrajectoryReport:
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)


# ----------------------------------------------------------------- db helpers
def _fresh_db() -> str:
    path = os.path.join(tempfile.mkdtemp(), "lf_traj.db")
    init_db(path)
    return path


def _seeded_db() -> str:
    """小型种子库：concurrency/database atom + 弱点事件（供诊断/写路径用）。"""
    path = _fresh_db()
    now = datetime.now(timezone.utc)
    ar = AtomRepository(db_path=path)
    for aid, topic in [("a0", "concurrency"), ("a1", "concurrency"), ("a2", "database")]:
        ar.upsert(KnowledgeAtom(atom_id=aid, topic=topic, title=topic,
                                canonical_question="?", mastery_score=0.2,
                                created_at=now, updated_at=now))
    er = EventRepository(db_path=path)
    for i in range(4):
        er.append(EventPayload(event_type=EventType.MOCK_WEAKNESS, topic="concurrency",
                               atom_refs=["a0", "a1"], signal={"weak": True}), event_id=f"e{i}")
    return path


def _mastery_snapshot(db_path: str) -> dict:
    """掌握度写边界快照：atom_id → (score, tier, review_count)。"""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT atom_id, mastery_score, mastery_tier, review_count FROM knowledge_atoms"
    ).fetchall()
    conn.close()
    return {r["atom_id"]: (r["mastery_score"], r["mastery_tier"], r["review_count"]) for r in rows}


def _table_snapshot(db_path: str, tables: List[str]) -> dict:
    conn = get_connection(db_path)
    snap = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()] for t in tables}
    conn.close()
    return snap


# ----------------------------------------------------------------- (1) 拓扑
def check_topology() -> CheckResult:
    """Manager.PLAN 的 DAG 与场景预期一致（Design §2c/§5.6）。"""
    cases: List[Tuple[str, List[str]]] = [
        ("快面试了帮我准备一下", ["diagnosis", "planning"]),  # 复合 §5.6
        ("乐观锁还是悲观锁？", ["qa"]),
        ("诊断我的弱点", ["diagnosis"]),
        ("帮我制定一份学习计划", ["planning"]),
    ]
    mgr = ManagerAgent(db_path=_fresh_db())
    bad: List[str] = []
    for user_input, expected in cases:
        got = [t["agent"] for t in mgr.make_plan(user_input)]
        if got != expected:
            bad.append(f"{user_input!r}: 期望 {expected} 实得 {got}")
    return CheckResult("topology_dag", not bad,
                       "；".join(bad) or f"{len(cases)} 个场景 DAG 全部匹配")


# --------------------------------------------------- (3) 诊断只读（无越权写）
def check_diagnosis_read_only_in_flow() -> CheckResult:
    """经 Manager 跑诊断，写边界表 + 掌握度全程不变（Design §9b-3）。"""
    db = _seeded_db()
    before_w = _table_snapshot(db, _WRITE_TABLES)
    before_m = _mastery_snapshot(db)
    ManagerAgent(db_path=db).handle("诊断我的弱点", trace_id="traj-diag")
    ok = (_table_snapshot(db, _WRITE_TABLES) == before_w
          and _mastery_snapshot(db) == before_m)
    return CheckResult("diagnosis_read_only", ok,
                       "诊断流程未改写 path/mastery" if ok else "诊断流程发生越权写！")


# ------------------------------------------ (2) 闭环：QA → event → Diagnosis
def check_qa_event_reflow() -> CheckResult:
    """QA 返回后 emit qa_signal，且诊断能在同库读到该信号（闭环回流，Design §4c/§5.5）。"""
    db = _fresh_db()
    ManagerAgent(db_path=db).handle("什么是乐观锁？", trace_id="traj-qa")
    events = EventRepository(db_path=db).list_window_dicts("all")
    qa_events = [e for e in events if e["event_type"] == EventType.QA_SIGNAL.value]
    if not qa_events:
        return CheckResult("qa_event_reflow", False, "QA 未 emit qa_signal 事件")
    # 闭环验证：把该事件喂给诊断，若被判为弱点信号则其 topic 应出现在簇中。
    ev = qa_events[0]
    weak = bool(ev["signal"].get("weak") or ev["signal"].get("correct") is False
                or ev["signal"].get("verdict") in ("unverified", "uncertain"))
    out = DiagnosisAgent(db_path=db).run(DiagnosisInput(time_window=TimeWindow.ALL))
    topics = {c.topic for c in out.clusters}
    closed = (ev["topic"] in topics) if weak else True
    detail = f"qa_signal(topic={ev['topic']}, weak={weak}) → 诊断簇={sorted(topics)}"
    return CheckResult("qa_event_reflow", closed, detail)


# ---------------------------------------- (2)+(3) 路径写回流 + atom 完整性
def check_path_write_reflow() -> CheckResult:
    """Manager 落 PathDiff：合法 item 写入、引用不存在 atom 的 item 被过滤、emit PATH_CHANGED。"""
    db = _seeded_db()
    mgr = ManagerAgent(db_path=db)
    diff = PathDiff(
        add=[
            PathItem(item_id="it1", atom_id="a0", order_idx=0, day_index=1),
            PathItem(item_id="it_ghost", atom_id="does_not_exist", order_idx=1, day_index=1),
        ],
        rationale="trajectory write reflow",
    )
    path_id = mgr.apply_path_diff(diff, user_id="default", trace_id="traj-path")
    if path_id is None:
        return CheckResult("path_write_reflow", False, "apply_path_diff 未落库")

    persisted = LearningPathRepository(db_path=db).get(path_id)
    item_ids = {it.item_id for it in (persisted.items if persisted else [])}
    events = EventRepository(db_path=db).list_window_dicts("all")
    path_changed = [e for e in events if e["event_type"] == EventType.PATH_CHANGED.value]

    ok = (item_ids == {"it1"}  # ghost 被外键完整性过滤
          and len(path_changed) == 1)
    detail = f"持久化 items={sorted(item_ids)}（ghost 应被过滤）, PATH_CHANGED={len(path_changed)}"
    return CheckResult("path_write_reflow", ok, detail)


# ---------------------------------------- (2)+(3) 掌握度写回流（唯一写者）
def check_mastery_write_reflow() -> CheckResult:
    """Manager.commit_mastery 按 EMA 更新、review_count+1、tier 重算（Design §4b/§4d）。"""
    db = _seeded_db()
    repo = AtomRepository(db_path=db)
    old = repo.get("a0").mastery_score
    n = ManagerAgent(db_path=db).commit_mastery(
        [{"atom_id": "a0", "signal": 0.9, "source": "mock"}]
    )
    after = repo.get("a0")
    expected = update_mastery(old, 0.9, "mock")
    ok = (n == 1 and abs(after.mastery_score - expected) < 1e-9 and after.review_count >= 1)
    return CheckResult("mastery_write_reflow", ok,
                       f"{old:.3f} → {after.mastery_score:.3f}（期望 {expected:.3f}），"
                       f"review_count={after.review_count}")


# ------------------------------------------ (3) 复合部分失败不半写（§8b）
def check_composite_no_half_write_on_skip() -> CheckResult:
    """空诊断 → 跳过 modify：复合任务结束时不应有任何 path 落库（Design §5.6 异常①/§8b）。"""
    db = _fresh_db()  # 空库 → 诊断为空 → 跳过 modify
    agg = ManagerAgent(db_path=db).handle("快面试了帮我准备一下", trace_id="traj-skip")
    paths = _table_snapshot(db, _WRITE_TABLES)
    no_write = all(len(rows) == 0 for rows in paths.values())
    topo_ok = [t["agent"] for t in agg["plan"]] == ["diagnosis", "planning"]
    return CheckResult("composite_no_half_write", no_write and topo_ok,
                       f"plan={[t['agent'] for t in agg['plan']]}, path 表均空={no_write}")


# ------------------------------------------ (1)+ 责任归属：trace 全链路可拉
def check_trace_completeness() -> CheckResult:
    """复合任务后，可按 trace_id 拉到各派发 worker 的 agent_traces（Design §9a）。"""
    db = _seeded_db()
    ManagerAgent(db_path=db).handle("快面试了帮我准备一下", trace_id="traj-trace")
    traces = TraceRepository(db_path=db).list_by_trace("traj-trace")
    recipients = {t["recipient"] for t in traces}
    ok = {"diagnosis", "planning"}.issubset(recipients)
    return CheckResult("trace_completeness", ok,
                       f"trace 覆盖 recipients={sorted(recipients)}")


_CHECKS: List[Callable[[], CheckResult]] = [
    check_topology,
    check_diagnosis_read_only_in_flow,
    check_qa_event_reflow,
    check_path_write_reflow,
    check_mastery_write_reflow,
    check_composite_no_half_write_on_skip,
    check_trace_completeness,
]


def run_eval() -> TrajectoryReport:
    return TrajectoryReport(checks=[c() for c in _CHECKS])


def format_report(report: TrajectoryReport) -> str:
    lines = [f"{'check':<28} {'result':<6} detail", "-" * 92]
    for c in report.checks:
        lines.append(f"{c.name:<28} {'PASS' if c.passed else 'FAIL':<6} {c.detail}")
    lines.append("-" * 92)
    lines.append(f"trajectory checks: {report.passed}/{len(report.checks)} passed -> "
                 f"{'ALL PASS' if report.all_passed else 'FAILURES PRESENT'}")
    return "\n".join(lines)


def _main(argv: Optional[List[str]] = None) -> None:
    print(format_report(run_eval()))


if __name__ == "__main__":
    _main()
