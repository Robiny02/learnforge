"""全局枚举（Phase 1 先定死，后续所有 agent 共用）。

对应 Design §3 / §4。所有枚举继承 str 以便直接 JSON 序列化与 SQLite 存储。
"""

from enum import Enum


class AgentId(str, Enum):
    """13 个 agent 的稳定标识（Design §2a 职责矩阵）。"""

    MANAGER = "manager"
    QA = "qa"
    ROUTER = "router"
    SYNTHESIZER = "synthesizer"
    VERIFIER = "verifier"
    RETRIEVAL = "retrieval"
    PLANNING = "planning"
    MOCK = "mock"
    INTERVIEWER = "interviewer"
    JUDGE = "judge"
    STRATEGIST = "strategist"
    COACH = "coach"
    DIAGNOSIS = "diagnosis"


class MsgType(str, Enum):
    """AgentMessage 信封类型（Design §4a）。"""

    TASK = "task"
    RESPONSE = "response"
    EVENT = "event"


class Status(str, Enum):
    """ResponsePayload 状态（Design §4a / §8）。"""

    OK = "ok"
    ERROR = "error"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    ESCALATE = "escalate"


class QType(str, Enum):
    """RouterAgent 问题类型（Design §3.3）。"""

    CONCEPT = "concept"
    PROJECT = "project"
    TEMPORAL = "temporal"
    CHITCHAT = "chitchat"


class Verdict(str, Enum):
    """VerifierAgent / QAAgent 事实把关结论（Design §3.5）。"""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    UNCERTAIN = "uncertain"


class StrategistAction(str, Enum):
    """StrategistAgent 决策动作（Design §3.11 / Q4 中断边界）。"""

    CONTINUE = "continue"
    RAISE = "raise"
    LOWER = "lower"
    SWITCH_TOPIC = "switch_topic"
    PAUSE = "pause"
    END = "end"
    ESCALATE = "escalate"


class PlanMode(str, Enum):
    """PlanningAgent 模式（Design §3.7，生成+修改合并）。"""

    GENERATE = "generate"
    MODIFY = "modify"


class MasteryTier(str, Enum):
    """Knowledge Atom 掌握度分档（Design §4d，Q1 离散映射）。"""

    UNKNOWN = "unknown"
    LEARNING = "learning"
    FAMILIAR = "familiar"
    PROFICIENT = "proficient"
    MASTERED = "mastered"


class EventType(str, Enum):
    """interaction_events 事件类型（Design §4a / §4c）。"""

    QA_SIGNAL = "qa_signal"
    MOCK_WEAKNESS = "mock_weakness"
    PATH_CHANGED = "path_changed"


class SourceType(str, Enum):
    """检索语料来源类型（Design §7c）。"""

    INTERVIEW_POST = "interview_post"
    BLOG = "blog"
    DOC = "doc"
    ATOM = "atom"


class TimeWindow(str, Enum):
    """DiagnosisAgent 聚合时间窗（Design §3.13）。"""

    D7 = "7d"
    D30 = "30d"
    ALL = "all"


class DiagnosisTrigger(str, Enum):
    """诊断触发来源（Design §5.5，Q5 自动触发）。"""

    USER = "user"
    POST_MOCK = "post_mock"
    COMPOSITE = "composite"


class RetrievalBackend(str, Enum):
    """RetrievalAgent 可插拔后端（Design §3.6 / §10a G1）。"""

    LOCAL = "local"
    CLOUD = "cloud"


class ModelTier(str, Enum):
    """模型分级（Proposal §2 成本目标：Haiku 高频小推理 / Sonnet 合成）。"""

    HAIKU = "haiku"
    SONNET = "sonnet"
