"""Phase 5b 前置 · **replay 评测**：模拟多天学习与 mastery 变化，检验诊断的时间跟踪能力。

回应「静态快照场景偏理想」的关切：本模块模拟一个学习者在 D 天内对若干话题的练习，
每个话题有一条**潜在真实技能轨迹**（latent skill），逐日产生交互（对/错），并用真实
`mastery.update_mastery`（= Manager 唯一写者口径）把 atom 掌握度逐日演进、写回事件流。
然后在多个检查点跑真实 DiagnosisAgent，对比预测薄弱话题 vs **潜在真值**（skill<0.5）。

要点：
- gold 来自**生成过程的潜在技能**，与诊断规则（eff<0.6）相互独立 → 非自洽回归。
- 覆盖四类时间行为：improving（脱薄弱）/ declining（退化）/ persistent_weak / strong。
- 输出每话题的「预测 vs 真值」时间线与转变滞后（lag），以及逐检查点 P/R/F1。
- DiagnosisAgent 全程只读；写 mastery 的是本 eval（模拟 Manager），不是诊断器。

全离线确定性。用法：python -m learnforge.orchestrator.eval_diagnosis_replay
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set

from ..mastery import update_mastery
from .eval_real_diagnosis import Atom, Ev, seed_db
from .executors import real_diagnosis_executor
from .tasks import Task

_TASK = Task(target_agent="diagnosis", user_goal="诊断薄弱点", task_type="diagnosis")

DAYS = 12
PER_DAY = 3                  # 每话题每天交互数
WEAK_THRESHOLD = 0.5         # 潜在 skill < 0.5 视为真薄弱
CHECKPOINTS = [1, 4, 7, 11]  # 0-based 天索引（含早/中/晚）


def _ramp(a: float, b: float, n: int) -> List[float]:
    if n == 1:
        return [b]
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _trajectories() -> Dict[str, List[float]]:
    """每话题 D 天潜在技能轨迹（确定性）。"""
    return {
        "improving": _ramp(0.15, 0.92, DAYS),          # 起步薄弱 → 逐渐掌握（应脱离薄弱）
        "declining": _ramp(0.92, 0.22, DAYS),          # 起步掌握 → 逐渐退化（应进入薄弱）
        "persistent_weak": [0.22] * DAYS,              # 一直薄弱（应始终命中）
        "strong": [0.9] * DAYS,                        # 一直强（应始终不报）
        "dip_recover": ([0.85] * 3 + [0.28] * 4 + [0.85] * 5),  # 中途塌陷再恢复
    }


# ---------------------------------------------------------------- 模拟
def _simulate():
    traj = _trajectories()
    mastery = {name: 0.5 for name in traj}           # 初始中性信念
    # events_log[d] = list of Ev for that day（atom_refs 用每话题单一 atom）
    day_events: List[List[Ev]] = [[] for _ in range(DAYS)]
    snapshots: List[Dict[str, float]] = []            # 每天结束时的 mastery 快照

    for d in range(DAYS):
        for name, skill in traj.items():
            s = skill[d]
            n_correct = round(s * PER_DAY)
            atom = f"{name}_atom"
            for i in range(PER_DAY):
                correct = i < n_correct
                mastery[name] = update_mastery(
                    mastery[name], 1.0 if correct else 0.0, source="mock"
                )
                if correct:
                    day_events[d].append(
                        Ev("qa_signal", name, [atom], age_days=0, signal={"correct": True})
                    )
                else:
                    day_events[d].append(
                        Ev("mock_weakness", name, [atom], age_days=0, signal={"weak": True})
                    )
        snapshots.append(dict(mastery))
    return traj, day_events, snapshots


def _build_db_asof(day: int, traj, day_events, snapshots) -> str:
    """构建「截至第 day 天」的库：atom 掌握度为当日快照，事件为 ≤day 的全部（含真实时效）。"""
    atoms = [
        Atom(f"{name}_atom", name, snapshots[day][name], reviewed_days_ago=(DAYS - 1 - day))
        for name in traj
    ]
    events: List[Ev] = []
    for d in range(day + 1):
        age = DAYS - 1 - d
        for e in day_events[d]:
            events.append(Ev(e.type, e.topic, e.atoms, age_days=age, signal=e.signal))
    return seed_db(f"replay-d{day}", atoms, events)


def _gold_weak(traj, day: int) -> Set[str]:
    return {name for name, skill in traj.items() if skill[day] < WEAK_THRESHOLD}


def _predict(db: str) -> Set[str]:
    res = real_diagnosis_executor(db_path=db)(_TASK)
    return {(wp.get("topic") or "").lower()
            for wp in res.result.get("weak_points", []) if wp.get("topic")}


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
class CheckpointResult:
    day: int
    gold: Set[str]
    pred: Set[str]
    precision: float
    recall: float
    f1: float


def run() -> dict:
    traj, day_events, snapshots = _simulate()
    # 每天（非仅检查点）都预测一次，便于刻画转变滞后
    daily_pred: List[Set[str]] = []
    daily_gold: List[Set[str]] = []
    for d in range(DAYS):
        db = _build_db_asof(d, traj, day_events, snapshots)
        daily_pred.append(_predict(db))
        daily_gold.append(_gold_weak(traj, d))

    checkpoints: List[CheckpointResult] = []
    for d in CHECKPOINTS:
        p, r, f = _prf(daily_pred[d], daily_gold[d])
        checkpoints.append(CheckpointResult(d, daily_gold[d], daily_pred[d], p, r, f))

    # 转变滞后：真值发生「进入/脱离薄弱」的那天，预测多少天后跟上
    lags: List[dict] = []
    for name, skill in traj.items():
        g = [skill[d] < WEAK_THRESHOLD for d in range(DAYS)]
        pr = [name in daily_pred[d] for d in range(DAYS)]
        for d in range(1, DAYS):
            if g[d] != g[d - 1]:  # 真值发生转变
                lag = None
                for k in range(d, DAYS):
                    if pr[k] == g[d]:
                        lag = k - d
                        break
                lags.append({"topic": name, "day": d,
                             "transition": "→weak" if g[d] else "→ok", "lag": lag})

    return {"traj": traj, "daily_pred": daily_pred, "daily_gold": daily_gold,
            "checkpoints": checkpoints, "lags": lags}


# ---------------------------------------------------------------- 报告
def main() -> None:
    out = run()
    print("=" * 78)
    print("Phase 5b 前置 · replay 评测：多天学习 + mastery 演进（gold=潜在技能，独立于诊断规则）")
    print(f"天数={DAYS} 每话题每天交互={PER_DAY} 薄弱阈值 skill<{WEAK_THRESHOLD}")
    print("=" * 78)

    # 时间线（每话题：真值 vs 预测）
    print("时间线（W=真薄弱, .=不薄弱 ；p=预测薄弱, -=预测不薄弱）：")
    daily_pred = out["daily_pred"]
    print(f"{'day':<16}" + "".join(f"{d:>3}" for d in range(DAYS)))
    for name, skill in out["traj"].items():
        gold_row = "".join(f"{'W' if skill[d] < WEAK_THRESHOLD else '.':>3}" for d in range(DAYS))
        pred_row = "".join(f"{'p' if name in daily_pred[d] else '-':>3}" for d in range(DAYS))
        print(f"  {name:<14}gold{gold_row}")
        print(f"  {'':<14}pred{pred_row}")

    print("\n检查点 P/R/F1：")
    print(f"{'day':<6}{'gold':<28}{'pred':<28}{'P':>6}{'R':>6}{'F1':>6}")
    f1s = []
    for c in out["checkpoints"]:
        f1s.append(c.f1)
        print(f"{c.day:<6}{','.join(sorted(c.gold)) or '∅':<28}"
              f"{','.join(sorted(c.pred)) or '∅':<28}{c.precision:>6.0%}{c.recall:>6.0%}{c.f1:>6.0%}")
    print(f"{'平均':<6}{'':<28}{'':<28}{'':>6}{'':>6}{sum(f1s) / len(f1s):>6.0%}")

    print("\n真值转变跟踪滞后（lag=真值变化后第几天预测跟上；None=始终没跟上）：")
    for lg in out["lags"]:
        print(f"  {lg['topic']:<16}day {lg['day']:<3}{lg['transition']:<8}lag={lg['lag']}")
    nones = [lg for lg in out["lags"] if lg["lag"] is None]
    avg_lag = [lg["lag"] for lg in out["lags"] if lg["lag"] is not None]
    print(f"  → 平均跟踪滞后 {sum(avg_lag) / len(avg_lag):.1f} 天" if avg_lag else "  → 无可计滞后",
          f"；始终未跟上 {len(nones)} 次。")
    print("=" * 78)


if __name__ == "__main__":
    main()
