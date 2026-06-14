"""MockInterviewAgent —— 模拟面试外壳（重构：InterviewDirector 智能规划，替代 LangGraph 状态机）。

旧版是 LangGraph S1–S7 子图 + checkpointer interrupt/resume，太僵硬。现在：
- 状态存 SQLite（MockStateStore，替代 checkpointer），每个调用 = 跑一轮：load→run_turn→save；
- 下一步面试官动作由 **InterviewDirector** 智能选取（Haiku + 确定性兜底），不预拆死一张图；
- 出题/解答/纠错走可配置强档（Interviewer），保证高质量；评分 Haiku，复盘 Sonnet（分层）；
- 退出可由用户中断触发；连续多轮"没出新题"（提示/复述/公布答案/重做）→ 主动询问继续拷打还是总结收尾。

对外契约不变：`run(MockInput)->MockOutput`（server/cli 经 actions.py 的 channel 路由调用），
status ∈ {active, paused, review, escalate, expired}；保留 `mastery_updates(sid)` / `handoff_summary(sid)`。
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional

from ...config import MOCK_IDLE_ROUNDS_THRESHOLD, MOCK_PHASE_QUESTIONS
from ...contracts.agents.mock import (
    CoachInput,
    InterviewContext,
    InterviewerInput,
    JudgeInput,
    MockInput,
    MockOutput,
    MockState,
    Score,
    Turn,
)
from ...contracts.enums import AgentId, InterviewMove, InterviewPhase
from ..base import BaseAgent
from ..retrieval import RetrievalAgent
from . import control as CTRL
from . import interview_skill as IS
from .coach import CoachAgent
from .director import InterviewDirector
from .handoff import build_handoff_summary
from .interviewer import InterviewerAgent
from .judge import JudgeAgent
from .state import MockStateStore

# 环节推进顺序（基础 → 项目 → 系统设计）。
_PHASE_ORDER = [InterviewPhase.BASICS.value, InterviewPhase.PROJECT.value,
                InterviewPhase.SYSTEM_DESIGN.value]


class MockInterviewAgent(BaseAgent):
    agent_id = AgentId.MOCK

    def __init__(self, db_path: Optional[str] = None, checkpointer: Optional[Any] = None) -> None:
        super().__init__()
        self._db_path = db_path
        self.interviewer = InterviewerAgent(db_path=db_path)
        self.judge = JudgeAgent()
        self.coach = CoachAgent()
        self.director = InterviewDirector()
        self.retrieval = RetrievalAgent(db_path=db_path)
        # 仓储句柄（best-effort 落库；DB 不可用时静默跳过）。
        self.mock_repo = self._maybe_repo("MockSessionRepository")
        self.event_repo = self._maybe_repo("EventRepository")
        # 状态存储（替代 LangGraph checkpointer）。checkpointer 参数保留仅为向后兼容，已忽略。
        self.store = MockStateStore(db_path=db_path)
        self.persistent = self.store._db_ok  # 观测：状态是否落盘持久化

    def _maybe_repo(self, cls_name: str):
        try:
            from ...storage import repositories as R

            return getattr(R, cls_name)(db_path=self._db_path)
        except Exception:
            return None

    # ------------------------------------------------------------- 公共入口
    def run(self, payload: MockInput) -> MockOutput:
        session_id = payload.session_id or f"mock-{uuid.uuid4().hex[:8]}"
        if (payload.user_answer is None and payload.user_interrupt is None
                and payload.control_action is None):
            return self.start(session_id, payload.topic, payload.target_difficulty,
                              payload.max_turns, context=payload.context)
        return self.answer(session_id, payload.user_answer, payload.user_interrupt,
                           payload.control_action)

    def start(self, session_id: str, topic: str, difficulty: int = 3,
              max_turns: int = 10, context: Optional[Any] = None) -> MockOutput:
        self.require_tool("mock.checkpoint")
        if self.mock_repo is not None:
            try:
                self.require_tool("repository.write.mock_sessions")
                self.mock_repo.upsert_session(session_id, topic, difficulty, max_turns)
            except Exception:
                pass
        ctx = context if isinstance(context, InterviewContext) or context is None \
            else InterviewContext(**context)
        state = MockState(
            session_id=session_id, topic=topic, difficulty=difficulty, max_turns=max_turns,
            context=ctx, topic_coverage=[topic], phase=InterviewPhase.BASICS.value,
            status="active",
        )
        self._ask(state, mode="ask")   # 出首题
        state.pending = "answer"
        self.store.save(state)
        return self._to_output(state)

    def answer(self, session_id: str, user_answer: Optional[str] = None,
               user_interrupt: Optional[str] = None,
               control_action: Optional[str] = None) -> MockOutput:
        self.require_tool("mock.checkpoint")
        state = self.store.load(session_id)
        # 护栏：会话不存在 / 已终结（review/settled/escalated/expired）→ 优雅过期，不盲目续跑。
        if state is None or state.status in ("review", "settled", "escalated", "expired"):
            return MockOutput(
                session_id=session_id, status="expired",
                followup="该模拟面试会话已过期或不存在（可能服务重启过）。请重新开始一场面试。",
            )
        # 暂停态：下一条消息即恢复面试（替代旧 S_PAUSE interrupt/resume）。
        if state.status == "paused":
            state.status = "active"
        followup = self._run_turn(state, user_answer, user_interrupt, control_action)
        self.store.save(state)
        return self._to_output(state, followup=followup)

    # ------------------------------------------------------------- 单轮引擎
    def _run_turn(self, state: MockState, user_answer: Optional[str],
                  user_interrupt: Optional[str], control_action: Optional[str]) -> Optional[str]:
        # A) 空转主动询问的回复（继续 vs 总结收尾）——纯在引擎内消化，server 无需感知。
        if state.pending == "continue_decision":
            text = user_answer or user_interrupt or control_action or ""
            if self._wants_summary(text):
                self._summarize(state)
                return None
            state.pending = "answer"
            state.rounds_since_question = 0
            cont = "好，我们继续。"
            return f"{cont}\n{state.current_question}" if state.current_question else cont
        # B) 即时控制（提示/复述/公布答案/点评/跳过/重做）——不评分。
        if control_action:
            return self._handle_control(state, control_action)
        # C) 中断（换题/调难度/暂停/总结/escalate）。
        if user_interrupt:
            return self._handle_interrupt(state, user_interrupt)
        # D) 作答 → 评分 → Director 选下一步。
        if user_answer is not None:
            return self._handle_answer(state, user_answer)
        return None

    def _handle_answer(self, state: MockState, user_answer: str) -> Optional[str]:
        role_type = self._role_type(state)
        score = self.judge.run(JudgeInput(
            question=state.current_question or "",
            expected_points=state.current_expected_points or [],
            user_answer=user_answer, role_type=role_type,
        ))
        state.turns.append(Turn(turn_index=state.turn_index, question=state.current_question or "",
                                user_answer=user_answer, score=score))
        state.turn_scores.append(score)
        state.turn_index += 1
        state.rounds_since_question = 0
        for aid in state.current_atom_refs or []:
            if aid not in state.answered_atom_refs:
                state.answered_atom_refs.append(aid)
        self._persist_turn(state, score)
        decision = self.director.decide_next(state, last_score=score)
        return self._execute_move(state, decision.move, decision.next_topic, user_answer, score)

    def _execute_move(self, state: MockState, move: str, next_topic: Optional[str],
                      last_answer: str, last_score: Score) -> Optional[str]:
        if move == InterviewMove.SUMMARIZE.value:
            self._summarize(state)
            return None
        if move == InterviewMove.ESCALATE.value:
            self._escalate(state)
            return None
        if move in (InterviewMove.RAISE.value, InterviewMove.LOWER.value):
            state.difficulty = (min(5, state.difficulty + 1) if move == InterviewMove.RAISE.value
                                else max(1, state.difficulty - 1))
            self._ask(state, mode="ask")
            state.pending = "answer"
            return None
        if move == InterviewMove.SWITCH_TOPIC.value:
            nt = (next_topic or "").strip() or state.topic
            if nt not in state.topic_coverage:
                state.topic_coverage.append(nt)
            state.topic = nt
            self._ask(state, mode="ask")
            state.pending = "answer"
            return None
        if move == InterviewMove.CORRECT.value:
            # 高质量纠错（强模型），再出下一题；纠错文案作 followup 露出。
            correction = self.interviewer.explain(
                "correct", state.current_question or "", state.current_expected_points,
                last_answer, self._role_type(state))
            self._ask(state, mode="ask")
            state.pending = "answer"
            return correction
        if move == InterviewMove.PROBE.value:
            self._ask(state, mode="probe")
            state.pending = "answer"
            return None
        if move == InterviewMove.FOLLOWUP.value:
            self._ask(state, mode="followup")
            state.pending = "answer"
            return None
        # default ASK
        self._ask(state, mode="ask")
        state.pending = "answer"
        return None

    def _handle_interrupt(self, state: MockState, text: str) -> Optional[str]:
        move = self.director.classify_interrupt(text)
        if move == InterviewMove.ESCALATE:
            self._escalate(state)
            return None
        if move == InterviewMove.PAUSE:
            state.status = "paused"
            return None
        if move == InterviewMove.SUMMARIZE:
            self._summarize(state)
            return None
        if move in (InterviewMove.RAISE, InterviewMove.LOWER):
            state.difficulty = (min(5, state.difficulty + 1) if move == InterviewMove.RAISE
                                else max(1, state.difficulty - 1))
            self._ask(state, mode="ask")
            state.pending = "answer"
            return None
        if move == InterviewMove.SWITCH_TOPIC:
            self._ask(state, mode="ask")  # 主题切换由检索/出题自然带过（确定性不强行解析新 topic）
            state.pending = "answer"
            return None
        # None（无明确中断意图）→ 视为继续，复述当前题。
        state.pending = "answer"
        return CTRL.build_repeat(state.current_question or "")

    def _handle_control(self, state: MockState, action: str) -> Optional[str]:
        role = self._role_type(state)
        state.pending = "answer"
        if action == "skip":  # 出新题（算"出题" → 不计空转），不评分、不加轮次。
            note = CTRL.build_skip_note()
            self._ask(state, mode="ask")
            return note
        if action == "redo":  # 回退到上一题（不出新题 → 计空转）。
            if state.prev_question and state.turns and state.turn_scores:
                state.turns.pop()
                state.turn_scores.pop()
                state.turn_index = max(0, state.turn_index - 1)
                state.current_question = state.prev_question
                state.current_expected_points = list(state.prev_expected_points)
                state.current_atom_refs = list(state.prev_atom_refs)
                note = CTRL.build_redo_note(state.prev_question)
            else:
                note = "还没有可重做的上一题——先答一题再说。"
            return self._maybe_idle(state, note)
        # 停留在当前题（hint/reveal 高质量解答；repeat/feedback 确定性）→ 计空转。
        if action in ("hint", "reveal"):
            note = self.interviewer.explain(action, state.current_question or "",
                                            state.current_expected_points, None, role)
        elif action == "feedback":
            note = CTRL.build_feedback(state.turns[-1].model_dump() if state.turns else None)
        else:  # repeat / 未知
            note = CTRL.build_repeat(state.current_question or "")
        return self._maybe_idle(state, note)

    def _maybe_idle(self, state: MockState, note: Optional[str]) -> Optional[str]:
        """连续多轮"没出新题" → 主动询问继续拷打还是总结收尾（阈值 MOCK_IDLE_ROUNDS_THRESHOLD）。"""
        state.rounds_since_question += 1
        if state.rounds_since_question >= MOCK_IDLE_ROUNDS_THRESHOLD:
            state.pending = "continue_decision"
            checkin = ("\n\n——我们已经连续几轮没推进新题了。要**继续拷打**（直接作答即可），"
                       "还是就此**总结收尾**出复盘？回复『总结』结束本场。")
            return (note or "") + checkin
        return note

    # ------------------------------------------------------------- 出题 / 推进环节
    def _ask(self, state: MockState, mode: str = "ask") -> None:
        """让 Interviewer 出题/追问，更新 current_*；mode ∈ {ask, followup, probe}。"""
        if mode == "ask" and state.phase_question_count >= MOCK_PHASE_QUESTIONS:
            self._advance_phase(state)
        # 快照刚答过的题（供 redo 回退）。
        last_q = state.current_question
        last_a = state.turns[-1].user_answer if state.turns else None
        state.prev_question = state.current_question
        state.prev_expected_points = list(state.current_expected_points)
        state.prev_atom_refs = list(state.current_atom_refs)
        out = self.interviewer.run(InterviewerInput(
            topic=state.topic, difficulty=state.difficulty,
            turn_history=state.turns[-6:],
            context=self._role_ctx(state),
            last_question=last_q if mode in ("followup", "probe") else None,
            last_answer=last_a if mode in ("followup", "probe") else None,
            turn_index=state.turn_index, phase=state.phase, mode=mode,
        ))
        state.current_question = out.question
        state.current_expected_points = list(out.expected_points)
        state.current_atom_refs = list(out.atom_refs)
        state.phase_question_count += 1
        state.rounds_since_question = 0   # 出了新题 → 不再算空转

    def _advance_phase(self, state: MockState) -> None:
        try:
            i = _PHASE_ORDER.index(state.phase)
        except ValueError:
            i = 0
        if i < len(_PHASE_ORDER) - 1:
            state.phase = _PHASE_ORDER[i + 1]
            state.phase_question_count = 0

    # ------------------------------------------------------------- 收尾 / 升级
    def _summarize(self, state: MockState) -> None:
        out = self.coach.run(CoachInput(
            turn_scores=state.turn_scores,
            topic_coverage=state.topic_coverage or [state.topic],
            context=self._role_ctx(state), turns=state.turns,
        ))
        state.review = out.report
        state.events = list(out.events)
        state.status = "review"
        state.pending = None
        state.mastery_updates = self._collect_mastery_updates(state)
        self._emit_events(state)

    def _escalate(self, state: MockState) -> None:
        state.status = "escalated"
        state.handoff_summary = build_handoff_summary(state.model_dump())
        state.pending = None

    # ------------------------------------------------------------- 输出 / 读取
    def _to_output(self, state: MockState, followup: Optional[str] = None) -> MockOutput:
        scores: List[Score] = state.turn_scores
        base = dict(session_id=state.session_id, turn_scores=scores,
                    turn_index=state.turn_index, phase=state.phase)
        if state.status == "paused":
            return MockOutput(status="paused", pending=state.pending, **base)
        if state.status == "escalated":
            return MockOutput(status="escalate", escalate_action="escalate",
                              handoff_summary=state.handoff_summary, **base)
        if state.status == "review":
            return MockOutput(status="review", review=state.review, events=state.events, **base)
        return MockOutput(status="active", question=state.current_question,
                          followup=followup, pending=state.pending, **base)

    def handoff_summary(self, session_id: str) -> str:
        state = self.store.load(session_id)
        return build_handoff_summary(state.model_dump() if state else {})

    def mastery_updates(self, session_id: str) -> list:
        state = self.store.load(session_id)
        return list(state.mastery_updates) if state else []

    # ------------------------------------------------------------- helpers
    def _role_ctx(self, state: MockState) -> Optional[InterviewContext]:
        return state.context

    @staticmethod
    def _role_type(state: MockState) -> Optional[str]:
        ctx = state.context
        if ctx is None:
            return None
        return ctx.role_type or IS.detect_role_type(ctx.jd_text, ctx.target_role)

    @staticmethod
    def _wants_summary(text: str) -> bool:
        """空转询问的回复：是否要总结收尾（否则继续）。确定性、离线安全。"""
        t = (text or "").strip().lower()
        # 明确"总结/结束"类先判（含多词，避免被 continue 的"不"误吞，如"不练了"）。
        if any(c in t for c in ("总结", "收尾", "复盘", "结束", "不练", "不想练", "够了",
                                "到此", "收工", "end", "stop", "summary")):
            return True
        if any(c in t for c in ("继续", "再来", "接着", "再问", "保留", "换一")):
            return False
        return False  # 歧义 → 默认继续，绝不误丢面试

    def _persist_turn(self, state: MockState, score: Score) -> None:
        if self.mock_repo is None:
            return
        try:
            self.mock_repo.add_turn(
                turn_id=str(uuid.uuid4()), session_id=state.session_id,
                turn_index=state.turn_index - 1, question=state.current_question,
                expected_points=state.current_expected_points or [],
                user_answer=state.turns[-1].user_answer if state.turns else None,
                score=score.model_dump(),
            )
        except Exception:
            pass

    def _emit_events(self, state: MockState) -> None:
        if self.event_repo is None or not state.events:
            return
        for i, ev in enumerate(state.events):
            try:
                self.event_repo.append(ev, event_id=f"{state.session_id}:weak:{i}",
                                       trace_id=state.session_id)
            except Exception:
                pass

    @staticmethod
    def _collect_mastery_updates(state: MockState) -> List[dict]:
        """逐轮评分聚合为 per-atom mock 信号（signal=avg overall/5），交 Manager 落库（唯一写者）。"""
        refs = state.answered_atom_refs or []
        valid = [s.overall for s in state.turn_scores if s.overall is not None]
        if not valid or not refs:
            return []
        signal = sum(valid) / len(valid) / 5.0
        return [{"atom_id": aid, "signal": signal, "source": "mock"} for aid in refs]
