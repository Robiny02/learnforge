"""记忆召回阈值闸门单测（离线，合成 cosine 分数；用 eval 实测的量级）。"""

from __future__ import annotations

from learnforge.memory.recall_gate import (
    ABSTAIN,
    ABSTAIN_MESSAGE,
    ACCEPT,
    UNCERTAIN,
    gate_recall,
)


def test_accept_clear_top1():
    d = gate_recall([0.61, 0.40, 0.30])  # 远高于阈值且 margin 足够
    assert d.decision == ACCEPT


def test_abstain_below_threshold():
    # 负样本量级（实测负样本 top sim ≤0.283 < 默认 0.33）→ 判"没有找到明确记忆"。
    d = gate_recall([0.283, 0.20], min_sim=0.33)
    assert d.decision == ABSTAIN
    assert ABSTAIN_MESSAGE in d.reason


def test_uncertain_low_margin():
    # 同主题干扰对：top1 过阈值但与 top2 过近 → 不直接采纳。
    d = gate_recall([0.55, 0.54], min_sim=0.33, margin_thr=0.05)
    assert d.decision == UNCERTAIN


def test_empty_abstains():
    assert gate_recall([]).decision == ABSTAIN


def test_thresholds_are_configurable():
    sims = [0.40, 0.10]
    assert gate_recall(sims, min_sim=0.50).decision == ABSTAIN   # 提高阈值 → 弃用
    assert gate_recall(sims, min_sim=0.30).decision == ACCEPT    # 降低阈值 → 采纳
    assert gate_recall([0.40, 0.39], min_sim=0.30, margin_thr=0.001).decision == ACCEPT


def test_decision_carries_threshold_source():
    d = gate_recall([0.61, 0.40])
    assert d.threshold_source and "eval" in d.threshold_source  # 标注来源是小规模 eval
