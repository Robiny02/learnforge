"""Phase 3 测试：串行协作 orchestrator（仅串行、next_actions 驱动补派发）。

覆盖任务要求与通过标准：
- diagnosis → planning 链路；
- mock → diagnosis → planning 链路；
- 某 agent 失败时降级；
- next_actions（followups）真实驱动后续任务，而非只停留在文本；
- 无重复 / 无意义派发（去重、空诊断不派发）；
- 简单 QA 不退化；
- trace 能解释每一步任务流。
全离线、确定性。
"""

from learnforge.orchestrator.manager import MAX_TASKS, ManagerAgent
from learnforge.orchestrator.tasks import AgentResult, FollowupHint, Task


def _flow(out):
    return [(s["target_agent"], s["task_type"]) for s in out["trace"]]


# ---------------- 链路一：diagnosis → planning ----------------
def test_diagnosis_then_planning_chain():
    out = ManagerAgent().orchestrate("帮我诊断 Redis 薄弱点并给学习计划")
    assert _flow(out) == [("diagnosis", "diagnosis"), ("planning", "planning.modify")]
    assert out["status"] == "ok"
    # planning 真的消费了 diagnosis 结果（diagnosis-driven modify）。
    planning_result = out["results"][1]["result"]
    assert planning_result["based_on_weak_atoms"]  # 非空 → 用到了上游薄弱点
    assert "薄弱点" in out["reply_text"]


# ---------------- 链路二：mock → diagnosis → planning ----------------
def test_mock_then_diagnosis_then_planning_chain():
    out = ManagerAgent().orchestrate("我要准备 mock 面试，并根据结果生成复习计划")
    assert _flow(out) == [
        ("mock", "mock"),
        ("diagnosis", "diagnosis"),
        ("planning", "planning.modify"),
    ]
    assert out["status"] == "ok"
    # 后两步都由上游派生（spawned_by 非空），证明 next_actions 驱动而非预先硬编码。
    assert out["trace"][1]["spawned_by"] is not None
    assert out["trace"][2]["spawned_by"] is not None


# ---------------- next_actions 真实驱动后续任务 ----------------
def test_followups_drive_dispatch_not_just_text():
    out = ManagerAgent().orchestrate("帮我诊断薄弱点")
    # 仅 seed=diagnosis，但 followup 把 planning 真正跑了起来（2 步而非 1 步）。
    assert len(out["trace"]) == 2
    assert out["trace"][1]["target_agent"] == "planning"


# ---------------- 失败降级 ----------------
def test_failure_is_degraded_not_crash():
    def boom(task: Task) -> AgentResult:
        raise RuntimeError("diagnosis worker 崩了")

    from learnforge.orchestrator.executors import DEFAULT_EXECUTORS

    execs = dict(DEFAULT_EXECUTORS)
    execs["diagnosis"] = boom
    out = ManagerAgent(executors=execs).orchestrate("帮我诊断 Redis 薄弱点并给学习计划")
    # diagnosis 失败 → 不派生 planning，整体降级，但不抛异常。
    assert _flow(out) == [("diagnosis", "diagnosis")]
    assert out["status"] == "failed"
    assert out["trace"][0]["status"] == "failed"


def test_partial_when_downstream_low_confidence():
    # planning 低置信 → 整体 partial，但 diagnosis 步仍完成。
    def weak_planning(task: Task) -> AgentResult:
        return AgentResult(result={"plan": "勉强"}, confidence=0.1, reason="证据不足")

    from learnforge.orchestrator.executors import DEFAULT_EXECUTORS

    execs = dict(DEFAULT_EXECUTORS)
    execs["planning"] = weak_planning
    out = ManagerAgent(executors=execs).orchestrate("帮我诊断 Redis 薄弱点并给学习计划")
    assert _flow(out) == [("diagnosis", "diagnosis"), ("planning", "planning.modify")]
    assert out["status"] == "partial"


# ---------------- 无重复 / 无意义派发 ----------------
def test_no_duplicate_dispatch_on_cycle():
    # 构造 diagnosis↔planning 互相 followup 的环，去重应让每个最多跑一次。
    def diag(task: Task) -> AgentResult:
        return AgentResult(
            result={"weak_atoms": ["x"]}, confidence=0.6,
            followups=[FollowupHint("planning", "planning.modify", "调整")],
        )

    def plan(task: Task) -> AgentResult:
        return AgentResult(
            result={"plan": "p"}, confidence=0.6,
            followups=[FollowupHint("diagnosis", "diagnosis", "再诊断")],
        )

    out = ManagerAgent(executors={"diagnosis": diag, "planning": plan}).orchestrate(
        "帮我诊断薄弱点"
    )
    keys = _flow(out)
    assert len(keys) == len(set(keys))  # 无重复
    assert len(keys) <= MAX_TASKS


def test_empty_diagnosis_does_not_dispatch_planning():
    # 诊断为空（无 weak_atoms）→ 不派发 planning（无意义派发防护）。
    def empty_diag(task: Task) -> AgentResult:
        return AgentResult(result={"weak_atoms": [], "clusters": []}, confidence=0.6)

    from learnforge.orchestrator.executors import DEFAULT_EXECUTORS

    execs = dict(DEFAULT_EXECUTORS)
    execs["diagnosis"] = empty_diag
    out = ManagerAgent(executors=execs).orchestrate("帮我诊断 Redis 薄弱点并给学习计划")
    assert _flow(out) == [("diagnosis", "diagnosis")]


# ---------------- 简单 QA 不退化 ----------------
def test_simple_qa_no_regression():
    out = ManagerAgent().orchestrate("乐观锁还是悲观锁?")
    assert _flow(out) == [("qa", "qa")]
    assert out["status"] == "ok"
    assert "qa-stub" in out["reply_text"]


# ---------------- trace 可解释每一步 ----------------
def test_trace_explains_every_step():
    out = ManagerAgent().orchestrate("我要准备 mock 面试，并根据结果生成复习计划")
    assert len(out["trace"]) == len(out["tasks"])
    for i, entry in enumerate(out["trace"], start=1):
        assert entry["step"] == i
        for key in ("task_id", "target_agent", "task_type", "status", "confidence", "reason"):
            assert key in entry


# ---------------- 不超过任务上限 ----------------
def test_respects_max_tasks_cap():
    out = ManagerAgent().orchestrate("我要准备 mock 面试，并根据结果生成复习计划")
    assert len(out["trace"]) <= MAX_TASKS
