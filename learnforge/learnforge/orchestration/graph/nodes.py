"""主图节点（Phase 3：Manager plan-and-execute，支持复合 DAG）。

对应 Design §2b/§5.6：PLAN → EXECUTE(按依赖序派发，含 replan) → AGGREGATE。
复合任务"诊断→改路径→建议 mock"由 ManagerAgent.execute 承载（Design §5.6）。
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
    # 记忆面板入口：本轮读取稳定记忆 + 会话短期记忆，并处理用户"记住…"受控写入（§3.1/§4）。
    convo = _mgr().begin_memory_turn(
        state.get("user_input", ""), state.get("session_id")
    ) or state.get("conversation_summary")
    # 强意图走确定性计划；其余交给 manager_execute 里的动态规划（decide_next 循环）。
    plan, is_dynamic = _mgr().plan_or_dynamic(
        state.get("user_input", ""), state.get("active_mock_session_id")
    )
    return {
        "plan": plan,
        "responses": [],
        "replan_count": state.get("replan_count", 0),
        "conversation_summary": convo,
        "_dynamic": is_dynamic,
    }


def manager_execute(state: MainState) -> Dict[str, Any]:
    plan = state.get("plan") or []
    user_input = state.get("user_input", "")
    trace_id = state.get("trace_id")
    if state.get("_dynamic"):
        # Manager 动态规划：每步看子 agent 结果决定下一步，直到 finish/预算。
        responses, meta, plan = _mgr().execute_dynamic(user_input, trace_id=trace_id)
        replan_count = 0
    elif state.get("active_mock_session_id"):
        responses, meta = _mgr().execute(plan, user_input, trace_id=trace_id)
        replan_count = 0
    else:
        plan, responses, meta, replan_count = _mgr().execute_with_replan(
            plan, user_input, trace_id=trace_id
        )
    return {
        "plan": plan,
        "responses": [r.model_dump() for r in responses],
        "replan_count": replan_count,
        "_meta": meta,
    }


def manager_aggregate(state: MainState) -> Dict[str, Any]:
    raw = state.get("responses") or []
    responses: List[ResponsePayload] = [ResponsePayload(**r) for r in raw]
    agg = _mgr().aggregate(responses, state.get("plan"), state.get("_meta"))
    # 短期记忆：本轮收尾追加原文轮 + 溢出压缩（供下一轮 manager_plan 载入）。best-effort。
    _mgr().record_turn(
        state.get("session_id"),
        state.get("user_input", ""),
        agg.get("reply_text") or "",
        state.get("active_mock_session_id"),
    )
    return {
        "reply_text": agg.get("reply_text"),
        "citations": agg.get("citations", []),
        "next_actions": agg.get("next_actions", []),
        "status": agg.get("status"),
    }
