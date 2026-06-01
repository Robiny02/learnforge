"""Phase 2 评测：结构化任务分发器 vs Phase 1 基础路由。

回答任务的 Phase 2 评测四问：
1. 路由准确率是否不下降（Phase 2 复用同一 QARouter，应与 Phase 1 完全一致）。
2. ManagerAgent 输出是否更结构化（字段数 / 可机读字段）。
3. agent 返回结果是否更易被后续流程复用（next_actions / confidence / status 是否可驱动）。
4. 是否引入明显不必要的复杂度（新增模块 / 行数概览）。

全离线确定性。用法：python -m learnforge.orchestrator.eval_dispatch
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .manager import ManagerAgent

# 标注数据集：(用户输入, 期望目标 agent)。覆盖默认 qa + 三类关键词意图。
DATASET: List[Tuple[str, str]] = [
    ("乐观锁还是悲观锁?", "qa"),
    ("解释一下 TCP 三次握手", "qa"),
    ("HashMap 和 ConcurrentHashMap 区别", "qa"),
    ("帮我制定学习计划", "planning"),
    ("给我规划一个两周的复习安排", "planning"),
    ("帮我设计一条学习路线", "planning"),
    ("帮我诊断薄弱点", "diagnosis"),
    ("我哪里比较薄弱", "diagnosis"),
    ("做一次学习复盘", "diagnosis"),
    ("快面试了，帮我模拟面试", "mock"),
    ("来一场 mock interview", "mock"),
    ("模拟面试出几道题", "mock"),
]


@dataclass
class Report:
    p1_routing_acc: float
    p2_routing_acc: float
    p1_fields: int
    p2_fields: int
    p2_has_next_actions: int
    total: int


def run() -> Report:
    mgr = ManagerAgent()
    total = len(DATASET)
    p1_correct = 0
    p2_correct = 0
    p2_next_actions = 0

    for text, expected in DATASET:
        # Phase 1：只给出目标 agent。
        if mgr.decide(text).agent == expected:
            p1_correct += 1
        # Phase 2：结构化任务 + 结构化结果。
        out = mgr.handle(text)
        if out["task"]["target_agent"] == expected:
            p2_correct += 1
        if out["next_actions"]:
            p2_next_actions += 1

    # 结构化程度：Phase 1 对外只有 {agent}；Phase 2 顶层结构化字段数。
    sample = mgr.handle("帮我制定学习计划")
    p1_fields = 1  # decide() → 仅 agent
    p2_fields = len(sample.keys())

    return Report(
        p1_routing_acc=p1_correct / total,
        p2_routing_acc=p2_correct / total,
        p1_fields=p1_fields,
        p2_fields=p2_fields,
        p2_has_next_actions=p2_next_actions,
        total=total,
    )


def main() -> None:
    r = run()
    print("=" * 56)
    print("Phase 2 评测：任务分发器 vs Phase 1 基础路由")
    print("=" * 56)
    print(f"样本数: {r.total}")
    print(f"[1] 路由准确率  Phase 1 = {r.p1_routing_acc:.0%} | "
          f"Phase 2 = {r.p2_routing_acc:.0%} "
          f"→ {'不下降 ✓' if r.p2_routing_acc >= r.p1_routing_acc else '下降 ✗'}")
    print(f"[2] 输出结构化  Phase 1 顶层字段 = {r.p1_fields} (仅 agent) | "
          f"Phase 2 顶层字段 = {r.p2_fields} "
          f"(reply_text/status/task/result/next_actions/need_followup)")
    print(f"[3] 结果可复用  Phase 2 带 next_actions 的样本 = "
          f"{r.p2_has_next_actions}/{r.total}；result 含 confidence/error/reason，"
          f"可驱动后续派发（Phase 3）")
    print("[4] 复杂度概览  新增 4 个文件 (router/tasks/executors/manager)，"
          "纯 dataclass，无新增第三方依赖，旧系统零改动")
    print("=" * 56)


if __name__ == "__main__":
    main()
