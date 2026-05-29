"""13 个 agent 实现（Phase 1 为 stub，返回占位数据）。

层级（Design §2b）：
  Manager(0) → {QA, Planning, Mock, Diagnosis}(1) ；QA/Mock 各下挂 sub-agent ；
  Retrieval 为横切共享子能力。
"""

from .diagnosis import DiagnosisAgent
from .manager import ManagerAgent
from .mock import (
    CoachAgent,
    InterviewerAgent,
    JudgeAgent,
    MockInterviewAgent,
    StrategistAgent,
)
from .planning import PlanningAgent
from .qa import QAAgent, RouterAgent, SynthesizerAgent, VerifierAgent
from .retrieval import RetrievalAgent

__all__ = [
    "ManagerAgent",
    "QAAgent",
    "RouterAgent",
    "SynthesizerAgent",
    "VerifierAgent",
    "RetrievalAgent",
    "PlanningAgent",
    "MockInterviewAgent",
    "InterviewerAgent",
    "JudgeAgent",
    "StrategistAgent",
    "CoachAgent",
    "DiagnosisAgent",
]
