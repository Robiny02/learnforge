"""FastAPI 入口（Phase 3）——/qa /plan /mock /diagnose（Tasks T-M1-15 / T-M2-07 / T-M3-08 / T-M4）。

- /qa /plan /diagnose：经主图 plan→execute→aggregate 走一遍（Design §2b）。
- /mock：多轮 interrupt/resume——首请求开场出题，带 user_answer/user_interrupt 续跑；
  出复盘(status=review)时自动结算（提交掌握度 + post_mock 诊断，Q5/§5.5）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, List, Optional

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
from ..contracts.agents.qa import QAInput
from ..contracts.agents.mock import MockInput
from ..contracts.enums import Status
from ..contracts.intent import Capability, IntentFrame
from ..graph.main_graph import compile_main_graph
from ..intent import Dispatcher, IntentResolver
from ..agents.mock.actions import Step, run_plan
from ..intent import slots as S
from ..multimodal import ingest_attachments, parse_attachments
from ..contracts.attachment import flatten_attachments
from ..intent.slots import has_mock_signal as _has_mock_signal

_app_graph = None
_manager: Optional[ManagerAgent] = None
_intent_resolver: Optional[IntentResolver] = None
_intent_dispatcher: Optional[Dispatcher] = None


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


def _resolver() -> IntentResolver:
    """统一意图层（生产入口）。无注入档案材料 → 拷打类缺料时会主动索要简历/JD。"""
    global _intent_resolver
    if _intent_resolver is None:
        _intent_resolver = IntentResolver()
    return _intent_resolver


def _dispatcher() -> Dispatcher:
    """轻量意图分发器（单/多意图 → Route）。槽位/澄清下沉到节点，仅在选中能力时懒触发。"""
    global _intent_dispatcher
    if _intent_dispatcher is None:
        _intent_dispatcher = Dispatcher()
    return _intent_dispatcher


# 会话对话账本现以 SQLite `dialogue_turns` 为真值（替代旧的进程内 _UI_CTX dict）：
# 持久、可查、可 replay。承接上下文（last_capability + aside 衰减 + active_mock + last_topic）
# 由 DialogueTurnRepository.derive_context 从最近若干轮**实时派生**——单一真值，重启不丢。
# 单用户本地单进程，SQLite 已够；多实例/高并发再考虑加 Redis。
def _dialogue_repo():
    from ..storage.repositories import DialogueTurnRepository
    return DialogueTurnRepository(db_path=_mgr()._db_path)


def _ctx_load(session_id: str) -> dict:
    """从 dialogue_turns 派生承接上下文（供意图分发/承接借用上文）。失败 → 空（链路永远通）。"""
    try:
        return _dialogue_repo().derive_context(session_id)
    except Exception:
        return {}


def _ctx_record(session_id: str, text: str, body: dict) -> None:
    """把本轮路由结果落一行 dialogue_turns（capability/topic/tool_calls/artifacts）。

    主线粘住/aside 衰减不再在此维护——改由 derive_context 读时计算（见仓储）。best-effort。
    """
    from ..contracts.dialogue import DialogueTurn, ToolCall
    from ..intent.slots import extract_known_topic

    if not isinstance(body, dict):
        return
    plan = body.get("plan") if isinstance(body.get("plan"), list) else []
    plan = [p for p in plan if isinstance(p, dict)]
    cap = plan[-1].get("agent") if plan else None
    tool_calls = [ToolCall(agent=str(p.get("agent"))) for p in plan if p.get("agent")]
    route_mode = "multi" if len(plan) > 1 else "single"
    artifacts: List[Dict[str, str]] = []
    if body.get("mock_active") and body.get("mock_session_id"):
        artifacts.append({"kind": "mock_active", "ref": str(body["mock_session_id"])})
    # 上传文件作为 artifact 记录（document 引用，后续可经 document_id 回查 KB）。
    for d in (body.get("documents") or []):
        if isinstance(d, dict) and d.get("document_id"):
            artifacts.append({
                "kind": "image" if d.get("kind") == "image" else "document",
                "ref": str(d["document_id"]),
                "filename": str(d.get("filename") or ""),
            })
    try:
        repo = _dialogue_repo()
        seq = repo.next_seq(session_id)
        repo.add_turn(DialogueTurn(
            turn_id=f"dt-{uuid.uuid4().hex[:10]}", session_id=session_id, seq=seq,
            role="user", text=(text or "")[:500], capability=cap, route_mode=route_mode,
            topic=extract_known_topic(text or ""), tool_calls=tool_calls, artifacts=artifacts,
            status=str(body.get("status") or "completed"), trace_id=body.get("trace_id"),
        ))
    except Exception:
        pass


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
    mock_session_id: Optional[str] = None  # 非空=前端处于进行中的 mock，本轮文本喂给面试循环
    # 进行中 mock 时本轮意图（前端消歧/确认后下发）：answer|side|stop|pause|auto。
    # auto = 交后端轻量 LLM 判「作答 vs 插问」。
    mock_action: Optional[str] = None
    mock_question: Optional[str] = None  # 当前待答的面试题（供 auto 分类给 LLM 上下文）
    # 澄清续接：上一轮 needs_input 回传的部分意图帧，本轮文本是对缺失槽位的回答。
    pending_intent: Optional[dict] = None
    # 多模态附件(本地上传，内联)：[{"filename","mime","data"(base64/dataURL)}]。
    attachments: Optional[List[dict]] = None


class UIFileRequest(BaseModel):
    path: str


class ImageRequest(BaseModel):
    kind: str  # plan | diagnosis
    spec: dict = {}


def _invoke(user_input: str, session_id: str) -> dict:
    return _graph().invoke(
        {
            "user_input": user_input,
            "session_id": session_id,
            "trace_id": f"t-{session_id}",
            "replan_count": 0,
        }
    )


# ----------------------------------------------------------------- mock 语义触发
# 进入 mock 的明确线索（刻意排除复合"准备面试"=诊断→改计划，§5.6，不在此自动开面试）。
# 退出/暂停/插问由前端消歧 + 确认后，以 mock_action 显式下发（见 /ui/chat）。
_MOCK_START_CUES = (
    "模拟面试", "mock", "出题", "考我", "考考我", "面试我", "面我",
    "来一场面试", "来场面试", "开始面试", "练习面试", "面试练习",
)


def _wants_start_mock(text: str) -> bool:
    t = (text or "").lower()
    return any(c in t for c in _MOCK_START_CUES)


def _ui_route(mode: str, text: str, session_id: str) -> dict:
    """常规(非 mock)对话路由：plan/diagnose/note/qa 经主图跑一遍。"""
    mode = (mode or "qa").lower()
    if mode == "mock":  # 面试中插问没指定子模式 → 当普通问答
        mode = "qa"
    if not text:
        text = "帮我看看今天应该复习什么"
    if mode == "plan":
        return _invoke(f"生成学习计划: {text}", session_id)
    if mode == "diagnose":
        return _invoke(text if "诊断" in text else f"诊断我的弱点：{text}", session_id)
    if mode == "note":
        return _invoke(f"生成一份美观、结构化、可复习的学习笔记：{text}", session_id)
    if mode == "qa" and _mgr().qa.is_fast_concept_request(QAInput(question=text)):
        return _ui_fast_qa(text, session_id)
    return _invoke(text, session_id)


def _ui_fast_qa(text: str, session_id: str) -> dict:
    """UI QA fast lane: bypass Manager LLM routing for clear concept questions."""
    mgr = _mgr()
    out = mgr.qa.run(QAInput(question=text))
    body = {
        "reply_text": out.answer,
        "citations": [c.model_dump() for c in out.citations],
        "next_actions": [],
        "status": Status.OK.value,
        "image_url": "",
        "image_spec": None,
        "plan": [{"agent": "qa", "fast": True}],
        "replan_count": 0,
        "trace_id": f"t-{session_id}",
    }
    # Keep short-session continuity, but skip daily QA indexing for fast concept cards.
    mgr.record_turn(session_id, text, out.answer)
    return body


def _qa_with_attachments(text: str, atts, session_id: str) -> dict:
    """带附件的问答（像 ChatGPT：看图/读文档作答）。附件文本进证据、图片走 vision。

    走 QA 完整链(检索+合成+核验)；图片/扫描件需 vision 模型(无 key → 降级为文本/占位回答)。
    """
    mgr = _mgr()
    # 1) 持久化进本地知识库：清洗 → 切片 → 入库（去重）；图片生成可检索 summary。
    #    真实内容落 chunks(local)，会话只留 document 引用（manifest），后续靠 retrieval 召回。
    manifest = ingest_attachments(atts, session_id=session_id, db_path=mgr._db_path)
    # 2) 当前轮仍按 ChatGPT 方式作答：文本进证据、图片走 vision（不破坏现有多模态 QA）。
    flat = flatten_attachments(atts)
    q = text or "请阅读/查看我上传的附件，并解答或说明其要点。"
    out = mgr.qa.run(QAInput(question=q, attachment_text=flat.text, image_data_urls=flat.images))
    note = ""
    degraded = [a for a in atts if a.degraded]
    if degraded:
        note = "（注：" + "；".join(f"{a.filename or a.kind} {a.note}" for a in degraded) + "）"
    body = {
        "reply_text": (out.answer or "") + note,
        "citations": [c.model_dump() for c in out.citations],
        "next_actions": [],
        "status": Status.OK.value,
        "image_url": "",
        "image_spec": None,
        "plan": [{"agent": "qa", "attachments": len(atts),
                  "documents": len(manifest.documents)}],
        "replan_count": 0,
        "trace_id": f"t-{session_id}",
        # document 引用 + 轻量 manifest（不含全文）：供前端展示与下一轮按 document_id 回查。
        "documents": [d.model_dump() for d in manifest.documents],
        "manifest": manifest.manifest_line(),
    }
    mgr.record_turn(session_id, q, out.answer)
    return body


def _extract_mock_topic(text: str) -> str:
    """从开场语里剥掉触发词，留下面试主题；剥空则给个通用主题。"""
    topic = text or ""
    for cue in ("开始模拟面试", "模拟面试", "开始面试", "练习面试", "面试练习",
                "来一场面试", "来场面试", "面试我", "面我", "考考我", "考我", "出题", "mock"):
        topic = topic.replace(cue, "")
    topic = topic.strip(" ：:，,。.、-—") or "综合技术面试"
    return topic


def _needs_input_response(frame: IntentFrame) -> dict:
    """缺关键槽位 → 主动澄清一轮：回一句问题 + pending_intent（前端原样回传续接）。"""
    return {
        "status": Status.NEEDS_INPUT.value,
        "plan": [{"agent": frame.capability.value}],
        "next_actions": [],
        "needs_input": True,
        "reply_text": frame.clarification,
        "pending_intent": frame.model_dump(),
    }


def _start_mock_from_frame(mgr, frame: IntentFrame, sid: str) -> dict:
    """用解析出的 MockSlots 开一场针对性面试（岗位/JD/简历 → InterviewContext + 难度/轮次）。"""
    ms = frame.mock_slots()
    mock_input = MockInput(
        topic=ms.topic or "综合技术面试",
        session_id=sid,
        target_difficulty=ms.difficulty or 3,
        max_turns=ms.max_turns or 10,
        context=ms.to_interview_context(),
    )
    out = mgr.mock.run(mock_input)
    return _mock_response(mgr, out, sid)


def _mock_response(mgr, out, sid: str) -> dict:
    """把一次 mock run 的输出装成 /ui/chat 统一响应（含进行中标志/评分/结算图）。"""
    from ..integrations.gpt_image import asset_url

    body = {
        "status": "ok",
        "plan": [{"agent": "mock"}],
        "next_actions": [],
        "mock": out.model_dump(),
        "mock_active": out.status in ("active", "paused"),
        "mock_session_id": sid,
    }
    last_score = out.turn_scores[-1].model_dump() if out.turn_scores else None
    if last_score:
        body["mock_score"] = last_score
    if out.status == "expired":
        # 会话已过期（多半服务重启过、checkpoint 丢失）→ 退出面试态，提示重开。
        body["mock_active"] = False
        body["mock_session_id"] = None
        body["mock_expired"] = True
        body["reply_text"] = out.followup or "该模拟面试会话已过期，请重新开始一场面试。"
    elif out.status == "active":
        # 即时控制（里程碑2）时 followup 携带提示/答案/点评/跳过说明，连同题目一起回显。
        parts = [p for p in (out.followup, out.question) if p]
        body["reply_text"] = "\n\n".join(parts) or "请作答。"
    elif out.status == "paused":
        body["reply_text"] = "面试已暂停。输入任意内容继续，或说“结束”出复盘。"
    elif out.status == "escalate":
        body["reply_text"] = (f"面试中触发了跨能力请求（{out.escalate_action}），"
                              "已退出面试模式，可继续常规对话。")
    elif out.status == "review":
        review = out.review.model_dump() if out.review else None
        summary = (review or {}).get("summary") or "本场模拟面试结束，已生成复盘。"
        body["reply_text"] = summary
        if review:
            body["next_actions"] = [f"→ {n}" for n in (review.get("next_steps") or [])[:3]]
        settlement = mgr.settle_mock(sid)
        settlement["diagnosis_image_url"] = asset_url(settlement.get("diagnosis_image_path"))
        body["settlement"] = settlement
    return body


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

    # 把出图目录 docs/assets/ 挂到 /assets，前端用 image_url(/assets/<file>) 直接 <img> 取图。
    from ..integrations.gpt_image import ASSETS_DIR as _ASSETS_DIR
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if StaticFiles is not None:
        api.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

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
        if _mgr().qa.is_fast_concept_request(QAInput(question=req.question)):
            return _ui_fast_qa(req.question, req.session_id)
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
            settlement = mgr.settle_mock(req.session_id)
            from ..integrations.gpt_image import asset_url
            settlement["diagnosis_image_url"] = asset_url(settlement.get("diagnosis_image_path"))
            body["settlement"] = settlement
        return body

    @api.post("/ui/image")
    def ui_image(req: ImageRequest):
        """按需出图：前端点"生成信息图"时才调用（避免每次 /plan、结算都同步出图）。"""
        from ..integrations import gpt_image as gi

        if not gi.available():
            return {"ok": False, "error": "未配置出图能力（缺 OPENROUTER_API_KEY 或已禁用）。"}
        spec = req.spec or {}
        kind = (req.kind or "").lower()
        if kind == "plan":
            days = {}
            for k, v in (spec.get("days") or {}).items():
                try:
                    days[int(k)] = list(v or [])
                except (ValueError, TypeError):
                    continue
            res = gi.generate_plan_infographic(
                spec.get("title") or "学习计划", days, spec.get("summary"), spec.get("tips"))
        elif kind == "diagnosis":
            res = gi.generate_diagnosis_chart(
                spec.get("clusters") or [], spec.get("weak_atoms") or [],
                spec.get("recommendations") or [])
        else:
            return {"ok": False, "error": f"未知 kind: {req.kind}"}
        if res.get("ok"):
            return {"ok": True, "image_url": gi.asset_url(res.get("path"))}
        return {"ok": False, "error": res.get("error")}

    @api.post("/ui/chat")
    def ui_chat(req: UIChatRequest):
        """前端对话统一入口：先清空本轮记忆面板，分发后把记忆加载日志附在响应里。

        记忆面板（events + 摘要 + 注入来源/token）让前端能直观看到「这轮想起了什么、
        加载了哪些文件、各占多少 token」。非主图路径（fast-qa / mock）不经记忆流水线，
        events 自然为空，但 loaded 概览仍给出当前可注入的稳定/会话记忆。
        """
        from ..memory.log import MEMORY_LOG, memory_panel_payload

        MEMORY_LOG.reset()
        ctx = _ctx_load(req.session_id)
        body = _ui_chat_dispatch(req, ctx)
        _ctx_record(req.session_id, req.text or "", body)  # 回写上下文供下一轮承接/切换
        if isinstance(body, dict) and "memory" not in body:
            body["memory"] = memory_panel_payload(req.session_id, _mgr()._db_path)
        return body

    def _ui_chat_dispatch(req: UIChatRequest, ctx: Optional[dict] = None):
        mode = (req.mode or "qa").lower()
        text = (req.text or "").strip()
        ctx = ctx or {}

        # 1) 进行中的 mock：按 mock_action 执行（answer|side|stop|pause|auto|confirm_exit）。
        #    退出意图与退出确认都用轻量 LLM 语义判定（见 agents/mock/intent.py）。
        if req.mock_session_id:
            from ..agents.mock.intent import classify_exit_confirmation, classify_mock_input
            mgr = _mgr()
            sid = req.mock_session_id
            q = req.mock_question or ""
            action = (req.mock_action or "answer").lower()

            # 退出确认回复：语义判定 confirm/continue。
            if action == "confirm_exit":
                if classify_exit_confirmation(text) == "confirm":
                    out = mgr.mock.run(MockInput(topic="", session_id=sid, user_interrupt="结束"))
                    return _mock_response(mgr, out, sid)
                # 不结束 → 取消并复位到当前题目，不推进、不把这句当作答。
                return {
                    "status": "ok", "plan": [{"agent": "mock"}], "next_actions": [],
                    "mock_active": True, "mock_session_id": sid,
                    "needs_exit_confirm": False, "exit_cancelled": True,
                    "reply_text": q or "好的，我们继续。",
                }

            # auto：五类语义分流（作答/插问/退出/暂停/跨能力升级）。
            if action == "auto":
                action = classify_mock_input(q, text)

            # 跨能力升级（诊断/改计划/重排）→ 结束面试，带交接摘要转入常规 Manager 链路（§6b）。
            if action == "escalate":
                summary = mgr.mock.handoff_summary(sid)
                handoff = f"{summary}\n\n基于以上模拟面试上下文，{text}"
                resp = _invoke(handoff, req.session_id)
                if isinstance(resp, dict):
                    resp["mock_active"] = False
                    resp["mock_session_id"] = None
                    resp["escalated"] = True
                    resp["handoff_summary"] = summary
                return resp

            # 退出意图 → 不直接结算，回一个"待确认"信号让前端确认。
            if action == "exit":
                return {
                    "status": "ok", "plan": [{"agent": "mock"}], "next_actions": [],
                    "mock_active": True, "mock_session_id": sid,
                    "needs_exit_confirm": True,
                    "reply_text": "确定结束本场模拟面试吗？回复“确定”出复盘与诊断，或继续作答以取消。",
                }
            if action == "side":
                # 面试中插入的普通问题/请求：走常规链路，不推进面试，保留进行态。
                resp = _ui_route(req.mode, text, req.session_id)
                if isinstance(resp, dict):
                    resp["mock_active"] = True
                    resp["mock_session_id"] = sid
                    resp["mock_side"] = True
                return resp
            # 其余面试内动作（作答/换题/调难度/暂停/结束/skip/hint/…）统一经动作执行器分发：
            # 一句话 → 计划（当前单步），按注册表 channel 落成 MockInput 跑子图。
            out = run_plan(mgr.mock, sid, [Step(action, text)])
            return _mock_response(mgr, out, sid)

        # 1.5) 带附件(图片/PDF/MD)→ 像 ChatGPT 一样看附件作答（v1 走 QA：文本进证据、图片走 vision）。
        atts = parse_attachments(req.attachments)
        if atts:
            return _qa_with_attachments(text, atts, req.session_id)

        # 2) 澄清续接：上一轮 needs_input 后，本轮文本补全缺失槽位。
        if req.pending_intent:
            mgr = _mgr()
            frame = _resolver().resume_pending(req.pending_intent, text)
            if frame.clarification:                 # 仍缺 → 再问一次（极少）。
                return _needs_input_response(frame)
            if frame.capability == Capability.MOCK:
                return _start_mock_from_frame(mgr, frame, f"ui-mock-{uuid.uuid4().hex[:8]}")
            return _ui_route("plan" if frame.capability == Capability.PLANNING else "qa",
                             text, req.session_id)

        # 3) 自由对话(mode=qa)或显式 mock：轻量分发器判单/多意图 → 路由。槽位/澄清下沉到节点。
        #    其余显式模式(plan/diagnose/note)直接走常规链路，不被意图层接管。
        if mode in ("qa", "mock"):
            mgr = _mgr()

            # 前端强制 mock 模式：直接开面试（绕过能力判定，仍懒抽槽位 + 缺料澄清）。
            if mode == "mock":
                frame = _resolver().mock_frame(text)
                if frame.clarification:
                    return _needs_input_response(frame)
                return _start_mock_from_frame(mgr, frame, f"ui-mock-{uuid.uuid4().hex[:8]}")

            route = _dispatcher().route(text, ctx)  # ctx：多轮承接借上文（来自 dialogue_turns）

            # 多意图 → 主图，由 Manager 内 plan-as-tool 编排（诊断→改计划等）。
            if route.mode == "multi":
                return _invoke(text, req.session_id)

            cap = route.capability or "qa"
            # mock：仅在有真实开场信号时才开（"什么是模拟面试"这类自包含问句不误开）。
            if cap == "mock":
                if S.is_self_contained(text) and not (_wants_start_mock(text) or _has_mock_signal(text)):
                    return _ui_route("qa", text, req.session_id)
                frame = _resolver().mock_frame(text)        # 节点级懒抽槽位 + 缺料澄清
                if frame.clarification:
                    return _needs_input_response(frame)
                return _start_mock_from_frame(mgr, frame, f"ui-mock-{uuid.uuid4().hex[:8]}")
            if cap == "note":
                return _ui_route("note", text, req.session_id)
            if cap == "planning":
                pf = _resolver().resolve(f"/plan {text}")   # 节点级懒澄清：缺目标/期限 → 反问
                if pf.clarification:
                    return _needs_input_response(pf)
                return _ui_route("plan", text, req.session_id)
            if cap == "diagnosis":
                return _ui_route("diagnose", text, req.session_id)
            # qa：fast lane 或主图。
            return _ui_route("qa", text, req.session_id)

        return _ui_route(mode, text, req.session_id)
