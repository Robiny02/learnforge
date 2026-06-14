"""主图节点（Manager ReAct 编排）。

对应 Design §2b/§5.6：PLAN(仅做记忆读取) → EXECUTE(ReAct 循环：每步看子 agent 结果决策) → AGGREGATE。
复合任务"诊断→改路径→建议 mock"由 ReAct 循环自然续跑承载（Design §5.6），不预拆 DAG。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..manager import ManagerAgent
from ...contracts.message import ResponsePayload
from ...contracts.state import MainState

N_PLAN = "manager_plan"
N_EXECUTE = "manager_execute"
N_AGGREGATE = "manager_aggregate"

_manager: ManagerAgent = None  # 懒加载单例


def _mgr() -> ManagerAgent:
    global _manager
    if _manager is None:
        _manager = ManagerAgent()
    return _manager


def manager_plan(state: MainState) -> Dict[str, Any]:
    # ReAct 不预拆 DAG：本节点只做记忆读取（稳定记忆 + 会话短期记忆 + 用户"记住…"受控写入，§3.1/§4），
    # 真正的逐步路由在 manager_execute 的 ReAct 循环里。
    convo = _mgr().begin_memory_turn(
        state.get("user_input", ""), state.get("session_id")
    ) or state.get("conversation_summary")
    return {
        "plan": [],
        "responses": [],
        "replan_count": 0,
        "conversation_summary": convo,
    }


def manager_execute(state: MainState) -> Dict[str, Any]:
    user_input = state.get("user_input", "")
    trace_id = state.get("trace_id")
    active_mock = state.get("active_mock_session_id")
    if active_mock:
        # 会话锚定在进行中的 mock → 直接续跑（多轮 interrupt/resume 在 mock 子图内）。
        responses, meta, plan = _mgr().run_active_mock(user_input, active_mock, trace_id)
    else:
        # Manager ReAct 编排：每步看子 agent 结果决定下一步，直到 finish/预算。
        # 阶段一：把 session_id 传下去，让 execute_dynamic 装配有界会话上下文注入子 agent prompt。
        responses, meta, plan = _mgr().execute_dynamic(
            user_input, trace_id=trace_id, session_id=state.get("session_id"))
    return {
        "plan": plan,
        "responses": [r.model_dump() for r in responses],
        "replan_count": 0,
        "_meta": meta,
    }


def manager_aggregate(state: MainState) -> Dict[str, Any]:
    raw = state.get("responses") or []
    responses: List[ResponsePayload] = [ResponsePayload(**r) for r in raw]
    agg = _mgr().aggregate(responses, state.get("plan"), state.get("_meta"))
    # 短期记忆：本轮收尾追加原文轮 + 会话级 token-阈值 compaction（供下一轮 manager_plan 载入）。
    # 重要结果（带引用答案 / 诊断·规划·结算产出）标 important → 进 pinned 永不压缩。best-effort。
    _mgr().record_turn(
        state.get("session_id"),
        state.get("user_input", ""),
        agg.get("reply_text") or "",
        state.get("active_mock_session_id"),
        important=_mgr().turn_is_important(agg.get("citations"), state.get("plan")),
    )
    return {
        "reply_text": agg.get("reply_text"),
        "citations": agg.get("citations", []),
        "next_actions": agg.get("next_actions", []),
        "status": agg.get("status"),
        "image_url": agg.get("image_url"),
        "image_spec": agg.get("image_spec"),
    }
