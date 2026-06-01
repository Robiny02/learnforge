"""ManagerAgent（Phase 1 路由 → Phase 2 任务分发 → Phase 3 串行协作）。

干净重建轨道的编排器入口，逐 phase 长能力：
- Phase 1：基础路由（decide）——把用户输入交给 QARouter 得到目标 agent。
- Phase 2：结构化任务分发器（create_task / dispatch / handle）——单任务闭环：
  输入→结构化 Task→执行器→结构化 AgentResult→回复；error / 低置信走 fallback。
- Phase 3：串行协作 orchestrator（orchestrate）——seed 任务的 followups 驱动后续
  补派发（diagnosis→planning、mock→diagnosis→planning），串行执行、去重防过度派发、
  全程 trace 可解释，任一步失败可降级。仅串行、无并行、无复杂 DAG / replan（留给 Phase 4）。

设计原则：可测试、可回退、可解释。Phase 3 为新增 orchestrate 入口，Phase 2 的
handle/dispatch 原样保留（便于 paired evaluation）。旧的 learnforge.agents.ManagerAgent
（完整 orchestrator）保持不动，留作 Phase 5 再融合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .executors import DEFAULT_EXECUTORS, AgentExecutor
from .router import QARouter
from .tasks import AgentResult, Task, TaskBoard, TaskStatus

# 低于此置信度视为「不可靠」，触发 fallback（与旧 manager 的 0.4 阈值一致）。
LOW_CONFIDENCE = 0.4

# Phase 3 串行协作单次最多派发的任务数（防 runaway / 无意义派发）。
MAX_TASKS = 4

# task_type 默认与 target_agent 同名；复合/特化类型留给 Phase 3。
_DEFAULT_TASK_TYPE = {
    "qa": "qa",
    "planning": "planning.generate",
    "diagnosis": "diagnosis",
    "mock": "mock",
}


@dataclass(frozen=True)
class DispatchDecision:
    agent: str
    reason: str


class ManagerAgent:
    """编排器：Phase 1 路由 + Phase 2 单任务结构化分发。"""

    def __init__(
        self,
        executors: Optional[Dict[str, AgentExecutor]] = None,
        *_,
        **__,
    ) -> None:
        self.router = QARouter()
        # 允许测试注入自定义执行器（制造 error / 低置信以验证 fallback）。
        # 注意：显式传入空 dict 表示「无执行器」，不能退回默认。
        self.executors: Dict[str, AgentExecutor] = dict(
            DEFAULT_EXECUTORS if executors is None else executors
        )
        self.board = TaskBoard()

    # ---------------- Phase 1：基础路由（保持向后兼容）----------------
    def decide(self, user_input: str) -> DispatchDecision:
        """决定该把这句话交给哪个 agent（仅路由，不执行）。"""
        routed = self.router.route(user_input)
        return DispatchDecision(agent=routed.agent, reason=routed.reason)

    # ---------------- Phase 2：结构化任务分发 ----------------
    def create_task(self, user_input: str) -> Task:
        """把用户输入变成一条结构化 Task 并登记到任务板。"""
        routed = self.router.route(user_input)
        task = Task(
            target_agent=routed.agent,
            user_goal=(user_input or "").strip(),
            task_type=_DEFAULT_TASK_TYPE.get(routed.agent, routed.agent),
            input_context={"route_reason": routed.reason},
        )
        return self.board.add(task)

    def dispatch(self, task: Task) -> AgentResult:
        """派发单条任务给对应执行器，流转任务状态，返回结构化结果。

        执行器抛异常 → 捕获成 AgentResult(error=...)，绝不让 Manager 崩溃。
        无对应执行器 → 也回一条 error 结果。状态流转：pending→running→completed/failed。
        """
        self.board.set_status(task.task_id, TaskStatus.RUNNING)
        executor = self.executors.get(task.target_agent)
        if executor is None:
            result = AgentResult(
                error="unknown_agent",
                reason=f"没有为 {task.target_agent} 注册执行器",
            )
        else:
            try:
                result = executor(task)
            except Exception as exc:  # noqa: BLE001 — 兜住一切执行器异常
                result = AgentResult(error="executor_exception", reason=str(exc))

        task.result = result
        self.board.set_status(
            task.task_id,
            TaskStatus.COMPLETED if result.ok else TaskStatus.FAILED,
        )
        return result

    def handle(self, user_input: str, *_, **__) -> Dict[str, Any]:
        """单任务闭环：建任务 → 派发 → 据结构化结果生成最终回复。

        返回结构化输出，便于后续 phase / 上层复用：
        reply_text / status / task / result / next_actions / need_followup。
        """
        task = self.create_task(user_input)
        result = self.dispatch(task)
        reply_text, status = self._compose_reply(task, result)
        return {
            "reply_text": reply_text,
            "status": status,
            "task": task.to_dict(),
            "result": result.to_dict(),
            "next_actions": result.next_actions,
            "need_followup": result.need_followup,
        }

    # ---------------- Phase 3：串行协作 orchestrator ----------------
    def orchestrate(self, user_input: str, *_, **__) -> Dict[str, Any]:
        """串行协作闭环：seed 任务 → 其 followups 驱动后续补派发 → 串行执行 → 汇总。

        - 仅串行、不并行；后续任务只来自上游成功结果的 followups（next_actions 驱动）。
        - 去重：同 (target_agent, task_type) 不重复派发（防过度/重复派发）。
        - 上限 MAX_TASKS：防 runaway。
        - 失败降级：某步 error 不阻断已完成步骤，整体 status=partial/failed。
        - trace：逐步记录任务流（谁派发的、状态、置信），随返回，全程可解释。
        返回：reply_text / status / tasks / results / next_actions / trace。
        """
        seed = self.create_task(user_input)
        queue: List[Task] = [seed]
        seen = {(seed.target_agent, seed.task_type)}
        steps: List[Tuple[Task, AgentResult]] = []
        trace: List[Dict[str, Any]] = []

        while queue and len(steps) < MAX_TASKS:
            task = queue.pop(0)
            result = self.dispatch(task)
            steps.append((task, result))
            trace.append(self._trace_entry(len(steps), task, result))

            if not result.ok:
                continue  # 失败任务不派生后续（降级）
            # next_actions 的机器可读形态 followups 驱动串行补派发。
            for fu in result.followups:
                key = (fu.target_agent, fu.task_type)
                if key in seen:
                    continue  # 去重：不重复派发
                seen.add(key)
                queue.append(self._spawn_followup(fu, task, result))

        return self._aggregate(steps, trace)

    def _spawn_followup(self, fu: Any, parent: Task, parent_result: AgentResult) -> Task:
        """据 followup 提示生成补派发任务，把上游结果注入 input_context 供下游消费。"""
        task = Task(
            target_agent=fu.target_agent,
            user_goal=parent.user_goal,
            task_type=fu.task_type,
            priority=parent.priority + 1,
            input_context={
                "spawned_by": parent.task_id,
                "spawn_reason": fu.reason,
                "upstream": {"from": parent.target_agent, "result": parent_result.result},
            },
            deps=[parent.task_id],
        )
        return self.board.add(task)

    @staticmethod
    def _trace_entry(step: int, task: Task, result: AgentResult) -> Dict[str, Any]:
        return {
            "step": step,
            "task_id": task.task_id,
            "target_agent": task.target_agent,
            "task_type": task.task_type,
            "status": task.status.value,
            "confidence": result.confidence,
            "spawned_by": task.input_context.get("spawned_by"),
            "reason": task.input_context.get("spawn_reason") or result.reason,
        }

    def _aggregate(
        self, steps: List[Tuple[Task, AgentResult]], trace: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """把串行多步结果汇总成最终回复 + 整体状态（模板拼接，离线确定性）。"""
        any_failed = any(not r.ok for _, r in steps)
        all_failed = steps and all(not r.ok for _, r in steps)
        completed = [(t, r) for t, r in steps if r.ok and r.confidence >= LOW_CONFIDENCE]

        if all_failed:
            status = "failed"
        elif any_failed or len(completed) < len(steps):
            status = "partial"
        else:
            status = "ok"

        if not completed:
            reply = "本次协作未能产出可用结果，请换个说法或更聚焦的目标再试。"
        else:
            lines = [self._step_line(t, r) for t, r in completed]
            reply = "；".join(lines) + "。"

        # 残余 next_actions：已被派发消费的不再重复建议，只透传未驱动的纯文本建议。
        consumed = {(t.target_agent, t.task_type) for t, _ in steps}
        next_actions: List[str] = []
        for t, r in completed:
            for fu in r.followups:
                if (fu.target_agent, fu.task_type) not in consumed:
                    next_actions.append(fu.reason)
            if not r.followups:
                next_actions.extend(r.next_actions)

        return {
            "reply_text": reply,
            "status": status,
            "tasks": [t.to_dict() for t, _ in steps],
            "results": [r.to_dict() for _, r in steps],
            "next_actions": next_actions,
            "trace": trace,
        }

    @staticmethod
    def _step_line(task: Task, result: AgentResult) -> str:
        res = result.result
        weak = res.get("weak_atoms")
        body = (
            res.get("answer")
            or res.get("plan")
            or res.get("summary")
            or (f"诊断到薄弱点：{'、'.join(weak)}" if weak else None)
            or result.reason
        )
        return f"[{task.target_agent}] {body}"

    # ---------------- 回复生成 + fallback ----------------
    def _compose_reply(self, task: Task, result: AgentResult) -> Tuple[str, str]:
        """据结构化结果生成回复文本与整体状态；error / 低置信走合理 fallback。"""
        if not result.ok:
            return (
                f"「{task.target_agent}」处理「{task.user_goal}」时出错，"
                f"暂时无法完成（原因：{result.reason or result.error}）。"
                "你可以换种说法再试，或先做一个更明确的小目标。",
                "failed",
            )
        if result.confidence < LOW_CONFIDENCE:
            return (
                f"我对这次「{task.target_agent}」的结果不太有把握"
                f"（置信度 {result.confidence:.2f}）。建议你补充更多背景，"
                "或换一个更聚焦的问题，我再试一次。",
                "low_confidence",
            )
        answer = result.result.get("answer") or result.result.get("plan") or result.reason
        return (str(answer), "ok")
