"""共享状态 + LangGraph 主图 State（Design §4b）。

- 共享状态对象（UserProfile / LearningPath / MockState）落 SQLite，跨会话与多 agent。
- MainState 为 LangGraph 主图的运行态：用 TypedDict 以契合 LangGraph 的 reducer/partial-update 语义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from .agents.mock import Score
from .agents.planning import PathItem
from .agents.qa import Citation


# ---------------------------------------------------------------------------
# 共享状态（Shared State，Design §4b）
# ---------------------------------------------------------------------------
class UserProfile(BaseModel):
    user_id: str
    goals: List[str] = Field(default_factory=list)
    current_level: Optional[str] = None
    preferences: Dict = Field(default_factory=dict)


class LearningPath(BaseModel):
    path_id: str
    items: List[PathItem] = Field(default_factory=list)
    updated_at: Optional[datetime] = None


class MockState(BaseModel):
    """mock 子图 checkpoint 持久化状态（Design §4b / §5.4）。"""

    session_id: str
    topic: str
    difficulty: int = 3
    turn_index: int = 0
    max_turns: int = 10
    turn_scores: List[Score] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LangGraph 主图 State（Manager plan-and-execute 运行态，Design §2b / §3.1）
# ---------------------------------------------------------------------------
class MainState(TypedDict, total=False):
    # 入参
    user_input: str
    session_id: str
    trace_id: str
    conversation_summary: Optional[str]
    active_mock_session_id: Optional[str]

    # Manager 编排态
    plan: List[Dict[str, Any]]      # DAG 子任务列表（{agent, task_type, deps}）
    responses: List[Dict[str, Any]] # 各 worker 的 ResponsePayload（序列化）
    replan_count: int
    _meta: Dict[str, Any]           # 复合编排决策（skipped_modify / suggest_mock…）

    # 输出（对用户）
    reply_text: Optional[str]
    status: Optional[str]
    citations: List[Citation]
    next_actions: List[str]
