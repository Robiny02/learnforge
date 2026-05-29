"""主图节点（Phase 3：Manager plan-and-execute，支持复合 DAG）。

对应 Design §2b/§5.6：PLAN → EXECUTE(按依赖序派发，含 replan) → AGGREGATE。
复合任务"诊断→改路径→建议 mock"由 ManagerAgent.execute 承载（Design §5.6）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..agents.manager import ManagerAgent
from ..contracts.message import ResponsePayload
from ..contracts.state import MainState

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
    plan = _mgr().make_plan(state.get("user_input", ""), state.get("active_mock_session_id"))
    return {"plan": plan, "responses": [], "replan_count": state.get("replan_count", 0)}


def manager_execute(state: MainState) -> Dict[str, Any]:
    plan = state.get("plan") or []
    user_input = state.get("user_input", "")
    trace_id = state.get("trace_id")
    if state.get("active_mock_session_id"):
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
    return {
        "reply_text": agg.get("reply_text"),
        "citations": agg.get("citations", []),
        "next_actions": agg.get("next_actions", []),
        "status": agg.get("status"),
    }
