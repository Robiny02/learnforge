"""Mock 子图 interrupt/resume 集成测试（Design §5.4）。

覆盖：开场出题 / 多轮累积评分 / 换 topic 不重置分 / pause-resume / escalate / end→review。
"""

from learnforge.agents.mock import MockInterviewAgent
from learnforge.contracts.agents.mock import MockInput


def _agent():
    # 每个用例独立 MemorySaver（默认），thread_id 用各自 session_id 隔离。
    return MockInterviewAgent()


def test_start_emits_first_question():
    m = _agent()
    out = m.run(MockInput(topic="并发", session_id="t-start"))
    assert out.status == "active"
    assert out.question
    assert out.turn_index == 0


def test_multi_turn_accumulates_scores():
    m = _agent()
    sid = "t-multi"
    m.run(MockInput(topic="并发", session_id=sid))
    m.answer(sid, user_answer="回答一")
    out = m.answer(sid, user_answer="回答二")
    assert out.status == "active"
    assert len(out.turn_scores) == 2
    assert out.turn_index == 2


def test_switch_topic_keeps_scores():
    m = _agent()
    sid = "t-switch"
    m.run(MockInput(topic="并发", session_id=sid))
    m.answer(sid, user_answer="回答一")
    before = len(m.answer(sid, user_answer="回答二").turn_scores)
    out = m.answer(sid, user_interrupt="换个topic")
    assert out.status == "active"
    assert len(out.turn_scores) == before, "换 topic 不应重置累积评分（Proposal §4d）"


def test_pause_then_resume():
    m = _agent()
    sid = "t-pause"
    m.run(MockInput(topic="网络", session_id=sid))
    m.answer(sid, user_answer="TCP三次握手")
    paused = m.answer(sid, user_interrupt="暂停")
    assert paused.status == "paused"
    resumed = m.answer(sid, user_answer="继续作答")
    assert resumed.status == "active"
    assert resumed.question


def test_escalate_returns_to_manager():
    m = _agent()
    sid = "t-esc"
    m.run(MockInput(topic="数据库", session_id=sid))
    out = m.answer(sid, user_interrupt="帮我改计划")
    assert out.status == "escalate"


def test_end_produces_review():
    m = _agent()
    sid = "t-end"
    m.run(MockInput(topic="并发", session_id=sid))
    m.answer(sid, user_answer="回答一")
    m.answer(sid, user_answer="回答二")
    out = m.answer(sid, user_interrupt="结束")
    assert out.status == "review"
    assert out.review is not None
