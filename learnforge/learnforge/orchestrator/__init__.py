"""干净重建轨道：ManagerAgent 从基础路由逐 phase 升级为协作 orchestrator。

与旧的 learnforge.agents.* 完全隔离，旧系统留作 Phase 5 再融合的 fallback。

- Phase 1：基础路由（router.QARouter / manager.ManagerAgent.decide）。
- Phase 2：结构化任务分发（tasks.Task/AgentResult/TaskBoard + executors + manager.handle）。
"""

from .executors import (
    DEFAULT_EXECUTORS,
    executors_with_real_diagnosis,
    real_diagnosis_executor,
)
from .manager import DispatchDecision, ManagerAgent
from .router import QARouter, RouteResult
from .tasks import AgentResult, FollowupHint, Task, TaskBoard, TaskStatus

__all__ = [
    "ManagerAgent",
    "DispatchDecision",
    "QARouter",
    "RouteResult",
    "Task",
    "AgentResult",
    "FollowupHint",
    "TaskBoard",
    "TaskStatus",
    "DEFAULT_EXECUTORS",
    "real_diagnosis_executor",
    "executors_with_real_diagnosis",
]
