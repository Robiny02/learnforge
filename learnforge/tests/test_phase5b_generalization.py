"""Phase 5b 前置 · 泛化能力评测的回归锁定（baselines + replay）。

这些测试**不设「真实 agent 必须高分」的门槛**（那会退回到自洽回归）。它们锁定的是：
- 评测harness 确定性、可运行、结构正确；
- 「无单一规则全胜」（证明 gold 非自洽）；
- 已实测到的**真实发现**（agent 的局限与稳健性），防止其被悄悄改动而无人察觉：
    * recent_regression：agent 漏诊近期退化、recent_error baseline 能命中；
    * ghost_atom_noise：agent 因缺失 atom 误报；
    * clusters 过度上报 > weak_points；
    * replay：persistent_weak 恒命中、strong 恒不报、declining 低滞后、improving 有“慢遗忘”滞后。
发现一旦改善（例如 agent 修好 ghost/regression），这些测试会提示更新 —— 那是好事。
"""

from learnforge.orchestrator import eval_diagnosis_baselines as B
from learnforge.orchestrator import eval_diagnosis_replay as R


# ============================ baselines / noisy ============================
_BO = B.run()
_SCORES = {s.name: s for s in _BO["scores"]}
_PRED = {s.id: {n: _BO["per_pred"][n][i]["pred"]
                for n in B.PREDICTORS}
         for i, s in enumerate(_BO["scenarios"])}


def test_baseline_harness_runs_all_predictors():
    assert set(_SCORES) == set(B.PREDICTORS)
    assert len(_BO["scenarios"]) >= 12


def test_no_single_rule_dominates():
    # gold 特意构造为规则分歧 → 不应有任何预测器 F1==100%（否则疑似自洽）。
    assert all(s.f1 < 1.0 for s in _SCORES.values()), \
        {n: s.f1 for n, s in _SCORES.items()}


def test_real_agent_competitive_not_perfect():
    real = _SCORES["real_agent"]
    # 与最强 baseline 同档（不弱于其它），但非满分（仍有真实失败）。
    best_baseline = max(_SCORES[n].f1 for n in B.PREDICTORS if n != "real_agent")
    assert real.f1 >= best_baseline - 1e-9
    assert real.f1 < 1.0


# —— 已实测发现：agent 的两处真实弱点 ——
def test_finding_recent_regression_missed_by_agent():
    # 近期退化、存储掌握仍高 → agent 漏诊；recent_error/topic_error baseline 命中。
    p = _PRED["recent_regression"]
    assert "kafka" not in p["real_agent"], "若已能识别近期退化，请更新此发现"
    assert "kafka" in p["recent_error"]
    assert "kafka" in p["topic_error_count"]


def test_finding_ghost_atom_false_positive():
    # 引用不存在 atom 的噪声事件 → agent 误报；simple_mastery 看真实 atom 不误报。
    p = _PRED["ghost_atom_noise"]
    assert "redis" in p["real_agent"], "若已修复 ghost-atom 误报，请更新此发现"
    assert "redis" not in p["simple_mastery"]


# —— agent 的稳健性优势（频次/时效 baseline 在此崩溃）——
def test_agent_robust_to_frequency_and_scatter_noise():
    assert _PRED["duplicate_events_noise"]["real_agent"] == set()
    assert _PRED["scattered_noise"]["real_agent"] == set()
    # 频次/时效 baseline 在噪声上误报。
    assert _PRED["duplicate_events_noise"]["topic_error_count"]
    assert _PRED["scattered_noise"]["recent_error"]


# —— clusters 过度上报 ——
def test_clusters_over_report_quantified():
    rows = _BO["cluster_rows"]
    cluster_fp = sum(len(r["cluster_fp"]) for r in rows)
    wp_fp = sum(len(r["wp_fp"]) for r in rows)
    over = sum(len(r["over_report"]) for r in rows)
    assert over > 0                    # clusters 确实多报
    assert cluster_fp > wp_fp          # clusters 相对 gold 的误报严重于 weak_points


def test_dirty_data_does_not_crash():
    # empty/None topic 等脏数据下所有预测器都应给出结果（不抛异常）。
    for n in B.PREDICTORS:
        assert isinstance(_PRED["empty_topic_dirty"][n], set)


# ============================ replay ============================
_RO = R.run()


def test_replay_deterministic():
    again = R.run()
    assert [sorted(s) for s in _RO["daily_pred"]] == [sorted(s) for s in again["daily_pred"]]


def test_replay_persistent_and_strong():
    traj = _RO["traj"]
    pred = _RO["daily_pred"]
    # 一直薄弱 → 每天都被预测；一直强 → 从不被预测。
    assert all("persistent_weak" in pred[d] for d in range(R.DAYS))
    assert all("strong" not in pred[d] for d in range(R.DAYS))
    assert "strong" in traj and "persistent_weak" in traj


def test_replay_tracks_decline_low_lag():
    decline_lags = [lg["lag"] for lg in _RO["lags"]
                    if lg["topic"] == "declining" and lg["transition"] == "→weak"]
    assert decline_lags and all(lag is not None and lag <= 1 for lag in decline_lags)


def test_replay_improving_has_forgiveness_lag():
    # 已实测发现：学会后 agent 仍滞后若干天才把话题移出薄弱（旧弱点事件未即时遗忘）。
    imp = [lg["lag"] for lg in _RO["lags"]
           if lg["topic"] == "improving" and lg["transition"] == "→ok"]
    assert imp and imp[0] is not None and imp[0] >= 2, \
        "若慢遗忘滞后已显著改善，请更新此发现"


def test_replay_checkpoint_f1_reasonable():
    f1s = [c.f1 for c in _RO["checkpoints"]]
    avg = sum(f1s) / len(f1s)
    # 时间跟踪整体不错，但因滞后/局限不应满分。
    assert 0.6 <= avg < 1.0
