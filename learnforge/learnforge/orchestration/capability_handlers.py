"""能力 Handler 实现（轻量 DDD 重构 Phase 3：真正接线）。

把 `ManagerAgent._dispatch_impl` 里硬编码的 `if agent=="qa"/"diagnosis"/"planning"/"mock"`
四段**原样搬进**各自的 `CapabilityHandler`，由 `HandlerRegistry` 分发。新增能力 = 加一个 handler
并注册，**不再改 Manager 主循环**。

行为保持:每个 handler.run 复刻对应分支的全部副作用（emit/persist/remember/sub-route/status 映射），
产 `AgentResult`；Manager 用 `.to_response_payload()` 投影回现有 `ResponsePayload`，对下游字节级等价
（黄金回归 tests/test_dispatch_golden.py 守护）。写仍是 Manager 唯一写者——handler 只读子 agent + 调
Manager 既有的写/记忆/埋点方法，不自己落 mastery/path。

handler 持有 manager 引用（同一实例的子 agent 与共享方法）；**不 import manager 模块**（避免环）。
设计文档见 docs/architecture/refactor-plan.md。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from ..contracts.agents.diagnosis import DiagnosisInput, DiagnosisResult
from ..contracts.agents.mock import InterviewContext, MockInput
from ..contracts.agents.planning import PlanningInput
from ..contracts.agents.qa import QAInput
from ..contracts.enums import DiagnosisTrigger, PlanMode, Status, TimeWindow
from ..contracts.intent import Capability
from ..contracts.result import AgentResult, NextRequest, NextRequestKind
from .handler_registry import HandlerRegistry

# 诊断信号不足阈值（与 diagnosis_agent 文档口径一致：confidence<0.5 视为数据不足）。
_DIAG_LOW_CONFIDENCE = 0.5

if TYPE_CHECKING:  # 仅类型；运行期不导入 manager，避免 manager↔handler 循环。
    from .manager import ManagerAgent


class _ManagerBoundHandler:
    """共享基类:持有 Manager 实例（拿子 agent + 复用其写/记忆/埋点方法）。"""

    capability: Capability

    def __init__(self, manager: "ManagerAgent") -> None:
        self.m = manager


class QACapabilityHandler(_ManagerBoundHandler):
    capability = Capability.QA

    def run(self, request: Any, context: Dict[str, Any]) -> AgentResult:
        m = self.m
        # 阶段一：把有界会话上下文注入 QA prompt。
        out = m.qa.run(QAInput(question=request,
                               session_context=context.get("session_context", "")))
        m._emit_qa_signal(out)
        m.remember_qa(request, out)
        return AgentResult(status=Status.OK, confidence=out.confidence,
                           payload=out.model_dump(), cost_usd=m.qa.last_cost_usd,
                           reason=m._reason_for("qa", out))


class DiagnosisCapabilityHandler(_ManagerBoundHandler):
    capability = Capability.DIAGNOSIS

    def run(self, request: Any, context: Dict[str, Any]) -> AgentResult:
        m = self.m
        # 简历诊断子路由（用户单独要求诊断简历时）；复合"准备面试"仍走弱点诊断。
        from ..agents.diagnosis.resume import looks_like_resume_request
        if not context.get("composite") and looks_like_resume_request(request):
            return AgentResult.from_response_payload(
                m._dispatch_resume_diagnosis(request, context))
        trigger = DiagnosisTrigger.COMPOSITE if context.get("composite") else DiagnosisTrigger.USER
        # need_evidence 回路（Phase 5）：若 Manager 已补来证据，作为额外只读接地注入诊断。
        evidence_artifact = context.get("evidence_artifact", "") if context.get("_evidence_gathered") else ""
        out = m.diagnosis.run(DiagnosisInput(time_window=TimeWindow.D30, trigger=trigger,
                                             evidence_context=evidence_artifact))

        # 信号不足 + 非复合 + 还没补过证据 → 不下终态结论，回 need_evidence 让 Manager 调统一证据 worker。
        # （复合"准备面试"流程的空诊断→跳过 modify 语义不变，故 composite 不走此路。）
        want_evidence = (out.confidence < _DIAG_LOW_CONFIDENCE
                         and not context.get("composite")
                         and not context.get("_evidence_gathered"))

        # 只在**终态**（不再要证据时）落库快照，避免一次逻辑诊断写两条 diagnosis_reports。
        if not want_evidence:
            m._persist_diagnosis(out.model_dump(), TimeWindow.D30.value, trigger.value,
                                 out.confidence, context.get("trace_id"))

        result = AgentResult(status=Status.OK, confidence=out.confidence,
                             payload=out.model_dump(), cost_usd=m.diagnosis.last_cost_usd,
                             reason=m._reason_for("diagnosis", out))
        if want_evidence:
            result.next_request = NextRequest(
                kind=NextRequestKind.NEED_EVIDENCE,
                reason="诊断信号不足，请求补充用户材料证据后再诊断。",
                payload={"query": str(request), "source_types": ["attachment", "resume"]},
            )
        elif context.get("_evidence_gathered"):
            # 复诊轮：把补来的证据指针挂到结果（透明），并在 reason 标注。
            refs = context.get("_evidence_refs") or []
            if refs:
                result.evidence_refs = list(refs)
            result.reason = (result.reason + "；已据补充证据复诊").strip("；")
        return result


class PlanningCapabilityHandler(_ManagerBoundHandler):
    capability = Capability.PLANNING

    def run(self, request: Any, context: Dict[str, Any]) -> AgentResult:
        m = self.m
        diag = context.get("diagnosis")
        sctx = context.get("session_context", "")
        if diag is not None:
            pin = PlanningInput(
                mode=PlanMode.MODIFY,
                diagnosis=DiagnosisResult(**diag),
                existing_path_ref=context.get("existing_path_ref", "current"),
                user_feedback=request,
                session_context=sctx,
            )
        else:
            pin = PlanningInput(mode=PlanMode.GENERATE, goal=request, session_context=sctx)
        out = m.planning.run(pin)
        conf = 0.6 if out.status == Status.OK else 0.3
        if out.status == Status.OK:
            # 学习路径生成/调整是阶段性项目决策 → 沉淀到 daily（kind=decision）。
            verb = "调整" if pin.mode == PlanMode.MODIFY else "生成"
            m.remember_decision(f"学习路径已{verb}（{pin.mode.value}）。")
        return AgentResult(status=out.status, confidence=conf,
                           payload=out.model_dump(), cost_usd=m.planning.last_cost_usd,
                           reason=m._reason_for("planning", out))


class MockCapabilityHandler(_ManagerBoundHandler):
    capability = Capability.MOCK

    def run(self, request: Any, context: Dict[str, Any]) -> AgentResult:
        m = self.m
        # 调用方可在 context["interview_context"] 显式传岗位/JD/简历/项目；否则由意图层抽取。
        ic = context.get("interview_context")
        mi = MockInput(topic=request, session_id=context.get("mock_session_id"))
        if ic is not None:
            mi.context = InterviewContext(**ic) if isinstance(ic, dict) else ic
        else:
            m._enrich_mock_from_intent(mi, request)
        out = m.mock.run(mi)
        status = Status.ESCALATE if out.status == "escalate" else Status.OK
        return AgentResult(status=status, confidence=0.5, payload=out.model_dump(),
                           reason=m._reason_for("mock", out))


def build_default_handlers(manager: "ManagerAgent") -> HandlerRegistry:
    """构造并注册四个能力 handler，返回一个 Manager 私有的注册表实例。

    与全局 HANDLER_REGISTRY 隔离:handler 绑定到具体 Manager 实例，故每个 Manager 持有自己的表。
    """
    reg = HandlerRegistry()
    reg.register(QACapabilityHandler(manager))
    reg.register(DiagnosisCapabilityHandler(manager))
    reg.register(PlanningCapabilityHandler(manager))
    reg.register(MockCapabilityHandler(manager))
    return reg
