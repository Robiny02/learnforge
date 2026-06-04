"""意图分发器（Dispatcher）准确率评测。

两种跑法：
  ① pytest（默认）：关掉 LLM，**评测确定性规则兜底**的准确率（CI 可回归，"链路永远通"）。
     断言离线规则在标注集上 ≥ 阈值，并打印 miss 明细。
  ② 直接运行（带 OPENROUTER_API_KEY）：`python -m pytest -s` 或
     `python tests/test_intent_accuracy.py`——评测 **LLM 主判（gpt-5-mini）** 的准确率，
     只打印报告、不断言（受模型波动影响，不入 CI 门禁）。

标注约定：单意图给 expected_cap（qa|planning|diagnosis|mock|note）；多意图给 expected_mode='multi'。
`ctx` 提供多轮承接/进行态信号（来自 dialogue_turns 派生）。`rules_hard=True` 标记"规则兜底注定
答不出、需 LLM"的样本——用于诚实区分两条路径的能力边界。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from learnforge.intent import Dispatcher

# (text, ctx, expected_cap, expected_mode, rules_hard)
Case = Tuple[str, Dict, Optional[str], Optional[str], bool]

DATASET: List[Case] = [
    # ---- qa：概念 / 聊项目 / 闲聊（大多数）----
    ("什么是RAG", {}, "qa", None, False),
    ("Transformer和RNN的区别", {}, "qa", None, False),
    ("帮我看下我这个project的检索为啥慢", {}, "qa", None, False),
    ("redis的持久化怎么做", {}, "qa", None, False),
    ("解释一下注意力机制", {}, "qa", None, False),
    ("你好呀", {}, "qa", None, False),
    ("帮我debug这段python", {}, "qa", None, False),
    ("rerank和recall有啥不一样", {}, "qa", None, False),

    # ---- planning ----
    ("帮我排个两周RAG复习计划", {}, "planning", None, False),
    ("制定一条学习路径", {}, "planning", None, False),
    ("安排一下我的复习", {}, "planning", None, False),
    ("2周内补齐redis八股", {}, "planning", None, False),
    ("接下来学点啥比较好", {}, "planning", None, True),       # 无关键词，规则注定落 qa

    # ---- diagnosis ----
    ("我哪里薄弱", {}, "diagnosis", None, False),
    ("诊断一下我的学习情况", {}, "diagnosis", None, False),
    ("帮我复盘", {}, "diagnosis", None, False),
    ("我这些题老做错是不是哪没掌握好", {}, "diagnosis", None, True),  # 无关键词，需 LLM

    # ---- mock ----
    ("开始模拟面试", {}, "mock", None, False),
    ("考考我kafka", {}, "mock", None, False),
    ("出几道题练练", {}, "mock", None, False),
    ("来一场面试", {}, "mock", None, False),
    ("我面RAG算法实习，拿我项目拷打我", {}, "mock", None, False),

    # ---- note ----
    ("把这些整理成md笔记", {}, "note", None, False),
    ("生成一份学习报告", {}, "note", None, False),

    # ---- composite / multi ----
    ("快面试了帮我准备", {}, None, "multi", False),
    ("面试冲刺，帮我准备一下", {}, None, "multi", False),

    # ---- 多轮承接 / 进行态（ctx 借上文）----
    ("再加上并发", {"last_capability": "planning"}, "planning", None, False),
    ("再来一题", {"last_capability": "mock"}, "mock", None, False),
    ("换个话题", {"active_mock": True}, "mock", None, False),
]


def _expected_match(route, exp_cap: Optional[str], exp_mode: Optional[str]) -> bool:
    if exp_mode == "multi":
        return route.mode == "multi"
    return route.mode == "single" and route.capability == exp_cap


def evaluate(dispatcher: Dispatcher, dataset: List[Case], rules_only: bool):
    """返回 (accuracy, misses)。rules_only=True 时只统计非 rules_hard 样本（规则能力边界内）。"""
    total = 0
    correct = 0
    misses = []
    for text, ctx, exp_cap, exp_mode, hard in dataset:
        if rules_only and hard:
            continue
        total += 1
        route = dispatcher.route(text, ctx)
        if _expected_match(route, exp_cap, exp_mode):
            correct += 1
        else:
            got = "multi" if route.mode == "multi" else route.capability
            want = exp_mode or exp_cap
            misses.append((text, want, got, route.signals))
    return (correct / total if total else 1.0), misses


def test_rules_path_accuracy(monkeypatch):
    """离线规则兜底在能力边界内（排除 rules_hard）应高准确率——CI 回归门禁。"""
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)

    acc, misses = evaluate(Dispatcher(), DATASET, rules_only=True)
    report = "\n".join(f"  ✗ {t!r}: want={w} got={g} {s}" for t, w, g, s in misses)
    assert acc >= 0.90, f"规则路径准确率 {acc:.0%} 偏低：\n{report}"


def test_carry_uses_context(monkeypatch):
    """承接句借 last_capability：无上下文落 qa，有上下文沿用主线（规则路径也成立）。"""
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    d = Dispatcher()
    assert d.route("再加上并发", {}).capability == "qa"               # 无上文
    assert d.route("再加上并发", {"last_capability": "planning"}).capability == "planning"


def test_self_contained_never_carries(monkeypatch):
    """自包含八股问句即使在主线里也不被劫持（按本句意图答）。"""
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    d = Dispatcher()
    r = d.route("什么是向量检索", {"last_capability": "mock"})
    assert r.capability == "qa"


def _run_live_report():
    """带 key 时评测 LLM 主判（gpt-5-mini）整体准确率；只打印，不断言。"""
    from learnforge.llm.client import LLM
    if not getattr(LLM, "available", False):
        print("LLM 不可用（无 OPENROUTER_API_KEY），跳过 live 评测。")
        return
    acc, misses = evaluate(Dispatcher(), DATASET, rules_only=False)
    print(f"\n=== LLM 主判准确率（gpt-5-mini）：{acc:.0%}  ({len(DATASET)-len(misses)}/{len(DATASET)}) ===")
    for t, w, g, s in misses:
        print(f"  ✗ {t!r}: want={w} got={g} {s}")


if __name__ == "__main__":
    _run_live_report()
