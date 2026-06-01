"""Mock 子图（Design §5.4，Phase 3 实装）——带 checkpoint 的 interrupt/resume 状态机。

状态机 S1-S7（合法中断点 = S3 AWAIT_USER）：
  S1 INIT → S2 INTERVIEWER_TURN(出题) → S3 AWAIT_USER(interrupt) → S4 JUDGE(评分,累积分不重置)
   → S5 STRATEGIST(决策) →
       continue/raise/lower/switch_topic → 回 S2
       pause                              → S_PAUSE(interrupt 持久化,可跨会话 resume)
       end / turn≥max_turns               → S6 COACH_REVIEW → S7 SETTLE(emit events) → END
       escalate                           → S_EXIT(返回 Manager)

LangGraph interrupt/resume：S3/S_PAUSE 调 `interrupt()` 暂停；调用方 `Command(resume=...)` 续跑，
节点从头重放、`interrupt()` 返回 resume 值。MockState 由 checkpointer 持久化（thread_id=session_id）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from ...contracts.agents.mock import (
    CoachInput,
    InterviewerInput,
    JudgeInput,
    Score,
    StrategistInput,
    Turn,
)
from ...contracts.enums import StrategistAction

# 节点名常量
S1_INIT = "s1_init"
S2_INTERVIEWER = "s2_interviewer_turn"
S3_AWAIT = "s3_await_user"
S4_JUDGE = "s4_judge"
S5_STRATEGIST = "s5_strategist"
S6_COACH = "s6_coach_review"
S7_SETTLE = "s7_settle"
S_PAUSE = "s_pause"
S_EXIT = "s_exit"

# 局部 resume（回 S2 继续）的 action 集合（Design §5.4）
_LOCAL_CONTINUE = {
    StrategistAction.CONTINUE,
    StrategistAction.RAISE,
    StrategistAction.LOWER,
    StrategistAction.SWITCH_TOPIC,
}


class MockGraphState(TypedDict, total=False):
    session_id: str
    topic: str
    difficulty: int
    turn_index: int
    max_turns: int
    topic_coverage: List[str]

    # 当前轮
    current_question: Optional[str]
    current_expected_points: List[str]
    current_atom_refs: List[str]
    user_answer: Optional[str]
    user_interrupt: Optional[str]

    # 累积（switch_topic 不重置，Proposal §4d）
    turn_scores: List[dict]  # Score 序列化
    answered_atom_refs: List[str]  # 全场已答题涉及的 atom（供结算 mastery）

    # 决策与产出
    action: Optional[str]
    status: str  # active | paused | review | settled | escalated
    review: Optional[dict]      # CoachReport 序列化
    events: List[dict]          # EventPayload 序列化
    mastery_updates: List[dict] # 交给 Manager 落库（唯一写者）


def build_mock_subgraph(agents: Any, checkpointer: Optional[Any] = None):
    """构建并编译 mock 子图。

    `agents` 需提供 .interviewer/.judge/.strategist/.coach（MockInterviewAgent 持有）。
    `checkpointer` 缺省用 MemorySaver（进程内）；生产可换 SqliteSaver 实现跨进程持久化。
    """

    # ---------------- S1 INIT ----------------
    def s1_init(state: MockGraphState) -> Dict[str, Any]:
        return {
            "turn_index": 0,
            "turn_scores": [],
            "answered_atom_refs": [],
            "topic_coverage": [state["topic"]],
            "status": "active",
            "events": [],
            "mastery_updates": [],
        }

    # ---------------- S2 INTERVIEWER_TURN ----------------
    def s2_interviewer_turn(state: MockGraphState) -> Dict[str, Any]:
        history = [
            Turn(turn_index=i, question="", user_answer=None)
            for i in range(state.get("turn_index", 0))
        ]
        out = agents.interviewer.run(
            InterviewerInput(
                topic=state["topic"],
                difficulty=state.get("difficulty", 3),
                turn_history=history,
            )
        )
        return {
            "current_question": out.question,
            "current_expected_points": out.expected_points,
            "current_atom_refs": out.atom_refs,
            "status": "active",
        }

    # ---------------- S3 AWAIT_USER (interrupt) ----------------
    def s3_await_user(state: MockGraphState) -> Dict[str, Any]:
        # 合法中断点：暂停等用户作答；resume 载荷 = {"user_answer":..., "user_interrupt":...}。
        payload = interrupt({
            "kind": "await_user",
            "question": state.get("current_question"),
            "turn_index": state.get("turn_index", 0),
        })
        payload = payload or {}
        return {
            "user_answer": payload.get("user_answer"),
            "user_interrupt": payload.get("user_interrupt"),
        }

    # ---------------- S4 JUDGE ----------------
    def s4_judge(state: MockGraphState) -> Dict[str, Any]:
        # 纯中断（只给 user_interrupt 不作答）不评分、不计入升降档、不推进轮次。
        if not state.get("user_answer"):
            return {}
        score = agents.judge.run(
            JudgeInput(
                question=state.get("current_question") or "",
                expected_points=state.get("current_expected_points") or [],
                user_answer=state["user_answer"],
            )
        )
        scores: List[dict] = list(state.get("turn_scores") or [])
        scores.append(score.model_dump())
        _persist_turn(agents, state, score)
        answered = list(state.get("answered_atom_refs") or [])
        for aid in state.get("current_atom_refs") or []:
            if aid not in answered:
                answered.append(aid)
        return {
            "turn_scores": scores,
            "turn_index": state.get("turn_index", 0) + 1,
            "answered_atom_refs": answered,
        }

    # ---------------- S5 STRATEGIST ----------------
    def s5_strategist(state: MockGraphState) -> Dict[str, Any]:
        score_objs = [Score(**s) for s in state.get("turn_scores") or []]
        out = agents.strategist.run(
            StrategistInput(
                score_history=score_objs,
                user_interrupt=state.get("user_interrupt"),
                session_state={
                    "difficulty": state.get("difficulty", 3),
                    "topic": state["topic"],
                    "turn_index": state.get("turn_index", 0),
                },
            )
        )
        upd: Dict[str, Any] = {"action": out.action.value, "difficulty": out.next_difficulty}
        if out.action == StrategistAction.SWITCH_TOPIC and out.next_topic:
            coverage = list(state.get("topic_coverage") or [])
            if out.next_topic not in coverage:
                coverage.append(out.next_topic)
            upd["topic"] = out.next_topic
            upd["topic_coverage"] = coverage
        return upd

    def route_after_strategist(state: MockGraphState) -> str:
        action = StrategistAction(state.get("action") or "continue")
        if action == StrategistAction.ESCALATE:
            return S_EXIT
        if action == StrategistAction.END:
            return S6_COACH
        if action == StrategistAction.PAUSE:
            return S_PAUSE
        if state.get("turn_index", 0) >= state.get("max_turns", 10):
            return S6_COACH  # 到轮数上限强制复盘（Design §5.4）
        return S2_INTERVIEWER  # 局部 continue/raise/lower/switch_topic

    # ---------------- S_PAUSE (interrupt, 可跨会话 resume) ----------------
    def s_pause(state: MockGraphState) -> Dict[str, Any]:
        payload = interrupt({"kind": "paused", "turn_index": state.get("turn_index", 0)})
        payload = payload or {}
        # resume 后清掉本轮中断语，回 S2 继续。
        return {"status": "active", "user_interrupt": None,
                "user_answer": payload.get("user_answer")}

    # ---------------- S6 COACH_REVIEW ----------------
    def s6_coach_review(state: MockGraphState) -> Dict[str, Any]:
        out = agents.coach.run(
            CoachInput(
                turn_scores=[Score(**s) for s in state.get("turn_scores") or []],
                topic_coverage=state.get("topic_coverage") or [state["topic"]],
            )
        )
        return {
            "review": out.report.model_dump(),
            "events": [e.model_dump() for e in out.events],
            "status": "review",
        }

    # ---------------- S7 SETTLE ----------------
    def s7_settle(state: MockGraphState) -> Dict[str, Any]:
        # emit events 到 interaction_events（append-only，非掌握度写，子图可写）。
        _emit_events(agents, state)
        # mastery 更新留给 Manager（唯一写者，Design §2a/§4b/G2）。
        updates = _collect_mastery_updates(state)
        return {"status": "settled", "mastery_updates": updates}

    # ---------------- S_EXIT (escalate 回 Manager) ----------------
    def s_exit(state: MockGraphState) -> Dict[str, Any]:
        return {"status": "escalated"}

    g = StateGraph(MockGraphState)
    for name, fn in [
        (S1_INIT, s1_init), (S2_INTERVIEWER, s2_interviewer_turn), (S3_AWAIT, s3_await_user),
        (S4_JUDGE, s4_judge), (S5_STRATEGIST, s5_strategist), (S6_COACH, s6_coach_review),
        (S7_SETTLE, s7_settle), (S_PAUSE, s_pause), (S_EXIT, s_exit),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, S1_INIT)
    g.add_edge(S1_INIT, S2_INTERVIEWER)
    g.add_edge(S2_INTERVIEWER, S3_AWAIT)
    g.add_edge(S3_AWAIT, S4_JUDGE)
    g.add_edge(S4_JUDGE, S5_STRATEGIST)
    g.add_conditional_edges(
        S5_STRATEGIST,
        route_after_strategist,
        {S2_INTERVIEWER: S2_INTERVIEWER, S6_COACH: S6_COACH, S_PAUSE: S_PAUSE, S_EXIT: S_EXIT},
    )
    g.add_edge(S_PAUSE, S2_INTERVIEWER)  # resume 后继续出题
    g.add_edge(S6_COACH, S7_SETTLE)
    g.add_edge(S7_SETTLE, END)
    g.add_edge(S_EXIT, END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


# --- 副作用 helpers（best-effort，失败不阻断子图） ---
def _persist_turn(agents: Any, state: MockGraphState, score: Score) -> None:
    repo = getattr(agents, "mock_repo", None)
    if repo is None:
        return
    try:
        import uuid

        repo.add_turn(
            turn_id=str(uuid.uuid4()),
            session_id=state["session_id"],
            turn_index=state.get("turn_index", 0),
            question=state.get("current_question"),
            expected_points=state.get("current_expected_points") or [],
            user_answer=state.get("user_answer"),
            score=score.model_dump(),
        )
    except Exception:
        pass


def _emit_events(agents: Any, state: MockGraphState) -> None:
    repo = getattr(agents, "event_repo", None)
    events = state.get("events") or []
    if repo is None or not events:
        return
    from ...contracts.enums import EventType
    from ...contracts.message import EventPayload

    for i, ev in enumerate(events):
        try:
            repo.append(
                EventPayload(
                    event_type=EventType(ev["event_type"]), topic=ev["topic"],
                    atom_refs=ev.get("atom_refs", []), signal=ev.get("signal", {}),
                ),
                event_id=f"{state.get('session_id', 'sess')}:weak:{i}",
                trace_id=state.get("session_id"),
            )
        except Exception:
            pass


def _collect_mastery_updates(state: MockGraphState) -> List[dict]:
    """把逐轮评分聚合为 per-atom 的 mock 信号（signal=avg overall/5），交 Manager 落库。"""
    refs = state.get("answered_atom_refs") or []
    scores = [Score(**s) for s in state.get("turn_scores") or []]
    valid = [s.overall for s in scores if s.overall is not None]
    if not valid or not refs:
        return []
    signal = sum(valid) / len(valid) / 5.0
    return [{"atom_id": aid, "signal": signal, "source": "mock"} for aid in refs]
