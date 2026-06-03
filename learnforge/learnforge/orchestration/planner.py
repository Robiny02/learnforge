"""复合任务的 plan-as-tool-calls（借鉴 Reactor 的 planning 工具：create/mark_step）。

把"复合任务"从写死的 `handoffs=[diagnosis, planning]` 升级成一个**显式、可检视、可标记**的
计划对象：LLM 先 create 一个有序步骤列表（每步选一个子 agent + 子任务），执行器逐步 dispatch、
完成即 mark_step=completed。无 key / LLM 失败 → 确定性兜底（已知"准备面试"= diagnosis→planning）。

只产出计划与状态，不直接写状态——执行仍走 Manager 的 dispatch/_apply_step（唯一写者不变）。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from ..contracts.enums import ModelTier

_AGENTS = ("qa", "planning", "diagnosis", "mock")


class PlanStep(BaseModel):
    agent: str                       # qa | planning | diagnosis | mock
    task: str = ""                   # 该步的子任务描述（缺省用原始 query）
    status: str = "not_started"      # not_started | completed


class Plan(BaseModel):
    steps: List[PlanStep] = Field(default_factory=list)


class _PlanDraft(BaseModel):
    """LLM create 的产物：有序的子 agent 名列表（单列表 schema，便宜模型更稳）。"""

    steps: List[str] = Field(default_factory=list)


# 已知复合「准备面试」的标准拆解（诊断 → 据此改计划），确定可回归。
_COMPOSITE_PLAN = [
    PlanStep(agent="diagnosis", task="先只读诊断薄弱点"),
    PlanStep(agent="planning", task="据诊断结果调整学习路径"),
]


def build_plan(user_input: str, wants_plan: bool, llm=None) -> Plan:
    """产出执行计划。

    - 已知复合（wants_plan，如"准备面试"）→ 确定性 diagnosis→planning（正确且可回归）；
    - 否则若 LLM 可用 → 让 LLM create 一个有序计划（plan-as-tool-calls）；
    - 兜底 → 单步（关键词选 agent 或 qa）。
    """
    if wants_plan:
        return Plan(steps=[s.model_copy() for s in _COMPOSITE_PLAN])

    drafted = _llm_create(user_input, llm) if llm is not None else None
    if drafted:
        return Plan(steps=[PlanStep(agent=a, task=user_input) for a in drafted])

    return Plan(steps=[PlanStep(agent=_keyword_agent(user_input), task=user_input)])


def _keyword_agent(user_input: str) -> str:
    text = (user_input or "").lower()
    table = {
        "mock": ("面试", "mock", "模拟", "出题", "考"),
        "diagnosis": ("诊断", "弱点", "薄弱", "复盘"),
        "planning": ("计划", "规划", "路径", "安排"),
    }
    for agent, kws in table.items():
        if any(k in text for k in kws):
            return agent
    return "qa"


def _llm_create(user_input: str, llm) -> Optional[List[str]]:
    """LLM create：把请求拆成有序子 agent 列表。非法/失败 → None。"""
    if not getattr(llm, "available", False):
        return None
    prompt = (
        "把用户请求拆成完成它所需的有序步骤，每步只选一个子 agent（按执行先后）：\n"
        "- diagnosis=找薄弱点；planning=排/调学习计划；mock=模拟面试出题；qa=答概念问题。\n"
        "常见：先 diagnosis 再 planning。只输出 steps（agent 名的有序数组），别加多余步骤。\n\n"
        f"用户请求：{(user_input or '').strip()}"
    )
    try:
        obj, _ = llm.complete_structured(
            prompt, _PlanDraft, model_tier=ModelTier.HAIKU,
            system="你是任务规划器，只输出 JSON。", max_tokens=120, timeout_s=20.0,
        )
    except Exception:  # noqa: BLE001 - 规划失败不阻断，交兜底
        return None
    steps: List[str] = []
    for a in obj.steps or []:
        a = (a or "").strip().lower()
        if a in _AGENTS and a not in steps:   # 白名单 + 去重
            steps.append(a)
    return steps[:4] or None
