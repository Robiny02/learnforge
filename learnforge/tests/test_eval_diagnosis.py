"""Diagnosis 评测门禁（T-EV-08 / Design §9b）——Recall@5 + 判别性检查接入回归。

离线运行：诊断打分确定性，指标可复现。门槛对齐 Design §9b（Recall@5 ≥ 0.70）。
判别性检查证明数据集非"循环论证"：时间衰减(rank-1)、掌握度过滤(forbidden)、负样本不臆造。
"""

from collections import Counter

from learnforge.eval.diagnosis_eval import load_profiles, run_eval

RECALL_THRESHOLD = 0.70


def _profile(pid: str) -> dict:
    return next(p for p in load_profiles() if p["id"] == pid)


def test_diagnosis_recall_meets_threshold():
    report = run_eval(k=5)
    assert report.positives, "应至少有一个正样本档案"
    assert report.mean_recall >= RECALL_THRESHOLD, (
        f"cluster Recall@5={report.mean_recall:.3f} 低于门槛 {RECALL_THRESHOLD}"
    )


def test_diagnosis_negative_profiles_return_empty_clusters():
    report = run_eval(k=5)
    assert report.negatives, "应至少有一个负样本档案"
    assert report.negative_accuracy == 1.0, "负样本（无弱点信号）必须返回空簇，不得臆造弱点"


def test_diagnosis_read_only_across_dataset():
    report = run_eval(k=5)
    assert report.read_only_ok, "诊断在评测全程必须严格只读（无任何表被写）"


def test_top5_truncation_lowers_recall_on_hard_profiles():
    """p25/p27/p35 标注 6 个弱项，top5 截断 → recall 应为 5/6，证明指标非平凡。"""
    report = run_eval(k=5)
    by_id = {r.profile_id: r for r in report.results}
    for pid in ("p25", "p27", "p35"):
        assert by_id[pid].recall == 5 / 6, (
            f"{pid} 期望 recall=5/6（6 弱项被 top5 截断），实得 {by_id[pid].recall}"
        )


def test_time_decay_and_mastery_discriminators_pass():
    """判别性检查全过：rank-1（时间衰减）、forbidden topics/atoms（掌握度过滤/精确率）。"""
    report = run_eval(k=5)
    assert report.rank1_accuracy == 1.0, "时间衰减 rank-1 检查应全过（p31）"
    assert report.forbidden_topics_rate == 1.0, "干扰话题不得入簇（p34）"
    assert report.forbidden_atoms_rate == 1.0, "高掌握 atom 不得入弱点 atom（p32）"
    assert report.mean_atom_recall == 1.0, "弱点 atom 召回应全过（含 p33 衰减复活）"


def test_dataset_is_not_tautological_naive_ranker_fails_p31():
    """证明数据集有判别力：按弱点事件频次（忽略时间衰减）的朴素排序会在 p31 选错 rank-1。

    p31：concurrency 近期 2 次错 vs database 120 天前 6 次错。
    - 朴素频次排序 → rank-1 = database（错，频次 6>2）。
    - 真实诊断（含 recency 衰减）→ rank-1 = concurrency（对）。
    两者结论相反 → 该档案非循环论证，能区分"好/坏实现"。
    """
    p31 = _profile("p31")
    # 朴素 baseline：只数弱点事件频次，不看时间。
    freq = Counter(ev[1] for ev in p31["events"])  # ev = [kind, topic, refs, age_days]
    naive_rank1 = freq.most_common(1)[0][0]
    assert naive_rank1 == "database", "朴素频次排序应选 database（被高频误导）"

    report = run_eval(k=5)
    real = next(r for r in report.results if r.profile_id == "p31")
    assert real.got_topics[0] == "concurrency", "含衰减的真实诊断应选 concurrency"
    assert real.got_topics[0] != naive_rank1, "真实结论须与朴素 baseline 相反，证明判别力"
