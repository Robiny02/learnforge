"""InterviewDirector 引擎：边界情况 + 多轮聊天下的状态保持（重构后核心测试）。

旧 LangGraph 状态机已换成 InterviewDirector 智能规划 + MockStateStore（SQLite/dict 兜底）。
这里聚焦用户最关心的两点：① 多轮状态跨调用无损 round-trip；② 各类边界（退出/空转/escalate/过期/空输入）。
全离线、确定性（无 OPENROUTER_API_KEY 也必须通——"链路永远通"）。
"""

from __future__ import annotations

from learnforge.agents.mock import InterviewDirector, MockInterviewAgent
from learnforge.config import MOCK_IDLE_ROUNDS_THRESHOLD
from learnforge.contracts.agents.mock import MockInput, MockState, Score
from learnforge.contracts.enums import InterviewMove, InterviewPhase


def _agent(tmp_db):
    return MockInterviewAgent(db_path=tmp_db)


# ------------------------------------------------------ 多轮状态保持（核心）
def test_multi_turn_state_roundtrip(tmp_db):
    """连续多轮，每步从存储 reload，断言 transcript/scores/phase/turn_index 跨调用无损。"""
    m = _agent(tmp_db)
    sid = "rt"
    m.run(MockInput(topic="MySQL", session_id=sid, max_turns=20))
    answers = ["B+树索引，范围查询高效", "MVCC 用 undo 版本链和 ReadView", "RR 用 next-key lock 防幻读"]
    for i, ans in enumerate(answers, start=1):
        m.run(MockInput(topic="", session_id=sid, user_answer=ans))
        st = m.store.load(sid)                       # 每轮都从持久层读回
        assert st is not None
        assert st.turn_index == i                    # 轮次正确推进
        assert len(st.turns) == i                    # 逐轮记录累积
        assert len(st.turn_scores) == i              # 评分累积不丢
        assert st.turns[i - 1].user_answer == ans    # 原文作答无损保存
        assert st.current_question                   # 始终有下一题待答
        assert st.pending == "answer"


def test_state_survives_new_agent_instance(tmp_db):
    """换一个 agent 实例（≈进程重启），同 db_path 能读回整场状态继续。"""
    sid = "survive"
    a = _agent(tmp_db)
    a.run(MockInput(topic="并发", session_id=sid))
    a.run(MockInput(topic="", session_id=sid, user_answer="乐观锁+版本号"))
    b = _agent(tmp_db)                               # 全新实例
    st = b.store.load(sid)
    assert st is not None and st.turn_index == 1 and len(st.turns) == 1
    o = b.run(MockInput(topic="", session_id=sid, user_answer="CAS 自旋"))
    assert o.status == "active" and o.turn_index == 2


def test_switch_topic_keeps_accumulated_scores(tmp_db):
    m = _agent(tmp_db)
    sid = "sw"
    m.run(MockInput(topic="并发", session_id=sid))
    m.run(MockInput(topic="", session_id=sid, user_answer="答一"))
    before = len(m.store.load(sid).turn_scores)
    o = m.run(MockInput(topic="", session_id=sid, user_interrupt="换个话题"))
    assert o.status == "active"
    assert len(o.turn_scores) == before              # 换 topic 不重置累积评分


# ------------------------------------------------------ 退出（用户输入触发）
def test_user_exit_then_review(tmp_db):
    m = _agent(tmp_db)
    sid = "exit"
    m.run(MockInput(topic="Go", session_id=sid))
    m.run(MockInput(topic="", session_id=sid, user_answer="GMP 调度"))
    o = m.run(MockInput(topic="", session_id=sid, user_interrupt="结束面试"))
    assert o.status == "review"
    assert o.review is not None


def test_pause_then_resume(tmp_db):
    m = _agent(tmp_db)
    sid = "pause"
    m.run(MockInput(topic="网络", session_id=sid))
    paused = m.run(MockInput(topic="", session_id=sid, user_interrupt="暂停一下"))
    assert paused.status == "paused"
    resumed = m.run(MockInput(topic="", session_id=sid, user_answer="继续"))
    assert resumed.status == "active" and resumed.question


# ------------------------------------------------------ 空转 → 主动询问继续/总结
def test_idle_rounds_trigger_check_continue(tmp_db):
    """连续多轮 hint（没出新题）→ 达阈值触发主动询问（pending=continue_decision）。"""
    m = _agent(tmp_db)
    sid = "idle"
    m.run(MockInput(topic="Redis", session_id=sid))
    m.run(MockInput(topic="", session_id=sid, user_answer="单线程+IO多路复用"))
    last = None
    for _ in range(MOCK_IDLE_ROUNDS_THRESHOLD):
        last = m.run(MockInput(topic="", session_id=sid, control_action="hint"))
    assert last.pending == "continue_decision"
    assert "总结" in (last.followup or "")           # 主动问：继续拷打还是总结


