"""个人 local memory 评测（eval/memory_eval）的 pytest 包装 —— 第一阶段 §14 通过标准。

全离线、确定性。完整 hybrid（§15）需向量环境，这里只断言 FTS 通路的第一阶段标准，
并显式断言"未冒称已验证 hybrid"。
"""

from __future__ import annotations

import tempfile

import pytest

from learnforge import config
from learnforge.eval import memory_eval


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    return tmp_path / "mem"


def _full_report(db):
    rep = memory_eval.run_eval(db)
    rep["ranking"] = memory_eval.ranking_checks()
    rep["smoke"] = memory_eval.smoke_checks(db)
    return rep


def test_phase1_all_criteria_pass(mem_dir, tmp_path):
    db = str(tmp_path / "eval.db")
    rep = _full_report(db)
    failed = [name for name, ok in memory_eval.phase1_criteria(rep) if not ok]
    assert not failed, f"未通过的第一阶段标准: {failed}"


def test_phase1_metric_targets(mem_dir, tmp_path):
    rep = memory_eval.run_eval(str(tmp_path / "eval.db"))
    m = rep["metrics"]
    assert m["hit@5"] >= 0.90
    assert m["MRR@5"] >= 0.75
    assert m["kind_accuracy"] >= 0.85 and m["topic_accuracy"] >= 0.85
    assert m["trigger_recall"] >= 0.90 and m["trigger_precision"] >= 0.80
    assert m["negative_no_recall_rate"] >= 0.99  # 负样本不编造（检索层代理）


def test_offline_does_not_claim_hybrid(mem_dir, tmp_path):
    # 离线无向量时必须 degraded/不就绪，绝不冒称 hybrid 已验证（§3.1/§16）。
    rep = memory_eval.run_eval(str(tmp_path / "eval.db"))
    if not rep["env"]["hybrid_ready"]:
        assert rep["env"]["chunk_vectors_table"] is False or rep["env"]["embedding_available"] is False


def test_ranking_decay_not_flattened(mem_dir):
    rk = memory_eval.ranking_checks()
    assert rk["importance_ok"]          # 重要性影响排序
    assert rk["rrf_not_flattened"]      # 单源衰减强度未被外层 RRF 抹平（§14-8）
