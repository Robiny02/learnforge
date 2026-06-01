"""Hybrid 路由回归测试（Agent-level hybrid router）。

逐条钉住升级后的能力，确保行为不漂移。全离线、确定性，不依赖 ANTHROPIC_API_KEY。
注意：路由器只决定 target_agent + 轻量增强信息，绝不替子 Agent 决定内部执行步骤。
"""

import pytest

from learnforge.orchestrator.router import QARouter, RouteResult


@pytest.fixture
def router():
    return QARouter()


# 0) 向后兼容 ----------------------------------------------------------------

def test_route_result_keeps_agent_and_reason(router):
    r = router.route("帮我模拟面试")
    assert isinstance(r, RouteResult)
    assert r.agent == "mock"
    assert isinstance(r.reason, str) and r.reason


def test_legacy_single_arg_call_still_works(router):
    assert router.route("帮我诊断薄弱点").agent == "diagnosis"
    assert router.route("帮我制定学习计划").agent == "planning"
    assert router.route("什么是闭包？").agent == "qa"


def test_empty_input_defaults_to_qa(router):
    assert router.route("   ").agent == "qa"


# 1) 显式命令优先 -----------------------------------------------------------

def test_explicit_command_wins(router):
    r = router.route("/mock 帮我顺便诊断一下弱点")
    assert r.agent == "mock"
    assert r.confidence == 1.0
    assert "explicit" in r.signals


def test_explicit_command_extracts_slots(router):
    r = router.route("/plan python in 3 weeks")
    assert r.agent == "planning"
    assert r.slots.get("topic") == "python"
    assert r.slots.get("deadline") == "3weeks"


# 2) 多轮状态感知 -----------------------------------------------------------

def test_in_progress_mock_continues(router):
    r = router.route("这道题我选 B", {"active_mock": True})
    assert r.agent == "mock"
    assert "multi_turn" in r.signals


def test_mock_does_not_hijack_clear_switch(router):
    r = router.route("先别面试了，帮我制定学习计划", {"active_mock": True})
    assert r.agent == "planning"


# 3) 否定意图识别 -----------------------------------------------------------

def test_negation_cancels_intent(router):
    r = router.route("别给我做模拟面试，解释一下闭包")
    assert r.agent == "qa"
    assert "negation" in r.signals


def test_negation_lets_active_mock_be_abandoned(router):
    r = router.route("stop the mock", {"active_mock": True})
    assert r.agent != "mock"


# 4) 多意图识别 + handoff ---------------------------------------------------

def test_multi_intent_primary_and_handoff(router):
    r = router.route("先诊断我的薄弱点，再制定学习计划")
    assert r.agent == "diagnosis"
    assert r.handoffs == ("planning",)
    assert "multi_intent" in r.signals


def test_multi_intent_primary_follows_priority(router):
    # mock > diagnosis > planning：与旧路由一致，handoff 收其余意图。
    r = router.route("做个模拟面试，然后给我学习计划")
    assert r.agent == "mock"
    assert "planning" in r.handoffs


# 5) retrieval policy -------------------------------------------------------

def test_retrieval_on_for_qa(router):
    assert router.route("什么是闭包？").use_retrieval is True


def test_retrieval_off_for_mock(router):
    assert router.route("帮我模拟面试").use_retrieval is False


def test_retrieval_for_planning_depends_on_topic(router):
    assert router.route("帮我制定 python 学习计划").use_retrieval is True
    assert router.route("帮我制定学习计划").use_retrieval is False


# 6) 可选一次性 LLM 增强 ----------------------------------------------------

def test_llm_enhancer_called_only_when_unsure(router):
    calls = []

    def enhancer(text):
        calls.append(text)
        return {"agent": "planning", "topic": "rust"}

    r = router.route("帮帮我嘛", llm=enhancer)  # 无关键词 → 低置信 qa → 触发增强
    assert len(calls) == 1
    assert r.agent == "planning"
    assert r.slots.get("topic") == "rust"
    assert "llm" in r.signals


def test_llm_enhancer_skipped_when_confident(router):
    calls = []

    def enhancer(text):
        calls.append(text)
        return {"agent": "qa"}

    r = router.route("/diagnose", llm=enhancer)
    assert calls == []  # 显式命令已确定，不调用 LLM
    assert r.agent == "diagnosis"


def test_llm_enhancer_failure_is_safe(router):
    def enhancer(text):
        raise RuntimeError("model down")

    r = router.route("帮帮我嘛", llm=enhancer)
    assert r.agent == "qa"  # 异常时保持确定性兜底结论


# 7) 确定性 ------------------------------------------------------------------

def test_deterministic(router):
    a = router.route("先诊断我的薄弱点，再制定学习计划")
    b = router.route("先诊断我的薄弱点，再制定学习计划")
    assert a.agent == b.agent
    assert a.handoffs == b.handoffs
