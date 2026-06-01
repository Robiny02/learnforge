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

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..config import HANDOFF_SUMMARY_MAX_TOKENS, MAX_REPLAN
from ..contracts.agents.diagnosis import DiagnosisInput, DiagnosisResult
from ..contracts.agents.mock import MockInput
from ..contracts.agents.planning import PathDiff, PathItem, PlanningInput
from ..contracts.agents.qa import QAInput
from ..contracts.enums import (
    AgentId,
    DiagnosisTrigger,
    EventType,
    PlanMode,
    Status,
    TimeWindow,
)
from ..contracts.message import ContextEnvelope, EventPayload, ResponsePayload
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
from ..agents.mock import MockInterviewAgent
from ..agents.planning import PlanningAgent
from ..agents.qa import QAAgent


class PlanTask(BaseModel):
    agent: str  # qa | planning | diagnosis | mock
    task_type: str = ""
    deps: List[int] = Field(default_factory=list)


class PlanDAG(BaseModel):
    tasks: List[PlanTask] = Field(default_factory=list)


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

    # ---------------- PLAN ----------------
    def make_plan(
        self,
        user_input: str,
        active_mock: Optional[str] = None,
        conversation_summary: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if active_mock:
            return [{"agent": "mock", "task_type": "mock", "deps": []}]
        # 强意图"准备面试"钉死成确定性复合链 diagnosis→planning：
        # 不交给 LLM 自由拆（它常过度拆解成多个 qa/planning，且会跳过 diagnosis 导致
        # planning 拿不到候选 atom → 计划落不了库）。诊断给出真实弱点主题，planning 才能取到真实 atom。
        text = user_input.lower()
        if any(k in text for k in ("准备", "prepare", "帮我")) and any(
            k in text for k in ("面试", "interview")
        ):
            return [
                {"agent": "diagnosis", "task_type": "diagnosis", "deps": []},
                {"agent": "planning", "task_type": "plan.modify", "deps": [0]},
            ]
        prompt = f"用户请求：{user_input}\n拆成有依赖的子任务 DAG。"
        if conversation_summary:
            # 短期记忆：上一轮会话摘要前置，给规划器跨轮连续性（§6c session 段）。
            prompt = f"会话摘要（上一轮）：{conversation_summary}\n" + prompt
        dag = self.llm_structured(prompt, PlanDAG, max_tokens=512)
        if dag is not None and dag.tasks:
            return self._dedup_tasks([t.model_dump() for t in dag.tasks])
        return self._keyword_plan(user_input)

    @staticmethod
    def _dedup_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """去掉重复子任务（如规划器把一个问题拆成两个相同的 qa）。

        仅当全部任务都无依赖时去重（避免删任务导致 deps 的下标错位）；
        复合（带 deps 的 DAG）原样保留。
        """
        if any(t.get("deps") for t in tasks):
            return tasks
        seen = set()
        out: List[Dict[str, Any]] = []
        for t in tasks:
            key = (t.get("agent"), t.get("task_type"))
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        return out or tasks

    @staticmethod
    def _wants_plan(user_input: str) -> bool:
        """是否应走"诊断→改计划"确定性复合链。

        收窄到两类强意图（避免误吞纯"生成计划"这类单意图——那类走动态路由到 planning）：
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

    def plan_or_dynamic(
        self, user_input: str, active_mock: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """决定本轮走"确定性复合"还是"动态规划"。返回 (plan, is_dynamic)。

        - active_mock → 续跑 mock。
        - 任何"要计划"的意图 → 钉死 diagnosis→planning：诊断给真实弱点主题，planning 才能取到
          真实候选 atom（可落库 + grounded）。弱模型多步链不稳，这步必须确定性，不交给动态决策。
        - 其它（概念问答 / 单独诊断 / 模拟面试）→ 动态规划，Manager 看结果路由到 qa/diagnosis/mock。
        """
        if active_mock:
            return [{"agent": "mock", "task_type": "mock", "deps": []}], False
        if self._wants_plan(user_input):
            return [
                {"agent": "diagnosis", "task_type": "diagnosis", "deps": []},
                {"agent": "planning", "task_type": "plan.modify", "deps": [0]},
            ], False
        return [], True

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
        self.require_tool(f"agent.{agent}")
        t0 = time.perf_counter()
        resp = self._dispatch_impl(agent, user_input, context)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        self._write_trace(agent, resp, trace_id, latency_ms)
        return resp

    def _dispatch_impl(self, agent: str, user_input: str, context: Dict[str, Any]) -> ResponsePayload:
        if agent == "qa":
            out = self.qa.run(QAInput(question=user_input))
            self._emit_qa_signal(out)
            self.remember_qa(user_input, out)
            return ResponsePayload(status=Status.OK, confidence=out.confidence,
                                   result=out.model_dump(), cost_usd=self.qa.last_cost_usd)
        if agent == "diagnosis":
            trigger = DiagnosisTrigger.COMPOSITE if context.get("composite") else DiagnosisTrigger.USER
            out = self.diagnosis.run(DiagnosisInput(time_window=TimeWindow.D30, trigger=trigger))
            self._persist_diagnosis(out.model_dump(), TimeWindow.D30.value, trigger.value,
                                    out.confidence, context.get("trace_id"))
            return ResponsePayload(status=Status.OK, confidence=out.confidence,
                                   result=out.model_dump(), cost_usd=self.diagnosis.last_cost_usd)
        if agent == "planning":
            diag = context.get("diagnosis")
            if diag is not None:
                pin = PlanningInput(
                    mode=PlanMode.MODIFY,
                    diagnosis=DiagnosisResult(**diag),
                    existing_path_ref=context.get("existing_path_ref", "current"),
                    user_feedback=user_input,
                )
            else:
                pin = PlanningInput(mode=PlanMode.GENERATE, goal=user_input)
            out = self.planning.run(pin)
            conf = 0.6 if out.status == Status.OK else 0.3
            if out.status == Status.OK:
                # 学习路径生成/调整是阶段性项目决策 → 沉淀到 daily（kind=decision，R6.4）。
                verb = "调整" if pin.mode == PlanMode.MODIFY else "生成"
                self.remember_decision(f"学习路径已{verb}（{pin.mode.value}）。")
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
        context: Dict[str, Any] = {"composite": len(plan) > 1, "trace_id": trace_id}
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
            self._apply_step(agent, resp, context, meta, trace_id)

        return responses, meta

    def _apply_step(self, agent: str, resp: ResponsePayload, context: Dict[str, Any],
                    meta: Dict[str, Any], trace_id: Optional[str]) -> None:
        """单步善后（静态 execute 与动态规划共用）：handoff 注入 / 诊断入 context / planning 唯一写者落库 + Notion。"""
        # §6b/§5.4：子系统 escalate 交回 Manager → 生成 handoff 摘要，注入后续任务 context。
        if resp.status == Status.ESCALATE:
            env = self._handoff_from_escalation(resp.result)
            context["handoff_summary"] = env.handoff_summary
            meta["handoff_summary"] = env.handoff_summary

        if agent == "diagnosis" and resp.status == Status.OK:
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
            if not _is_empty_diagnosis(context.get("diagnosis", {})):
                meta["suggest_mock"] = True  # 改完路径后建议 mock（§5.6 P5）

    # ---------------- 动态规划（decide_next 循环）----------------
    def decide_next(self, user_input: str, done: List[str],
                    responses: List[ResponsePayload], context: Dict[str, Any]) -> str:
        """看已完成步骤 + 各步结果，决定下一个子 agent 或 finish（动作空间受限、安全）。"""
        from ..llm.client import LLM, LLMStructuredError, LLMUnavailable

        valid = ("qa", "planning", "diagnosis", "mock", "finish")
        kw_first = self._keyword_plan(user_input)[0]["agent"]  # 关键词先验（兜底用）
        if not LLM.available or self.skill is None:
            return kw_first if not done else "finish"

        summaries = "\n".join(
            f"- {a}: {str(r.result)[:100]}" for a, r in zip(done, responses)
        ) or "（无）"
        # 简短 prompt：长 prompt + “已满足→finish” 会把弱模型带偏成过早 finish。
        prompt = (
            f"用户请求：{user_input}\n已做步骤：{done or '无'}\n各步结果：\n{summaries}\n\n"
            "下一步调哪个子 agent？\n"
            "qa=答概念/技术问题；diagnosis=找薄弱点；planning=排学习计划(通常先有 diagnosis)；"
            "mock=模拟面试；finish=用户请求已被满足。"
        )
        # 用独立 system prompt（不要复用 Manager 的 DAG 规划 prompt，否则模型会输出 {agent,task_type,deps} 与 NextStep 冲突）。
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
            nxt = kw_first if not done else "finish"  # 非法/解析失败 → 关键词兜底
        if not done and nxt == "finish":
            nxt = kw_first  # 首步不允许直接 finish（否则一上来就空转）
        return nxt

    def execute_dynamic(
        self, user_input: str, trace_id: Optional[str] = None, max_steps: int = 4
    ) -> Tuple[List[ResponsePayload], Dict[str, Any], List[Dict[str, Any]]]:
        """Manager 作为动态规划者：每步看结果决定下一子 agent，直到 finish/预算。

        动作空间 = {qa, planning, diagnosis, mock, finish}；写仍是确定性 commit（_apply_step）。
        返回 (responses, meta, executed)；executed 供 aggregate/UI 画链路。
        """
        responses: List[ResponsePayload] = []
        # 动态单步路由默认不是 composite（避免把单独 diagnosis 的 trigger 误标成 composite）。
        context: Dict[str, Any] = {"composite": False, "trace_id": trace_id}
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
        """渲染会话短期记忆 = 早期压缩摘要 + 最近 N 轮原文（无记录 → None）。best-effort。"""
        if not session_id:
            return None
        try:
            from ..storage.repositories import SessionStateRepository

            st = SessionStateRepository(db_path=self._db_path).get(session_id)
            if not st:
                MEMORY_LOG.record(READ, "加载会话记忆", "无历史，跳过")
                return None
            parts: List[str] = []
            if st.get("summary"):
                parts.append(f"[早期会话摘要] {st['summary']}")
            rounds = st.get("recent_messages") or []
            for r in rounds:
                parts.append(f"用户：{r.get('user', '')}\n回复：{r.get('reply', '')}")
            if not parts:
                MEMORY_LOG.record(READ, "加载会话记忆", "无历史，跳过")
                return None
            MEMORY_LOG.record(
                READ, "加载会话记忆",
                f"{len(rounds)} 轮原文 + {'有' if st.get('summary') else '无'}早期摘要",
            )
            MEMORY_LOG.record(INJECT, "注入会话摘要", "进入 prompt", count=1)
            return "\n".join(parts)
        except Exception:
            return None

    def record_turn(
        self,
        session_id: Optional[str],
        user_input: str,
        reply: str,
        active_mock: Optional[str] = None,
    ) -> Optional[str]:
        """本轮收尾：追加一轮原文；超过 N 轮则把溢出轮压成 summary，只留最近 N 轮。返回 summary。"""
        if not session_id:
            return None
        try:
            from ..config import SESSION_RECENT_ROUNDS
            from ..memory.base import MEMORY
            from ..storage.repositories import SessionStateRepository

            repo = SessionStateRepository(db_path=self._db_path)
            st = repo.get(session_id) or {}
            summary = st.get("summary") or ""
            rounds = list(st.get("recent_messages") or [])
            rounds.append({"user": (user_input or "")[:500], "reply": (reply or "")[:500]})

            if len(rounds) > SESSION_RECENT_ROUNDS:
                overflow = rounds[:-SESSION_RECENT_ROUNDS]
                rounds = rounds[-SESSION_RECENT_ROUNDS:]
                # 只摘要"新溢出"的轮并追加到既有摘要后；绝不把旧摘要再喂回摘要器
                # （否则离线兜底会递归套娃、无限膨胀，REQUIREMENTS 测试 §14-7）。
                folded = MEMORY.summarize(
                    {"intent": "session_recap",
                     "key_facts": [f"用户：{r['user'][:80]} / 回复：{r['reply'][:80]}"
                                   for r in overflow],
                     "constraints": [], "open_items": []}
                )
                summary = f"{summary} ｜ {folded}".strip(" ｜") if summary else folded
                summary = summary[-800:]  # 防膨胀：保留最近折叠内容
                MEMORY_LOG.record(MAINTAIN, "压缩会话记忆",
                                  f"{len(overflow)} 轮压成摘要，保留最近 {SESSION_RECENT_ROUNDS} 轮")

            repo.upsert(session_id, summary, {"active_mock": active_mock}, rounds)
            return summary
        except Exception:
            return None

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
        if meta.get("suggest_mock"):
            # §5.6 P5：建议 mock，但不自动启动（控制权交回用户）。
            next_actions.append("建议进行一场模拟面试以巩固/采集数据（需你确认后开始，系统不会自动开始）。")
        if meta.get("handoff_summary"):
            # §6b：mock 升级交回 Manager，已生成交接摘要供后续深入。
            next_actions.append("已根据模拟面试中的难点生成交接摘要，可转入问答/诊断进一步深入。")

        return {
            "reply_text": reply,
            "citations": [],
            "next_actions": next_actions,
            "status": status.value,
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
                    prompt="把以下各 worker 的结果整合成一段给用户的简洁中文回复：\n"
                    + "\n".join(str(r.result) for r in responses),
                    model_tier=self.skill.spec.model_tier,
                    system=(
                        "你是回复聚合器。把下游 worker 的结构化结果整合成自然、简洁、面向用户的中文回复；"
                        "直接给结论，不要输出 JSON、不要输出任务/计划结构、不要罗列字段名。"
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

    # ---------------- 复合一站式（供 app/测试，Design §5.6）----------------
    def handle(self, user_input: str, active_mock: Optional[str] = None,
               trace_id: Optional[str] = None) -> Dict[str, Any]:
        trace_id = trace_id or str(uuid.uuid4())
        plan, is_dynamic = self.plan_or_dynamic(user_input, active_mock)
        replan_count = 0
        if is_dynamic:
            # Manager 动态规划：看子 agent 结果逐步决策（动作空间=5 子 agent + finish）。
            responses, meta, plan = self.execute_dynamic(user_input, trace_id=trace_id)
        else:
            responses, meta = self.execute(plan, user_input, trace_id=trace_id)
        agg = self.aggregate(responses, plan, meta)
        agg["plan"] = plan
        agg["replan_count"] = replan_count
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
        return {"mastery_committed": committed, "diagnosis": diagnosis,
                "diagnosis_cost_usd": diag_cost}
