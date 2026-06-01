"""Phase 5b 前置 **泛化能力评测**：独立 baseline + noisy 数据 + clusters 过度上报。

回应「规则回归通过 ≠ 真实诊断能力通过」的关切：规则回归（eval_real_diagnosis.py）里
gold 与 eff<0.6 规则一致、baseline 是硬编码 stub，过弱且自洽。本模块改用：

1) **独立 baseline**（规则可与 gold 不一致，公平对照真实 agent 的「增量价值」）：
   - simple_mastery_baseline   ：只看存储 mastery_score < 0.5（忽略事件/时效）。
   - recent_error_baseline     ：只看近 7 天是否有弱点事件（忽略 mastery）。
   - topic_error_count_baseline：只看全窗弱点事件计数 ≥ 阈值（忽略 mastery/时效）。
   真实 agent = (1-effective_mastery)*recency*error_freq，eff<0.6 门控。

2) **noisy / dirty data 场景**：ghost atom、空 topic、重复事件、零散噪声、信号冲突
   —— 检验稳健性（噪声是否诱发 over-diagnosis）。

3) **clusters 过度上报**：量化 clusters 相对 weak_points / gold 的多报。

gold 来自「场景叙事的潜在真值」，**特意构造为不同规则会分歧**，因此没有任何单一规则
能全胜。结论按实测如实报告（含真实 agent 输给某 baseline 的场景），不设高通过门槛。

全离线确定性。用法：python -m learnforge.orchestrator.eval_diagnosis_baselines
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Set

from ..agents.diagnosis import _is_weak_signal
from ..storage.db import get_connection
from ..storage.repositories import EventRepository
from .eval_real_diagnosis import Atom, Ev, seed_db
from .executors import real_diagnosis_executor
from .tasks import Task

_TASK = Task(target_agent="diagnosis", user_goal="诊断薄弱点", task_type="diagnosis")
MASTERY_WEAK = 0.5        # simple_mastery 阈值
ERR_COUNT_MIN = 2         # topic_error_count 阈值


# ---------------------------------------------------------------- 独立 baselines
def _all_atoms(db: str) -> List[dict]:
    conn = get_connection(db)
    try:
        rows = conn.execute(
            "SELECT atom_id, topic, mastery_score FROM knowledge_atoms"
        ).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def _weak_events(db: str, window: str) -> List[dict]:
    evs = EventRepository(db_path=db).list_window_dicts(window)
    return [e for e in evs if _is_weak_signal(e)]


def simple_mastery_baseline(db: str, focus: List[str]) -> Set[str]:
    """只看存储 mastery：topic 存在 atom mastery_score < 0.5 → 薄弱。忽略事件/时效。"""
    out = set()
    for a in _all_atoms(db):
        if a["mastery_score"] < MASTERY_WEAK:
            out.add((a["topic"] or "").lower())
    return _apply_focus(out, focus)


def recent_error_baseline(db: str, focus: List[str]) -> Set[str]:
    """只看近 7 天是否出现弱点事件 → 薄弱。忽略 mastery。"""
    out = {(e.get("topic") or "").lower() for e in _weak_events(db, "7d")}
    return _apply_focus({t for t in out if t}, focus)


def topic_error_count_baseline(db: str, focus: List[str]) -> Set[str]:
    """只看全窗（30d）弱点事件计数 ≥ 阈值 → 薄弱。忽略 mastery/时效。"""
    counts: Dict[str, int] = {}
    for e in _weak_events(db, "30d"):
        t = (e.get("topic") or "").lower()
        if t:
            counts[t] = counts.get(t, 0) + 1
    return _apply_focus({t for t, c in counts.items() if c >= ERR_COUNT_MIN}, focus)


def real_agent_predict(db: str, focus: List[str]) -> Set[str]:
    res = real_diagnosis_executor(db_path=db, focus_topics=focus)(_TASK)
    return {(wp.get("topic") or "").lower()
            for wp in res.result.get("weak_points", []) if wp.get("topic")}


def real_agent_clusters(db: str, focus: List[str]) -> Set[str]:
    res = real_diagnosis_executor(db_path=db, focus_topics=focus)(_TASK)
    return {(c.get("topic") or "").lower()
            for c in res.result.get("clusters", []) if c.get("topic")}


def _apply_focus(topics: Set[str], focus: List[str]) -> Set[str]:
    if not focus:
        return topics
    f = {t.lower() for t in focus}
    return topics & f


PREDICTORS: Dict[str, Callable[[str, List[str]], Set[str]]] = {
    "simple_mastery": simple_mastery_baseline,
    "recent_error": recent_error_baseline,
    "topic_error_count": topic_error_count_baseline,
    "real_agent": real_agent_predict,
}


# ---------------------------------------------------------------- 泛化场景（gold 独立）
@dataclass
class GScenario:
    id: str
    desc: str
    atoms: List[Atom]
    events: List[Ev]
    gold: Set[str]
    focus: List[str] = field(default_factory=list)
    note: str = ""           # 该场景意在暴露哪种规则分歧/噪声


def _mw(topic, atoms, n, age=1):
    return [Ev("mock_weakness", topic, atoms, age_days=age) for _ in range(n)]


def _qa(topic, atoms, n, correct, age=1):
    return [Ev("qa_signal", topic, atoms, age_days=age, signal={"correct": correct})
            for _ in range(n)]


def scenarios() -> List[GScenario]:
    return [
        # —— 规则分歧场景（gold 不与任一规则恒等）——
        GScenario(
            "recent_regression",
            "高存储掌握(0.85,近期复习) 但近 2 天大量答错 → 真值：近期退化=薄弱",
            [Atom("k0", "kafka", 0.85, reviewed_days_ago=1)],
            _mw("kafka", ["k0"], 5, age=1),
            gold={"kafka"},
            note="real_agent 读存储掌握→漏诊；recent_error/topic_error 应命中（暴露 agent 局限）",
        ),
        GScenario(
            "stale_low_mastery",
            "低掌握(0.2) 但弱点事件都在 25 天前、近期无活动 → 真值：仍是知识缺口=薄弱",
            [Atom("r0", "redis", 0.2, reviewed_days_ago=25)],
            _mw("redis", ["r0"], 4, age=25),
            gold={"redis"},
            note="recent_error(7d) 会漏诊；simple_mastery/real_agent 应命中",
        ),
        GScenario(
            "recovered_high_mastery",
            "曾经薄弱但已复习到高掌握(0.85)，仅剩 25 天前旧弱点事件 → 真值：已恢复=不薄弱",
            [Atom("c0", "concurrency", 0.85, reviewed_days_ago=1)],
            _mw("concurrency", ["c0"], 4, age=25),
            gold=set(),
            note="topic_error_count 会误报旧账；real_agent(eff 高)应正确排除",
        ),
        GScenario(
            "borderline_mastery",
            "掌握 0.55（>0.5 略高）+ 近期 3 条弱点 → 真值：边界但近期持续出错=薄弱",
            [Atom("j0", "java", 0.55, reviewed_days_ago=2)],
            _mw("java", ["j0"], 3, age=2),
            gold={"java"},
            note="simple_mastery(0.5 阈值)会漏诊；recent_error 命中；real_agent eff~0.55<0.6 应命中",
        ),
        GScenario(
            "high_mastery_one_slip",
            "高掌握(0.8) 偶发 1 次近期错 → 真值：偶发不算薄弱",
            [Atom("p0", "payment", 0.8, reviewed_days_ago=1)],
            _mw("payment", ["p0"], 1, age=1),
            gold=set(),
            note="recent_error 会误报；real_agent(eff 高)应排除",
        ),
        # —— noisy / dirty data 场景 ——
        GScenario(
            "ghost_atom_noise",
            "健康话题(redis,0.8) 但一条噪声事件引用不存在的 atom → 真值：不薄弱",
            [Atom("r0", "redis", 0.8, reviewed_days_ago=1)],
            _mw("redis", ["ghost_xyz"], 3, age=1),
            gold=set(),
            note="real_agent 对缺失 atom 取 eff=0→可能误报；simple_mastery 看真实 atom 应排除",
        ),
        GScenario(
            "empty_topic_dirty",
            "脏数据：事件 topic 为空 + 引用低掌握 atom → 不应崩溃，真值按 atom 真实话题",
            [Atom("g0", "graphql", 0.2, reviewed_days_ago=2)],
            [Ev("mock_weakness", "", ["g0"], age_days=2),
             Ev("mock_weakness", None, ["g0"], age_days=2),
             Ev("mock_weakness", "graphql", ["g0"], age_days=2)],
            gold={"graphql"},
            note="脏 topic 稳健性；真实弱点仍应识别",
        ),
        GScenario(
            "duplicate_events_noise",
            "高掌握(0.85) 话题被重复写入同一弱点事件 10 次（日志 bug）→ 真值：不薄弱",
            [Atom("d0", "docker", 0.85, reviewed_days_ago=1)],
            _mw("docker", ["d0"], 10, age=1),
            gold=set(),
            note="topic_error_count 被重复刷爆→误报；real_agent(eff 高)应排除",
        ),
        GScenario(
            "scattered_noise",
            "多话题各 1 条零散弱点、均高掌握 → 真值：噪声，无薄弱",
            [Atom("a0", "linux", 0.8), Atom("a1", "git", 0.85),
             Atom("a2", "http", 0.82), Atom("a3", "dns", 0.8)],
            (_mw("linux", ["a0"], 1) + _mw("git", ["a1"], 1)
             + _mw("http", ["a2"], 1) + _mw("dns", ["a3"], 1)),
            gold=set(),
            note="recent_error 全误报；topic_error_count(阈值2)与 real_agent 应排除",
        ),
        GScenario(
            "conflicting_signals",
            "低掌握(0.3) 话题：近期既有答对又有答错 → 真值：仍薄弱（净体验偏负）",
            [Atom("s0", "sql", 0.3, reviewed_days_ago=1)],
            _qa("sql", ["s0"], 2, correct=True) + _mw("sql", ["s0"], 3, age=1),
            gold={"sql"},
            note="信号冲突；real_agent/simple_mastery 应命中",
        ),
        # —— 真薄弱（正例，确保不是一味保守）——
        GScenario(
            "clear_weak_multi",
            "两话题低掌握 + 近期多弱点 → 真值：都薄弱",
            [Atom("m0", "mongodb", 0.15), Atom("e0", "elasticsearch", 0.2)],
            _mw("mongodb", ["m0"], 4) + _mw("elasticsearch", ["e0"], 3),
            gold={"mongodb", "elasticsearch"},
            note="正例：所有合理规则都应命中",
        ),
        GScenario(
            "focus_filter_generalize",
            "redis & java 都真薄弱，focus=redis → 只诊断 redis",
            [Atom("r0", "redis", 0.15), Atom("j0", "java", 0.15)],
            _mw("redis", ["r0"], 4) + _mw("java", ["j0"], 4),
            gold={"redis"}, focus=["redis"],
            note="focus 生效",
        ),
    ]


# ---------------------------------------------------------------- 指标
def _prf(pred: Set[str], gold: Set[str]):
    if not pred and not gold:
        return 1.0, 1.0, 1.0
    tp = len(pred & gold)
    prec = tp / len(pred) if pred else (1.0 if not gold else 0.0)
    rec = tp / len(gold) if gold else (1.0 if not pred else 0.0)
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1


@dataclass
class PredScore:
    name: str
    precision: float
    recall: float
    f1: float
    exact: float            # 预测集合 == gold 的比例


def run() -> dict:
    scns = scenarios()
    dbs = {s.id: seed_db(s.id, s.atoms, s.events) for s in scns}

    # 各预测器逐场景结果
    per_pred: Dict[str, List[dict]] = {name: [] for name in PREDICTORS}
    for s in scns:
        for name, fn in PREDICTORS.items():
            try:
                pred = fn(dbs[s.id], s.focus)
            except Exception as exc:  # 稳健性：噪声不应让预测器崩溃
                per_pred[name].append({"sid": s.id, "pred": set(), "gold": s.gold,
                                       "error": str(exc)})
                continue
            p, r, f = _prf(pred, s.gold)
            per_pred[name].append({
                "sid": s.id, "pred": pred, "gold": s.gold,
                "precision": p, "recall": r, "f1": f,
                "exact": 1.0 if pred == s.gold else 0.0,
            })

    scores: List[PredScore] = []
    for name, recs in per_pred.items():
        valid = [r for r in recs if "error" not in r]
        n = len(valid) or 1
        scores.append(PredScore(
            name=name,
            precision=sum(r["precision"] for r in valid) / n,
            recall=sum(r["recall"] for r in valid) / n,
            f1=sum(r["f1"] for r in valid) / n,
            exact=sum(r["exact"] for r in valid) / n,
        ))

    # clusters 过度上报分析
    cluster_rows = []
    for s in scns:
        wp = real_agent_predict(dbs[s.id], s.focus)
        cl = real_agent_clusters(dbs[s.id], s.focus)
        cluster_rows.append({
            "sid": s.id, "gold": s.gold, "weak_points": wp, "clusters": cl,
            "over_report": cl - wp,                 # clusters 多出 weak_points 的话题
            "cluster_fp": cl - s.gold,              # clusters 相对 gold 的误报
            "wp_fp": wp - s.gold,                   # weak_points 相对 gold 的误报
        })

    return {"scenarios": scns, "per_pred": per_pred, "scores": scores,
            "cluster_rows": cluster_rows}


# ---------------------------------------------------------------- 报告
def main() -> None:
    out = run()
    scores: List[PredScore] = out["scores"]
    print("=" * 78)
    print("Phase 5b 前置 · 泛化能力评测：真实 agent vs 独立 baseline（含 noisy 数据）")
    print(f"场景数: {len(out['scenarios'])}（规则分歧 + noisy/dirty + 正例）")
    print("gold 来自场景潜在真值，特意构造为不同规则会分歧 → 无单一规则全胜")
    print("=" * 78)
    print(f"{'predictor':<20}{'precision':>11}{'recall':>9}{'F1':>8}{'exact':>9}")
    print("-" * 78)
    for s in sorted(scores, key=lambda x: x.f1, reverse=True):
        print(f"{s.name:<20}{s.precision:>11.0%}{s.recall:>9.0%}{s.f1:>8.0%}{s.exact:>9.0%}")
    print("-" * 78)

    # 逐场景对照（谁对谁错）
    print("\n逐场景预测（✓=与 gold 完全一致）：")
    names = list(PREDICTORS)
    print(f"{'scenario':<26}{'gold':<22}" + "".join(f"{n[:12]:<14}" for n in names))
    for i, s in enumerate(out["scenarios"]):
        cells = ""
        for n in names:
            rec = out["per_pred"][n][i]
            pred = rec.get("pred", set())
            mark = "✓" if pred == s.gold else "✗"
            cells += f"{mark}{','.join(sorted(pred)) or '∅':<13}"[:14]
        print(f"{s.id:<26}{','.join(sorted(s.gold)) or '∅':<22}{cells}")

    # clusters 过度上报
    print("\nclusters 过度上报分析（clusters 相对 weak_points / gold 的多报）：")
    tot_over = tot_cluster_fp = tot_wp_fp = 0
    for r in out["cluster_rows"]:
        if r["over_report"] or r["cluster_fp"] or r["wp_fp"]:
            print(f"  {r['sid']:<26} gold={sorted(r['gold']) or '∅'} "
                  f"weak_points={sorted(r['weak_points']) or '∅'} "
                  f"clusters={sorted(r['clusters']) or '∅'} "
                  f"| clusters多报={sorted(r['over_report']) or '∅'} "
                  f"clusters误报={sorted(r['cluster_fp']) or '∅'}")
        tot_over += len(r["over_report"])
        tot_cluster_fp += len(r["cluster_fp"])
        tot_wp_fp += len(r["wp_fp"])
    print(f"  合计：clusters 比 weak_points 多报话题 {tot_over} 个；"
          f"clusters 误报(相对 gold) {tot_cluster_fp} 个；weak_points 误报 {tot_wp_fp} 个。")
    print("  → clusters 过度上报已被量化；操作性结论应以 weak_points 为准。")
    print("=" * 78)


if __name__ == "__main__":
    main()
