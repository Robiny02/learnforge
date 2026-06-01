"""Phase 5a+ **规则回归测试**：stub diagnosis vs 真实 DiagnosisWorker（人工构造场景）。

⚠️ 口径声明（重要）：本模块是**规则回归测试**，不是真实泛化能力证明。
   - 样本仅 10 个、数据人工精确构造；
   - gold label 与实现规则（eff<0.6 门控）高度一致 —— 通过只证明「代码与自身规则自洽」；
   - baseline 是硬编码 stub（过弱）；confidence 校准按当前公式的预期评估。
   因此：**「规则回归通过」≠「真实诊断能力通过」**。真实泛化能力见
   eval_diagnosis_baselines.py（独立 baseline + noisy 数据 + clusters 过度上报）
   与 eval_diagnosis_replay.py（多天学习 replay）。

在一组带「已知薄弱话题真值」的种子库上，逐场景对比 stub 与真实 diagnosis 执行器，
验证真实 DiagnosisWorker 在**人工构造场景**下稳定、可靠、可解释。Phase 5a+ 只做评测加固：
不改 orchestrator 主架构、不接真实 PlanningAgent、不改 memory/retrieval/mock 子图。

设计要点（与算法实际行为对齐，诚实评测）：
- 「诊断出的薄弱点」以 weak_points（=weak_atoms，按有效掌握度门控 eff<0.6）为准，
  而非 clusters。clusters 会把「有弱点事件的任意话题」都列出（过度上报），
  weak_points 才是真正驱动 planning followup 的操作性结论。
- 因此 stale/high-mastery 等场景：旧弱点已被近期高掌握度覆盖时，weak_points 正确排除该话题。

场景（覆盖任务 §Step2 的 10 类）：
  1 obvious_weakness    明显薄弱点（多话题）
  2 empty_db            空库 → honest fallback
  3 sparse_evidence     证据稀疏 → 低置信
  4 multi_topic_mixed   多话题混杂 → 只选真正薄弱
  5 stale_vs_recent     新旧冲突 → 重视近期、排除已掌握旧弱点
  6 low_mastery_few     低 mastery 但 evidence 少 → 谨慎（低置信）
  7 high_mastery_recent_wrong  高 mastery 近期答错 → 不过度诊断
  8 unrelated_topics    无关健康话题不应被诊断
  9 focus_topics        focus_topics 生效（只诊断相关话题）
  10 all_correct        有活动但全对 → 无薄弱点、不 followup（非空库路径）

指标（详见模块底部 _METRIC_DOC）：weak_point_accuracy / topic_accuracy /
fallback_correctness / unnecessary_fallback_rate / over_diagnosis_rate /
followup_correctness / confidence_calibration / empty_state_honesty / trace_completeness。

全离线确定性。用法：python -m learnforge.orchestrator.eval_real_diagnosis
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Set

from ..contracts.atom import KnowledgeAtom
from ..storage.db import get_connection, init_db
from ..storage.repositories import AtomRepository
from .executors import diagnosis_executor, real_diagnosis_executor
from .tasks import AgentResult, Task

# 置信度阈值：≥ 视为「高置信」，< 视为「低置信」（与 manager LOW_CONFIDENCE 一致）。
CONF_THRESHOLD = 0.5


# ---------------------------------------------------------------- 场景数据模型
@dataclass
class Atom:
    id: str
    topic: str
    mastery: float
    reviewed_days_ago: Optional[int] = 1


@dataclass
class Ev:
    type: str          # 'mock_weakness' | 'qa_signal'
    topic: str
    atoms: List[str]
    age_days: int = 1
    signal: Optional[dict] = None


@dataclass
class Scenario:
    id: str
    desc: str
    atoms: List[Atom]
    events: List[Ev]
    gold_weak: Set[str]                       # 真值薄弱话题（小写）
    focus: List[str] = field(default_factory=list)
    evidence: str = "strong"                  # strong | sparse | none
    expect_fallback: bool = False             # 正确行为是否应为「无诊断」

    @property
    def expect_followup(self) -> bool:
        return bool(self.gold_weak)

    @property
    def expect_high_conf(self) -> bool:
        return self.evidence == "strong"


# --- 事件构造小工具（降低噪声）---
def _mock_weak(topic: str, atoms: List[str], n: int, age: int = 1) -> List[Ev]:
    return [Ev("mock_weakness", topic, atoms, age_days=age) for _ in range(n)]


def _qa(topic: str, atoms: List[str], n: int, correct: bool, age: int = 1) -> List[Ev]:
    return [Ev("qa_signal", topic, atoms, age_days=age, signal={"correct": correct})
            for _ in range(n)]


# ---------------------------------------------------------------- 场景定义
def _scenarios() -> List[Scenario]:
    return [
        Scenario(
            id="obvious_weakness",
            desc="明显薄弱点：concurrency/database 低掌握 + 多条近期弱点事件",
            atoms=[Atom("c0", "concurrency", 0.15), Atom("c1", "concurrency", 0.2),
                   Atom("d0", "database", 0.2)],
            events=_mock_weak("concurrency", ["c0", "c1"], 5) + _mock_weak("database", ["d0"], 3),
            gold_weak={"concurrency", "database"},
            evidence="strong",
        ),
        Scenario(
            id="empty_db",
            desc="空库：无 atom 无事件 → honest fallback",
            atoms=[], events=[], gold_weak=set(),
            evidence="none", expect_fallback=True,
        ),
        Scenario(
            id="sparse_evidence",
            desc="证据稀疏：concurrency 低掌握但仅 1 条事件 → 应低置信",
            atoms=[Atom("c0", "concurrency", 0.2)],
            events=_mock_weak("concurrency", ["c0"], 1),
            gold_weak={"concurrency"},
            evidence="sparse",
        ),
        Scenario(
            id="multi_topic_mixed",
            desc="多话题混杂：redis/agent 薄弱；java(高掌握无弱点)、payment(高掌握仅1弱点)健康",
            atoms=[Atom("r0", "redis", 0.15), Atom("ag0", "agent", 0.2),
                   Atom("j0", "java", 0.85), Atom("p0", "payment", 0.8)],
            events=(_mock_weak("redis", ["r0"], 4) + _mock_weak("agent", ["ag0"], 3)
                    + _mock_weak("payment", ["p0"], 1)),
            gold_weak={"redis", "agent"},
            evidence="strong",
        ),
        Scenario(
            id="stale_vs_recent",
            desc="新旧冲突：concurrency 旧弱点但近期高掌握(应排除)；redis 近期弱点低掌握(应诊断)",
            atoms=[Atom("c0", "concurrency", 0.85, reviewed_days_ago=1),
                   Atom("r0", "redis", 0.15, reviewed_days_ago=1)],
            events=_mock_weak("concurrency", ["c0"], 5, age=25) + _mock_weak("redis", ["r0"], 4, age=1),
            gold_weak={"redis"},
            evidence="strong",
        ),
        Scenario(
            id="low_mastery_few",
            desc="低 mastery 但 evidence 少：graphql mastery=0.1 仅 1 条 → 谨慎(低置信)",
            atoms=[Atom("g0", "graphql", 0.1)],
            events=_mock_weak("graphql", ["g0"], 1),
            gold_weak={"graphql"},
            evidence="sparse",
        ),
        Scenario(
            id="high_mastery_recent_wrong",
            desc="高 mastery 近期答错：kafka mastery=0.85 + 2 条近期错 → 不应过度诊断",
            atoms=[Atom("k0", "kafka", 0.85, reviewed_days_ago=1)],
            events=_qa("kafka", ["k0"], 2, correct=False),
            gold_weak=set(),
            evidence="sparse", expect_fallback=True,
        ),
        Scenario(
            id="unrelated_topics",
            desc="无关健康话题不应被诊断：redis 薄弱；payment/java 高掌握无弱点事件",
            atoms=[Atom("r0", "redis", 0.15), Atom("p0", "payment", 0.85), Atom("j0", "java", 0.8)],
            events=_mock_weak("redis", ["r0"], 5),
            gold_weak={"redis"},
            evidence="strong",
        ),
        Scenario(
            id="focus_topics",
            desc="focus_topics 生效：redis & java 都薄弱，focus=redis → 只诊断 redis",
            atoms=[Atom("r0", "redis", 0.15), Atom("j0", "java", 0.15)],
            events=_mock_weak("redis", ["r0"], 5) + _mock_weak("java", ["j0"], 5),
            gold_weak={"redis"},
            focus=["redis"],
            evidence="strong",
        ),
        Scenario(
            id="all_correct",
            desc="有活动但全对：kafka 近期全部答对 → 无薄弱点、不 followup（非空库路径）",
            atoms=[Atom("k0", "kafka", 0.8, reviewed_days_ago=1)],
            events=_qa("kafka", ["k0"], 4, correct=True),
            gold_weak=set(),
            evidence="none", expect_fallback=True,
        ),
    ]


# ---------------------------------------------------------------- 种子
def seed_db(sid: str, atoms: List[Atom], events: List[Ev]) -> str:
    """建库并写入 atom（含 mastery/last_reviewed）与事件（精确 created_at）。

    中性 seeding 助手：供本模块（规则回归）与 eval_diagnosis_* （泛化评测）共用。
    事件需精确控制 created_at（recency），故直连写入（EventRepository.append 强制 now）。
    """
    db = os.path.join(tempfile.mkdtemp(), "lf.db")
    init_db(db)
    now = datetime.now(timezone.utc)
    ar = AtomRepository(db_path=db)
    for a in atoms:
        lr = now - timedelta(days=a.reviewed_days_ago) if a.reviewed_days_ago is not None else None
        ar.upsert(KnowledgeAtom(
            atom_id=a.id, topic=a.topic, title=a.id, canonical_question="?",
            mastery_score=a.mastery, last_reviewed_at=lr, created_at=now, updated_at=now,
        ))
    conn = get_connection(db)
    for i, e in enumerate(events):
        ca = (now - timedelta(days=e.age_days)).isoformat()
        conn.execute(
            "INSERT INTO interaction_events "
            "(event_id, trace_id, event_type, topic, atom_refs, signal, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"{sid}-e{i}", None, e.type, e.topic if e.topic is not None else "",
             json.dumps(e.atoms), json.dumps(e.signal or {}), ca),
        )
    conn.commit()
    return db


def _seed(scn: Scenario) -> str:
    return seed_db(scn.id, scn.atoms, scn.events)


# ---------------------------------------------------------------- 结果解析
def _pred_topics(res: AgentResult) -> Set[str]:
    """预测薄弱话题：真实 worker 用 weak_points(掌握度门控的操作性结论)；stub 退化用 clusters。

    关键：真实结果恒带 'weak_points' 键（可能为空 → 预测空），不可在空时回退到 clusters
    （clusters 会过度上报有事件的健康话题）。stub 无该键 → 用其 clusters 字符串。
    """
    if "weak_points" in res.result:
        return {(wp.get("topic") or "").lower()
                for wp in res.result["weak_points"] if wp.get("topic")}
    out: Set[str] = set()
    for c in res.result.get("clusters", []):
        t = c if isinstance(c, str) else c.get("topic", "")
        if t:
            out.add(t.lower())
    return out


def _has_planning_followup(res: AgentResult) -> bool:
    return any(f.target_agent == "planning" for f in res.followups)


def _evidence_count(res: AgentResult) -> int:
    wps = res.result.get("weak_points") or []
    return sum(int(wp.get("evidence_count", 0)) for wp in wps)


def _trace_ok(res: AgentResult) -> bool:
    """报告是否可解释：有建议，且带 ReAct 轨迹；有诊断时弱点须带 evidence_count。"""
    if not res.result.get("recommendations"):
        return False
    trace = res.result.get("react_trace")
    if trace is None or len(trace) < 1:
        return False
    for wp in res.result.get("weak_points") or []:
        if "evidence_count" not in wp:
            return False
    return True


# ---------------------------------------------------------------- 单场景评估
@dataclass
class ScenarioRecord:
    scenario_id: str
    focus: List[str]
    gold: Set[str]
    pred: Set[str]
    confidence: float
    evidence_count: int
    has_followup: bool
    trace_ok: bool
    failures: List[str]
    react_trace: List[dict]

    @property
    def passed(self) -> bool:
        return not self.failures


def _eval_scenario(scn: Scenario, res: AgentResult) -> ScenarioRecord:
    pred = _pred_topics(res)
    conf = res.confidence
    has_fu = _has_planning_followup(res)
    failures: List[str] = []

    # ① 话题集合正确性（精确匹配 gold）
    if pred != scn.gold_weak:
        missed = scn.gold_weak - pred
        extra = pred - scn.gold_weak
        if missed:
            failures.append(f"under_diagnosis: 漏诊 {sorted(missed)}")
        if extra:
            failures.append(f"over_diagnosis: 误诊 {sorted(extra)}")
    # ② 应 fallback 却诊断 / followup
    if scn.expect_fallback and (pred or has_fu):
        failures.append("should_fallback_but_diagnosed")
    # ③ followup 门控：有薄弱点才派发
    if has_fu != scn.expect_followup:
        failures.append(f"followup_mismatch: has={has_fu} expect={scn.expect_followup}")
    # ④ 置信校准
    high = conf >= CONF_THRESHOLD
    if high != scn.expect_high_conf:
        band = "high" if high else "low"
        want = "high" if scn.expect_high_conf else "low"
        failures.append(f"confidence_miscalibrated: conf={conf:.2f} band={band} want={want}")
    # ⑤ 空库诚实
    if scn.id == "empty_db" and conf > 0.1:
        failures.append(f"empty_state_dishonest: conf={conf:.2f} 应≈0")
    # ⑥ 可解释性
    trace_ok = _trace_ok(res)
    if not trace_ok:
        failures.append("trace_incomplete")

    return ScenarioRecord(
        scenario_id=scn.id, focus=scn.focus, gold=set(scn.gold_weak), pred=pred,
        confidence=conf, evidence_count=_evidence_count(res), has_followup=has_fu,
        trace_ok=trace_ok, failures=failures,
        react_trace=res.result.get("react_trace") or [],
    )


# ---------------------------------------------------------------- 聚合指标
@dataclass
class Metrics:
    weak_point_accuracy: float        # gold 非空场景：召回 |pred∩gold|/|gold|
    topic_accuracy: float             # pred 非空场景：精确率 |pred∩gold|/|pred|
    fallback_correctness: float       # 应 fallback 场景：正确返回无诊断的比例
    unnecessary_fallback_rate: float  # 明显薄弱场景：却漏诊(空 pred)的比例
    over_diagnosis_rate: float        # 全场景：pred 含 gold 外话题的比例
    followup_correctness: float       # 全场景：followup 与「应否 followup」一致比例
    confidence_calibration: float     # 全场景：置信高低与证据强弱匹配比例
    empty_state_honesty: float        # 空库场景：诚实(空+conf≈0+不 followup)
    trace_completeness: float         # 全场景：报告可解释比例


def _aggregate(scns: List[Scenario], recs: List[ScenarioRecord]) -> Metrics:
    recall, prec = [], []
    fb_correct, unnec_fb, over, fu_ok, calib, trace = [], [], [], [], [], []
    empty_honest = 1.0
    for scn, r in zip(scns, recs):
        if scn.gold_weak:
            recall.append(len(r.pred & scn.gold_weak) / len(scn.gold_weak))
        if r.pred:
            prec.append(len(r.pred & scn.gold_weak) / len(r.pred))
        if scn.expect_fallback:
            fb_correct.append(1.0 if (not r.pred and not r.has_followup) else 0.0)
        if scn.evidence == "strong":  # 明显薄弱场景
            unnec_fb.append(1.0 if not r.pred else 0.0)
        over.append(1.0 if (r.pred - scn.gold_weak) else 0.0)
        fu_ok.append(1.0 if r.has_followup == scn.expect_followup else 0.0)
        calib.append(1.0 if (r.confidence >= CONF_THRESHOLD) == scn.expect_high_conf else 0.0)
        trace.append(1.0 if r.trace_ok else 0.0)
        if scn.id == "empty_db":
            empty_honest = 1.0 if (not r.pred and not r.has_followup
                                   and r.confidence <= 0.1) else 0.0

    def avg(xs: List[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return Metrics(
        weak_point_accuracy=avg(recall),
        topic_accuracy=avg(prec),
        fallback_correctness=avg(fb_correct),
        unnecessary_fallback_rate=avg(unnec_fb),
        over_diagnosis_rate=avg(over),
        followup_correctness=avg(fu_ok),
        confidence_calibration=avg(calib),
        empty_state_honesty=empty_honest,
        trace_completeness=avg(trace),
    )


def evaluate(executor_factory: Callable[[str, List[str]], object]):
    """对所有场景跑给定执行器工厂，返回 (Metrics, [ScenarioRecord])。

    executor_factory(db_path, focus_topics) → AgentExecutor。
    """
    scns = _scenarios()
    recs: List[ScenarioRecord] = []
    for scn in scns:
        db = _seed(scn)
        execu = executor_factory(db, scn.focus)
        task = Task(target_agent="diagnosis", user_goal="帮我诊断薄弱点", task_type="diagnosis")
        res = execu(task)
        recs.append(_eval_scenario(scn, res))
    return _aggregate(scns, recs), recs


def evaluate_real():
    return evaluate(lambda db, focus: real_diagnosis_executor(db_path=db, focus_topics=focus))


def evaluate_stub():
    # stub 忽略 db / focus，对任意输入硬编码 redis 弱点。
    return evaluate(lambda db, focus: diagnosis_executor)


# ---------------------------------------------------------------- 报告
def _print_failures(recs: List[ScenarioRecord]) -> None:
    failed = [r for r in recs if not r.passed]
    print(f"\n失败样本：{len(failed)}/{len(recs)}")
    if not failed:
        print("  （无失败样本）")
        return
    for r in failed:
        print("-" * 64)
        print(f"  scenario_id   : {r.scenario_id}")
        print(f"  focus_topics  : {r.focus or '—'}")
        print(f"  gold_weak     : {sorted(r.gold) or '—'}")
        print(f"  predicted     : {sorted(r.pred) or '—'}")
        print(f"  confidence    : {r.confidence:.2f}")
        print(f"  evidence_count: {r.evidence_count}")
        print(f"  followup      : {'planning' if r.has_followup else '—'}")
        print(f"  failure_reason: {'; '.join(r.failures)}")
        if r.react_trace:
            obs = " | ".join(s.get("observation", "") for s in r.react_trace)
            print(f"  trace         : {obs}")


def main() -> None:
    stub_m, _ = evaluate_stub()
    real_m, real_recs = evaluate_real()

    n = len(_scenarios())
    print("=" * 64)
    print("Phase 5a+ 规则回归测试（人工构造场景，非泛化能力证明）")
    print("stub diagnosis vs 真实 DiagnosisWorker")
    print(f"场景数: {n}（明显薄弱 / 空库 / 稀疏 / 多话题 / 新旧冲突 / 低掌握少证据 / "
          "高掌握近期错 / 无关话题 / focus / 全对）")
    print("⚠️ gold 与 eff<0.6 规则一致、baseline 为硬编码 stub → 仅证明规则自洽；"
          "真实泛化见 eval_diagnosis_baselines / eval_diagnosis_replay。")
    print("=" * 64)
    rows = [
        ("weak_point_accuracy", stub_m.weak_point_accuracy, real_m.weak_point_accuracy, "↑薄弱召回"),
        ("topic_accuracy", stub_m.topic_accuracy, real_m.topic_accuracy, "↑话题精确率"),
        ("fallback_correctness", stub_m.fallback_correctness, real_m.fallback_correctness, "↑该退则退"),
        ("unnecessary_fallback_rate", stub_m.unnecessary_fallback_rate, real_m.unnecessary_fallback_rate, "↓不该漏诊"),
        ("over_diagnosis_rate", stub_m.over_diagnosis_rate, real_m.over_diagnosis_rate, "↓不该误诊"),
        ("followup_correctness", stub_m.followup_correctness, real_m.followup_correctness, "↑派发门控"),
        ("confidence_calibration", stub_m.confidence_calibration, real_m.confidence_calibration, "↑置信校准"),
        ("empty_state_honesty", stub_m.empty_state_honesty, real_m.empty_state_honesty, "↑空库诚实"),
        ("trace_completeness", stub_m.trace_completeness, real_m.trace_completeness, "↑可解释"),
    ]
    print(f"{'指标':<28}{'stub':>9}{'real':>9}   说明")
    for name, s, r, note in rows:
        print(f"{name:<28}{s:>9.0%}{r:>9.0%}   {note}")
    print("-" * 64)
    print("解读：stub 对任意输入硬编码 redis 弱点 → 精确率/召回低、空库与全对也强行诊断+派发"
          "（over_diagnosis=100%、不会 fallback，危险）；真实 diagnosis 按真实数据诊断，"
          "无信号/已掌握时如实排除、低证据时低置信。")

    _print_failures(real_recs)
    print("=" * 64)


# 指标释义（供报告/审阅引用）
_METRIC_DOC = {
    "weak_point_accuracy": "诊断出的 weak point 是否命中 gold weak topic（gold 非空场景召回）。",
    "topic_accuracy": "诊断出的 topic 是否正确（pred 非空场景精确率）。",
    "fallback_correctness": "无数据/证据不足时是否正确 fallback。",
    "unnecessary_fallback_rate": "明明有明显薄弱点却 fallback 的比例（越低越好）。",
    "over_diagnosis_rate": "没有足够证据却强行诊断的比例（越低越好）。",
    "followup_correctness": "是否只在有 weak_points 时触发 planning followup。",
    "confidence_calibration": "confidence 是否和证据强弱匹配。",
    "empty_state_honesty": "空库时是否诚实返回无诊断。",
    "trace_completeness": "报告是否能解释诊断依据（建议 + ReAct 轨迹 + 证据计数）。",
}


if __name__ == "__main__":
    main()
