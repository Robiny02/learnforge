"""Phase 2 测试：ManagerAgent 作为结构化任务分发器（单任务闭环）。

覆盖任务要求：
- qa / planning / diagnosis / mock 任务能被正确创建并分发；
- agent 返回 next_actions 后 Manager 能正确接收；
- agent 报错或置信度低时 Manager 给出合理 fallback。
全离线、确定性，不依赖 ANTHROPIC_API_KEY / DB。
"""

import pytest

from learnforge.orchestrator.manager import ManagerAgent
from learnforge.orchestrator.tasks import AgentResult, Task, TaskStatus


# ---------------- 任务创建：结构化且字段完整 ----------------
def test_create_task_has_all_required_fields():
    mgr = ManagerAgent()
    task = mgr.create_task("乐观锁还是悲观锁?")
    d = task.to_dict()
    for field in (
        "task_id", "target_agent", "user_goal", "task_type",
        "priority", "status", "input_context",
    ):
        assert field in d
    assert d["target_agent"] == "qa"
    assert d["user_goal"] == "乐观锁还是悲观锁?"
    assert d["status"] == TaskStatus.PENDING.value


@pytest.mark.parametrize(
    "user_input,expected_agent",
    [
        ("乐观锁还是悲观锁?", "qa"),
        ("帮我制定学习计划", "planning"),
        ("帮我诊断薄弱点", "diagnosis"),
        ("快面试了，帮我模拟面试", "mock"),
    ],
)
def test_each_agent_task_created_and_dispatched(user_input, expected_agent):
    mgr = ManagerAgent()
    task = mgr.create_task(user_input)
    assert task.target_agent == expected_agent
    result = mgr.dispatch(task)
    assert isinstance(result, AgentResult)
    assert result.ok
    assert mgr.board.get(task.task_id).status == TaskStatus.COMPLETED


# ---------------- agent 返回 next_actions 能被 Manager 接收 ----------------
def test_manager_receives_next_actions():
    mgr = ManagerAgent()
    out = mgr.handle("帮我诊断薄弱点")
    assert out["status"] == "ok"
    assert out["next_actions"]  # diagnosis 执行器返回了 next_actions
    assert any("学习计划" in a for a in out["next_actions"])


def test_handle_output_is_structured():
    mgr = ManagerAgent()
    out = mgr.handle("乐观锁还是悲观锁?")
    for key in ("reply_text", "status", "task", "result", "next_actions", "need_followup"):
        assert key in out
    assert out["result"]["confidence"] >= 0.4


def test_mock_sets_need_followup():
    mgr = ManagerAgent()
    out = mgr.handle("快面试了，帮我模拟面试")
    assert out["need_followup"] is True


# ---------------- fallback：执行器报错 ----------------
def test_fallback_on_executor_error():
    def boom(task: Task) -> AgentResult:
        raise RuntimeError("worker 崩了")

    mgr = ManagerAgent(executors={"qa": boom})
    out = mgr.handle("乐观锁还是悲观锁?")
    assert out["status"] == "failed"
    assert out["result"]["error"] == "executor_exception"
    assert "无法完成" in out["reply_text"]
    # 任务板状态应流转为 FAILED，且不崩溃。
    task_id = out["task"]["task_id"]
    assert mgr.board.get(task_id).status == TaskStatus.FAILED


def test_fallback_on_unknown_agent_executor():
    # 没有为路由到的 agent 注册执行器 → error 结果 + fallback，不抛异常。
    mgr = ManagerAgent(executors={})
    out = mgr.handle("帮我制定学习计划")
    assert out["status"] == "failed"
    assert out["result"]["error"] == "unknown_agent"


# ---------------- fallback：低置信 ----------------
def test_fallback_on_low_confidence():
    def unsure(task: Task) -> AgentResult:
        return AgentResult(result={"answer": "也许吧"}, confidence=0.2, reason="证据不足")

    mgr = ManagerAgent(executors={"qa": unsure})
    out = mgr.handle("乐观锁还是悲观锁?")
    assert out["status"] == "low_confidence"
    assert "不太有把握" in out["reply_text"]


# ---------------- 向后兼容：Phase 1 的 decide 仍可用 ----------------
def test_phase1_decide_still_works():
    mgr = ManagerAgent()
    assert mgr.decide("帮我制定学习计划").agent == "planning"
