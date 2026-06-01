"""Phase 3 paired evaluation：串行协作 orchestrate vs Phase 2 单任务 handle。

在同一组带「期望 agent 序列」的多步骤任务上对比两代：
1. 多步骤任务完成率（期望步骤是否都真实执行）—— Phase 3 应显著高于 Phase 2。
2. next_actions 是否真实驱动后续任务（统计被 followup 派生的步数）。
3. 重复 / 无意义派发（同 (agent,task_type) 是否出现多次）—— 应为 0。
4. 简单 QA 是否退化（单步任务两代行为应一致）。

全离线确定性。用法：python -m learnforge.orchestrator.eval_collaborate
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .manager import ManagerAgent

# (用户输入, 期望 agent 序列)。前两条多步骤，后两条单步（回归对照）。
DATASET: List[Tuple[str, List[str]]] = [
    ("帮我诊断 Redis 薄弱点并给学习计划", ["diagnosis", "planning"]),
    ("我要准备 mock 面试，并根据结果生成复习计划", ["mock", "diagnosis", "planning"]),
    ("乐观锁还是悲观锁?", ["qa"]),
    ("解释一下 TCP 三次握手", ["qa"]),
]


@dataclass
class Report:
    p2_completion: float
    p3_completion: float
    p3_followup_driven_steps: int
    p3_duplicate_dispatches: int
    qa_regression: bool
    total: int
    multistep_total: int


def _p2_executed_agents(mgr: ManagerAgent, text: str) -> List[str]:
    """Phase 2 handle 只跑 seed 这一个任务。"""
    out = mgr.handle(text)
    return [out["task"]["target_agent"]] if out["status"] != "failed" else []


def _p3_executed_agents(mgr: ManagerAgent, text: str):
    out = mgr.orchestrate(text)
    agents = [s["target_agent"] for s in out["trace"] if s["status"] == "completed"]
    followup_driven = sum(1 for s in out["trace"] if s["spawned_by"] is not None)
    keys = [(s["target_agent"], s["task_type"]) for s in out["trace"]]
    duplicates = len(keys) - len(set(keys))
    return agents, followup_driven, duplicates, out


def _covered(expected: List[str], executed: List[str]) -> bool:
    """期望序列是否都被执行（顺序敏感的子序列覆盖）。"""
    it = iter(executed)
    return all(agent in it for agent in expected)


def run() -> Report:
    mgr = ManagerAgent()
    total = len(DATASET)
    multistep = [(t, e) for t, e in DATASET if len(e) > 1]

    p2_done = p3_done = 0
    followup_driven = duplicates = 0
    qa_regression = False

    for text, expected in DATASET:
        p2_exec = _p2_executed_agents(mgr, text)
        p3_exec, fd, dup, p3_out = _p3_executed_agents(mgr, text)
        followup_driven += fd
        duplicates += dup

        if len(expected) > 1:
            p2_done += int(_covered(expected, p2_exec))
            p3_done += int(_covered(expected, p3_exec))
        else:
            # 单步：检查两代是否都正常完成且一致（回归）。
            p2_out = mgr.handle(text)
            if not (p2_out["status"] == "ok" and p3_out["status"] == "ok"):
                qa_regression = True

    n_multi = len(multistep)
    return Report(
        p2_completion=(p2_done / n_multi) if n_multi else 0.0,
        p3_completion=(p3_done / n_multi) if n_multi else 0.0,
        p3_followup_driven_steps=followup_driven,
        p3_duplicate_dispatches=duplicates,
        qa_regression=qa_regression,
        total=total,
        multistep_total=n_multi,
    )


def main() -> None:
    r = run()
    print("=" * 60)
    print("Phase 3 paired evaluation：串行协作 vs Phase 2 单任务分发")
    print("=" * 60)
    print(f"样本数: {r.total}（其中多步骤任务: {r.multistep_total}）")
    print(f"[1] 多步骤完成率  Phase 2 = {r.p2_completion:.0%} | "
          f"Phase 3 = {r.p3_completion:.0%} "
          f"→ {'提升 ✓' if r.p3_completion > r.p2_completion else '未提升 ✗'}")
    print(f"[2] next_actions 驱动  Phase 3 由 followup 派生的后续步数 = "
          f"{r.p3_followup_driven_steps}（>0 说明真实驱动而非文本建议）")
    print(f"[3] 重复/无意义派发  重复派发次数 = {r.p3_duplicate_dispatches} "
          f"→ {'无 ✓' if r.p3_duplicate_dispatches == 0 else '有 ✗'}")
    print(f"[4] 简单 QA 回归  {'退化 ✗' if r.qa_regression else '无退化 ✓'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
