"""Agent 间消息协议（Design §4a，Proposal §5 决策 7）。

强类型 `task / context / response` 三段式信封，是 hierarchical 可调试性的基础：
每条消息可校验、可日志（agent_traces）、可单测。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field

from .enums import AgentId, EventType, MsgType, Status


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContextEnvelope(BaseModel):
    """上下文隔离核心（Proposal §5 决策 8，Design §6b）。

    默认隔离、按需放行：每个 agent 只拿到完成任务所需的最小切片 + 父级摘要。
    """

    global_static_ref: str = Field(
        default="constitution@1.0",
        description="全局静态上下文的版本化引用（宪法/分类法/当前日期），不内联。",
    )
    handoff_summary: str = Field(
        default="",
        description="父级上下文摘要，≤512 tok，由 Haiku 生成（Design §6b，Q2）。",
    )
    scoped_slice: Dict = Field(
        default_factory=dict,
        description="本任务专属作用域切片（如检索片段、相关 atom brief、诊断结论）。",
    )


class Budget(BaseModel):
    """单条 task 的资源预算（对齐 Design §3 各 agent Bounds）。"""

    max_cost_usd: float = Field(..., description="本次任务成本上限（USD）。")
    timeout_s: float = Field(..., description="超时阈值（秒），超时抛 AgentTimeout。")
    max_iterations: int = Field(default=1, description="允许的内部迭代次数。")


class TaskPayload(BaseModel):
    """msg_type=task 的载荷（Manager→Worker / Worker→sub-agent 派发）。"""

    task_type: str = Field(..., description="任务类型，如 'qa' / 'plan.generate' / 'diagnose'。")
    instruction: str = Field(..., description="自然语言指令（不替 worker 思考答案）。")
    params: Dict = Field(default_factory=dict, description="结构化参数（版本化松散载荷区，缓解 R2）。")
    budget: Budget = Field(..., description="成本/超时/迭代预算。")


class TokenUsage(BaseModel):
    """token 计量（Proposal §7 责任归属 / 成本监控）。"""

    prompt: int = 0
    completion: int = 0


class ErrorInfo(BaseModel):
    code: str
    message: str


class ResponsePayload(BaseModel):
    """msg_type=response 的载荷（worker 回收结果，Manager 据此判定 replan）。"""

    status: Status = Field(..., description="ok/error/partial/needs_input/escalate。")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度，<0.4 触发 Manager replan。")
    result: Dict = Field(default_factory=dict, description="结构化结果（各 agent 的 *Output 序列化）。")
    reason: str = Field(
        default="",
        description="子工具给 Manager 的简短「为什么」（如 degraded/置信/覆盖/缺口）——供 replan 决策"
                    "看到结论背后的依据，不只看结构化结论；不外传给用户。",
    )
    cost_usd: float = Field(default=0.0, description="本次调用累计成本。")
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    error: Optional[ErrorInfo] = None


class EventPayload(BaseModel):
    """msg_type=event 的载荷（弱点信号异步写入 interaction_events，Proposal §4c）。"""

    event_type: EventType
    topic: str
    atom_refs: List[str] = Field(default_factory=list)
    signal: Dict = Field(default_factory=dict)


class AgentMessage(BaseModel):
    """统一信封（Design §4a）。

    payload 为 task/response/event 三型之一；同一用户请求全链路共享 trace_id。
    """

    message_id: str = Field(default_factory=_new_id)
    trace_id: str = Field(..., description="同一用户请求全链路共享（Proposal §7）。")
    parent_message_id: Optional[str] = None
    sender: AgentId
    recipient: AgentId
    msg_type: MsgType
    payload: Union[TaskPayload, ResponsePayload, EventPayload]
    context: ContextEnvelope = Field(default_factory=ContextEnvelope)
    created_at: datetime = Field(default_factory=_now)
    schema_version: str = "1.0"
