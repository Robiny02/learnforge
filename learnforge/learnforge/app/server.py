"""FastAPI 入口（Phase 3）——/qa /plan /mock /diagnose（Tasks T-M1-15 / T-M2-07 / T-M3-08 / T-M4）。

- /qa /plan /diagnose：经主图 plan→execute→aggregate 走一遍（Design §2b）。
- /mock：多轮 interrupt/resume——首请求开场出题，带 user_answer/user_interrupt 续跑；
  出复盘(status=review)时自动结算（提交掌握度 + post_mock 诊断，Q5/§5.5）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel

try:
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
except Exception:  # pragma: no cover - FastAPI 可选
    FastAPI = None  # type: ignore
    FileResponse = None  # type: ignore
    StaticFiles = None  # type: ignore

from ..agents.manager import ManagerAgent
from ..contracts.agents.mock import MockInput
from ..graph.main_graph import compile_main_graph

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


class UIChatRequest(BaseModel):
    text: str
    mode: str = "qa"
    session_id: str = "ui-default"


class UIFileRequest(BaseModel):
    path: str


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

    # 启动时连接外部 MCP server（LF_MCP_SERVERS 配置；未配置=零开销 no-op）。
    try:
        from ..tools.mcp import bootstrap_mcp
        _mcp_tools = bootstrap_mcp()
        if _mcp_tools:
            print(f"[mcp] 已接入外部 MCP 工具: {_mcp_tools}")
    except Exception as _e:  # noqa: BLE001
        print(f"[mcp] bootstrap 跳过: {_e}")

    _UI_DIR = Path(__file__).resolve().parent / "ui"
    if _UI_DIR.exists() and StaticFiles is not None:
        api.mount("/ui-static", StaticFiles(directory=str(_UI_DIR)), name="ui-static")

    @api.get("/")
    def root():
        return {"name": "LearnForge", "ui": "/ui"}

    @api.get("/ui")
    def ui_index():
        return FileResponse(str(_UI_DIR / "index.html"))

    @api.get("/ui/files")
    def ui_files():
        roots = [
            ("reports", Path(__file__).resolve().parents[2] / "docs" / "reports"),
            ("memory", Path(__file__).resolve().parents[2] / "data" / "memory"),
        ]
        files = []
        for kind, root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.md"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append({
                    "kind": kind,
                    "name": path.name,
                    "path": str(path),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })
        files.sort(key=lambda x: x["mtime"], reverse=True)
        return {"files": files[:100]}

    @api.post("/ui/files/read")
    def ui_file_read(req: UIFileRequest):
        path = Path(req.path).resolve()
        allowed_roots = [
            (Path(__file__).resolve().parents[2] / "docs" / "reports").resolve(),
            (Path(__file__).resolve().parents[2] / "data" / "memory").resolve(),
        ]
        if not any(path == root or root in path.parents for root in allowed_roots):
            return {"error": "path not allowed"}
        if not path.exists() or path.suffix.lower() != ".md":
            return {"error": "file not found"}
        return {"path": str(path), "content": path.read_text(encoding="utf-8")[:50000]}

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

    @api.post("/ui/chat")
    def ui_chat(req: UIChatRequest):
        mode = (req.mode or "qa").lower()
        text = (req.text or "").strip()
        if not text:
            text = "帮我看看今天应该复习什么"
        if mode == "plan":
            return _invoke(f"生成学习计划: {text}", req.session_id)
        if mode == "diagnose":
            return _invoke(text if "诊断" in text else f"诊断我的弱点：{text}", req.session_id)
        if mode == "mock":
            mgr = _mgr()
            out = mgr.mock.run(MockInput(topic=text, session_id=req.session_id))
            return {"status": "ok", "reply_text": out.question or out.status,
                    "mock": out.model_dump(), "plan": [{"agent": "mock"}], "next_actions": []}
        if mode == "note":
            return _invoke(f"生成一份美观、结构化、可复习的学习笔记：{text}", req.session_id)
        return _invoke(text, req.session_id)
