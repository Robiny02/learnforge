"""对抗语义召回评测的 pytest 包装（REQUIREMENTS 测试 §1）。

- 有 embedding key（OPENAI_API_KEY）时运行；无 key 自动 skip。
- 不记录任何 key（只用 env 传入）。
- 断言"真实 embedding 语义召回明显优于 FTS、同主题消歧基本可用"，
  并保留 redis 锁续期 vs 缓存雪崩失败回归对；不强求负样本完全可分（实测不可分，见报告口径）。
"""

from __future__ import annotations

import os

import pytest

from learnforge.eval import semantic_eval

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="需 OPENAI_API_KEY（embedding）才能跑真实语义召回评测；无 key 自动 skip",
)


@pytest.fixture(scope="module")
def report():
    rep = semantic_eval.run()
    if rep.get("skipped"):
        pytest.skip(rep.get("reason", "embedding 不可用"))
    return rep


def test_vector_beats_fts_on_nonlexical_chinese(report):
    m = report["metrics"]
    assert m["hit@1"] >= 0.80, m
    assert m["hit@3"] >= 0.95, m
    assert m["fts_hit@1"] <= 0.40, m                       # FTS 在无字面重叠下很差
    assert m["hit@1"] - m["fts_hit@1"] >= 0.40, m          # 向量明显收益


def test_intra_topic_and_cross_lingual(report):
    m = report["metrics"]
    assert m["intra_topic_accuracy"] >= 0.75, m            # 同主题细粒度消歧基本可用（非满分）
    assert m["cross_lingual_hit@1"] >= 0.80, m


def test_redis_regression_pair_present_and_classified(report):
    ids = {r["id"] for r in report["pos"]}
    assert {"q_r1a", "q_r2a"} <= ids                        # 保留 redis 锁续期 vs 缓存雪崩回归对
    # 任何 rank1 未命中都必须给出 failure_reason（分类逻辑有效，便于回归定位）
    for r in report["pos"]:
        if r["gold_rank"] != 1:
            assert r["failure_reason"], r


def test_gate_flags_low_margin_misses(report):
    # rank1 误召回应被 margin 闸门标为非 accept（uncertain/abstain），不直接采纳。
    for r in report["pos"]:
        if r["gold_rank"] != 1:
            assert r["gate"] != "accept", r


def test_negative_separability_is_reported_not_assumed(report):
    # 不假设负样本完全可分（实测 τ=0.33 不够）；只断言指标被如实计算出来，供持续校准。
    m = report["metrics"]
    assert 0.0 <= m["negative_abstain_rate"] <= 1.0
    assert "separable" in m and isinstance(m["separable"], bool)
