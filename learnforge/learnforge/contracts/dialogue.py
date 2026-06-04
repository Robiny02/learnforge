"""会话对话账本契约：一个对话轮的结构化记录（替代进程内 _UI_CTX 的薄 dict）。

设计参考 Reactor 的 DialogueRun/ToolInvocation/ToolOutput——一条轮记录携带 role/content/
tool_calls/status 与产物索引，既供**意图分类**取紧凑投影（capability/topic/tool 名），也供
**主 agent**取完整上下文（含 artifacts 引用，如 path_id/mock_sid，可直接复用而不重查）。

持久化在 SQLite `dialogue_turns`（单一真值），server 侧另加进程内缓存做延迟优化——
不引入 Redis（单用户本地、单进程，SQLite 足矣）。

Python 3.9 兼容：用 typing.Optional/List/Dict，不用 `X | None`。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """一次工具/子 agent 调用记录（≈ Reactor ToolInvocation + ToolOutput 的精简）。"""

    agent: str                       # 被 dispatch 的子能力 / 真实工具名（qa|planning|diagnosis|mock|…）
    task: str = ""                   # 该步子任务描述
    status: str = "completed"        # pending | completed | failed


class DialogueTurn(BaseModel):
    """单个对话轮的完整记录。role=user 的轮带路由结果，role=assistant 的轮带产物。"""

    turn_id: str
    session_id: str
    seq: int
    role: str = "user"               # user | assistant | system
    text: str = ""

    capability: Optional[str] = None     # 路由到的能力（qa|planning|diagnosis|mock|note|composite）
    route_mode: Optional[str] = None     # single | multi
    topic: Optional[str] = None

    tool_calls: List[ToolCall] = Field(default_factory=list)
    artifacts: List[Dict[str, str]] = Field(default_factory=list)  # [{"kind":"path","ref":"<path_id>"}]

    status: str = "completed"            # completed | failed | needs_input
    trace_id: Optional[str] = None
    created_at: str = ""


class DialogueContext(BaseModel):
    """从最近若干轮派生出的会话上下文——意图分类与承接判断的输入。

    `_UI_CTX` 里那套主线粘住/aside 衰减逻辑收敛到这里：last_capability 只认实质任务轮
    （planning/diagnosis/mock），连续 aside（qa/note）≥ 衰减阈值才让主线失效。
    """

    history: List[Dict[str, object]] = Field(default_factory=list)  # [{text, capability, topic}]
    last_capability: Optional[str] = None
    last_topic: Optional[str] = None
    active_mock: bool = False
    pending_intent: Optional[Dict[str, object]] = None  # 上一轮挂起的澄清（续接用）

    def to_resolver_ctx(self) -> Dict[str, object]:
        """转成 IntentResolver/Dispatcher 消费的 ctx dict（保持既有键名兼容）。"""
        ctx: Dict[str, object] = {
            "history": list(self.history),
            "active_mock": self.active_mock,
        }
        if self.last_capability:
            ctx["last_capability"] = self.last_capability
        if self.last_topic:
            ctx["last_topic"] = self.last_topic
        if self.pending_intent:
            ctx["pending_intent"] = self.pending_intent
        return ctx
