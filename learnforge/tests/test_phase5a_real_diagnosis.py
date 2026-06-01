"""Phase 5a 测试：接入真实 DiagnosisWorker（其余仍 stub，orchestrator 不大改）。

验证：
- 真实 diagnosis 在有信号的库上返回结构化 weak_points + confidence；
- 真实 diagnosis→stub planning 链路打通，planning 消费的是真实薄弱点；
- 空库 → honest fallback（低置信、不派发 planning、不崩溃）；
- 真实诊断只读，不改任何 state。
依赖 conftest 的 tmp_db / seeded_db 离线 fixture。
"""

from learnforge.orchestrator.executors import (
    executors_with_real_diagnosis,
    real_diagnosis_executor,
)
from learnforge.orchestrator.manager import ManagerAgent
from learnforge.orchestrator.tasks import Task


def _flow(out):
    return [(s["target_agent"], s["task_type"]) for s in out["trace"]]


# ---------------- 真实 diagnosis：结构化输出 ----------------
def test_real_diagnosis_returns_structured_weak_points(seeded_db):
    execu = real_diagnosis_executor(db_path=seeded_db)
    res = execu(Task(target_agent="diagnosis", user_goal="诊断", task_type="diagnosis"))
    assert res.ok
    assert res.confidence >= 0.4  # seeded_db 有 5 条弱点事件
    assert res.result["weak_points"]  # 结构化弱点非空
    wp = res.result["weak_points"][0]
    for key in ("atom_id", "topic", "mastery", "evidence_count"):
        assert key in wp
    # weak_atoms 仍是 atom_id 字符串列表，下游 planning 可直接消费。
    assert all(isinstance(a, str) for a in res.result["weak_atoms"])
    assert set(res.result["weak_atoms"]) >= {"a0", "a1"}


def test_real_diagnosis_emits_planning_followup_when_weak(seeded_db):
    execu = real_diagnosis_executor(db_path=seeded_db)
    res = execu(Task(target_agent="diagnosis", user_goal="诊断", task_type="diagnosis"))
    assert any(f.target_agent == "planning" and f.task_type == "planning.modify"
               for f in res.followups)


# ---------------- 真实 diagnosis → stub planning 链路 ----------------
def test_real_diagnosis_drives_stub_planning(seeded_db):
    mgr = ManagerAgent(executors=executors_with_real_diagnosis(db_path=seeded_db))
    out = mgr.orchestrate("帮我诊断薄弱点并给学习计划")
    assert _flow(out) == [("diagnosis", "diagnosis"), ("planning", "planning.modify")]
    assert out["status"] == "ok"
    # planning 消费的是真实诊断的 atom（而非 stub 硬编码的 redis-*）。
    based_on = out["results"][1]["result"]["based_on_weak_atoms"]
    assert set(based_on) >= {"a0", "a1"}
    assert "redis-持久化" not in based_on


# ---------------- 空库：honest fallback ----------------
def test_empty_db_honest_fallback_no_planning(tmp_db):
    mgr = ManagerAgent(executors=executors_with_real_diagnosis(db_path=tmp_db))
    out = mgr.orchestrate("帮我诊断薄弱点并给学习计划")
    # 无信号 → 不派发 planning（无意义派发防护），低置信，不崩溃。
    assert _flow(out) == [("diagnosis", "diagnosis")]
    assert out["results"][0]["confidence"] < 0.5
    assert out["status"] == "partial"


# ---------------- 只读：诊断不改 state ----------------
def test_real_diagnosis_is_read_only(seeded_db):
    # 跑前后 atom 掌握度不变（DiagnosisAgent 严格只读）。
    from learnforge.storage.repositories import AtomRepository

    repo = AtomRepository(db_path=seeded_db)
    before = repo.get("a0").mastery_score
    real_diagnosis_executor(db_path=seeded_db)(
        Task(target_agent="diagnosis", user_goal="诊断", task_type="diagnosis")
    )
    assert repo.get("a0").mastery_score == before
