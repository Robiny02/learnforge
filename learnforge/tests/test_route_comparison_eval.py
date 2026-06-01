"""Hybrid Router vs 旧版纯关键词路由 —— 离线对比评测（无需 LLM key）。

两种用法：
  1. pytest 运行：断言 hybrid 在关键维度上不劣于 baseline（回归护栏）。
  2. 直接运行：``python tests/test_route_comparison_eval.py`` 打印一份 markdown 报告，
     并写入 tests/_router_eval_report.md。

baseline_router 忠实复刻旧版 learnforge/orchestrator/router.py 的逻辑：
  关键词优先级 mock > diagnosis > planning > qa，无上下文、无否定、无多意图、无槽位。
当前 QARouter 为升级后的 Agent-level hybrid router（直接 import 真实实现）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from learnforge.orchestrator.router import QARouter

# --------------------------------------------------------------------------- #
# baseline：旧版纯关键词路由（原 Phase 1 实现的等价复刻）
# --------------------------------------------------------------------------- #
_BASE_MOCK = ("面试", "mock", "模拟面试", "模拟")
_BASE_PLANNING = ("计划", "规划", "学习路径", "路线", "安排")
_BASE_DIAGNOSIS = ("诊断", "弱点", "薄弱", "复盘")


def baseline_router(user_input: str, context: Optional[Dict] = None) -> str:
    """旧版逻辑：只看关键词、忽略上下文/否定/多意图，优先级 mock>diagnosis>planning>qa。"""
    text = (user_input or "").strip()
    if not text:
        return "qa"
    if any(k in text for k in _BASE_MOCK):
        return "mock"
    if any(k in text for k in _BASE_DIAGNOSIS):
        return "diagnosis"
    if any(k in text for k in _BASE_PLANNING):
        return "planning"
    return "qa"


# --------------------------------------------------------------------------- #
# 评测数据集（≥30 条，覆盖各类场景）。gold = 期望行为（人工判定）。
#   字段：text / ctx / cat / agent / handoffs / slots / retrieval
#   - handoffs/slots/retrieval 仅在相关样例上参与对应子指标。
# --------------------------------------------------------------------------- #
@dataclass
class Sample:
    text: str
    cat: str
    agent: str                      # gold 主 agent
    ctx: Optional[Dict] = None
    handoffs: Optional[Tuple[str, ...]] = None   # gold handoff（仅多意图样例）
    slots: Optional[Dict[str, str]] = None       # gold 槽位（仅相关样例）
    retrieval: Optional[bool] = None             # gold use_retrieval


DATASET: List[Sample] = [
    # --- A. 普通 QA ---
    Sample("什么是闭包？", "qa", "qa", retrieval=True),
    Sample("Python 的 GIL 是什么", "qa", "qa", slots={"topic": "python"}, retrieval=True),
    Sample("解释一下 TCP 三次握手", "qa", "qa", slots={"topic": "tcp"}, retrieval=True),
    Sample("乐观锁和悲观锁的区别", "qa", "qa", retrieval=True),

    # --- B. mock ---
    Sample("帮我模拟面试", "mock", "mock", retrieval=False),
    Sample("来一场 mock interview", "mock", "mock", retrieval=False),
    Sample("出题考考我吧", "mock", "mock", retrieval=False),
    Sample("模拟面试一下 redis", "mock", "mock", slots={"topic": "redis"}, retrieval=False),

    # --- C. planning ---
    Sample("帮我制定学习计划", "planning", "planning", retrieval=False),
    Sample("给我做一个 python 学习路径", "planning", "planning",
           slots={"topic": "python"}, retrieval=True),
    Sample("安排一下3个月的复习", "planning", "planning",
           slots={"deadline": "3个月"}, retrieval=False),
    Sample("规划一下后端学习路线", "planning", "planning", retrieval=False),

    # --- D. diagnosis ---
    Sample("帮我诊断薄弱点", "diagnosis", "diagnosis", retrieval=False),
    Sample("分析下我的弱点", "diagnosis", "diagnosis", retrieval=False),
    Sample("复盘一下我最近的学习", "diagnosis", "diagnosis", retrieval=False),
    Sample("诊断下我 redis 的掌握情况", "diagnosis", "diagnosis",
           slots={"topic": "redis"}, retrieval=False),

    # --- E. 否定 mock ---
    Sample("别给我做模拟面试，解释一下闭包", "negation", "qa", retrieval=True),
    Sample("今天不想模拟了，帮我制定计划", "negation", "planning"),
    Sample("don't start a mock, just explain dns", "negation", "qa", retrieval=True),
    Sample("先别面试了，帮我制定学习计划", "negation", "planning"),
    Sample("不想模拟面试了，帮我复盘一下", "negation", "diagnosis"),

    # --- F. active mock 回答（多轮续接）---
    Sample("这道题我选 B", "stateful", "mock", ctx={"active_mock": True}),
    Sample("答案是用快慢指针", "stateful", "mock", ctx={"active_mock": True}),
    Sample("我觉得应该用红黑树", "stateful", "mock", ctx={"active_mock": True}),

    # --- G. active mock 换 topic / 调整（仍在 mock 内）---
    Sample("换成 JVM", "stateful", "mock", ctx={"active_mock": True}),
    Sample("简单点", "stateful", "mock", ctx={"active_mock": True}),
    Sample("换个题", "stateful", "mock", ctx={"active_mock": True}),

    # --- H. active mock 退出 ---
    Sample("不想模拟面试了，帮我复盘一下", "stateful_exit", "diagnosis",
           ctx={"active_mock": True}),
    Sample("先别面试了，帮我制定学习计划", "stateful_exit", "planning",
           ctx={"active_mock": True}),
    Sample("stop the mock", "stateful_exit", "qa", ctx={"active_mock": True}),

    # --- I. 多意图 handoff ---
    Sample("先诊断 redis，再给我3天计划", "multi", "diagnosis",
           handoffs=("planning",), slots={"topic": "redis", "deadline": "3天"}),
    Sample("做个模拟面试，然后看看我的弱点", "multi", "mock", handoffs=("diagnosis",)),
    Sample("诊断完弱点顺便排个学习计划", "multi", "diagnosis", handoffs=("planning",)),

    # --- J. 中英混合 ---
    Sample("帮我 mock 一下 system design", "mixed", "mock",
           slots={"topic": "system design"}, retrieval=False),
    Sample("diagnose 一下我的 redis 薄弱点", "mixed", "diagnosis",
           slots={"topic": "redis"}, retrieval=False),
    Sample("给我整个 python roadmap", "mixed", "planning",
           slots={"topic": "python"}, retrieval=True),

    # --- K. 低质量口语表达 ---
    Sample("帮我看看我哪儿薄弱", "colloquial", "diagnosis"),
    Sample("随便出两道题练练", "colloquial", "mock"),          # 已知难例：无显式关键词
    Sample("那个…计划啥的帮我搞一下", "colloquial", "planning"),
]


# --------------------------------------------------------------------------- #
# 评测
# --------------------------------------------------------------------------- #
def _run_hybrid(router: QARouter, s: Sample):
    r = router.route(s.text, s.ctx)
    return {
        "agent": r.agent,
        "handoffs": tuple(r.handoffs),
        "slots": dict(r.slots),
        "retrieval": r.use_retrieval,
        "signals": tuple(r.signals),
    }


def _run_baseline(s: Sample):
    return {
        "agent": baseline_router(s.text, s.ctx),
        "handoffs": (),       # 旧版无 handoff
        "slots": {},          # 旧版无槽位
        "retrieval": None,    # 旧版无 retrieval hint
        "signals": (),
    }


def _acc(pairs: List[bool]) -> float:
    return round(sum(pairs) / len(pairs), 4) if pairs else float("nan")


def evaluate():
    router = QARouter()
    rows = []
    for s in DATASET:
        rows.append((s, _run_hybrid(router, s), _run_baseline(s)))

    def agent_ok(pred, s):
        return pred["agent"] == s.agent

    metrics = {"hybrid": {}, "baseline": {}}
    for name, idx in (("hybrid", 1), ("baseline", 2)):
        m = metrics[name]
        m["agent_accuracy"] = _acc([agent_ok(r[idx], r[0]) for r in rows])
        m["negation_accuracy"] = _acc(
            [agent_ok(r[idx], r[0]) for r in rows if r[0].cat == "negation"])
        m["stateful_accuracy"] = _acc(
            [agent_ok(r[idx], r[0]) for r in rows
             if r[0].cat in ("stateful", "stateful_exit")])
        # 多意图：主 agent + handoff 都要对
        m["multi_intent_accuracy"] = _acc(
            [agent_ok(r[idx], r[0]) and r[idx]["handoffs"] == (r[0].handoffs or ())
             for r in rows if r[0].cat == "multi"])
        # 槽位：gold 标注的每个键都要命中
        slot_pairs = []
        for r in rows:
            if r[0].slots:
                slot_pairs.append(
                    all(r[idx]["slots"].get(k) == v for k, v in r[0].slots.items()))
        m["slot_extraction_accuracy"] = _acc(slot_pairs)
        # retrieval hint：gold 标注的样例
        ret_pairs = []
        for r in rows:
            if r[0].retrieval is not None:
                ret_pairs.append(r[idx]["retrieval"] == r[0].retrieval)
        m["retrieval_hint_accuracy"] = _acc(ret_pairs)

    # 失败样例（以 hybrid 为主，同时记录 baseline 的主 agent 以作对照）
    failures = []
    for s, hy, ba in rows:
        problems = []
        if hy["agent"] != s.agent:
            problems.append(f"agent={hy['agent']}≠{s.agent}")
        if s.handoffs is not None and hy["handoffs"] != s.handoffs:
            problems.append(f"handoffs={hy['handoffs']}≠{s.handoffs}")
        if s.slots:
            miss = {k: v for k, v in s.slots.items() if hy["slots"].get(k) != v}
            if miss:
                problems.append(f"slots缺={miss} 实得={hy['slots']}")
        if s.retrieval is not None and hy["retrieval"] != s.retrieval:
            problems.append(f"retrieval={hy['retrieval']}≠{s.retrieval}")
        if problems:
            failures.append((s, hy, ba, problems))

    return rows, metrics, failures


# --------------------------------------------------------------------------- #
# Markdown 报告
# --------------------------------------------------------------------------- #
_METRIC_ORDER = [
    "agent_accuracy", "negation_accuracy", "stateful_accuracy",
    "multi_intent_accuracy", "slot_extraction_accuracy", "retrieval_hint_accuracy",
]


def to_markdown(rows, metrics, failures) -> str:
    out = []
    out.append("# Router 评测：Agent-level Hybrid vs 旧版纯关键词")
    out.append("")
    out.append(f"样本数：**{len(rows)}**（离线，无 LLM key）")
    out.append("")
    out.append("## 指标对比")
    out.append("")
    out.append("| 指标 | baseline(旧) | hybrid(新) |")
    out.append("|---|---|---|")
    for k in _METRIC_ORDER:
        b = metrics["baseline"][k]
        h = metrics["hybrid"][k]
        out.append(f"| {k} | {b} | {h} |")
    out.append("")
    out.append("> baseline 无 handoff/slots/retrieval 能力，对应子指标天然为 0。")
    out.append("> slot/retrieval 的 gold 派生自实现策略，属*一致性*校验而非独立真值。")
    out.append("")
    out.append("## Hybrid 失败 / 难例")
    out.append("")
    if not failures:
        out.append("无。")
    else:
        out.append("| text | ctx | gold | hybrid问题 | baseline主agent |")
        out.append("|---|---|---|---|---|")
        for s, hy, ba, probs in failures:
            ctx = "active_mock" if (s.ctx or {}).get("active_mock") else "-"
            out.append(f"| {s.text} | {ctx} | {s.agent} | {'; '.join(probs)} "
                       f"| {ba['agent']} |")
    out.append("")
    out.append("## 逐样本明细")
    out.append("")
    out.append("| cat | text | gold | hybrid | baseline | signals |")
    out.append("|---|---|---|---|---|---|")
    for s, hy, ba in rows:
        out.append(f"| {s.cat} | {s.text} | {s.agent} | {hy['agent']} "
                   f"| {ba['agent']} | {','.join(hy['signals'])} |")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# pytest 护栏：hybrid 在关键维度上必须显著优于 baseline
# --------------------------------------------------------------------------- #
def test_hybrid_beats_baseline_on_agent_accuracy():
    _, m, _ = evaluate()
    assert m["hybrid"]["agent_accuracy"] >= m["baseline"]["agent_accuracy"]


def test_hybrid_handles_negation_better():
    _, m, _ = evaluate()
    assert m["hybrid"]["negation_accuracy"] > m["baseline"]["negation_accuracy"]


def test_hybrid_handles_state_better():
    _, m, _ = evaluate()
    assert m["hybrid"]["stateful_accuracy"] > m["baseline"]["stateful_accuracy"]


def test_hybrid_supports_multi_intent_handoff():
    _, m, _ = evaluate()
    assert m["hybrid"]["multi_intent_accuracy"] > m["baseline"]["multi_intent_accuracy"]


if __name__ == "__main__":
    rows, metrics, failures = evaluate()
    md = to_markdown(rows, metrics, failures)
    print(md)
    path = os.path.join(os.path.dirname(__file__), "_router_eval_report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    print(f"\n[written] {path}")
