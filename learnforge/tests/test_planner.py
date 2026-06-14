"""复合任务 plan-as-tool-calls：build_plan（LLM create + 确定性兜底）+ plan_execute（mark_step）。"""

from __future__ import annotations

from learnforge.orchestration.manager import ManagerAgent
from learnforge.orchestration.planner import _PlanDraft, build_plan


class _FakeLLM:
    """可控的假 LLM：available + complete_structured 返回固定 _PlanDraft。"""

    def __init__(self, steps, available=True):
        self.available = available
        self._steps = steps

    def complete_structured(self, *a, **k):
        return _PlanDraft(steps=self._steps), None


# ----------------------------------------------------------------- build_plan
def test_composite_is_deterministic_diagnosis_then_planning():
    plan = build_plan("快面试了帮我准备", wants_plan=True, llm=_FakeLLM(["mock"]))
    assert [s.agent for s in plan.steps] == ["diagnosis", "planning"]  # 已知复合不被 LLM 改写


def test_llm_create_for_non_composite():
    plan = build_plan("先看我哪弱再排计划再考我", wants_plan=False,
                      llm=_FakeLLM(["diagnosis", "planning", "mock"]))
    assert [s.agent for s in plan.steps] == ["diagnosis", "planning", "mock"]


def test_llm_create_whitelists_and_dedupes():
    plan = build_plan("xxx", wants_plan=False,
                      llm=_FakeLLM(["diagnosis", "garbage", "diagnosis", "planning"]))
    assert [s.agent for s in plan.steps] == ["diagnosis", "planning"]


def test_fallback_single_step_without_llm():
    plan = build_plan("诊断我的弱点", wants_plan=False, llm=_FakeLLM([], available=False))
    assert [s.agent for s in plan.steps] == ["diagnosis"]   # 关键词兜底单步


def test_empty_llm_falls_back_to_keyword():
    plan = build_plan("什么是 RAG", wants_plan=False, llm=_FakeLLM([]))
    assert [s.agent for s in plan.steps] == ["qa"]


# ----------------------------------------------------------------- plan_execute（mark_step）
def test_plan_execute_marks_steps_completed(seeded_db):
    mgr = ManagerAgent(db_path=seeded_db)
    responses, meta, executed = mgr.plan_execute("快面试了帮我准备", trace_id="pe-1")
    assert meta["planned"] and meta["composite"]
    # 显式计划带回，且执行过的步骤被标记完成。
    agents = [s["agent"] for s in meta["plan_steps"]]
    assert agents == ["diagnosis", "planning"]
    assert all(s["status"] == "completed" for s in meta["plan_steps"])
    assert [e["agent"] for e in executed] == ["diagnosis", "planning"]


def test_plan_execute_skips_modify_on_empty_diagnosis(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    responses, meta, executed = mgr.plan_execute("快面试了帮我准备", trace_id="pe-2")
    assert meta["skipped_modify"] and meta["suggest_mock"]   # §5.6 保留
    assert any(r.result.get("skipped") for r in responses)
