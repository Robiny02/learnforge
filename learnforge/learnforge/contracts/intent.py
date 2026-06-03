"""统一意图契约：槽位填充 + 主动澄清（替代碎在三处的关键词路由）。

历史上意图识别碎成三处：顶层 `orchestrator/router.py`（关键词→agent，只抽 topic/deadline）、
入口 `app/server.py`（`_wants_start_mock`/`_extract_mock_topic` 暴力剥词）、面试内
`agents/mock/intent.py`（五分类）。三者互不相通，且面试上下文（岗位/JD/简历/项目）从不
从自然语言抽取——必须调用方手填，导致"我面 RAG 算法实习，拿我项目拷打我"退化成"综合技术面试"。

本模块给出**单一结构化意图帧** `IntentFrame`：capability（调哪个能力）+ action（能力内子意图，
面试最细）+ slots（类型化槽位）+ missing_required/clarification（缺关键槽位时的一句澄清）。
`intent/` 包的 resolver/slots/clarify 负责填它；server/cli/manager 消费它。

所有枚举继承 str 以便 JSON/SQLite 序列化（与 `contracts/enums.py` 一致）。
Python 3.9 兼容：用 typing.Optional/List/Dict，不用 `X | None`。
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .agents.mock import InterviewContext


class Capability(str, Enum):
    """顶层能力（决定调哪个域 worker）。COMPOSITE = 跨能力复合（如"准备面试"）。"""

    QA = "qa"
    PLANNING = "planning"
    DIAGNOSIS = "diagnosis"
    MOCK = "mock"
    COMPOSITE = "composite"


class MockAction(str, Enum):
    """面试能力内的子意图（开场 + 面试内回合控制）。

    里程碑 1 已接通（经 user_interrupt → StrategistAction，无需改图）：
      START/ANSWER/SIDE/PAUSE/EXIT/ESCALATE/SWITCH_TOPIC/SET_DIFFICULTY。
    里程碑 2 需新图支持（不消耗轮次的即时控制）：
      SKIP/HINT/REPEAT/REVEAL/REDO/FEEDBACK。
    """

    START = "start"            # 开一场新面试（槽位决定针对性）
    ANSWER = "answer"          # 在回答当前题
    SIDE = "side"              # 插问一个无关知识点（不消耗轮次）
    PAUSE = "pause"            # 暂停
    EXIT = "exit"              # 结束本场（需二次确认）
    ESCALATE = "escalate"      # 转去诊断/改计划等跨能力
    SWITCH_TOPIC = "switch_topic"      # 换个话题问
    SET_DIFFICULTY = "set_difficulty"  # 调难度（太难/再难点）
    # --- 里程碑 2 ---
    SKIP = "skip"              # 跳过这题/不会，下一题
    HINT = "hint"              # 给点提示
    REPEAT = "repeat"          # 没听懂/再说一遍/这题啥意思
    REVEAL = "reveal"          # 直接告诉我答案
    REDO = "redo"              # 重答上一题
    FEEDBACK = "feedback"      # 我刚才答得怎么样


class MockSlots(BaseModel):
    """面试能力的深槽位（开场针对性 + 配置）。全可选——缺省退回原"纯主题"行为。

    与 `InterviewContext` 同源：`to_interview_context()` 直接映射，复用证据式拷打链路，
    不重造。`difficulty`/`max_turns` 是 MockInput 的配置，不进 InterviewContext。
    """

    topic: Optional[str] = None
    target_role: Optional[str] = None
    jd_text: Optional[str] = None
    role_type: Optional[str] = None
    resume_claims: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)
    focus: List[str] = Field(default_factory=list)
    difficulty: Optional[int] = Field(default=None, ge=1, le=5)
    max_turns: Optional[int] = Field(default=None, ge=1, le=30)

    def to_interview_context(self) -> Optional[InterviewContext]:
        """映射成现有 InterviewContext；无任何岗位/JD/简历/项目信号时返回 None（退回纯主题）。"""
        if not (self.target_role or self.jd_text or self.role_type
                or self.resume_claims or self.projects):
            return None
        return InterviewContext(
            target_role=self.target_role,
            jd_text=self.jd_text,
            role_type=self.role_type,
            resume_claims=list(self.resume_claims),
            projects=list(self.projects),
        )


class PlanSlots(BaseModel):
    """规划能力的槽位。goal 与 deadline 皆空时需澄清。"""

    goal: Optional[str] = None
    deadline: Optional[str] = None
    topic: Optional[str] = None


class IntentFrame(BaseModel):
    """单条用户消息的结构化意图帧——意图层的唯一产物。

    `slots` 用 dict 承载（按 capability 解释为 MockSlots/PlanSlots），便于跨进程/前端回传。
    `clarification` 非空 ⇒ 缺关键槽位、应主动澄清一轮；`missing_required` 记缺了哪些键。
    `pending` 标记"这是一次澄清的续接帧"（resolver 把新消息当作缺失槽位的答案合并）。
    """

    capability: Capability = Capability.QA
    action: Optional[str] = None              # MockAction.value（mock 时）或 None
    slots: Dict[str, object] = Field(default_factory=dict)
    missing_required: List[str] = Field(default_factory=list)
    clarification: Optional[str] = None       # 非空 = 需主动澄清的一句问题
    pending_slot: Optional[str] = None        # 澄清在等哪个槽位（续接时用）
    confidence: float = 0.5
    handoffs: List[str] = Field(default_factory=list)   # 复合意图后续应转交的能力（有序，仅提示）
    use_retrieval: bool = False               # 是否建议走 RAG（提示，非命令）
    signals: List[str] = Field(default_factory=list)    # 命中的规则，供 trace/调试

    def mock_slots(self) -> MockSlots:
        """把 slots dict 解释成 MockSlots（容错：忽略未知键）。"""
        return MockSlots(**{k: v for k, v in self.slots.items()
                            if k in MockSlots.model_fields})

    def plan_slots(self) -> PlanSlots:
        return PlanSlots(**{k: v for k, v in self.slots.items()
                            if k in PlanSlots.model_fields})
