"""FastAPI 入口（Phase 3）——/qa /plan /mock /diagnose（Tasks T-M1-15 / T-M2-07 / T-M3-08 / T-M4）。

- /qa /plan /diagnose：经主图 plan→execute→aggregate 走一遍（Design §2b）。
- /mock：多轮 interrupt/resume——首请求开场出题，带 user_answer/user_interrupt 续跑；
  出复盘(status=review)时自动结算（提交掌握度 + post_mock 诊断，Q5/§5.5）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, Optional

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
from ..intent import IntentResolver
from ..agents.mock.actions import Step, run_plan
from ..intent.slots import has_mock_signal as _has_mock_signal

_app_graph = None
_manager: Optional[ManagerAgent] = None
_intent_resolver: Optional[IntentResolver] = None


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


# 每会话对话上下文（进程内）：供意图层多轮承接/切换借用上文。重启即清空，符合会话语义。
_UI_CTX: Dict[str, dict] = {}


def _ctx_load(session_id: str) -> dict:
    return _UI_CTX.get(session_id, {})


# 主线意图能力（开了一条"线程"）；qa/note 是自包含旁支（aside），不接管主线。
_THREAD_CAPS = {"planning", "diagnosis", "mock"}
# 连续多少个 aside 后认为主线已放弃（避免 last_capability 长期粘住而误带后续承接句）。
_THREAD_DECAY = 3


def _ctx_record(session_id: str, text: str, body: dict) -> None:
    """回写每会话上下文，**让主线意图粘住、旁支插入不污染**（用户跳脱时仍稳）。

    - 实质任务轮(planning/diagnosis/mock) → 更新主线 last_capability，清零 aside 计数；
    - 八股问答 / 生成笔记等旁支 → 不动主线，aside 计数 +1；连续 ≥3 次才让主线失效。
    """
    from ..intent.slots import extract_known_topic

    if not isinstance(body, dict):
        return
    cap = None
    plan = body.get("plan")
    if isinstance(plan, list) and plan and isinstance(plan[-1], dict):
        cap = plan[-1].get("agent")
    cur = _UI_CTX.setdefault(session_id, {"history": []})
    if cap in _THREAD_CAPS:                 # 实质任务 → 接管/刷新主线
        cur["last_capability"] = cap
        cur["aside_streak"] = 0
    elif cap:                                # qa/note 旁支 → 主线粘住，计 aside
        cur["aside_streak"] = cur.get("aside_streak", 0) + 1
        if cur["aside_streak"] >= _THREAD_DECAY:
            cur.pop("last_capability", None)  # 久未延续 → 主线失效
    cur["active_mock"] = bool(body.get("mock_active"))
    topic = extract_known_topic(text or "")
    if topic:
        cur["last_topic"] = topic
    hist = cur.setdefault("history", [])
    hist.append({"text": (text or "")[:80], "capability": cap})
    del hist[:-4]  # 对话窗口：只留最近 4 轮


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
    if out.status == "active":
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

        # 3) 自由对话(mode=qa)或显式 mock：交统一意图层判断是否针对性开面试 / 是否要澄清。
        #    其余显式模式(plan/diagnose/note)直接走常规链路，不被意图层接管。
        if mode in ("qa", "mock"):
            mgr = _mgr()
            frame = (_resolver().mock_frame(text) if mode == "mock"
                     else _resolver().resolve(text, ctx))  # ctx：多轮承接/切换借用上文
            # 生成 md/笔记/文档（用户随手插入的生成请求）→ 走笔记呈现链路。
            if "note_gen" in frame.signals:
                return _ui_route("note", text, req.session_id)
            # 仅在有真实开场信号时才开面试：避免"什么是面试技巧"这类裸含"面试"的问答被误开。
            # active_mock 续接（"再来一题"等无关键词承接）也算真实开场信号。
            want_mock = (mode == "mock" or _wants_start_mock(text) or _has_mock_signal(text)
                         or "context_carry" in frame.signals or "llm_intent" in frame.signals)
            if frame.capability == Capability.MOCK and want_mock:
                if frame.clarification:
                    return _needs_input_response(frame)
                return _start_mock_from_frame(mgr, frame, f"ui-mock-{uuid.uuid4().hex[:8]}")
            # 规划缺目标/期限 → 主动澄清一轮（其余仍交常规链路）。
            if frame.capability == Capability.PLANNING and frame.clarification:
                return _needs_input_response(frame)
            # 意义不明的含糊求助（兜底 qa + 反问）→ 主动澄清一轮。
            if frame.capability == Capability.QA and frame.clarification:
                return _needs_input_response(frame)
            # LLM 主判 / 多轮承接到的 planning/diagnosis → 显式按该能力走（否则被 _ui_route 默认 mode 覆盖）。
            if {"context_carry", "llm_intent"} & set(frame.signals):
                if frame.capability == Capability.PLANNING:
                    return _ui_route("plan", text, req.session_id)
                if frame.capability == Capability.DIAGNOSIS:
                    return _ui_route("diagnose", text, req.session_id)

        return _ui_route(mode, text, req.session_id)
