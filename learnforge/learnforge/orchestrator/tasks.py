"""任务板契约（Phase 2）。

定义最小、可解释的「结构化任务」与「结构化结果」契约，以及一个内存任务板。
Phase 2 只支持单任务分发，但契约本身为 Phase 3（多子任务串行协作）预留：
- Task 带 task_id / deps，TaskBoard 可容纳多条任务并查询状态。
- TaskStatus 已含 SKIPPED（Phase 3 的「诊断为空跳过 modify」等会用到）。

纯 dataclass，无 LLM / DB 依赖，确定性可单测。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Task:
    """ManagerAgent 派发给某个 agent 的一条结构化任务。

    字段对齐 Phase 2 要求：task_id / target_agent / user_goal / task_type /
    priority / status / input_context。deps 为 Phase 3 串行协作预留（本阶段恒为空）。
    """

    target_agent: str
    user_goal: str
    task_type: str
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    priority: int = 0  # 数字越小越优先
    status: TaskStatus = TaskStatus.PENDING
    input_context: Dict[str, Any] = field(default_factory=dict)
    deps: List[str] = field(default_factory=list)  # Phase 3 用
    result: Optional["AgentResult"] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "target_agent": self.target_agent,
            "user_goal": self.user_goal,
            "task_type": self.task_type,
            "priority": self.priority,
            "status": self.status.value,
            "input_context": self.input_context,
            "deps": self.deps,
        }


@dataclass
class FollowupHint:
    """机器可读的「下一步派发」提示（Phase 3）。

    next_actions 是给用户看的文本；followups 是其机器可读形态，由 Manager 消费来
    串行补派发后续任务。target_agent / task_type 决定派给谁、做什么；reason 供 trace 解释。
    """

    target_agent: str
    task_type: str
    reason: str = ""


@dataclass
class AgentResult:
    """agent 执行任务后返回给 Manager 的结构化结果。

    字段对齐 Phase 2 要求：result（answer/result）/ confidence / next_actions /
    need_followup / error / reason。
    Phase 3 新增 followups：next_actions 的机器可读形态，驱动串行补派发。
    """

    result: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    next_actions: List[str] = field(default_factory=list)
    need_followup: bool = False
    error: Optional[str] = None
    reason: str = ""
    followups: List[FollowupHint] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result": self.result,
            "confidence": self.confidence,
            "next_actions": self.next_actions,
            "need_followup": self.need_followup,
            "error": self.error,
            "reason": self.reason,
        }


class TaskBoard:
    """内存任务板：登记任务、按 id 取用、流转状态。

    Phase 2 通常只有一条任务；保留多条容量与查询接口给 Phase 3。
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}

    def add(self, task: Task) -> Task:
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def all(self) -> List[Task]:
        return list(self._tasks.values())

    def by_status(self, status: TaskStatus) -> List[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def set_status(self, task_id: str, status: TaskStatus) -> None:
        t = self._tasks.get(task_id)
        if t is not None:
            t.status = status
