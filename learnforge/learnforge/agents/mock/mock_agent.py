"""MockInterviewAgent —— 模拟面试子系统外壳（Design §3.8 / §5.4）。Phase 3：驱动 LangGraph 子图。

持有四个子 agent + 仓储，构建带 checkpoint 的 mock 子图（graph/mock_graph.py），
对外暴露 start/answer（多轮 interrupt/resume）。run() 作为统一入口：
- 无 user_answer/user_interrupt → 开场（出首题，停在 S3）。
- 有 → resume（评分→决策→下一题 / 复盘 / escalate）。
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from langgraph.types import Command

from ...contracts.agents.mock import CoachReport, MockInput, MockOutput, Score
from ...contracts.enums import AgentId
from ..base import BaseAgent
from ..retrieval import RetrievalAgent
from .coach import CoachAgent
from .interviewer import InterviewerAgent
from .judge import JudgeAgent
from .strategist import StrategistAgent


class MockInterviewAgent(BaseAgent):
    agent_id = AgentId.MOCK

    def __init__(self, db_path: Optional[str] = None, checkpointer: Optional[Any] = None) -> None:
        super().__init__()
        self._db_path = db_path
        self.interviewer = InterviewerAgent(db_path=db_path)
        self.judge = JudgeAgent()
        self.strategist = StrategistAgent()
        self.coach = CoachAgent()
        self.retrieval = RetrievalAgent(db_path=db_path)
        # 仓储句柄（best-effort 落库；DB 不可用时子图静默跳过）。
        self.mock_repo = self._maybe_repo("MockSessionRepository")
        self.event_repo = self._maybe_repo("EventRepository")
        # 延迟导入避免 graph 包 ↔ agents 包的循环 import（在实例化期模块已全部加载）。
        from ...graph.mock_graph import build_mock_subgraph

        self.graph = build_mock_subgraph(self, checkpointer=checkpointer)

    def _maybe_repo(self, cls_name: str):
        try:
            from ...storage import repositories as R

            return getattr(R, cls_name)(db_path=self._db_path)
        except Exception:
            return None

    # ------------------------------------------------------------- 公共入口
    def run(self, payload: MockInput) -> MockOutput:
        session_id = payload.session_id or f"mock-{uuid.uuid4().hex[:8]}"
        if payload.user_answer is None and payload.user_interrupt is None:
            return self.start(
                session_id, payload.topic, payload.target_difficulty, payload.max_turns
            )
        return self.answer(session_id, payload.user_answer, payload.user_interrupt)

    def start(self, session_id: str, topic: str, difficulty: int = 3,
              max_turns: int = 10) -> MockOutput:
        self.require_tool("mock.checkpoint")
        if self.mock_repo is not None:
            try:
                self.require_tool("repository.write.mock_sessions")
                self.mock_repo.upsert_session(session_id, topic, difficulty, max_turns)
            except Exception:
                pass
        config = {"configurable": {"thread_id": session_id}}
        result = self.graph.invoke(
            {
                "session_id": session_id,
                "topic": topic,
                "difficulty": difficulty,
                "max_turns": max_turns,
            },
            config=config,
        )
        return self._to_output(session_id, result, config)

    def answer(self, session_id: str, user_answer: Optional[str] = None,
               user_interrupt: Optional[str] = None) -> MockOutput:
        self.require_tool("mock.checkpoint")
        config = {"configurable": {"thread_id": session_id}}
        result = self.graph.invoke(
            Command(resume={"user_answer": user_answer, "user_interrupt": user_interrupt}),
            config=config,
        )
        return self._to_output(session_id, result, config)

    # ------------------------------------------------------------- 输出装配
    def _to_output(self, session_id: str, result: dict, config: dict) -> MockOutput:
        values = self.graph.get_state(config).values or result
        scores = [Score(**s) for s in values.get("turn_scores") or []]
        turn_index = values.get("turn_index", 0)

        interrupts = result.get("__interrupt__")
        if interrupts:
            intr = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
            kind = (intr or {}).get("kind")
            if kind == "paused":
                return MockOutput(session_id=session_id, status="paused",
                                  turn_scores=scores, turn_index=turn_index)
            return MockOutput(
                session_id=session_id, status="active",
                question=(intr or {}).get("question"),
                turn_scores=scores, turn_index=turn_index,
            )

        status = values.get("status", "settled")
        if status == "escalated":
            return MockOutput(
                session_id=session_id, status="escalate",
                escalate_action=values.get("action"),
                turn_scores=scores, turn_index=turn_index,
            )
        # 终场复盘（settled）。
        review = values.get("review")
        events_raw = values.get("events") or []
        from ...contracts.enums import EventType
        from ...contracts.message import EventPayload

        events = [
            EventPayload(event_type=EventType(e["event_type"]), topic=e["topic"],
                         atom_refs=e.get("atom_refs", []), signal=e.get("signal", {}))
            for e in events_raw
        ]
        return MockOutput(
            session_id=session_id, status="review",
            review=CoachReport(**review) if review else None,
            turn_scores=scores, turn_index=turn_index, events=events,
        )

    # mastery 更新交给 Manager（唯一写者）——供结算后读取。
    def mastery_updates(self, session_id: str) -> list:
        config = {"configurable": {"thread_id": session_id}}
        try:
            return self.graph.get_state(config).values.get("mastery_updates") or []
        except Exception:
            return []
