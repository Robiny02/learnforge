"""ManagerAgent —— 编排者 / 唯一对外入口（Design §3.1 / §2b / §5.6）。

ReAct 动态编排 + 唯一写者：Manager 不预先拆死一张 DAG，而是**每步看子 agent 的结果再决定下一步**。
- DECIDE（decide_next）：看「已完成步骤 + 各步结果」，从受限动作空间 {qa, planning, diagnosis,
  mock, finish} 中选下一步；LLM 不可用/解析失败时走确定性兜底（_fallback_next）。
- LOOP（execute_dynamic）：循环 decide→dispatch→apply，直到 finish / 预算用尽。复合"准备面试"由
  ReAct 自然续跑：diagnosis → 看结果 → planning；诊断为空则跳过 modify、改建议先 mock（§5.6 异常①）。
- AGGREGATE：聚合各 worker 结果为用户回复 + next_actions（复合后建议 mock，控制权交回用户，§5.6 P5）。
- 唯一写者（Design §2a/§4b）：commit_path（路径）、commit_mastery（掌握度，mock S7 结算）。
- handoff_summary（Q2/§6b）：跨子系统 escalate/handoff 时由 Haiku 生成 ≤512tok 摘要。
- post_mock 自动诊断（Q5/§5.5）：mock 结算后自动触发一次只读诊断，成本单独记账。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from ..config import HANDOFF_SUMMARY_MAX_TOKENS
from ..contracts.agents.diagnosis import DiagnosisInput
from ..contracts.agents.mock import InterviewContext, MockInput
from ..contracts.agents.planning import PathDiff, PathItem
from ..contracts.enums import (
    AgentId,
    DiagnosisTrigger,
    EventType,
    Status,
    TimeWindow,
)
from ..contracts.message import ContextEnvelope, EventPayload, ResponsePayload
from ..contracts.result import NextRequest, NextRequestKind
from ..contracts.state import LearningPath, UserProfile
from ..memory.base import MEMORY
from ..memory.log import INJECT, MAINTAIN, READ, WRITE, MEMORY_LOG
from ..storage.repositories import (
    DiagnosisReportRepository,
    EventRepository,
    LearningPathRepository,
    TraceRepository,
)
from ..agents.base import BaseAgent
from ..agents.diagnosis import DiagnosisAgent
from ..agents.evidence import EvidenceResearchAgent
from ..agents.mock import MockInterviewAgent
from ..agents.planning import PlanningAgent
from ..agents.qa import QAAgent
from ..contracts.agents.evidence import EvidencePacket, EvidenceRequest


class NextStep(BaseModel):
    """Manager 动态规划每步的决策（动作空间受限、安全）。"""

    next_agent: str = "finish"  # qa | planning | diagnosis | mock | finish
    reason: str = ""


# 顺序敏感：diagnosis 在 planning 之前——"诊断+复习计划"这类应先 diagnosis，再由动态规划续到 planning。
_KEYWORDS = {
    "diagnosis": ("诊断", "弱点", "薄弱", "diagnos"),
    "planning": ("计划", "plan", "路径", "复习安排", "学习路线"),
    "mock": ("面试", "mock", "模拟"),
}

# 稳定规则层"用户明确记住"触发词（REQUIREMENTS R3.3）。长触发词在前，先匹配更具体的。
_REMEMBER_TRIGGERS = (
    "请记住", "帮我记住", "记一下", "记住", "以后请", "以后都", "我希望你以后",
)


def _is_empty_diagnosis(diag: Dict[str, Any]) -> bool:
    return not (diag.get("weak_atoms") or diag.get("clusters"))


class ManagerAgent(BaseAgent):
    agent_id = AgentId.MANAGER

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self._db_path = db_path
        self.qa = QAAgent(db_path=db_path)
        self.planning = PlanningAgent(db_path=db_path)
        self.diagnosis = DiagnosisAgent(db_path=db_path)
        self.mock = MockInterviewAgent(db_path=db_path)
        self.evidence = EvidenceResearchAgent(db_path=db_path)
        # 可插拔能力分发（Phase 3）：Capability → CapabilityHandler。新增能力=注册一个 handler，
        # 不再改 _dispatch_impl 的 if/elif。handler 绑定到本 Manager 实例（私有表，非全局单例）。
        from .capability_handlers import build_default_handlers
        self._handlers = build_default_handlers(self)

    # ---------------- ReAct 兜底意图（仅 LLM 不可用时用，不预拆 DAG）----------------
    @staticmethod
    def _wants_plan(user_input: str) -> bool:
        """是否属于"诊断→改计划"复合意图（ReAct 兜底用，判定 diagnosis 后是否续到 planning）。

        收窄到两类强意图（避免误吞纯"生成计划"这类单意图——那类直接路由到 planning）：
        - 准备面试（prep + interview）;
        - 同时出现诊断意图 + 计划意图（"诊断…并安排复习计划"）。
        """
        text = (user_input or "").lower()
        prep_interview = any(k in text for k in ("准备", "prepare", "帮我")) and any(
            k in text for k in ("面试", "interview")
        )
        diag_cue = any(k in text for k in ("诊断", "弱点", "薄弱", "diagnos"))
        plan_cue = any(k in text for k in ("计划", "复习", "路径", "规划", "plan", "安排"))
        return prep_interview or (diag_cue and plan_cue)

    @staticmethod
    def _wants_planning_outcome(user_input: str) -> bool:
        """用户是否明确要一份学习计划 / 面试准备计划（在线 ReAct 防过早 finish 的护栏判据）。

        比 _wants_plan 更宽：也覆盖纯"生成学习计划"（无诊断意图）。仅用强计划信号，避免误吞。
        """
        text = (user_input or "").lower()
        prep_interview = any(k in text for k in ("准备", "prepare", "帮我")) and any(
            k in text for k in ("面试", "interview")
        )
        plan_cue = any(
            k in text for k in ("计划", "学习路径", "学习路线", "规划", "plan")
        )
        return prep_interview or plan_cue

    @staticmethod
    def _keyword_first_agent(user_input: str) -> str:
        """关键词先验：LLM 不可用时 ReAct 首步兜底选哪个子 agent（非复合单意图）。"""
        text = user_input.lower()
        for agent, kws in _KEYWORDS.items():
            if any(kw in text for kw in kws):
                return agent
        return "qa"

    # ---------------- EXECUTE ----------------
    def dispatch(self, agent: str, user_input: str, context: Dict[str, Any],
                 trace_id: Optional[str] = None) -> ResponsePayload:
        """派发单个子任务给域 worker（同步请求-响应，Design §6a）。

        副作用（best-effort，失败不阻断）：落一条 agent_traces（§9a）；qa 路径 emit qa_signal 事件（§4c）。
        """
        self.require_tool(f"agent.{agent}")
        t0 = time.perf_counter()
        resp = self._dispatch_impl(agent, user_input, context)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        self._write_trace(agent, resp, trace_id, latency_ms)
        return resp

    def _dispatch_impl(self, agent: str, user_input: str, context: Dict[str, Any]) -> ResponsePayload:
        """经能力注册表分发（Phase 3）：选 handler 不再硬编码 if/elif。

        各能力的真正逻辑（含副作用/状态映射）在 `orchestration/capability_handlers.py` 的
        CapabilityHandler 里；这里只查表→run→投影回 ResponsePayload（对下游字节级等价）。
        未注册能力（理论上 require_tool 已先拦）→ unknown_agent。

        need_evidence 回路（Phase 5）：子能力（如 Diagnosis 信号不足）回 `next_request=need_evidence`
        而非自己读 source；Manager 调统一只读证据 worker 补证据、注入 context，再复跑一次（仅一次）。
        """
        handler = self._handlers.get(agent)
        if handler is None:
            return ResponsePayload(status=Status.ERROR, confidence=0.0, result={},
                                   error={"code": "unknown_agent", "message": agent})
        result = handler.run(user_input, context)
        if (result.next_request is not None
                and result.next_request.kind == NextRequestKind.NEED_EVIDENCE
                and not context.get("_evidence_gathered")):
            self._fulfill_need_evidence(result.next_request, user_input, context)
            result = handler.run(user_input, context)  # 带证据复跑一次（防重入由 _evidence_gathered 守护）
        return result.to_response_payload()

    def _fulfill_need_evidence(self, request: "NextRequest", user_input: str,
                               context: Dict[str, Any]) -> None:
        """响应子能力的 need_evidence：调统一只读证据 worker 采证据，注入 context 供复跑。

        只读不变量：经 `gather_evidence`（require_tool("agent.evidence")），EvidenceResearchAgent
        只调 READ 工具。证据本体不进 Conversation State——只注入 artifact 摘要 + 指针。
        """
        from ..contracts.enums import EvidenceSourceType

        sts = []
        for s in (request.payload.get("source_types") or []):
            try:
                sts.append(EvidenceSourceType(s))
            except ValueError:
                continue
        if not sts:
            sts = [EvidenceSourceType.ATTACHMENT, EvidenceSourceType.RESUME]
        packet = self.gather_evidence(EvidenceRequest(
            query=str(request.payload.get("query") or user_input), source_types=sts))
        self.attach_evidence_to_context(packet, context)  # 注入 context["evidence_artifact"]
        context["_evidence_gathered"] = True              # 防重入：一次诊断只补一次证据
        context["_evidence_refs"] = list(packet.evidence_refs)

    @staticmethod
    def _reason_for(agent: str, out: Any) -> str:
        """把子工具输出浓缩成一句「为什么」（依据/缺口/降级），供 Manager replan 看背后理由。

        只读 out 的稳定字段（getattr 兜底），≤160 字；不外传给用户。
        """
        try:
            if agent == "qa":
                cites = len(getattr(out, "citations", []) or [])
                degraded = getattr(out, "degraded", False)
                verdict = getattr(out, "verifier_verdict", "") or getattr(out, "verdict", "")
                bits = [f"conf={getattr(out, 'confidence', 0):.2f}", f"引用{cites}条"]
                if degraded:
                    bits.append("降级(无证据→降断言)")
                if verdict:
                    bits.append(f"核验={verdict}")
                return "；".join(bits)
            if agent == "diagnosis":
                clusters = getattr(out, "clusters", []) or []
                top = "、".join(getattr(c, "topic", "") for c in clusters[:2]) or "无明显弱点"
                recs = len(getattr(out, "recommendations", []) or [])
                return (f"conf={getattr(out, 'confidence', 0):.2f}；{len(clusters)}簇[{top}]；"
                        f"{recs}条建议" + ("；信号不足" if getattr(out, "confidence", 0) < 0.5 else ""))
            if agent == "planning":
                diff = getattr(out, "diff", None)
                adds = len(getattr(diff, "add_nodes", []) or []) if diff else 0
                mods = len(getattr(diff, "update_nodes", []) or []) if diff else 0
                return f"status={getattr(getattr(out, 'status', ''), 'value', out.status)}；diff +{adds}/~{mods}"
            if agent == "mock":
                return f"status={getattr(out, 'status', '')}；轮次推进/评分采集中"
        except Exception:  # noqa: BLE001 - reason 仅供观测，绝不阻断
            pass
        return ""

    def _dispatch_resume_diagnosis(self, user_input: str, context: Dict[str, Any]) -> ResponsePayload:
        """诊断简历问题：取简历全文（context 显式 > 上传附件）→ DiagnosisAgent.diagnose_resume。

        简历来源全空 → NEEDS_INPUT 提示上传，不硬凑。诊断结果已在 diagnose_resume 内存记忆可召回；
        这里不写 diagnosis_reports（那是弱点诊断的 schema），结果用 kind 标注避免与弱点结论混淆。
        """
        ic = context.get("interview_context")
        ctx = InterviewContext(**ic) if isinstance(ic, dict) else (ic or InterviewContext())
        # 简历文本来源：context 显式传入 > 自动从上传附件重建 > resume_claims 兜底。
        resume_text = str(context.get("resume_text") or "").strip()
        if not resume_text:
            from ..agents.diagnosis.resume import load_resume_text
            resume_text = load_resume_text(db_path=self._db_path,
                                           session_id=context.get("session_id"))
        if not resume_text and ctx.resume_claims:
            resume_text = "\n".join(ctx.resume_claims)
        if not resume_text:
            return ResponsePayload(
                status=Status.NEEDS_INPUT, confidence=0.0,
                result={"kind": "resume_diagnosis",
                        "message": "未找到简历内容。请上传简历文件（PDF/MD/TXT）或直接粘贴简历正文后再诊断。"},
            )
        diag = self.diagnosis.diagnose_resume(resume_text, ctx, persist=True)
        result = diag.model_dump()
        result["kind"] = "resume_diagnosis"  # 判别标签：与弱点 DiagnosisResult 区分
        return ResponsePayload(status=Status.OK, confidence=diag.confidence,
                               result=result, cost_usd=self.diagnosis.last_cost_usd)

    @staticmethod
    def _enrich_mock_from_intent(mi: MockInput, user_input: str) -> None:
        """从自然语言抽取面试槽位填进 MockInput（岗位/JD/简历 → context；难度/轮次）。

        纯确定性、可离线；无信号则保持原"纯主题"行为。失败不阻断（best-effort）。
        """
        try:
            from ..intent import IntentResolver
            slots = IntentResolver().mock_frame(user_input).mock_slots()
        except Exception:  # noqa: BLE001 - 意图增强绝不能打断派发
            return
        ctx = slots.to_interview_context()
        if ctx is not None:
            mi.context = ctx
        if slots.difficulty:
            mi.target_difficulty = slots.difficulty
        if slots.max_turns:
            mi.max_turns = slots.max_turns

    # ---------------- 只读证据采集（隔离上下文 worker → EvidencePacket）----------------
    def gather_evidence(self, request: EvidenceRequest) -> EvidencePacket:
        """委派给只读 EvidenceResearchAgent 采证据，返回结构化 EvidencePacket。

        经 `require_tool("agent.evidence")` 权限门；db_path 缺省时回填 Manager 的。worker 在隔离
        上下文里读 resume/repo/file/attachment，只回精炼片段 + 指针，不把原始全文灌回 Manager。
        """
        self.require_tool("agent.evidence")
        if request.db_path is None:
            request.db_path = self._db_path
        return self.evidence.run(request)

    def attach_evidence_to_context(self, packet: EvidencePacket,
                                   context: Dict[str, Any]) -> str:
        """把 EvidencePacket 渲染成**只读 artifact 摘要**注入 context，供 Diagnosis 等下游消费。

        只放 summary + 证据指针（artifact_text），不放原始文件内容 —— Conversation State 不被污染。
        """
        artifact = packet.artifact_text()
        context["evidence_artifact"] = artifact
        return artifact

    def _apply_step(self, agent: str, resp: ResponsePayload, context: Dict[str, Any],
                    meta: Dict[str, Any], trace_id: Optional[str]) -> None:
        """单步善后（ReAct 循环每步调）：handoff 注入 / 诊断入 context / planning 唯一写者落库 + Notion。"""
        # §6b/§5.4：子系统 escalate 交回 Manager → 生成 handoff 摘要，注入后续任务 context。
        if resp.status == Status.ESCALATE:
            env = self._handoff_from_escalation(resp.result)
            context["handoff_summary"] = env.handoff_summary
            meta["handoff_summary"] = env.handoff_summary

        if agent == "diagnosis" and resp.status == Status.OK:
            # 简历诊断结果 shape 不同（kind=resume_diagnosis），不灌进供 planning 消费的 diagnosis 槽。
            if resp.result.get("kind") != "resume_diagnosis":
                context["diagnosis"] = resp.result
        if agent == "planning" and resp.status == Status.OK:
            # 唯一写者：把 PlanningAgent 的增量 diff 落库 + emit PATH_CHANGED（§2a/§3.7/§4c）。
            committed_path = self._commit_planning_result(resp.result, context, trace_id)
            if committed_path:
                meta["path_committed"] = committed_path
            # PlanningAgent 作为 tool-calling agent 自主发布的产物（若有）。
            if getattr(self.planning, "last_notion_url", None):
                meta["notion_url"] = self.planning.last_notion_url
            if getattr(self.planning, "last_report_path", None):
                meta["report_path"] = self.planning.last_report_path
            if getattr(self.planning, "last_plan_image_path", None):
                meta["plan_image_path"] = self.planning.last_plan_image_path
            if getattr(self.planning, "last_plan_image_spec", None):
                meta["plan_image_spec"] = self.planning.last_plan_image_spec
            if not _is_empty_diagnosis(context.get("diagnosis", {})):
                meta["suggest_mock"] = True  # 改完路径后建议 mock（§5.6 P5）

    # ---------------- ReAct 决策（decide_next → execute_dynamic 循环）----------------
    def _fallback_next(self, user_input: str, done: List[str],
                       context: Dict[str, Any]) -> str:
        """LLM 不可用/解析失败时的确定性 ReAct 兜底——复刻"看结果选下一步"的直觉。

        - 复合"准备面试"/"诊断+计划"意图：diagnosis → planning → finish
          （诊断为空的跳过/建议 mock 由 execute_dynamic 拦截处理）。
        - 其余单意图：首步按关键词路由，完成后 finish。
        """
        if self._wants_plan(user_input):
            if "diagnosis" not in done:
                return "diagnosis"
            if "planning" not in done:
                return "planning"
            return "finish"
        if not done:
            return self._keyword_first_agent(user_input)
        return "finish"

    def decide_next(self, user_input: str, done: List[str],
                    responses: List[ResponsePayload], context: Dict[str, Any]) -> str:
        """看已完成步骤 + 各步结果，决定下一个子 agent 或 finish（动作空间受限、安全）。"""
        from ..llm.client import LLM, LLMStructuredError, LLMUnavailable

        valid = ("qa", "planning", "diagnosis", "mock", "finish")
        if not LLM.available or self.skill is None:
            return self._fallback_next(user_input, done, context)

        # 不只喂结构化结论（result），还喂子工具的「为什么」(reason：依据/缺口/降级)——让 replan
        # 看到结论背后的理由（如 diagnosis 信号不足、qa 降级无证据），而非只看一行 dict。
        summaries = "\n".join(
            f"- {a}: {r.reason or '-'}（结论：{str(r.result)[:60]}）" for a, r in zip(done, responses)
        ) or "（无）"
        # 简短 prompt：长 prompt + “已满足→finish” 会把弱模型带偏成过早 finish。
        prompt = (
            f"用户请求：{user_input}\n已做步骤：{done or '无'}\n各步结果（含为什么）：\n{summaries}\n\n"
            "下一步调哪个子 agent？\n"
            "qa=答概念/技术问题；diagnosis=找薄弱点；planning=排学习计划(通常先有 diagnosis)；"
            "mock=模拟面试；finish=用户请求已被满足。"
        )
        # 独立 system prompt：只让模型输出 NextStep.next_agent，不要输出任务结构。
        try:
            self.require_tool("llm.complete_structured")
            obj, res = LLM.complete_structured(
                prompt, NextStep, model_tier=self.skill.spec.model_tier,
                system="你是任务路由器，只选下一步的子 agent，输出 next_agent ∈ {qa, planning, diagnosis, mock, finish}。",
                max_tokens=64,
            )
            self.last_cost_usd = res.cost_usd
            nxt = (obj.next_agent or "").strip().lower()
        except (LLMUnavailable, LLMStructuredError):
            nxt = ""
        if nxt not in valid:
            nxt = self._fallback_next(user_input, done, context)  # 非法/解析失败 → 确定性兜底
        if not done and nxt == "finish":
            nxt = self._fallback_next(user_input, [], context)  # 首步不允许直接 finish（否则空转）
        # 护栏（在线 ReAct）：用户明确要学习计划/面试准备计划时，planning 跑之前不允许 finish——
        # 弱模型常在 diagnosis 后过早收尾。这不是恢复固定 DAG，只拦"提前结束"，下一步仍由 ReAct 推进：
        # 复合意图先补 diagnosis 再到 planning；诊断为空 → planning 由 execute_dynamic 拦成"跳过+建议 mock"。
        if (
            nxt == "finish"
            and "planning" not in done
            and self._wants_planning_outcome(user_input)
        ):
            if self._wants_plan(user_input) and "diagnosis" not in done:
                nxt = "diagnosis"
            else:
                nxt = "planning"
        return nxt

    def plan_execute(
        self, user_input: str, trace_id: Optional[str] = None, max_steps: int = 4,
        session_ctx: str = "",
    ) -> Tuple[List[ResponsePayload], Dict[str, Any], List[Dict[str, Any]]]:
        """复合任务的 plan-as-tool-calls：LLM(或确定性) 先 create 一个显式步骤计划，
        执行器逐步 dispatch + mark_step=completed，§5.6 异常（诊断空→跳过改计划+建议 mock）保留。

        写仍走 dispatch/_apply_step（唯一写者不变）；meta["plan_steps"] 带回带状态的计划供 UI/trace。
        """
        from ..llm.client import LLM
        from .planner import build_plan

        plan = build_plan(user_input, self._wants_plan(user_input), llm=LLM)
        responses: List[ResponsePayload] = []
        context: Dict[str, Any] = {"composite": True, "trace_id": trace_id,
                                   "session_context": session_ctx}
        meta: Dict[str, Any] = {"composite": True, "skipped_modify": False,
                                "suggest_mock": False, "planned": True}
        executed: List[Dict[str, Any]] = []
        done: List[str] = []

        for step in plan.steps[:max_steps]:
            agent = step.agent
            if agent not in ("qa", "planning", "diagnosis", "mock") or agent in done:
                continue
            # §5.6 异常①：planning.modify 但诊断为空 → 跳过，建议先 mock。
            if agent == "planning" and "diagnosis" in context and _is_empty_diagnosis(context["diagnosis"]):
                meta["skipped_modify"] = True
                meta["suggest_mock"] = True
                responses.append(ResponsePayload(
                    status=Status.OK, confidence=0.6,
                    result={"skipped": True, "reason": "诊断信号不足，跳过改计划，建议先做模拟面试采集数据。"}))
                executed.append({"agent": "planning", "task_type": "plan.modify"})
                done.append("planning")
                step.status = "completed"
                continue
            resp = self.dispatch(agent, step.task or user_input, context, trace_id=trace_id)
            responses.append(resp)
            self._apply_step(agent, resp, context, meta, trace_id)
            executed.append({"agent": agent})
            done.append(agent)
            step.status = "completed"

        if not executed:  # 空计划兜底：至少回答一次
            resp = self.dispatch("qa", user_input, context, trace_id=trace_id)
            responses.append(resp)
            executed.append({"agent": "qa"})
        meta["plan_steps"] = [s.model_dump() for s in plan.steps]
        return responses, meta, executed

    def build_session_context(self, session_id: Optional[str]) -> str:
        """装配有界会话上下文并记入记忆面板（真实注入点，§5/§7）。无历史 → 空串。

        单一真值：与 memory.log.prompt_load_overview 共用 build_session_context，面板不再各算一套。
        """
        from ..memory.session_context import build_session_context as _build

        sctx = _build(session_id, db_path=self._db_path)
        if sctx.is_empty():
            return ""
        MEMORY_LOG.record(
            INJECT, "注入会话上下文",
            f"{sctx.total_tokens()} tokens / {len(sctx.sections)} 段（"
            + "、".join(s.kind for s in sctx.sections) + "）",
            count=1)
        return sctx.render()

    def execute_dynamic(
        self, user_input: str, trace_id: Optional[str] = None, max_steps: int = 4,
        session_id: Optional[str] = None,
    ) -> Tuple[List[ResponsePayload], Dict[str, Any], List[Dict[str, Any]]]:
        """Manager 作为动态规划者：每步看结果决定下一子 agent，直到 finish/预算。

        动作空间 = {qa, planning, diagnosis, mock, finish}；写仍是确定性 commit（_apply_step）。
        返回 (responses, meta, executed)；executed 供 aggregate/UI 画链路。
        复合任务（"准备面试"等）→ 走显式 plan-as-tool-calls（plan_execute）。
        """
        session_ctx = self.build_session_context(session_id)
        if self._wants_plan(user_input):
            return self.plan_execute(user_input, trace_id=trace_id, max_steps=max_steps,
                                     session_ctx=session_ctx)

        responses: List[ResponsePayload] = []
        # 动态单步路由默认不是 composite（避免把单独 diagnosis 的 trigger 误标成 composite）。
        context: Dict[str, Any] = {"composite": False, "trace_id": trace_id,
                                   "session_context": session_ctx}
        meta: Dict[str, Any] = {"composite": False, "skipped_modify": False, "suggest_mock": False,
                                "dynamic": True}
        executed: List[Dict[str, Any]] = []
        done: List[str] = []

        for _ in range(max_steps):
            nxt = self.decide_next(user_input, done, responses, context)
            if nxt == "finish":
                break
            if nxt in done:  # 防止反复调同一个子 agent 死循环
                break
            # §5.6 异常①：planning.modify 但诊断为空 → 跳过，建议先 mock。
            if nxt == "planning" and "diagnosis" in context and _is_empty_diagnosis(context["diagnosis"]):
                meta["skipped_modify"] = True
                meta["suggest_mock"] = True
                responses.append(ResponsePayload(
                    status=Status.OK, confidence=0.6,
                    result={"skipped": True, "reason": "诊断信号不足，跳过改计划，建议先做模拟面试采集数据。"}))
                executed.append({"agent": "planning", "task_type": "plan.modify"})
                done.append("planning")
                continue
            resp = self.dispatch(nxt, user_input, context, trace_id=trace_id)
            responses.append(resp)
            self._apply_step(nxt, resp, context, meta, trace_id)
            executed.append({"agent": nxt})
            done.append(nxt)

            # 早停：qa 是叶子能力，成功产出面向用户的答案即终态，无需再花一次 decide_next 去确认 finish
            # （纯 ReAct 路由开销，简单八股可省一次 LLM 往返）。仅对 qa 生效——diagnosis 可能是
            # 复合"准备面试"的前缀（后接 planning），不能早停。
            if nxt == "qa" and resp.status == Status.OK:
                break

        if not executed:  # 模型一上来就 finish 的兜底：至少回答一次
            resp = self.dispatch("qa", user_input, context, trace_id=trace_id)
            responses.append(resp)
            executed.append({"agent": "qa"})
        return responses, meta, executed

    def _commit_planning_result(
        self, result: Dict[str, Any], context: Dict[str, Any], trace_id: Optional[str]
    ) -> Optional[str]:
        """从 planning 的 ResponsePayload.result 还原 PathDiff 并落库。失败/空 diff → None。"""
        try:
            diff = PathDiff(**(result.get("diff") or {}))
        except Exception:
            return None
        return self.apply_path_diff(
            diff,
            existing_path_ref=context.get("existing_path_ref", "current"),
            user_id=context.get("user_id", "default"),
            trace_id=trace_id,
        )

    def run_active_mock(
        self, user_input: str, session_id: str, trace_id: Optional[str] = None
    ) -> Tuple[List[ResponsePayload], Dict[str, Any], List[Dict[str, Any]]]:
        """续跑进行中的 mock：单步派发 mock 子系统（多轮 interrupt/resume 在 mock 子图内）。

        不走 ReAct 路由——会话已锚定在 mock，直接 dispatch；返回 (responses, meta, executed)。
        """
        context: Dict[str, Any] = {"composite": False, "trace_id": trace_id,
                                   "mock_session_id": session_id,
                                   "session_context": self.build_session_context(session_id)}
        meta: Dict[str, Any] = {"composite": False, "skipped_modify": False,
                                "suggest_mock": False, "dynamic": False}
        resp = self.dispatch("mock", user_input, context, trace_id=trace_id)
        self._apply_step("mock", resp, context, meta, trace_id)
        return [resp], meta, [{"agent": "mock"}]

    # ---------------- 埋点 / 事件（best-effort）----------------
    def _write_trace(self, agent: str, resp: ResponsePayload, trace_id: Optional[str],
                     latency_ms: int) -> None:
        """每次 dispatch 落一条 agent_traces（§9a）。失败不阻断主流程。"""
        try:
            self.require_tool("repository.write.traces")
            TraceRepository(db_path=self._db_path).write(
                message_id=str(uuid.uuid4()),
                trace_id=trace_id or "no-trace",
                sender=AgentId.MANAGER.value,
                recipient=agent,
                status=resp.status.value,
                confidence=resp.confidence,
                cost_usd=resp.cost_usd,
                latency_ms=latency_ms,
            )
        except Exception:
            pass

    def _persist_diagnosis(self, result: Dict[str, Any], time_window: str, trigger: str,
                           confidence: float, trace_id: Optional[str] = None) -> Optional[str]:
        """落一条 diagnosis_reports 快照（Manager 单点写，Design §4c）。失败不阻断。返回 report_id。"""
        report_id = str(uuid.uuid4())
        try:
            self.require_tool("repository.write.diagnosis_reports")
            DiagnosisReportRepository(db_path=self._db_path).write(
                report_id=report_id, time_window=time_window, trigger=trigger,
                result=result, confidence=confidence, trace_id=trace_id,
            )
            return report_id
        except Exception:
            return None

    def _emit_qa_signal(self, out: Any) -> None:
        """QA 返回后写 interaction_event(qa_signal)（§4c / T-M4-01）。append-only，非状态写。"""
        try:
            self.require_tool("repository.write.events")
            verdict = out.verdict.value if hasattr(out.verdict, "value") else str(out.verdict)
            EventRepository(db_path=self._db_path).append(
                EventPayload(
                    event_type=EventType.QA_SIGNAL,
                    topic=out.topic or "general",
                    atom_refs=[c.chunk_id for c in out.citations][:5],
                    signal={"verdict": verdict, "confidence": out.confidence,
                            "weak": verdict in ("unverified", "uncertain")},
                ),
                event_id=str(uuid.uuid4()),
            )
        except Exception:
            pass

    # ---------------- 个人长期记忆（daily markdown 真值 + chunks 索引）----------------
    def remember_qa(self, question: str, out: Any) -> Optional[str]:
        """把"值得记住"的 QA 写入 daily 记忆（memory/YYYY-MM-DD.md + chunks/local 索引）。

        过滤（非每轮都写）：verdict ∈ {verified, uncertain} 且答案实质（非闲聊/报错/极短）。
        markdown 是真值、可重建索引；收口在 Manager（单写者），仅写内容/索引，不碰 mastery。best-effort。
        """
        try:
            verdict = out.verdict.value if hasattr(out.verdict, "value") else str(out.verdict)
            answer = (out.answer or "").strip()
            if verdict not in ("verified", "uncertain") or len(answer) < 20:
                MEMORY_LOG.record(WRITE, "跳过保存", "内容价值不足",
                                  reason=f"verdict={verdict} / 答案过短")
                return None
            MEMORY_LOG.record(WRITE, "提取候选记忆", f"类型 qa（verdict={verdict}）")
            from ..memory.daily import record

            note = record(
                kind="qa",
                text=f"Q: {question}\nA: {answer}",
                topic=out.topic,
                db_path=self._db_path,
            )
            return note.get("date")
        except Exception:
            return None

    def _remember_mock(
        self, session_id: str, updates: List[Dict[str, Any]], diagnosis: Optional[Dict[str, Any]]
    ) -> None:
        """mock 结算后把薄弱点/反馈写入 daily 记忆（kind=weak 慢衰减）。best-effort。"""
        try:
            from ..memory.daily import record

            weak = [u for u in (updates or []) if float(u.get("signal", 1.0)) < 0.6]
            if weak:
                atoms = "、".join(str(u.get("atom_id")) for u in weak[:5])
                record(kind="weak", db_path=self._db_path,
                       text=f"mock 会话 {session_id} 暴露薄弱点（掌握信号<0.6）：{atoms}")
            else:
                record(kind="mock", db_path=self._db_path,
                       text=f"mock 会话 {session_id} 已结算，本场无明显薄弱点。")
        except Exception:
            pass

    def remember_decision(self, text: str, topic: Optional[str] = None) -> Optional[str]:
        """把阶段性项目决策写入 daily 记忆（kind=decision 慢衰减，高重要性）。best-effort。

        Agent 判定沉淀（REQUIREMENTS R6.6）；返回 note 日期，去重命中/失败 → None。
        """
        return self._remember_daily("decision", text, topic)

    def remember_progress(self, text: str, topic: Optional[str] = None) -> Optional[str]:
        """把学习进展写入 daily 记忆（kind=progress 慢衰减）。best-effort。

        本阶段作为可调用入口（mock 结算/路径修改等节点或后续 daemon 调用）；返回 note 日期。
        """
        return self._remember_daily("progress", text, topic)

    def _remember_daily(self, kind: str, text: str, topic: Optional[str]) -> Optional[str]:
        try:
            from ..memory.daily import record

            note = record(kind=kind, text=text, topic=topic, db_path=self._db_path)
            return None if note.get("duplicate") else note.get("date")
        except Exception:
            return None

    # ---------------- 记忆面板：每轮入口（读取稳定 + 会话 + 受控写规则）----------------
    def begin_memory_turn(
        self, user_input: str, session_id: Optional[str] = None
    ) -> Optional[str]:
        """一轮对话开始时的记忆读取流水线，并记入记忆日志面板（REQUIREMENTS §3.1/§4）。

        ① 清空本轮面板 → ② 加载稳定记忆 MEMORY.md（载入即注入 prompt）
        → ③ 加载会话短期记忆（返回供规划器使用）→ ④ 用户明确"记住…"则受控写规则。
        返回会话短期记忆文本（无则 None）。best-effort，不阻断主链路。
        """
        MEMORY_LOG.reset()
        try:
            stable = MEMORY.memory_prefix(db_path=self._db_path)
            if stable:
                MEMORY_LOG.record(READ, "加载稳定记忆",
                                  f"已加载 MEMORY.md（{stable.count(chr(10)) + 1} 行）")
                MEMORY_LOG.record(INJECT, "注入稳定记忆", "进入 prompt", count=1)
            else:
                MEMORY_LOG.record(READ, "加载稳定记忆", "为空，跳过")
        except Exception:
            pass
        convo = self.load_session_memory(session_id)
        self.maybe_remember_rule(user_input)
        return convo

    # ---------------- 稳定规则层（MEMORY.md，仅用户明确要求记住时受控写入）----------------
    def maybe_remember_rule(self, user_input: str) -> Optional[str]:
        """用户明确要求记住稳定偏好 → 写入 MEMORY.md（REQUIREMENTS R3.3，稳定层唯一受控写入口）。

        稳定层不自动更新：仅当输入以"记住/请记住/以后都…"等明确触发词开头才写。
        返回写入的规则文本；非记住意图 / 内容过短 / 已存在 / 失败 → None。best-effort。
        """
        text = (user_input or "").strip()
        for trig in _REMEMBER_TRIGGERS:
            if text.startswith(trig):
                rule = text[len(trig):].strip(" ：:，,。、")
                if len(rule) < 6:  # 过短（如"记住了"）不视为稳定偏好
                    return None
                try:
                    from ..memory.files import append_root_memory

                    if append_root_memory(rule):
                        MEMORY_LOG.record(WRITE, "保存记忆", "成功，类型为 stable_rule",
                                          reason="用户明确要求记住")
                        return rule
                    MEMORY_LOG.record(WRITE, "跳过保存", "稳定规则已存在")
                    return None
                except Exception:
                    return None
        return None

    # ---------------- 短期会话记忆（recent 原文 + 压缩摘要，§6b/§6c session 段）----------------
    def load_session_memory(self, session_id: Optional[str]) -> Optional[str]:
        """渲染会话短期记忆 = 固定重要结果(pinned) + 早期压缩摘要 + 最近原文轮（无记录 → None）。

        pinned 置顶（永不压缩的重要结果，全文）；其后是 compaction 折叠出的早期摘要 + 仍保留的
        最近原文轮。best-effort。
        """
        if not session_id:
            return None
        try:
            from ..storage.repositories import SessionStateRepository

            st = SessionStateRepository(db_path=self._db_path).get(session_id)
            if not st:
                MEMORY_LOG.record(READ, "加载会话记忆", "无历史，跳过")
                return None
            parts: List[str] = []
            pinned = (st.get("active_task") or {}).get("pinned") or []
            for p in pinned:
                parts.append(f"[重要结果·固定] 用户：{p.get('user', '')}\n回复：{p.get('reply', '')}")
            if st.get("summary"):
                parts.append(f"[早期会话摘要] {st['summary']}")
            rounds = st.get("recent_messages") or []
            for r in rounds:
                parts.append(f"用户：{r.get('user', '')}\n回复：{r.get('reply', '')}")
            if not parts:
                MEMORY_LOG.record(READ, "加载会话记忆", "无历史，跳过")
                return None
            # 仅记 READ：真正注入 prompt 发生在 execute_dynamic 的 build_session_context（INJECT 在那里记），
            # 这里只是把会话记忆读出来供 manager_plan 回填 state（避免谎报一次未发生的注入，§7）。
            MEMORY_LOG.record(
                READ, "加载会话记忆",
                f"{len(pinned)} 条固定 + {len(rounds)} 轮原文 + {'有' if st.get('summary') else '无'}早期摘要",
            )
            return "\n".join(parts)
        except Exception:
            return None

    def record_turn(
        self,
        session_id: Optional[str],
        user_input: str,
        reply: str,
        active_mock: Optional[str] = None,
        *,
        important: bool = False,
    ) -> Optional[str]:
        """本轮收尾：追加一轮原文 → Claude Code 式**会话级 compaction**（按 token 阈值，不按轮数）。

        统计 session context tokens = summary + 最近原文轮（**不含 pinned**）；超
        SESSION_COMPACTION_THRESHOLD_TOKENS 才把最旧轮逐个折叠进 summary，直到 ≤TARGET，但至少
        保留 SESSION_MIN_RECENT_ROUNDS 轮。`important=True` 的轮额外存入 **pinned 区**，永不被
        compaction 折叠、渲染时置顶全文注入（重要结果保护）。返回 summary。
        """
        if not session_id:
            return None
        try:
            from ..config import (
                SESSION_COMPACTION_TARGET_TOKENS,
                SESSION_COMPACTION_THRESHOLD_TOKENS,
                SESSION_MAX_PINNED,
                SESSION_MIN_RECENT_ROUNDS,
                SESSION_PIN_MAX_CHARS,
                SESSION_RESUMMARIZE_EVERY,
            )
            from ..memory.base import MEMORY
            from ..memory.tokens import count_tokens
            from ..llm.client import LLM
            from ..storage.repositories import SessionStateRepository

            repo = SessionStateRepository(db_path=self._db_path)
            st = repo.get(session_id) or {}
            summary = st.get("summary") or ""
            task = dict(st.get("active_task") or {})
            fold_count = int(task.get("fold_count", 0))
            pinned = list(task.get("pinned") or [])
            rounds = list(st.get("recent_messages") or [])
            new_round = {"user": (user_input or "")[:500], "reply": (reply or "")[:500]}
            rounds.append(new_round)

            # 重要结果保护：important 轮进 pinned（FIFO cap），永不参与 compaction、永不折叠。
            if important and (new_round["user"] or new_round["reply"]):
                pinned.append({"user": new_round["user"][:SESSION_PIN_MAX_CHARS],
                               "reply": new_round["reply"][:SESSION_PIN_MAX_CHARS]})
                if len(pinned) > SESSION_MAX_PINNED:
                    pinned = pinned[-SESSION_MAX_PINNED:]  # FIFO：丢最旧 pin
                MEMORY_LOG.record(WRITE, "固定重要结果",
                                  f"pin 第 {len(pinned)} 条，永不压缩")

            # session context tokens（pinned 不可压 → 不计入触发，避免压不下去；它单独占注入预算）。
            def _ctx_tokens() -> int:
                t = count_tokens(summary)
                for r in rounds:
                    t += count_tokens(r.get("user", "")) + count_tokens(r.get("reply", ""))
                return t

            # 触发：本轮结束后 token 超阈 → 折叠最旧轮直到 ≤TARGET（至少留 MIN_RECENT 轮）。
            if _ctx_tokens() > SESSION_COMPACTION_THRESHOLD_TOKENS:
                overflow: List[Dict[str, str]] = []
                while (_ctx_tokens() > SESSION_COMPACTION_TARGET_TOKENS
                       and len(rounds) > SESSION_MIN_RECENT_ROUNDS):
                    overflow.append(rounds.pop(0))
                if overflow:
                    fold_count += 1
                    # #2 周期性全量重摘：每 N 次折叠，把"旧 summary + 新溢出"**重新组织**成结构化纯
                    # 文本（目标/决策/未决项/主题），修正增量折叠的漂移与碎片化。仅 LLM 可用时触发——
                    # 离线严格保持纯增量（绝不把旧摘要喂回离线摘要器，避免递归套娃/膨胀，§14-7）。
                    if LLM.available and fold_count % SESSION_RESUMMARIZE_EVERY == 0:
                        summary = self._resummarize_session(summary, overflow)
                        MEMORY_LOG.record(MAINTAIN, "全量重摘会话记忆",
                                          f"第 {fold_count} 次折叠（token 超阈触发结构化重组），保留最近 {len(rounds)} 轮")
                    else:
                        # 增量折叠：只摘要"新溢出"的轮并追加到既有摘要后（旧摘要不再喂回摘要器）。
                        folded = MEMORY.summarize(
                            {"intent": "session_recap",
                             "key_facts": [f"用户：{r['user'][:80]} / 回复：{r['reply'][:80]}"
                                           for r in overflow],
                             "constraints": [], "open_items": []}
                        )
                        # LLM 可用时 summarize 会返回 JSON(含 key_facts/转义引号) → 规整成纯文本，
                        # 避免原始 JSON 污染会话 summary（§14-7 在 LLM 模式下也保持无嵌套）。
                        folded = self._summary_to_text(folded)
                        summary = f"{summary} ｜ {folded}".strip(" ｜") if summary else folded
                        summary = summary[-800:]  # 防膨胀：保留最近折叠内容
                        MEMORY_LOG.record(MAINTAIN, "压缩会话记忆",
                                          f"{len(overflow)} 轮压成摘要（token 超阈），保留最近 {len(rounds)} 轮")

            task["active_mock"] = active_mock
            task["fold_count"] = fold_count
            task["pinned"] = pinned
            repo.upsert(session_id, summary, task, rounds)
            return summary
        except Exception:
            return None

    @staticmethod
    def turn_is_important(citations: Optional[list] = None,
                          plan: Optional[List[Dict[str, Any]]] = None) -> bool:
        """重要结果判定（→ record_turn(important=True) 进 pinned 永不压缩）。

        信号：① 带引用的可核验答案（citations 非空）；② 诊断/规划/模拟面试的结构化产出
        （plan 命中 diagnosis/planning/mock）。纯闲聊 QA（无引用）不 pin。
        """
        if citations:
            return True
        agents = {str((s or {}).get("agent") or "").lower() for s in (plan or [])}
        return bool(agents & {"diagnosis", "planning", "mock"})

    @staticmethod
    def _summary_to_text(s: str) -> str:
        """把折叠摘要规整成**纯文本**：LLM 返回 JSON({intent,key_facts,...}) 时抽值拼成中文短句，

        避免原始 JSON（含 key_facts/转义引号/markdown 围栏）污染会话 summary
        （§14-7 在 LLM 模式下亦无嵌套）。非 JSON → 原样返回。
        """
        import re as _re

        t = (s or "").strip()
        if not t:
            return t
        # 先剥 markdown 代码围栏 ```json ... ```（Haiku 常这样包 JSON）。
        fence = _re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", t, _re.DOTALL)
        if fence:
            t = fence.group(1).strip()
        if "key_facts" not in t and not t.lstrip().startswith("{"):
            return t
        try:
            import json as _json
            d = _json.loads(t)
            if isinstance(d, dict):
                bits: List[str] = []
                if d.get("intent"):
                    bits.append(str(d["intent"]))
                for k in ("key_facts", "constraints", "open_items"):
                    v = d.get(k)
                    if isinstance(v, list):
                        bits += [str(x) for x in v if x]
                    elif v:
                        bits.append(str(v))
                return "；".join(bits)[:800] or t
        except Exception:  # noqa: BLE001 - 不是合法 JSON → 去结构噪声兜底
            pass
        t = _re.sub(r'```[a-zA-Z]*|["{}\[\]`]|key_facts|constraints|open_items|intent', "", t)
        t = _re.sub(r"(?m)^\s*[:：,，]\s*$", "", t)   # 去掉只剩冒号/逗号的空行
        return _re.sub(r"\n{2,}", "\n", t).strip()[:800]

    def _resummarize_session(self, prior_summary: str, overflow: List[Dict[str, str]]) -> str:
        """#2：把（旧 summary + 新溢出轮）重摘成**结构化纯文本**（目标/决策/未决项/主题）。

        仅在 LLM 可用时由 record_turn 调用。喂旧 summary 是安全的：LLM 输出经固定 schema **有界**且
        渲染为纯文本（无 JSON 嵌套），不会像离线兜底那样递归膨胀（§14-7）。失败 → 退回旧 summary。
        """
        from ..llm.client import LLM, LLMStructuredError, LLMUnavailable
        from ..contracts.enums import ModelTier
        from pydantic import BaseModel, Field

        class _SessionDigest(BaseModel):
            goal: str = Field(default="", description="会话的原始诉求/总目标")
            decisions: List[str] = Field(default_factory=list, description="已达成的结论/决策")
            open_items: List[str] = Field(default_factory=list, description="未决/待办")
            topics: List[str] = Field(default_factory=list, description="聊过的主题")

        convo = "\n".join(f"用户：{r['user'][:120]} / 回复：{r['reply'][:120]}" for r in overflow)
        prompt = (
            f"已有会话概要（参考，可能零散）：\n{prior_summary or '（无）'}\n\n"
            f"新增对话（更近）：\n{convo}\n\n"
            "把两者**合并重组**成一份简洁的会话状态（不要逐轮流水账）：goal=贯穿全程的原始诉求；"
            "decisions=已确定的结论；open_items=仍未决/待办；topics=聊过的主题。各 ≤4 条、每条 ≤30 字。")
        try:
            obj, res = LLM.complete_structured(
                prompt, _SessionDigest, model_tier=ModelTier.HAIKU, max_tokens=400, timeout_s=20,
                system="你把多轮对话压成结构化会话状态，只输出 schema 字段，简洁、不编造。")
            self.last_cost_usd = getattr(res, "cost_usd", 0.0)
        except (LLMUnavailable, LLMStructuredError, Exception):  # noqa: BLE001
            return prior_summary
        if obj is None:
            return prior_summary
        # 渲染为**纯文本**（绝不 json.dumps，保证无 key_facts/转义引号嵌套，§14-7）。
        parts = []
        if obj.goal:
            parts.append(f"目标：{obj.goal}")
        if obj.decisions:
            parts.append("已决策：" + "；".join(obj.decisions[:4]))
        if obj.open_items:
            parts.append("未决项：" + "；".join(obj.open_items[:4]))
        if obj.topics:
            parts.append("聊过：" + "、".join(obj.topics[:5]))
        return "\n".join(parts)[:800] or prior_summary

    # ---------------- AGGREGATE ----------------
    def aggregate(
        self,
        responses: List[ResponsePayload],
        plan: Optional[List[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta = meta or {}

        # NEEDS_INPUT 追问回路（Design §5.2）：worker 缺必填信息 → 不强答，向用户回澄清问题，
        # 暂停等待补全（无状态 HTTP 下即返回问题，下一轮用户回复携带答案）。
        questions = self._clarifying_questions(responses)
        if questions and not any(r.status == Status.ERROR for r in responses):
            return {
                "reply_text": "为完成你的请求，我还需要你补充以下信息：" + "；".join(questions),
                "citations": [],
                "next_actions": [f"请补充：{q}" for q in questions],
                "status": Status.NEEDS_INPUT.value,
            }

        status = Status.OK
        if any(r.status == Status.ERROR for r in responses):
            status = Status.PARTIAL
        elif any(r.status == Status.ESCALATE for r in responses):
            status = Status.ESCALATE

        # 单个 worker 已产出面向用户的答案（如 QA 的 answer）时直接透传，
        # 不要再过一次 Manager LLM 聚合（否则会把好答案压成「操作已完成」之类的废话，且多花一次调用）。
        reply = self._direct_reply(responses)
        if reply is None:
            reply = self._llm_aggregate(responses)
        if reply is None:
            reply = self._template_reply(responses, meta)

        next_actions: List[str] = []
        if meta.get("notion_url"):
            next_actions.append(f"📘 学习计划已同步到 Notion：{meta['notion_url']}")
        if meta.get("report_path"):
            next_actions.append(f"📄 学习计划报告已生成：{meta['report_path']}")
        if meta.get("plan_image_path"):
            next_actions.append(f"🖼️ 学习计划信息图已生成：{meta['plan_image_path']}")
        if meta.get("suggest_mock"):
            # §5.6 P5：建议 mock，但不自动启动（控制权交回用户）。
            next_actions.append("建议进行一场模拟面试以巩固/采集数据（需你确认后开始，系统不会自动开始）。")
        if meta.get("handoff_summary"):
            # §6b：mock 升级交回 Manager，已生成交接摘要供后续深入。
            next_actions.append("已根据模拟面试中的难点生成交接摘要，可转入问答/诊断进一步深入。")

        from ..integrations.gpt_image import asset_url

        return {
            "reply_text": reply,
            "citations": [],
            "next_actions": next_actions,
            "status": status.value,
            "image_url": asset_url(meta.get("plan_image_path")),
            "image_spec": meta.get("plan_image_spec"),  # 供前端按需出图（无 image_url 时）
        }

    @staticmethod
    def _clarifying_questions(responses: List[ResponsePayload]) -> List[str]:
        """从 NEEDS_INPUT 响应中抽取要向用户追问的澄清问题（取 diff.rationale / reason 兜底）。"""
        questions: List[str] = []
        for r in responses:
            if r.status != Status.NEEDS_INPUT:
                continue
            diff = r.result.get("diff") or {}
            q = diff.get("rationale") or r.result.get("reason") or "需要你补充更多信息以继续。"
            questions.append(q)
        return questions

    @staticmethod
    def _direct_reply(responses: List[ResponsePayload]) -> Optional[str]:
        """单个成功 worker 已带面向用户的答案 → 直接透传，跳过 LLM 聚合。

        复合（多 worker）请求仍走 _llm_aggregate 整合。
        """
        if len(responses) != 1:
            return None
        r = responses[0]
        if r.status != Status.OK:
            return None
        res = r.result or {}
        # 简历诊断：直接用结构化渲染器（含逐条证据包/改写），不要再过 LLM 聚合重写（会重写+截断）。
        if res.get("kind") == "resume_diagnosis":
            try:
                from ..agents.diagnosis.resume import render_resume_diagnosis
                from ..contracts.agents.diagnosis import ResumeDiagnosis
                diag = ResumeDiagnosis.model_validate({k: v for k, v in res.items() if k != "kind"})
                return render_resume_diagnosis(diag)
            except Exception:  # noqa: BLE001 - 渲染失败回退到下游聚合
                pass
        ans = res.get("answer")  # QA 的自然语言答案
        if isinstance(ans, str) and len(ans.strip()) >= 10:
            return ans.strip()
        return None

    def _llm_aggregate(self, responses: List[ResponsePayload]) -> Optional[str]:
        try:
            from ..llm.client import LLM

            if LLM.available and self.skill is not None and responses:
                self.require_tool("llm.complete")
                # 注意：不要复用 Manager 的 DAG 规划 system_prompt（那会让模型输出
                # {agent,task_type,deps} 而非给用户的自然回复）。聚合用独立 prompt。
                summary = LLM.complete(
                    prompt="把以下各 worker 的结果整合成给用户的 LearnForge 风格回复：\n"
                    + "\n".join(str(r.result) for r in responses),
                    model_tier=self.skill.spec.model_tier,
                    system=(
                        "你是 LearnForge 回复聚合器。保留下游 worker 的结构化价值，输出自然中文；"
                        "不要输出 JSON 字段名。QA 回答要保留核心结论、机制、取舍、误区/练习点；"
                        "计划/诊断要保留优先级依据、每日目标、验收标准和下一步行动。"
                        "避免把多步结果压成一句“已完成”。"
                    ),
                    max_tokens=512,
                )
                return summary.text
        except Exception:
            return None
        return None

    @staticmethod
    def _template_reply(responses: List[ResponsePayload], meta: Dict[str, Any]) -> str:
        if meta.get("skipped_modify"):
            return "诊断信号不足，已跳过改计划；建议先做一场模拟面试采集数据后再优化路径。"
        return f"[stub aggregate] 已收集 {len(responses)} 个 worker 结果。"

    # ---------------- 一站式入口（供 app/测试，Design §5.6）----------------
    def handle(self, user_input: str, active_mock: Optional[str] = None,
               trace_id: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
        trace_id = trace_id or str(uuid.uuid4())
        if active_mock:
            # 会话锚定在进行中的 mock → 直接续跑，不走 ReAct 路由。
            responses, meta, plan = self.run_active_mock(user_input, active_mock, trace_id)
        else:
            # Manager 作为 ReAct 编排者：每步看子 agent 结果逐步决策（动作空间=4 子 agent + finish）。
            # session_id 传入 → execute_dynamic 装配有界会话上下文注入子 agent（阶段一）。
            responses, meta, plan = self.execute_dynamic(
                user_input, trace_id=trace_id, session_id=session_id)
        agg = self.aggregate(responses, plan, meta)
        agg["plan"] = plan
        agg["replan_count"] = 0  # ReAct 无 replan 循环；保留字段做向后兼容。
        agg["trace_id"] = trace_id
        return agg

    # ---------------- handoff_summary（Q2 / Design §6b）----------------
    def _handoff_from_escalation(self, mock_result: Dict[str, Any]) -> ContextEnvelope:
        """从 mock escalate 结果构造交接摘要（供后续 QA/诊断/规划接力，Design §5.4/§6b）。"""
        action = mock_result.get("escalate_action") or "escalate"
        turn_index = mock_result.get("turn_index", 0)
        scores = mock_result.get("turn_scores") or []
        key_facts = [f"模拟面试进行 {turn_index} 轮后触发交回", f"触发动作={action}"]
        if scores:
            key_facts.append(f"已采集 {len(scores)} 轮评分")
        return self.make_handoff_summary(
            intent=f"mock_escalation:{action}",
            key_facts=key_facts,
            open_items=["用户在模拟面试中要求转交（如改计划/换能力），需 Manager 接力后续意图。"],
        )

    def make_handoff_summary(
        self,
        intent: str,
        key_facts: Optional[List[str]] = None,
        constraints: Optional[List[str]] = None,
        open_items: Optional[List[str]] = None,
    ) -> ContextEnvelope:
        """跨子系统 handoff 携带的 ≤512tok 摘要。

        统一走 MEMORY.summarize（与跨轮 conversation_summary 同一个摘要器），不再各写一套。
        """
        from ..memory.base import MEMORY

        summary = MEMORY.summarize(
            {
                "intent": intent,
                "key_facts": key_facts or [],
                "constraints": constraints or [],
                "open_items": open_items or [],
            },
            max_tokens=HANDOFF_SUMMARY_MAX_TOKENS,
        )
        return ContextEnvelope(handoff_summary=summary)

    # ---------------- 写边界（唯一写者，Design §2a）----------------
    @staticmethod
    def _resolve_path_id(existing_path_ref: Optional[str], user_id: str) -> str:
        """把路径引用解析为稳定 path_id：真实 id 直接用，'current'/空 → 每用户单路径。"""
        if existing_path_ref and existing_path_ref != "current":
            return existing_path_ref
        return f"path-{user_id}"

    @staticmethod
    def _diff_is_empty(diff: PathDiff) -> bool:
        return not (diff.add or diff.remove or diff.reorder)

    def _atom_exists(self, atom_id: str) -> bool:
        """commit 前校验 atom 存在（path_items → knowledge_atoms 外键约束）。"""
        try:
            self.require_tool("repository.read.atoms")
            from ..storage.repositories import AtomRepository

            return AtomRepository(db_path=self._db_path).get(atom_id) is not None
        except Exception:
            return False

    def apply_path_diff(
        self,
        diff: PathDiff,
        existing_path_ref: Optional[str] = None,
        user_id: str = "default",
        trace_id: Optional[str] = None,
    ) -> Optional[str]:
        """把 PlanningAgent 的增量 PathDiff 应用到现有路径并落库（唯一写者，Design §3.7/§2a）。

        读现有路径 → remove → add（过滤不存在的 atom，避免破坏外键）→ reorder → 落库
        → emit PATH_CHANGED（§4c）。空 diff 直接 no-op。返回写入的 path_id（no-op/失败为 None）。
        """
        self.require_tool("repository.write.learning_path")
        if self._diff_is_empty(diff):
            return None
        path_id = self._resolve_path_id(existing_path_ref, user_id)
        try:
            repo = LearningPathRepository(db_path=self._db_path)
            existing = repo.get(path_id)
            items: List[PathItem] = list(existing.items) if existing else []

            if diff.remove:
                rm = set(diff.remove)
                items = [it for it in items if it.item_id not in rm]

            existing_ids = {it.item_id for it in items}
            added = [
                it for it in diff.add
                if it.item_id not in existing_ids and self._atom_exists(it.atom_id)
            ]
            items.extend(added)

            if diff.reorder:
                order_map = {op.item_id: op.new_order for op in diff.reorder}
                for it in items:
                    if it.item_id in order_map:
                        it.order_idx = order_map[it.item_id]
            items.sort(key=lambda it: it.order_idx)

            self.commit_path(path_id, items, user_id=user_id)
        except Exception:
            return None
        self._emit_path_changed(path_id, diff, len(added), trace_id)
        return path_id

    def commit_path(self, path_id: str, items: List[PathItem], user_id: str = "default") -> None:
        from ..storage.repositories import UserProfileRepository

        # learning_paths.user_id → user_profile 外键：确保用户档案行存在再落路径。
        prof_repo = UserProfileRepository(db_path=self._db_path)
        if prof_repo.get(user_id) is None:
            prof_repo.upsert(UserProfile(user_id=user_id))
        LearningPathRepository(db_path=self._db_path).commit(
            LearningPath(path_id=path_id, items=items), user_id=user_id
        )

    def _emit_path_changed(self, path_id: str, diff: PathDiff, added_count: int,
                           trace_id: Optional[str] = None) -> None:
        """路径变更后写 interaction_event(path_changed)（§4c）。append-only，best-effort。"""
        try:
            self.require_tool("repository.write.events")
            atom_refs = [it.atom_id for it in diff.add][:5]
            EventRepository(db_path=self._db_path).append(
                EventPayload(
                    event_type=EventType.PATH_CHANGED,
                    topic="general",
                    atom_refs=atom_refs,
                    signal={"added": added_count, "removed": len(diff.remove),
                            "reordered": len(diff.reorder), "path_id": path_id},
                ),
                event_id=str(uuid.uuid4()),
                trace_id=trace_id,
            )
        except Exception:
            pass

    def commit_mastery(self, updates: List[Dict[str, Any]]) -> int:
        """掌握度落库（唯一写者，Design §4b/§G2）。updates: [{atom_id, signal, source}]。

        读旧分 → mastery.update_mastery → 落库 + 重算 tier。返回成功更新条数。
        """
        self.require_tool("repository.write.mastery")
        if not updates:
            return 0
        from ..mastery import to_tier, update_mastery
        from ..storage.repositories import AtomRepository

        repo = AtomRepository(db_path=self._db_path)
        done = 0
        for u in updates:
            try:
                atom = repo.get(u["atom_id"])
                old = atom.mastery_score if atom else 0.0
                new = update_mastery(old, float(u["signal"]), u.get("source", "qa"))
                if repo.update_mastery(u["atom_id"], new, to_tier(new).value,
                                       correct=float(u["signal"]) >= 0.6):
                    done += 1
            except Exception:
                continue
        return done

    # ---------------- mock 结算 + post_mock 自动诊断（Q5 / §5.5）----------------
    def settle_mock(self, session_id: str) -> Dict[str, Any]:
        """mock S7 结算后调用：提交掌握度（唯一写者）+ 自动触发只读诊断（成本单独记账）。

        诊断失败不影响 mock 结算（Design T-M4-03 AC③）。
        """
        committed = 0
        updates: List[Dict[str, Any]] = []
        try:
            updates = self.mock.mastery_updates(session_id)
            committed = self.commit_mastery(updates)
        except Exception:
            committed = 0
        diagnosis = None
        diag_cost = 0.0
        try:
            diag_result = self.diagnosis.run(
                DiagnosisInput(time_window=TimeWindow.D30, trigger=DiagnosisTrigger.POST_MOCK)
            )
            diag_cost = self.diagnosis.last_cost_usd
            diagnosis = diag_result.model_dump()
            self._persist_diagnosis(diagnosis, TimeWindow.D30.value,
                                    DiagnosisTrigger.POST_MOCK.value, diag_result.confidence)
        except Exception:
            diagnosis = None
        # 把本场薄弱点/反馈写入 daily 记忆（kind=weak 慢衰减）。
        self._remember_mock(session_id, updates, diagnosis)
        # 出图改为按需（前端经 /ui/image 触发）；仅 LF_GPT_IMAGE_AUTO 开启时在结算里同步出图。
        diagnosis_image_path = None
        try:
            from ..integrations import gpt_image as _gpt_image

            if diagnosis and diagnosis.get("clusters") and _gpt_image.auto_enabled():
                res = _gpt_image.generate_diagnosis_chart(
                    clusters=diagnosis.get("clusters") or [],
                    weak_atoms=diagnosis.get("weak_atoms") or [],
                    recommendations=diagnosis.get("recommendations") or [],
                )
                if res.get("ok"):
                    diagnosis_image_path = res.get("path")
        except Exception:
            diagnosis_image_path = None
        return {"mastery_committed": committed, "diagnosis": diagnosis,
                "diagnosis_cost_usd": diag_cost, "diagnosis_image_path": diagnosis_image_path}
