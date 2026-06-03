"""里程碑2：面试内即时控制（skip/hint/repeat/reveal/redo/feedback）回归（全离线）。

三层覆盖：
  ① 意图检测——六类控制能被 classify_mock_input 命中，且不偷正常作答；
  ② 纯文案——control.py 的确定性 builder；
  ③ 子图不变量——控制不消耗轮次、不评分；skip 换题、redo 回退一轮、followup 露出。
"""

from __future__ import annotations

import pytest

from learnforge.agents.mock import MockInterviewAgent, control as CTRL
from learnforge.contracts.agents.mock import MockInput
from learnforge.intent.mock_turn import CONTROL_ACTIONS, classify_mock_input


def _agent():
    return MockInterviewAgent()


# ----------------------------------------------------------------- ① 意图检测
@pytest.mark.parametrize("msg,kind", [
    ("跳过这题", "skip"),
    ("不会这题，下一题", "skip"),
    ("给点提示", "hint"),
    ("卡住了，给个思路", "hint"),
    ("这题什么意思，再说一遍", "repeat"),
    ("没听懂", "repeat"),
    ("直接告诉我答案", "reveal"),
    ("参考答案是什么", "reveal"),
    ("我想重答上一题", "redo"),
    ("我刚才答得怎么样", "feedback"),
])
def test_control_intents_detected(msg, kind):
    assert classify_mock_input("讲讲 Redis 持久化", msg) == kind


def test_control_actions_constant():
    assert set(CONTROL_ACTIONS) == {"skip", "hint", "repeat", "reveal", "redo", "feedback"}


def test_real_answer_not_stolen_by_control():
    # 含技术词的正常作答不应被误判成控制。
    ans = "我用 AOF everysec 加 RDB 快照，并用 INFO persistence 监控恢复时间"
    assert classify_mock_input("讲讲持久化", ans) == "answer"


def test_redo_beats_feedback_priority():
    # "重答上一题"与 feedback 的"上一题"重叠 → 必须判 redo。
    assert classify_mock_input("Q", "重答上一题") == "redo"


# ----------------------------------------------------------------- ② 纯文案
def test_build_hint_uses_expected_points():
    h = CTRL.build_hint("Q", ["chunk 取舍", "rerank 设计"])
    assert "提示" in h and "chunk 取舍" in h
    assert "答案" not in h  # 提示不直接给答案


def test_build_reveal_lists_points():
    r = CTRL.build_reveal("Q", ["要点A", "要点B"])
    assert "要点A" in r and "要点B" in r


def test_build_feedback_handles_missing_turn():
    assert "还没有" in CTRL.build_feedback(None)


def test_build_feedback_summarizes_score():
    turn = {"score": {"overall": 3, "dims": {"correctness": 3, "depth": 2, "clarity": 4},
                      "risk_flags": ["vague"], "missed_points": ["漏了取舍"]}}
    fb = CTRL.build_feedback(turn)
    assert "3/5" in fb and "含糊" in fb and "漏了取舍" in fb


# ----------------------------------------------------------------- ③ 子图不变量
@pytest.mark.parametrize("action", ["hint", "repeat", "reveal", "feedback"])
def test_immediate_controls_do_not_consume_turn(action):
    m = _agent()
    sid = f"t-ctrl-{action}"
    m.run(MockInput(topic="redis", session_id=sid))
    after = m.answer(sid, user_answer="我用 AOF 加 RDB 并监控指标")
    q, t = after.question, after.turn_index
    out = m.run(MockInput(topic="", session_id=sid, control_action=action))
    assert out.status == "active"
    assert out.turn_index == t          # 不推进轮次
    assert out.question == q            # 原题不变
    assert out.followup                 # 即时回应露出


def test_skip_changes_question_without_consuming_turn():
    m = _agent()
    sid = "t-ctrl-skip"
    first = m.run(MockInput(topic="redis", session_id=sid))
    t = first.turn_index
    out = m.run(MockInput(topic="", session_id=sid, control_action="skip"))
    assert out.turn_index == t          # skip 不计轮次
    assert out.followup                 # "已跳过"说明
    # 换了新题后仍能正常作答推进。
    ans = m.answer(sid, user_answer="新题作答内容，含监控与对比数据")
    assert ans.turn_index == t + 1


def test_redo_rewinds_one_turn():
    m = _agent()
    sid = "t-ctrl-redo"
    m.run(MockInput(topic="redis", session_id=sid))
    m.answer(sid, user_answer="第一题作答")
    after2 = m.answer(sid, user_answer="第二题作答")
    assert after2.turn_index == 2
    out = m.run(MockInput(topic="", session_id=sid, control_action="redo"))
    assert out.turn_index == 1           # 回退一轮
    assert len(out.turn_scores) == 1     # 撤销了一次评分
    assert out.followup and "重做" in out.followup


def test_redo_without_prior_turn_is_safe():
    m = _agent()
    sid = "t-ctrl-redo0"
    m.run(MockInput(topic="redis", session_id=sid))
    out = m.run(MockInput(topic="", session_id=sid, control_action="redo"))
    assert out.status == "active"
    assert out.turn_index == 0
    assert out.followup  # 友好提示而非崩溃


def test_followup_cleared_after_normal_answer():
    m = _agent()
    sid = "t-ctrl-clear"
    m.run(MockInput(topic="redis", session_id=sid))
    m.answer(sid, user_answer="作答一，含指标对比")
    m.run(MockInput(topic="", session_id=sid, control_action="hint"))
    normal = m.answer(sid, user_answer="再作答，补充证据与取舍")
    assert normal.followup is None       # 控制回应只露一次，正常作答后清掉
