"""ManagerAgent —— 编排者 / 唯一对外入口（Design §3.1 / §2b / §5.6）。

Phase 3：plan-and-execute 复合编排 + 唯一写者。
- PLAN：LLM 产有依赖子任务 DAG（无 key 回退关键词；复合"准备面试"→ 诊断→改路径）。
- EXECUTE：按依赖序派发；前序诊断结论注入后续 planning；诊断为空则跳过 modify 改建议先 mock
  （Design §5.6 异常①）。
- AGGREGATE：聚合各 worker 结果为用户回复 + next_actions（复合后建议 mock，控制权交回用户，§5.6 P5）。
- REPLAN：worker error / confidence<0.4 触发重规划，replan_count>2 终止（Design §3.1）。
- 唯一写者（Design §2a/§4b）：commit_path（路径）、commit_mastery（掌握度，mock S7 结算）。
- handoff_summary（Q2/§6b）：跨子系统 escalate/handoff 时由 Haiku 生成 ≤512tok 摘要。
- post_mock 自动诊断（Q5/§5.5）：mock 结算后自动触发一次只读诊断，成本单独记账。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..config import HANDOFF_SUMMARY_MAX_TOKENS, MAX_REPLAN
from ..contracts.agents.diagnosis import DiagnosisInput, DiagnosisResult
from ..contracts.agents.mock import MockInput
from ..contracts.agents.planning import PathItem, PlanningInput
from ..contracts.agents.qa import QAInput
from ..contracts.enums import (
    AgentId,
    DiagnosisTrigger,
    EventType,
    ModelTier,
    PlanMode,
    Status,
    TimeWindow,
)
from ..contracts.message import ContextEnvelope, EventPayload, ResponsePayload
from ..contracts.state import LearningPath
from ..storage.repositories import (
    EventRepository,
    LearningPathRepository,
    TraceRepository,
)
from .base import BaseAgent
from .diagnosis import DiagnosisAgent
from .mock import MockInterviewAgent
from .planning import PlanningAgent
from .qa import QAAgent


class PlanTask(BaseModel):
    agent: str  # qa | planning | diagnosis | mock
    task_type: str = ""
    deps: List[int] = Field(default_factory=list)


class PlanDAG(BaseModel):
    tasks: List[PlanTask] = Field(default_factory=list)


_KEYWORDS = {
    "planning": ("计划", "plan", "路径", "学习"),
    "diagnosis": ("诊断", "弱点", "diagnos"),
    "mock": ("面试", "mock", "模拟"),
}


def _is_empty_diagnosis(diag: Dict[str, Any]) -> bool:
    return not (diag.get("weak_atoms") or diag.get("clusters"))


class ManagerAgent(BaseAgent):
    agent_id = AgentId.MANAGER

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self._db_path = db_path
        self.qa = QAAgent()
        self.planning = PlanningAgent(db_path=db_path)
        self.diagnosis = DiagnosisAgent(db_path=db_path)
        self.mock = MockInterviewAgent(db_path=db_path)

    # ---------------- PLAN ----------------
    def make_plan(self, user_input: str, active_mock: Optional[str] = None) -> List[Dict[str, Any]]:
        if active_mock:
            return [{"agent": "mock", "task_type": "mock", "deps": []}]
        dag = self.llm_structured(
            f"用户请求：{user_input}\n拆成有依赖的子任务 DAG。", PlanDAG, max_tokens=512
        )
        if dag is not None and dag.tasks:
            return [t.model_dump() for t in dag.tasks]
        return self._keyword_plan(user_input)

    @staticmethod
    def _keyword_plan(user_input: str) -> List[Dict[str, Any]]:
        text = user_input.lower()
        # 复合"准备面试"（Design §5.6 "快面试了帮我准备"）→ 诊断 → 改路径。
        prep_cue = any(k in text for k in ("准备", "prepare", "帮我"))
        interview_cue = any(k in text for k in ("面试", "interview"))
        if prep_cue and interview_cue:
            return [
                {"agent": "diagnosis", "task_type": "diagnosis", "deps": []},
                {"agent": "planning", "task_type": "plan.modify", "deps": [0]},
            ]
        for agent, kws in _KEYWORDS.items():
            if any(kw in text for kw in kws):
                return [{"agent": agent, "task_type": agent, "deps": []}]
        return [{"agent": "qa", "task_type": "qa", "deps": []}]

    # ---------------- EXECUTE ----------------
    def dispatch(self, agent: str, user_input: str, context: Dict[str, Any],
                 trace_id: Optional[str] = None) -> ResponsePayload:
        """派发单个子任务给域 worker（同步请求-响应，Design §6a）。

        副作用（best-effort，失败不阻断）：落一条 agent_traces（§9a）；qa 路径 emit qa_signal 事件（§4c）。
        """
        t0 = time.perf_counter()
        resp = self._dispatch_impl(agent, user_input, context)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        self._write_trace(agent, resp, trace_id, latency_ms)
        return resp

    def _dispatch_impl(self, agent: str, user_input: str, context: Dict[str, Any]) -> ResponsePayload:
        if agent == "qa":
            out = self.qa.run(QAInput(question=user_input))
            self._emit_qa_signal(out)
            return ResponsePayload(status=Status.OK, confidence=out.confidence,
                                   result=out.model_dump(), cost_usd=self.qa.last_cost_usd)
        if agent == "diagnosis":
            trigger = DiagnosisTrigger.COMPOSITE if context.get("composite") else DiagnosisTrigger.USER
            out = self.diagnosis.run(DiagnosisInput(time_window=TimeWindow.D30, trigger=trigger))
            return ResponsePayload(status=Status.OK, confidence=out.confidence,
                                   result=out.model_dump(), cost_usd=self.diagnosis.last_cost_usd)
        if agent == "planning":
            diag = context.get("diagnosis")
            if diag is not None:
                pin = PlanningInput(
                    mode=PlanMode.MODIFY,
                    diagnosis=DiagnosisResult(**diag),
                    existing_path_ref=context.get("existing_path_ref", "current"),
                )
            else:
                pin = PlanningInput(mode=PlanMode.GENERATE, goal=user_input)
            out = self.planning.run(pin)
            conf = 0.6 if out.status == Status.OK else 0.3
            return ResponsePayload(status=out.status, confidence=conf,
                                   result=out.model_dump(), cost_usd=self.planning.last_cost_usd)
        if agent == "mock":
            out = self.mock.run(
                MockInput(topic=user_input, session_id=context.get("mock_session_id"))
            )
            status = Status.ESCALATE if out.status == "escalate" else Status.OK
            return ResponsePayload(status=status, confidence=0.5, result=out.model_dump())
        return ResponsePayload(status=Status.ERROR, confidence=0.0, result={},
                               error={"code": "unknown_agent", "message": agent})

    def execute(
        self, plan: List[Dict[str, Any]], user_input: str, trace_id: Optional[str] = None
    ) -> Tuple[List[ResponsePayload], Dict[str, Any]]:
        """按依赖序执行（第一版串行）。返回 (responses, meta)。

        meta 记录复合编排决策（如诊断为空跳过 modify），供 aggregate 生成 next_actions。
        """
        responses: List[ResponsePayload] = []
        context: Dict[str, Any] = {"composite": len(plan) > 1}
        meta: Dict[str, Any] = {"composite": len(plan) > 1, "skipped_modify": False,
                                "suggest_mock": False}

        for task in plan:
            agent = task["agent"]

            # §5.6 异常①：诊断为空 → 跳过 modify，改建议先做 mock 采集数据。
            if (
                agent == "planning"
                and task.get("task_type") == "plan.modify"
                and "diagnosis" in context
                and _is_empty_diagnosis(context["diagnosis"])
            ):
                meta["skipped_modify"] = True
                meta["suggest_mock"] = True
                responses.append(ResponsePayload(
                    status=Status.OK, confidence=0.6,
                    result={"skipped": True, "reason": "诊断信号不足，跳过改计划，建议先做模拟面试采集数据。"},
                ))
                continue

            resp = self.dispatch(agent, user_input, context, trace_id=trace_id)
            responses.append(resp)
            if agent == "diagnosis" and resp.status == Status.OK:
                context["diagnosis"] = resp.result
            if agent == "planning" and resp.status == Status.OK and not _is_empty_diagnosis(
                context.get("diagnosis", {})
            ):
                meta["suggest_mock"] = True  # 改完路径后建议 mock（§5.6 P5）

        return responses, meta

    def execute_with_replan(
        self, plan: List[Dict[str, Any]], user_input: str, trace_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], List[ResponsePayload], Dict[str, Any], int]:
        responses, meta = self.execute(plan, user_input, trace_id=trace_id)
        replan_count = 0
        while self._needs_replan(responses) and replan_count < MAX_REPLAN:
            replan_count += 1
            plan = self.make_plan(user_input)
            responses, meta = self.execute(plan, user_input, trace_id=trace_id)
        return plan, responses, meta, replan_count

    # ---------------- 埋点 / 事件（best-effort）----------------
    def _write_trace(self, agent: str, resp: ResponsePayload, trace_id: Optional[str],
                     latency_ms: int) -> None:
        """每次 dispatch 落一条 agent_traces（§9a）。失败不阻断主流程。"""
        try:
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

    def _emit_qa_signal(self, out: Any) -> None:
        """QA 返回后写 interaction_event(qa_signal)（§4c / T-M4-01）。append-only，非状态写。"""
        try:
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

    @staticmethod
    def _needs_replan(responses: List[ResponsePayload]) -> bool:
        """error 必触发；低置信触发，但排除"诚实低置信"（诊断数据不足 / 受控跳过 / escalate）。

        诊断只读且数据不足时返回低置信是合法终态，重规划同一 DAG 无意义（Design §3.1 语义）。
        """
        for r in responses:
            if r.status == Status.ERROR:
                return True
            if r.status in (Status.ESCALATE, Status.NEEDS_INPUT):
                continue
            honest_low = "weak_atoms" in r.result or r.result.get("skipped")
            if r.confidence < 0.4 and not honest_low:
                return True
        return False

    # ---------------- AGGREGATE ----------------
    def aggregate(
        self,
        responses: List[ResponsePayload],
        plan: Optional[List[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta = meta or {}
        status = Status.OK
        if any(r.status == Status.ERROR for r in responses):
            status = Status.PARTIAL
        elif any(r.status == Status.ESCALATE for r in responses):
            status = Status.ESCALATE

        reply = self._llm_aggregate(responses)
        if reply is None:
            reply = self._template_reply(responses, meta)

        next_actions: List[str] = []
        if meta.get("suggest_mock"):
            # §5.6 P5：建议 mock，但不自动启动（控制权交回用户）。
            next_actions.append("建议进行一场模拟面试以巩固/采集数据（需你确认后开始，系统不会自动开始）。")

        return {
            "reply_text": reply,
            "citations": [],
            "next_actions": next_actions,
            "status": status.value,
        }

    def _llm_aggregate(self, responses: List[ResponsePayload]) -> Optional[str]:
        try:
            from ..llm.client import LLM

            if LLM.available and self.skill is not None and responses:
                summary = LLM.complete(
                    prompt="聚合以下 worker 结果为给用户的简短回复：\n"
                    + "\n".join(str(r.result) for r in responses),
                    model_tier=self.skill.spec.model_tier,
                    system=self.skill.spec.system_prompt,
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

    # ---------------- 复合一站式（供 app/测试，Design §5.6）----------------
    def handle(self, user_input: str, active_mock: Optional[str] = None,
               trace_id: Optional[str] = None) -> Dict[str, Any]:
        trace_id = trace_id or str(uuid.uuid4())
        plan = self.make_plan(user_input, active_mock)
        if active_mock:
            responses, meta = self.execute(plan, user_input, trace_id=trace_id)
            replan_count = 0
        else:
            plan, responses, meta, replan_count = self.execute_with_replan(
                plan, user_input, trace_id=trace_id
            )
        agg = self.aggregate(responses, plan, meta)
        agg["plan"] = plan
        agg["replan_count"] = replan_count
        agg["trace_id"] = trace_id
        return agg

    # ---------------- handoff_summary（Q2 / Design §6b）----------------
    def make_handoff_summary(
        self,
        intent: str,
        key_facts: Optional[List[str]] = None,
        constraints: Optional[List[str]] = None,
        open_items: Optional[List[str]] = None,
    ) -> ContextEnvelope:
        """跨子系统 handoff 携带的 ≤512tok 摘要（由 Haiku 生成，缺 key 回退结构化模板）。"""
        payload = {
            "intent": intent,
            "key_facts": key_facts or [],
            "constraints": constraints or [],
            "open_items": open_items or [],
        }
        summary = None
        try:
            from ..llm.client import LLM

            if LLM.available:
                res = LLM.complete(
                    prompt="把以下交接信息压缩成 ≤512token 的 JSON 摘要"
                    "{intent,key_facts,constraints,open_items}：\n" + json.dumps(payload, ensure_ascii=False),
                    model_tier=ModelTier.HAIKU,
                    max_tokens=HANDOFF_SUMMARY_MAX_TOKENS,
                )
                summary = res.text
        except Exception:
            summary = None
        if summary is None:
            summary = json.dumps(payload, ensure_ascii=False)
        return ContextEnvelope(handoff_summary=summary)

    # ---------------- 写边界（唯一写者，Design §2a）----------------
    def commit_path(self, path_id: str, items: List[PathItem], user_id: str = "default") -> None:
        repo = LearningPathRepository(db_path=self._db_path)
        repo.commit(LearningPath(path_id=path_id, items=items), user_id=user_id)

    def commit_mastery(self, updates: List[Dict[str, Any]]) -> int:
        """掌握度落库（唯一写者，Design §4b/§G2）。updates: [{atom_id, signal, source}]。

        读旧分 → mastery.update_mastery → 落库 + 重算 tier。返回成功更新条数。
        """
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
        try:
            committed = self.commit_mastery(self.mock.mastery_updates(session_id))
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
        except Exception:
            diagnosis = None
        return {"mastery_committed": committed, "diagnosis": diagnosis,
                "diagnosis_cost_usd": diag_cost}