def test_idle_checkin_summarize(tmp_db):
    m = _agent(tmp_db)
    sid = "idle2"
    m.run(MockInput(topic="Redis", session_id=sid))
    m.run(MockInput(topic="", session_id=sid, user_answer="单线程"))
    for _ in range(MOCK_IDLE_ROUNDS_THRESHOLD):
        m.run(MockInput(topic="", session_id=sid, control_action="hint"))
    o = m.run(MockInput(topic="", session_id=sid, user_answer="总结"))   # 选择总结
    assert o.status == "review" and o.review is not None


def test_idle_checkin_continue(tmp_db):
    m = _agent(tmp_db)
    sid = "idle3"
    m.run(MockInput(topic="Redis", session_id=sid))
    m.run(MockInput(topic="", session_id=sid, user_answer="单线程"))
    for _ in range(MOCK_IDLE_ROUNDS_THRESHOLD):
        m.run(MockInput(topic="", session_id=sid, control_action="hint"))
    o = m.run(MockInput(topic="", session_id=sid, user_answer="继续"))   # 选择继续
    assert o.status == "active"
    assert m.store.load(sid).rounds_since_question == 0                  # 空转计数已复位


def test_answer_resets_idle_counter(tmp_db):
    m = _agent(tmp_db)
    sid = "reset"
    m.run(MockInput(topic="Redis", session_id=sid))
    m.run(MockInput(topic="", session_id=sid, control_action="hint"))   # 空转 +1
    assert m.store.load(sid).rounds_since_question == 1
    m.run(MockInput(topic="", session_id=sid, user_answer="正经作答，给出证据与取舍"))
    assert m.store.load(sid).rounds_since_question == 0                  # 真正作答 → 复位


# ------------------------------------------------------ escalate / 过期 / 空输入
def test_escalate_returns_handoff(tmp_db):
    m = _agent(tmp_db)
    sid = "esc"
    m.run(MockInput(topic="数据库", session_id=sid))
    o = m.run(MockInput(topic="", session_id=sid, user_interrupt="帮我改学习计划"))
    assert o.status == "escalate"
    assert o.handoff_summary


def test_unknown_session_expires(tmp_db):
    m = _agent(tmp_db)
    o = m.run(MockInput(topic="", session_id="nope", user_answer="hi"))
    assert o.status == "expired"


def test_review_session_not_reanswerable(tmp_db):
    m = _agent(tmp_db)
    sid = "done"
    m.run(MockInput(topic="Go", session_id=sid))
    m.run(MockInput(topic="", session_id=sid, user_interrupt="结束"))   # → review
    o = m.run(MockInput(topic="", session_id=sid, user_answer="还想答"))
    assert o.status == "expired"                                        # 已结束的会话不再续答


def test_empty_answer_does_not_crash(tmp_db):
    m = _agent(tmp_db)
    sid = "empty"
    m.run(MockInput(topic="Go", session_id=sid))
    o = m.run(MockInput(topic="", session_id=sid, user_answer=""))      # 空作答
    assert o.status in ("active", "review")


def test_redo_without_prior_turn_is_graceful(tmp_db):
    m = _agent(tmp_db)
    sid = "redo0"
    m.run(MockInput(topic="Go", session_id=sid))
    o = m.run(MockInput(topic="", session_id=sid, control_action="redo"))  # 还没答过题
    assert o.status == "active"
    assert "重做" in (o.followup or "") or "先答" in (o.followup or "")


# ------------------------------------------------------ Director 单元（确定性）
def test_director_interrupt_classification():
    d = InterviewDirector
    assert d.classify_interrupt("帮我改计划") == InterviewMove.ESCALATE
    assert d.classify_interrupt("暂停一下") == InterviewMove.PAUSE
    assert d.classify_interrupt("结束吧") == InterviewMove.SUMMARIZE
    assert d.classify_interrupt("太难了简单点") == InterviewMove.LOWER
    assert d.classify_interrupt("普通的一句作答") is None


def test_director_fallback_summarize_at_max_turns():
    st = MockState(session_id="x", topic="t", max_turns=2, turn_index=2,
                   phase=InterviewPhase.BASICS.value)
    assert InterviewDirector()._fallback_move(st, None) == InterviewMove.SUMMARIZE


def test_director_low_confidence_does_not_force_correct():
    """离线低置信打分（conf<0.5）不应触发 CORRECT，否则几乎每轮都'纠错'。"""
    st = MockState(session_id="x", topic="t", max_turns=10, phase=InterviewPhase.BASICS.value)
    lowconf_wrong = Score(overall=0, confidence=0.2)
    assert InterviewDirector()._fallback_move(st, lowconf_wrong) != InterviewMove.CORRECT


def test_phase_advances_after_enough_questions(tmp_db):
    """答够 MOCK_PHASE_QUESTIONS 题后环节从 basics 推进（出题计数驱动）。"""
    m = _agent(tmp_db)
    sid = "phase"
    m.run(MockInput(topic="后端", session_id=sid, max_turns=30))
    # 多答几轮，迫使环节推进。
    for i in range(8):
        m.run(MockInput(topic="", session_id=sid, user_answer=f"作答{i}，给出证据与取舍对比"))
    st = m.store.load(sid)
    assert st.phase in (InterviewPhase.PROJECT.value, InterviewPhase.SYSTEM_DESIGN.value)
