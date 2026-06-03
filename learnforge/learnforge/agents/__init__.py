"""13 个 agent 实现（Phase 1 为 stub，返回占位数据）。

层级（Design §2b）：
  Manager(0) → {QA, Planning, Mock, Diagnosis}(1) ；QA/Mock 各下挂 sub-agent ；
  Retrieval 为横切共享子能力。
"""

from typing import TYPE_CHECKING

from .diagnosis import DiagnosisAgent
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

if TYPE_CHECKING:  # 仅类型检查期可见，不参与运行时导入。
    from .manager import ManagerAgent


def __getattr__(name: str):
    """ManagerAgent 延迟导入（PEP 562），打破包级 import 环。

    ManagerAgent 经 `orchestration.manager` 实现，而后者又依赖本包的 worker agents
    （agents.base/diagnosis/mock/planning/qa），构成 agents ↔ orchestration.manager 的循环。
    冷导入 orchestration.manager 时若此处即时拉 shim，会在 orchestration.manager 尚未定义
    NextStep/ManagerAgent 时回环失败。改为按需加载：只有真正访问
    learnforge.agents.ManagerAgent 时才导入 shim，此时 orchestration.manager 已就绪。
    """
    if name == "ManagerAgent":
        from .manager import ManagerAgent

        return ManagerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
