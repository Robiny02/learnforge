"""FastAPI 入口（Phase 3）——/qa /plan /mock /diagnose（Tasks T-M1-15 / T-M2-07 / T-M3-08 / T-M4）。

- /qa /plan /diagnose：经主图 plan→execute→aggregate 走一遍（Design §2b）。
- /mock：多轮 interrupt/resume——首请求开场出题，带 user_answer/user_interrupt 续跑；
  出复盘(status=review)时自动结算（提交掌握度 + post_mock 诊断，Q5/§5.5）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

try:
    from fastapi import FastAPI
except Exception:  # pragma: no cover - FastAPI 可选
    FastAPI = None  # type: ignore

from .agents.manager import ManagerAgent
from .contracts.agents.mock import MockInput
from .graph.main_graph import compile_main_graph

_app_graph = None
_manager: Optional[ManagerAgent] = None


def _graph():
    global _app_graph
    if _app_graph is None:
        _app_graph = compile_main_graph()
    return _app_graph


def _mgr() -> ManagerAgent:
    global _manager
    if _manager is None:
        _manager = ManagerAgent()
    return _manager


class QARequest(BaseModel):
    question: str
    session_id: str = "default"


class PlanRequest(BaseModel):
    goal: str
    session_id: str = "default"


class DiagnoseRequest(BaseModel):
    session_id: str = "default"


class MockRequest(BaseModel):
    topic: str
    session_id: str = "default"
    user_answer: Optional[str] = None
    user_interrupt: Optional[str] = None


def _invoke(user_input: str, session_id: str) -> dict:
    return _graph().invoke(
        {
            "user_input": user_input,
            "session_id": session_id,
            "trace_id": f"t-{session_id}",
            "replan_count": 0,
        }
    )


if FastAPI is not None:
    api = FastAPI(title="LearnForge (MVP skeleton)")

    @api.post("/qa")
    def qa(req: QARequest):
        return _invoke(req.question, req.session_id)

    @api.post("/plan")
    def plan(req: PlanRequest):
        return _invoke(f"生成学习计划: {req.goal}", req.session_id)

    @api.post("/diagnose")
    def diagnose(req: DiagnoseRequest):
        return _invoke("诊断我的弱点", req.session_id)

    @api.post("/mock")
    def mock(req: MockRequest):
        """多轮 mock：首请求(无 user_answer/interrupt)开场，其后逐轮 resume。"""
        mgr = _mgr()
        out = mgr.mock.run(
            MockInput(
                topic=req.topic,
                session_id=req.session_id,
                user_answer=req.user_answer,
                user_interrupt=req.user_interrupt,
            )
        )
        body = out.model_dump()
        # 出复盘即结算（唯一写者提交掌握度 + post_mock 自动诊断，Q5）。
        if out.status == "review":
            body["settlement"] = mgr.settle_mock(req.session_id)
        return body
