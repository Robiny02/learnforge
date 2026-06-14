"""面试内动作注册表 + List[Step] 执行器（解耦铺路）回归。

护住三条解耦约定：
  ① 注册表是单一来源——每个可被分类出来的动作都在表里、channel 合法；
  ② 控制动作集合与子图、mock_turn 一致（不再各自硬编码）；
  ③ run_plan 单步 = 历史直连行为（零行为变化），且串行执行器能跑多步计划。
"""

from __future__ import annotations

import pytest

from learnforge.agents.mock import MockInterviewAgent
from learnforge.agents.mock import actions as ACT
from learnforge.contracts.agents.mock import MockInput
from learnforge.intent.mock_turn import classify_mock_input


def _agent():
    return MockInterviewAgent()


# ----------------------------------------------------------------- ① 单一来源 / 合法性
_VALID_CHANNELS = {
    ACT.CHANNEL_ANSWER, ACT.CHANNEL_INTERRUPT, ACT.CHANNEL_CONTROL,
    ACT.CHANNEL_SIDE, ACT.CHANNEL_ESCALATE,
}


def test_every_classified_action_is_registered():
    # classify_mock_input 可能产出的全部动作都应在注册表里（否则分发会落兜底）。
    produced = {
        classify_mock_input("Q", m) for m in [
            "我正常作答含指标对比", "顺便问什么是RDB", "结束面试吧", "暂停一下",
            "换个话题", "太难了简单点", "跳过这题", "给点提示", "这题什么意思",
            "直接告诉我答案", "重答上一题", "我刚才答得怎么样", "顺便帮我诊断弱点",
        ]
    }
    for action in produced:
        assert action in ACT.REGISTRY, f"{action} 未注册"
        assert ACT.REGISTRY[action].channel in _VALID_CHANNELS


def test_control_action_names_match_mock_turn():
    # 单一来源：动作注册表的控制动作名 == mock_turn 的控制动作集合（旧子图已被 InterviewDirector 取代）。
    from learnforge.intent.mock_turn import CONTROL_ACTIONS

    assert set(ACT.CONTROL_ACTION_NAMES) == set(CONTROL_ACTIONS)


def test_control_specs_have_valid_goto():
    for name in ACT.CONTROL_ACTION_NAMES:
        spec = ACT.control_spec(name)
        assert spec is not None
        assert spec.goto in ("await", "next", "redo")


def test_channel_of_defaults_to_answer_for_unknown():
    assert ACT.channel_of("totally_unknown_action") == ACT.CHANNEL_ANSWER


# ----------------------------------------------------------------- ② 控制行为不变
@pytest.mark.parametrize("action", ["hint", "repeat", "reveal", "feedback"])
def test_registry_control_does_not_consume_turn(action):
    m = _agent()
    sid = f"t-reg-{action}"
    m.run(MockInput(topic="redis", session_id=sid))
    after = m.answer(sid, user_answer="我用 AOF 加 RDB 并监控指标")
    q, t = after.question, after.turn_index
    out = ACT.run_step(m, sid, ACT.Step(action, ""))
    assert out.turn_index == t and out.question == q and out.followup


def test_registry_skip_changes_question():
    m = _agent()
    sid = "t-reg-skip"
    first = m.run(MockInput(topic="redis", session_id=sid))
    out = ACT.run_step(m, sid, ACT.Step("skip", ""))
    assert out.turn_index == first.turn_index and out.followup


# ----------------------------------------------------------------- ③ 执行器 = 历史直连
def test_run_step_answer_equals_direct():
    # run_step("answer") 应与直接 user_answer 等价（评分推进一轮）。
    m = _agent()
    sid = "t-reg-ans"
    m.run(MockInput(topic="redis", session_id=sid))
    out = ACT.run_step(m, sid, ACT.Step("answer", "我用 AOF everysec 加 RDB 并监控恢复时间"))
    assert out.status == "active" and out.turn_index == 1 and len(out.turn_scores) == 1


def test_run_step_interrupt_pause_pauses():
    m = _agent()
    sid = "t-reg-pause"
    m.run(MockInput(topic="redis", session_id=sid))
    out = ACT.run_step(m, sid, ACT.Step("pause", ""))
    assert out.status == "paused"


def test_plan_from_message_is_single_step():
    plan = ACT.plan_from_message("讲讲 Redis 持久化", "给点提示")
    assert len(plan) == 1 and plan[0].action == "hint"


def test_run_plan_executes_steps_sequentially():
    # 串行多步：先作答推进一轮，再 skip 换题——run_plan 返回最后一步输出。
    m = _agent()
    sid = "t-reg-plan"
    m.run(MockInput(topic="redis", session_id=sid))
    out = ACT.run_plan(m, sid, [
        ACT.Step("answer", "我用 AOF 加 RDB 并监控指标"),
        ACT.Step("skip", ""),
    ])
    assert out.turn_index == 1        # 作答 +1，skip 不计
    assert out.followup               # 最后一步 skip 的说明
