"""各 agent 的 input/output Pydantic 契约（Design §3）。"""

from .diagnosis import Cluster, DiagnosisInput, DiagnosisResult, WeakAtom
from .mock import (
    CoachInput,
    CoachOutput,
    CoachReport,
    InterviewerInput,
    InterviewerOutput,
    JudgeInput,
    JudgeOutput,
    MockInput,
    MockOutput,
    Score,
    ScoreDims,
    StrategistInput,
    StrategistOutput,
    Turn,
    Weakness,
)
from .planning import PathDiff, PathItem, PlanningInput, PlanningOutput, ReorderOp
from .qa import (
    Citation,
    Claim,
    FlaggedClaim,
    QAInput,
    QAOutput,
    RouterInput,
    RouterOutput,
    SynthesizerInput,
    SynthesizerOutput,
    VerifierInput,
    VerifierOutput,
)
from .retrieval import Chunk, RetrievalFilters, RetrievalInput, RetrievalOutput

__all__ = [
    # retrieval
    "Chunk",
    "RetrievalFilters",
    "RetrievalInput",
    "RetrievalOutput",
    # qa
    "Citation",
    "Claim",
    "FlaggedClaim",
    "QAInput",
    "QAOutput",
    "RouterInput",
    "RouterOutput",
    "SynthesizerInput",
    "SynthesizerOutput",
    "VerifierInput",
    "VerifierOutput",
    # planning
    "PathDiff",
    "PathItem",
    "PlanningInput",
    "PlanningOutput",
    "ReorderOp",
    # mock
    "CoachInput",
    "CoachOutput",
    "CoachReport",
    "InterviewerInput",
    "InterviewerOutput",
    "JudgeInput",
    "JudgeOutput",
    "MockInput",
    "MockOutput",
    "Score",
    "ScoreDims",
    "StrategistInput",
    "StrategistOutput",
    "Turn",
    "Weakness",
    # diagnosis
    "Cluster",
    "DiagnosisInput",
    "DiagnosisResult",
    "WeakAtom",
]
