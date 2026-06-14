"""Mock 子系统 input/output（Design §3.8-§3.12）。

子图编排 Interviewer → Judge → Strategist（中断决策）→ 终场 Coach。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from ..enums import StrategistAction
from ..message import EventPayload
from .retrieval import Chunk


class InterviewContext(BaseModel):
    """候选人材料 + 目标岗位上下文（接入 LLMInternSkill 的证据约束式拷打）。

    全部可选：缺省时 mock 退回原"纯主题"行为，保持向后兼容。
    role_type 为空时由 interview_skill.detect_role_type 从 jd/target_role 推断。
    """

    target_role: Optional[str] = None       # 目标岗位/方向，如 "RAG 算法实习"
    jd_text: Optional[str] = None           # 目标 JD 原文
    role_type: Optional[str] = None         # rag/agent/agentic-rl/...（空则推断）
    resume_claims: List[str] = Field(default_factory=list)  # 简历要点，每条一个 claim
    projects: List[str] = Field(default_factory=list)       # 项目简述


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
    # 回答风险标签：overclaim/no_evidence/vague（接入 LLMInternSkill 真实性边界）。
    risk_flags: List[str] = Field(default_factory=list)


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
    # 证据式追问上下文（接入 LLMInternSkill）：缺省时退回原始出题行为。
    context: Optional[InterviewContext] = None
    last_question: Optional[str] = None
    last_answer: Optional[str] = None
    turn_index: int = 0
    # 当前环节 + 出题模式（接入 tech-interview skill 三环节 + 证据式追问/简历诚信点破）。
    phase: Optional[str] = None      # basics | project | system_design（空 → 不约束环节）
    mode: str = "ask"                # ask（出新题）| followup（深挖上一答）| probe（简历诚信点破）


class InterviewerOutput(BaseModel):
    question: str
    expected_points: List[str] = Field(default_factory=list)
    atom_refs: List[str] = Field(default_factory=list)


# --- JudgeAgent (§3.10) ---
class JudgeInput(BaseModel):
    question: str
    expected_points: List[str] = Field(default_factory=list)
    user_answer: str
    role_type: Optional[str] = None  # 目标角色，影响风险判定口径（可选）


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


class AnswerCard(BaseModel):
    """高风险问题的更优回答建议（接入 LLMInternSkill answer-cards.md）。"""

    question: str
    why_risky: str = ""
    dangerous: str = ""  # 会被问穿的危险回答
    passable: str = ""   # 承认边界的及格回答
    strong: str = ""     # 补证据/取舍的强回答
    evidence_needed: List[str] = Field(default_factory=list)


class CoachInput(BaseModel):
    turn_scores: List[Score] = Field(default_factory=list)
    topic_coverage: List[str] = Field(default_factory=list)
    context: Optional[InterviewContext] = None
    turns: List[Turn] = Field(default_factory=list, description="带题面/回答的逐轮记录，供生成 answer card。")


class CoachReport(BaseModel):
    summary: str = ""
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[Weakness] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    # 针对高风险轮次的更优回答建议（接入 LLMInternSkill）。
    answer_cards: List[AnswerCard] = Field(default_factory=list)


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
    context: Optional[InterviewContext] = None  # 简历/项目/JD/岗位上下文（接入 LLMInternSkill）
    # 不消耗轮次的即时控制（里程碑2）：skip/hint/repeat/reveal/redo/feedback。
    # 与 user_answer/user_interrupt 互斥：置它即走控制节点 S_CONTROL，不评分、不推进。
    control_action: Optional[str] = None


class MockOutput(BaseModel):
    session_id: Optional[str] = None
    # status：active(待答题) | paused(暂停可 resume) | review(出复盘) | escalate(交回 Manager) | expired
    status: str = "active"
    question: Optional[str] = None
    followup: Optional[str] = None
    review: Optional[CoachReport] = None
    turn_scores: List[Score] = Field(default_factory=list)
    turn_index: int = 0
    # escalate 时携带：跨子系统 handoff 摘要 + 触发动作（Design §6b / §5.4）。
    escalate_action: Optional[str] = None
    handoff_summary: Optional[str] = None  # 交回常规链路的面试上下文（§6b）
    events: List[EventPayload] = Field(default_factory=list)
    # 当前环节 + 待办状态（供 UI/观测；向后兼容，可选）。
    phase: Optional[str] = None
    pending: Optional[str] = None  # answer | continue_decision | None


# --- MockState：跨 HTTP 调用持久化的整场面试状态（替代旧 LangGraph checkpointer，§5.4 重构） ---
class MockState(BaseModel):
    """整场模拟面试的全量状态，由 MockStateStore 序列化进 SQLite（thread key = session_id）。

    每个 HTTP 调用 = 跑一轮：load(state) → run_turn → save(state)。无 LangGraph replay。
    """

    session_id: str
    topic: str
    difficulty: int = Field(default=3, ge=1, le=5)
    max_turns: int = 10
    phase: str = "basics"            # InterviewPhase.value
    turn_index: int = 0              # 已答题轮数
    phase_question_count: int = 0    # 当前环节已出题数（达 MOCK_PHASE_QUESTIONS 推进环节）
    rounds_since_question: int = 0   # 连续"未推进出题"轮数（提示/复述/公布答案/跳过…），达阈值触发主动询问
    pending: Optional[str] = None    # answer | continue_decision | None
    status: str = "active"           # active | paused | review | escalated | settled | expired

    # 当前待答题
    current_question: Optional[str] = None
    current_expected_points: List[str] = Field(default_factory=list)
    current_atom_refs: List[str] = Field(default_factory=list)
    # 上一题快照（供 redo 回退）
    prev_question: Optional[str] = None
    prev_expected_points: List[str] = Field(default_factory=list)
    prev_atom_refs: List[str] = Field(default_factory=list)

    # 累积（switch_topic 不重置）
    turns: List[Turn] = Field(default_factory=list)
    turn_scores: List[Score] = Field(default_factory=list)
    topic_coverage: List[str] = Field(default_factory=list)
    answered_atom_refs: List[str] = Field(default_factory=list)

    context: Optional[InterviewContext] = None

    # 产出
    handoff_summary: Optional[str] = None
    review: Optional[CoachReport] = None
    events: List[EventPayload] = Field(default_factory=list)
    mastery_updates: List[dict] = Field(default_factory=list)
