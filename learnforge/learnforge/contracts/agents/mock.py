"""Mock 子系统 input/output（Design §3.8-§3.12）。

子图编排 Interviewer → Judge → Strategist（中断决策）→ 终场 Coach。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from ..enums import StrategistAction
from ..message import EventPayload
from .retrieval import Chunk


class ScoreDims(BaseModel):
    correctness: int = Field(default=0, ge=0, le=5)
    depth: int = Field(default=0, ge=0, le=5)
    clarity: int = Field(default=0, ge=0, le=5)


class Score(BaseModel):
    """JudgeAgent 单轮评分（Design §3.10，0-5 档）。"""

    overall: Optional[int] = Field(default=None, ge=0, le=5, description="解析失败时为 None，不计入一致性。")
    dims: ScoreDims = Field(default_factory=ScoreDims)
    missed_points: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Turn(BaseModel):
    turn_index: int
    question: str
    user_answer: Optional[str] = None
    score: Optional[Score] = None


# --- InterviewerAgent (§3.9) ---
class InterviewerInput(BaseModel):
    topic: str
    difficulty: int = Field(ge=1, le=5)
    turn_history: List[Turn] = Field(default_factory=list, description="本场，≤近 6 轮。")
    retrieved: List[Chunk] = Field(default_factory=list)


class InterviewerOutput(BaseModel):
    question: str
    expected_points: List[str] = Field(default_factory=list)
    atom_refs: List[str] = Field(default_factory=list)


# --- JudgeAgent (§3.10) ---
class JudgeInput(BaseModel):
    question: str
    expected_points: List[str] = Field(default_factory=list)
    user_answer: str


# JudgeOutput 即 Score（Design §3.10）。
JudgeOutput = Score


# --- StrategistAgent (§3.11) ---
class StrategistInput(BaseModel):
    score_history: List[Score] = Field(default_factory=list)
    user_interrupt: Optional[str] = None
    # session_state 以 dict 传入避免与 state.MockState 循环依赖；运行期为 MockState 序列化。
    session_state: dict = Field(default_factory=dict)


class StrategistOutput(BaseModel):
    action: StrategistAction = StrategistAction.CONTINUE
    next_topic: Optional[str] = None
    next_difficulty: int = Field(default=3, ge=1, le=5)


# --- CoachAgent (§3.12) ---
class Weakness(BaseModel):
    topic: str
    evidence: str = Field(description="必带证据：引哪一轮。")


class CoachInput(BaseModel):
    turn_scores: List[Score] = Field(default_factory=list)
    topic_coverage: List[str] = Field(default_factory=list)


class CoachReport(BaseModel):
    summary: str = ""
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[Weakness] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)


class CoachOutput(BaseModel):
    report: CoachReport = Field(default_factory=CoachReport)
    events: List[EventPayload] = Field(default_factory=list)


# --- MockInterviewAgent 外壳 (§3.8) ---
class MockInput(BaseModel):
    topic: str
    session_id: Optional[str] = None  # 多轮中断/恢复的 checkpoint thread key
    target_difficulty: int = Field(default=3, ge=1, le=5)
    max_turns: int = 10
    user_answer: Optional[str] = None  # 每轮用户输入（None=开场首题）
    user_interrupt: Optional[str] = None  # 中断语（换topic/暂停/结束/改计划…）


class MockOutput(BaseModel):
    session_id: Optional[str] = None
    # status：active(待答题) | paused(暂停可 resume) | review(出复盘) | escalate(交回 Manager)
    status: str = "active"
    question: Optional[str] = None
    followup: Optional[str] = None
    review: Optional[CoachReport] = None
    turn_scores: List[Score] = Field(default_factory=list)
    turn_index: int = 0
    # escalate 时携带：跨子系统 handoff 摘要 + 触发动作（Design §6b / §5.4）。
    escalate_action: Optional[str] = None
    events: List[EventPayload] = Field(default_factory=list)
