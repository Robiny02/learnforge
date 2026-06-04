"""MockInterviewAgent —— 模拟面试子系统外壳（Design §3.8 / §5.4）。Phase 3：驱动 LangGraph 子图。

持有四个子 agent + 仓储，构建带 checkpoint 的 mock 子图（graph/mock_graph.py），
对外暴露 start/answer（多轮 interrupt/resume）。run() 作为统一入口：
- 无 user_answer/user_interrupt → 开场（出首题，停在 S3）。
- 有 → resume（评分→决策→下一题 / 复盘 / escalate）。
"""

from __future__ import annotations

import os
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


def _checkpoint_sidecar_path(db_path: Optional[str]) -> str:
    """mock checkpoint 落在主库旁的 sidecar 文件（与知识库 schema 解耦，避免锁竞争）。"""
    from ...config import DB_PATH

    main = db_path or DB_PATH
    folder = os.path.dirname(main)
    return os.path.join(folder, "mock_checkpoints.db") if folder else "mock_checkpoints.db"


def _build_checkpointer(db_path: Optional[str]) -> Optional[Any]:
    """默认用 SqliteSaver 持久化 mock 图状态（跨进程重启可 resume）。

    - `LF_MOCK_CHECKPOINT=memory`（测试/临时）→ 返回 None，子图回退进程内 MemorySaver；
    - 缺 langgraph-checkpoint-sqlite / 建库失败 → 返回 None，优雅回退，不阻断面试。
    checkpoint 库路径：`LF_MOCK_CHECKPOINT_DB` 覆盖，否则主库旁的 mock_checkpoints.db。
    """
    if os.getenv("LF_MOCK_CHECKPOINT", "").lower() == "memory":
        return None
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        path = os.getenv("LF_MOCK_CHECKPOINT_DB") or _checkpoint_sidecar_path(db_path)
        conn = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(conn)
        try:
            saver.setup()  # 幂等建 checkpoints/writes 表
        except Exception:
            pass
        return saver
    except Exception:
        return None  # 缺包/IO 失败 → MemorySaver 兜底（仅进程内，不阻断当场面试）


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
        # checkpoint：默认 SqliteSaver（跨重启可 resume）；缺包/测试 → MemorySaver 兜底。
        if checkpointer is None:
            checkpointer = _build_checkpointer(db_path)
        self.persistent = checkpointer is not None  # 供观测：是否落盘持久化
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
        if (payload.user_answer is None and payload.user_interrupt is None
                and payload.control_action is None):
            return self.start(
                session_id, payload.topic, payload.target_difficulty, payload.max_turns,
                context=payload.context,
            )
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
        config = {"configurable": {"thread_id": session_id}}
        result = self.graph.invoke(
            {
                "session_id": session_id,
                "topic": topic,
                "difficulty": difficulty,
                "max_turns": max_turns,
                # 候选人材料 + 目标岗位上下文经 checkpoint 持久化整场可用（接入 LLMInternSkill）。
                "context": context.model_dump() if context is not None else None,
            },
            config=config,
        )
        return self._to_output(session_id, result, config)

    def answer(self, session_id: str, user_answer: Optional[str] = None,
               user_interrupt: Optional[str] = None,
               control_action: Optional[str] = None) -> MockOutput:
        self.require_tool("mock.checkpoint")
        config = {"configurable": {"thread_id": session_id}}
        # 护栏：无可恢复的中断点（重启后 checkpoint 丢失 / 会话不存在 / 已结束）→ 优雅过期，
        # 不再盲目 Command(resume=...)（那会在空状态上从 START 重放、读 state["topic"] 抛 KeyError）。
        if not self._has_resumable(config):
            return MockOutput(
                session_id=session_id, status="expired",
                followup="该模拟面试会话已过期或不存在（可能服务重启过）。请重新开始一场面试。",
            )
        result = self.graph.invoke(
            Command(resume={"user_answer": user_answer, "user_interrupt": user_interrupt,
                            "control_action": control_action}),
            config=config,
        )
        return self._to_output(session_id, result, config)

    def _has_resumable(self, config: dict) -> bool:
        """该 thread 是否停在一个可 resume 的中断点（S3/S_PAUSE）。"""
        try:
            snap = self.graph.get_state(config)
        except Exception:
            return False
        return bool(getattr(snap, "next", ()))

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
                followup=(intr or {}).get("followup"),  # 即时控制的提示/答案/点评（里程碑2）
                turn_scores=scores, turn_index=turn_index,
            )

        status = values.get("status", "settled")
        if status == "escalated":
            return MockOutput(
                session_id=session_id, status="escalate",
                escalate_action=values.get("action"),
                handoff_summary=values.get("handoff_summary"),
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

    def handoff_summary(self, session_id: str) -> str:
        """读当前会话状态，拼一句交接摘要（escalate 时交回常规链路，§6b）。"""
        from .handoff import build_handoff_summary

        config = {"configurable": {"thread_id": session_id}}
        try:
            values = self.graph.get_state(config).values or {}
        except Exception:
            values = {}
        return build_handoff_summary(values)

    # mastery 更新交给 Manager（唯一写者）——供结算后读取。
    def mastery_updates(self, session_id: str) -> list:
        config = {"configurable": {"thread_id": session_id}}
        try:
            return self.graph.get_state(config).values.get("mastery_updates") or []
        except Exception:
            return []
