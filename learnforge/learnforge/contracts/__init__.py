"""LearnForge 数据契约层（Phase 1 核心，先于一切实现）。

包含：消息信封、ContextEnvelope、Knowledge Atom、共享状态/主图 State、各 agent in/out。
"""

from . import agents, enums
from .atom import AtomBrief, KnowledgeAtom
from .message import (
    AgentMessage,
    Budget,
    ContextEnvelope,
    ErrorInfo,
    EventPayload,
    ResponsePayload,
    TaskPayload,
    TokenUsage,
)
from .handlers import (
    CapabilityHandler,
    EvidenceProviderHandler,
    ToolHandler,
)
from .result import (
    AgentResult,
    ArtifactKind,
    ArtifactRef,
    NextRequest,
    NextRequestKind,
)
from .state import LearningPath, MainState, MockState, UserProfile

__all__ = [
    "agents",
    "enums",
    "AtomBrief",
    "KnowledgeAtom",
    "AgentMessage",
    "Budget",
    "ContextEnvelope",
    "ErrorInfo",
    "EventPayload",
    "ResponsePayload",
    "TaskPayload",
    "TokenUsage",
    "AgentResult",
    "ArtifactKind",
    "ArtifactRef",
    "NextRequest",
    "NextRequestKind",
    "CapabilityHandler",
    "EvidenceProviderHandler",
    "ToolHandler",
    "LearningPath",
    "MainState",
    "MockState",
    "UserProfile",
]
