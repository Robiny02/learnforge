"""Phase 5a+ 测试：真实 DiagnosisWorker 的评测加固（扩大样本 + 多指标 + 失败样本）。

不改 orchestrator 主架构、不接真实 PlanningAgent、不改 memory/retrieval/mock 子图。
仅验证真实 DiagnosisWorker 在 10 类场景上稳定、可靠、可解释，并优于硬编码 stub。

判据（决定能否进入 Phase 5b）：
- 有明显薄弱点 → 能诊断出来；
- 空库 / 证据不足 → honest fallback；
- focus_topics 生效；
- followup 只在有 weak_points 时触发；
- confidence 不过度自信；
- over-diagnosis / unnecessary-fallback 为 0；
- 全部场景无失败样本，且真实 worker 各项指标不劣于 stub。
"""

from learnforge.orchestrator.eval_real_diagnosis import (
    CONF_THRESHOLD,
    _scenarios,
    evaluate_real,
    evaluate_stub,
)


def _by_id(recs):
    return {r.scenario_id: r for r in recs}


# 共享一次评估结果（确定性、离线）。
_REAL_METRICS, _REAL_RECS = evaluate_real()
_REC = _by_id(_REAL_RECS)


# ---------------- 样本规模 ----------------
def test_scenario_coverage_expanded():
    ids = {s.id for s in _scenarios()}
    # 覆盖任务 §Step2 列出的 10 类代表性场景。
    assert ids == {
        "obvious_weakness", "empty_db", "sparse_evidence", "multi_topic_mixed",
        "stale_vs_recent", "low_mastery_few", "high_mastery_recent_wrong",
        "unrelated_topics", "focus_topics", "all_correct",
    }
    assert len(_REAL_RECS) == 10


# ---------------- 无失败样本 ----------------
def test_no_failing_scenarios():
    failed = {r.scenario_id: r.failures for r in _REAL_RECS if not r.passed}
    assert not failed, f"真实诊断仍有失败样本：{failed}"


# ---------------- 明显薄弱点：能诊断出来 ----------------
def test_obvious_weakness_detected():
    r = _REC["obvious_weakness"]
    assert r.pred == {"concurrency", "database"}
    assert r.has_followup
    assert r.confidence >= CONF_THRESHOLD


def test_multi_topic_selects_only_weak():
    # redis/agent 薄弱被选中；java/payment(高掌握)不应入选。
    r = _REC["multi_topic_mixed"]
    assert r.pred == {"redis", "agent"}
    assert "java" not in r.pred and "payment" not in r.pred


# ---------------- 空库 / 全对：honest fallback ----------------
def test_empty_db_honest_fallback():
    r = _REC["empty_db"]
    assert r.pred == set()
    assert not r.has_followup
    assert r.confidence <= 0.1


def test_all_correct_no_weakness_no_followup():
    # 有活动但全部答对 → 非空库路径，但仍无薄弱点、不派发 planning。
    r = _REC["all_correct"]
    assert r.pred == set()
    assert not r.has_followup


def test_high_mastery_recent_wrong_not_over_diagnosed():
    # 高掌握 + 少量近期答错 → 不应在 atom 层强行判定薄弱。
    r = _REC["high_mastery_recent_wrong"]
    assert r.pred == set()
    assert not r.has_followup
    assert r.confidence < CONF_THRESHOLD


# ---------------- 新旧冲突：重视近期、排除已掌握旧弱点 ----------------
def test_stale_weakness_excluded_recent_included():
    r = _REC["stale_vs_recent"]
    # 旧 concurrency 弱点已被近期高掌握覆盖 → 排除；近期 redis 弱点 → 诊断。
    assert r.pred == {"redis"}
    assert "concurrency" not in r.pred


# ---------------- 证据稀疏 / 低掌握少证据：低置信 ----------------
def test_sparse_evidence_low_confidence():
    r = _REC["sparse_evidence"]
    assert r.pred == {"concurrency"}          # 仍能指出候选
    assert r.confidence < CONF_THRESHOLD       # 但不过度自信


def test_low_mastery_few_evidence_cautious():
    r = _REC["low_mastery_few"]
    assert r.confidence < CONF_THRESHOLD       # 一个低分不强行高置信


# ---------------- focus_topics 生效 ----------------
def test_focus_topics_filters_unrelated():
    r = _REC["focus_topics"]
    assert r.pred == {"redis"}                 # java 虽薄弱但被 focus 过滤
    assert "java" not in r.pred


def test_unrelated_healthy_topics_not_diagnosed():
    r = _REC["unrelated_topics"]
    assert r.pred == {"redis"}
    assert not (r.pred - {"redis"})            # payment/java 不入选


# ---------------- followup 门控：有薄弱点才派发 ----------------
def test_followup_only_when_weak():
    for r in _REAL_RECS:
        # 真实 worker：weak_points 非空 ⇔ 派发 planning followup。
        assert r.has_followup == bool(r.pred), r.scenario_id


# ---------------- 可解释性：报告带依据 ----------------
def test_trace_completeness():
    for r in _REAL_RECS:
        assert r.trace_ok, r.scenario_id
        assert r.react_trace  # ReAct 三段式轨迹随结果回传


# ---------------- 聚合指标：真实 worker 可信且优于 stub ----------------
def test_aggregate_metrics_meet_thresholds():
    m = _REAL_METRICS
    assert m.weak_point_accuracy == 1.0
    assert m.topic_accuracy == 1.0
    assert m.fallback_correctness == 1.0
    assert m.unnecessary_fallback_rate == 0.0
    assert m.over_diagnosis_rate == 0.0
    assert m.followup_correctness == 1.0
    assert m.confidence_calibration == 1.0
    assert m.empty_state_honesty == 1.0
    assert m.trace_completeness == 1.0


def test_real_strictly_better_than_stub():
    stub_m, _ = evaluate_stub()
    real_m = _REAL_METRICS
    # 真实 worker 在关键指标上严格优于硬编码 stub。
    assert real_m.over_diagnosis_rate < stub_m.over_diagnosis_rate
    assert real_m.topic_accuracy > stub_m.topic_accuracy
    assert real_m.fallback_correctness > stub_m.fallback_correctness
    assert real_m.empty_state_honesty > stub_m.empty_state_honesty
