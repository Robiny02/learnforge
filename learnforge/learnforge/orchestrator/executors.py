"""Agent 执行器（Phase 2 stub + Phase 3 followups）。

每个目标 agent 对应一个执行器：接收 Task，返回结构化 AgentResult。
本阶段是离线确定性 stub，只为打通「结构化任务分发 → 结构化结果 → 串行补派发」闭环；
Phase 5 再把这些 stub 替换为对真实 learnforge.agents.* worker 的薄封装（旧逻辑作 fallback）。

Phase 3：部分执行器在结果里带 followups（机器可读的下一步），由 Manager 串行补派发：
- diagnosis → planning.modify（仅当诊断到薄弱点，避免无意义派发）
- mock → diagnosis（mock 后先诊断，诊断再驱动 planning，形成 mock→diagnosis→planning）
planning / qa 不带 followups（防止 diagnosis↔planning 互相无限派发）。

执行器签名：Callable[[Task], AgentResult]。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .tasks import AgentResult, FollowupHint, Task

AgentExecutor = Callable[[Task], AgentResult]


def qa_executor(task: Task) -> AgentResult:
    return AgentResult(
        result={"answer": f"[qa-stub] 针对「{task.user_goal}」的回答。", "citations": []},
        confidence=0.7,
        reason="qa stub answer",
    )


def planning_executor(task: Task) -> AgentResult:
    # Phase 3：若由 diagnosis 串下来（input_context 带 upstream 诊断结果），则做 modify，
    # 计划里点名薄弱点；否则做 generate。planning 不再外发 followups，避免循环派发。
    upstream = task.input_context.get("upstream") or {}
    weak = (upstream.get("result") or {}).get("weak_atoms") if upstream else None
    if weak:
        plan_text = f"[planning-stub] 针对薄弱点 {('、'.join(weak))} 调整后的学习路径。"
        reason = "planning stub modify (diagnosis-driven)"
    else:
        plan_text = f"[planning-stub] 为「{task.user_goal}」生成的学习路径草案。"
        reason = "planning stub generate"
    return AgentResult(
        result={"plan": plan_text, "based_on_weak_atoms": weak or []},
        confidence=0.6,
        next_actions=["可做一次模拟面试检验计划是否对症"],
        reason=reason,
    )


def diagnosis_executor(task: Task) -> AgentResult:
    weak_atoms: List[str] = ["redis-持久化", "redis-过期策略"]
    followups: List[FollowupHint] = []
    next_actions: List[str] = []
    # 仅当诊断到薄弱点才驱动 planning —— 诊断为空则不派发（无意义派发防护）。
    if weak_atoms:
        next_actions.append("根据诊断出的薄弱点调整学习计划")
        followups.append(FollowupHint(
            target_agent="planning",
            task_type="planning.modify",
            reason="据诊断薄弱点调整学习计划",
        ))
    return AgentResult(
        result={"weak_atoms": weak_atoms, "clusters": ["Redis"]},
        confidence=0.65,
        next_actions=next_actions,
        followups=followups,
        reason="diagnosis stub report",
    )


def mock_executor(task: Task) -> AgentResult:
    # Phase 3：mock 结束后自然先做一次诊断，诊断再驱动 planning。
    return AgentResult(
        result={"summary": "[mock-stub] 一场模拟面试已完成，记录了若干答题表现。",
                "turns": 3},
        confidence=0.55,
        next_actions=["根据本场表现做一次弱点诊断"],
        need_followup=True,  # mock 多轮，用户可继续答题（保持 Phase 2 契约）
        followups=[FollowupHint(
            target_agent="diagnosis",
            task_type="diagnosis",
            reason="据 mock 表现诊断薄弱点",
        )],
        reason="mock stub settled",
    )


DEFAULT_EXECUTORS: Dict[str, AgentExecutor] = {
    "qa": qa_executor,
    "planning": planning_executor,
    "diagnosis": diagnosis_executor,
    "mock": mock_executor,
}


# ---------------- Phase 5a：接入真实 DiagnosisWorker ----------------
def real_diagnosis_executor(
    db_path: Optional[str] = None,
    focus_topics: Optional[List[str]] = None,
) -> AgentExecutor:
    """把真实 DiagnosisAgent（只读弱点诊断）包成执行器，返回结构化 AgentResult。

    - 真实诊断读 interaction_events + knowledge_atoms，离线确定性（无需 API key）。
    - 映射：DiagnosisResult → AgentResult(result{weak_points/weak_atoms/clusters/
      recommendations}, confidence, followups, next_actions)。
    - weak_atoms 仍输出为 atom_id 字符串列表，保持与 stub 一致，下游 planning 无需改动。
    - followups：仅当诊断到薄弱点才发 planning.modify（无意义派发防护，与 stub 同口径）。
    - 数据不足：真实 agent 返回 confidence=0 + 空弱点（honest fallback，非 error）。
    - 真实 agent 内部已兜底；这里再包一层 try/except，异常 → AgentResult(error=...)。

    不改 DiagnosisAgent / memory / retrieval / mock 子图：纯薄封装。
    """

    def _exec(task: Task) -> AgentResult:
        try:
            from ..agents.diagnosis import DiagnosisAgent
            from ..contracts.agents.diagnosis import DiagnosisInput
            from ..contracts.enums import DiagnosisTrigger, TimeWindow

            agent = DiagnosisAgent(db_path=db_path)
            res = agent.run(DiagnosisInput(
                time_window=TimeWindow.D30,
                focus_topics=focus_topics or [],
                trigger=DiagnosisTrigger.USER,
            ))
            react_trace = list(agent.last_react_trace)
        except Exception as exc:  # noqa: BLE001 — 真实 worker 异常 → 降级为 error 结果
            return AgentResult(error="diagnosis_failed", reason=str(exc))

        weak_points: List[Dict[str, Any]] = [wa.model_dump() for wa in res.weak_atoms]
        weak_atom_ids: List[str] = [wa.atom_id for wa in res.weak_atoms]
        clusters: List[Dict[str, Any]] = [c.model_dump() for c in res.clusters]

        followups: List[FollowupHint] = []
        next_actions = list(res.recommendations)
        if weak_atom_ids:
            followups.append(FollowupHint(
                target_agent="planning",
                task_type="planning.modify",
                reason="据真实诊断的薄弱点调整学习计划",
            ))
        return AgentResult(
            result={
                "weak_points": weak_points,
                "weak_atoms": weak_atom_ids,
                "clusters": clusters,
                "recommendations": res.recommendations,
                # 诊断依据（ReAct 三段式轨迹），供 trace_completeness / 失败样本可解释性。
                "react_trace": react_trace,
            },
            confidence=res.confidence,
            next_actions=next_actions,
            followups=followups,
            reason="real diagnosis (DiagnosisAgent, read-only)",
        )

    return _exec


def executors_with_real_diagnosis(
    db_path: Optional[str] = None,
    focus_topics: Optional[List[str]] = None,
) -> Dict[str, AgentExecutor]:
    """默认 stub 执行器，但 diagnosis 换成真实 worker（Phase 5a：只接一个真实 worker）。"""
    execs = dict(DEFAULT_EXECUTORS)
    execs["diagnosis"] = real_diagnosis_executor(db_path=db_path, focus_topics=focus_topics)
    return execs
