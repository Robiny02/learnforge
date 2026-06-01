"""Diagnosis 评测脚手架（T-EV-08 / Design §9b-c）——只读弱点诊断的 Recall@K + 判别性检查。

数据来源：伪造的 interaction_events 用户档案（eval/golden/diagnosis.json），
不依赖知识检索语料库。每个档案独立建临时库 → 灌 atoms/events → 跑 DiagnosisAgent →
比对人工标注。离线运行（无 ANTHROPIC_API_KEY）：诊断打分公式确定性，给出真实指标。

== 为什么不止测 Recall ==
簇 severity = recency_weight × error_freq。若所有事件同一时刻发生（recency≡1），公式退化为
"弱点事件频次"，此时按频次标注的 Recall 会接近 1.0（循环论证）。为让指标有判别力，本集额外覆盖：
- **时间衰减**：事件带 age_days，近期弱项应胜过陈旧高频项（rank-1 检查）。
- **掌握度过滤**：高 effective_mastery 的 atom 不应入弱点（forbidden_atoms / 精确率）；
  反之久未复习导致 effective 衰减的 atom 应被"复活"为弱点。
- **干扰话题**：答对的话题不应入簇（forbidden_topics）。

指标：
- cluster Recall@K（主，对齐 Design §9b "Diagnosis Recall@5 ≥70%"）。
- weak_atom Recall@K（次，仅当档案带 expected_atoms）。
- rank-1 准确率：expected_rank1 是否为返回的首个簇（判别时间衰减）。
- forbidden 命中率：forbidden_topics/atoms 是否被正确排除（判别掌握度过滤/精确率）。
- negative accuracy：负样本（无标注弱点）应返回空簇。

编码（紧凑数组，由本模块解码）：
  atoms : [atom_id, topic, mastery]  或  [atom_id, topic, mastery, last_review_age_days]
  events: [kind, topic, [refs]]      或  [kind, topic, [refs], age_days]
    kind ∈ {mock, qa_bad, qa_unverified, qa_good}；qa_good 为干扰项（非弱点信号）。

用法：
    python -m learnforge.eval.diagnosis_eval            # 跑内置 golden 并打印报告
    python -m learnforge.eval.diagnosis_eval --k 3      # 改 Recall@K 的 K
    python -m learnforge.eval.diagnosis_eval --file X    # 用自定义数据集
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from ..agents.diagnosis import DiagnosisAgent
from ..contracts.agents.diagnosis import DiagnosisInput
from ..contracts.atom import KnowledgeAtom
from ..contracts.enums import TimeWindow
from ..storage.db import get_connection, init_db
from ..storage.repositories import AtomRepository

DEFAULT_K = 5
DEFAULT_GOLDEN = Path(__file__).parent / "golden" / "diagnosis.json"

# kind → (event_type, signal_json)。qa_good 为干扰项（非弱点信号）。
_KIND_TO_EVENT = {
    "mock": ("mock_weakness", {"weak": True}),
    "qa_bad": ("qa_signal", {"correct": False}),
    "qa_unverified": ("qa_signal", {"verdict": "unverified"}),
    "qa_good": ("qa_signal", {"correct": True}),
}

# 诊断只读所断言的表（与 test_diagnosis 一致）。
_RO_TABLES = ["knowledge_atoms", "interaction_events", "learning_paths",
              "path_items", "diagnosis_reports"]


@dataclass
class ProfileResult:
    profile_id: str
    desc: str
    expected: List[str]
    got_topics: List[str]
    is_negative: bool
    recall: Optional[float] = None          # 正样本 cluster Recall@K
    atom_recall: Optional[float] = None     # 可选 weak_atom recall
    rank1_ok: Optional[bool] = None         # expected_rank1 是否命中首簇
    forbidden_topics_ok: Optional[bool] = None  # 干扰话题是否被正确排除
    forbidden_atoms_ok: Optional[bool] = None   # 高掌握 atom 是否被正确排除
    negative_ok: Optional[bool] = None      # 负样本是否如实返回空簇
    read_only_ok: bool = True


@dataclass
class EvalReport:
    k: int
    results: List[ProfileResult] = field(default_factory=list)

    @property
    def positives(self) -> List[ProfileResult]:
        return [r for r in self.results if not r.is_negative]

    @property
    def negatives(self) -> List[ProfileResult]:
        return [r for r in self.results if r.is_negative]

    @staticmethod
    def _mean(vals: List[float]) -> Optional[float]:
        return sum(vals) / len(vals) if vals else None

    @property
    def mean_recall(self) -> float:
        return self._mean([r.recall for r in self.positives if r.recall is not None]) or 0.0

    @property
    def mean_atom_recall(self) -> Optional[float]:
        return self._mean([r.atom_recall for r in self.results if r.atom_recall is not None])

    def _rate(self, attr: str) -> Optional[float]:
        vals = [getattr(r, attr) for r in self.results if getattr(r, attr) is not None]
        return self._mean([1.0 if v else 0.0 for v in vals]) if vals else None

    @property
    def rank1_accuracy(self) -> Optional[float]:
        return self._rate("rank1_ok")

    @property
    def forbidden_topics_rate(self) -> Optional[float]:
        return self._rate("forbidden_topics_ok")

    @property
    def forbidden_atoms_rate(self) -> Optional[float]:
        return self._rate("forbidden_atoms_ok")

    @property
    def negative_accuracy(self) -> Optional[float]:
        return self._rate("negative_ok")

    @property
    def read_only_ok(self) -> bool:
        return all(r.read_only_ok for r in self.results)


def _seed_db(profile: dict) -> str:
    """为单个档案建临时库并灌入 atoms/events（支持时间龄回填 created_at / last_reviewed_at）。"""
    path = os.path.join(tempfile.mkdtemp(), "lf_eval.db")
    init_db(path)
    now = datetime.now(timezone.utc)

    ar = AtomRepository(db_path=path)
    for atom in profile.get("atoms", []):
        atom_id, topic, mastery = atom[0], atom[1], float(atom[2])
        last_reviewed = now - timedelta(days=float(atom[3])) if len(atom) > 3 else None
        ar.upsert(KnowledgeAtom(
            atom_id=atom_id, topic=topic, title=topic, canonical_question="?",
            mastery_score=mastery, last_reviewed_at=last_reviewed,
            created_at=now, updated_at=now,
        ))

    conn = get_connection(path)
    for idx, ev in enumerate(profile.get("events", [])):
        kind, topic = ev[0], ev[1]
        refs = ev[2] if len(ev) > 2 else []
        age_days = float(ev[3]) if len(ev) > 3 else 0.0
        event_type, signal = _KIND_TO_EVENT[kind]
        created_at = (now - timedelta(days=age_days)).isoformat()
        # 直接写 interaction_events 以控制 created_at（评测专用；append 只盖当前时间）。
        conn.execute(
            """INSERT OR IGNORE INTO interaction_events
               (event_id, trace_id, event_type, topic, atom_refs, signal, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (f"{profile['id']}-e{idx}", profile["id"], event_type, topic,
             json.dumps(refs), json.dumps(signal), created_at),
        )
    conn.commit()
    conn.close()
    return path


def _snapshot(db_path: str) -> dict:
    conn = get_connection(db_path)
    snap = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
            for t in _RO_TABLES}
    conn.close()
    return snap


def _recall_at_k(expected: List[str], ranked: List[str], k: int) -> float:
    if not expected:
        return 0.0
    topk = set(ranked[:k])
    return sum(1 for t in set(expected) if t in topk) / len(set(expected))


def evaluate_profile(profile: dict, k: int = DEFAULT_K) -> ProfileResult:
    db_path = _seed_db(profile)
    before = _snapshot(db_path)

    out = DiagnosisAgent(db_path=db_path).run(DiagnosisInput(time_window=TimeWindow.ALL))

    read_only_ok = _snapshot(db_path) == before  # 诊断必须严格只读
    got_topics = [c.topic for c in out.clusters]  # 已按 severity 降序
    got_atoms = [w.atom_id for w in out.weak_atoms]
    expected = profile.get("expected", [])
    is_negative = len(expected) == 0

    res = ProfileResult(
        profile_id=profile["id"], desc=profile.get("desc", ""),
        expected=expected, got_topics=got_topics,
        is_negative=is_negative, read_only_ok=read_only_ok,
    )
    if is_negative:
        res.negative_ok = len(out.clusters) == 0
    else:
        res.recall = _recall_at_k(expected, got_topics, k)
        if profile.get("expected_atoms"):
            res.atom_recall = _recall_at_k(profile["expected_atoms"], got_atoms, k)

    # 判别性检查（任意样本可带）。
    if profile.get("expected_rank1"):
        res.rank1_ok = bool(got_topics) and got_topics[0] == profile["expected_rank1"]
    if "forbidden_topics" in profile:
        res.forbidden_topics_ok = not (set(profile["forbidden_topics"]) & set(got_topics))
    if "forbidden_atoms" in profile:
        res.forbidden_atoms_ok = not (set(profile["forbidden_atoms"]) & set(got_atoms))
    return res


def load_profiles(path: Optional[str] = None) -> List[dict]:
    p = Path(path) if path else DEFAULT_GOLDEN
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)["profiles"]


def run_eval(path: Optional[str] = None, k: int = DEFAULT_K) -> EvalReport:
    report = EvalReport(k=k)
    for profile in load_profiles(path):
        report.results.append(evaluate_profile(profile, k=k))
    return report


def _flags(r: ProfileResult) -> str:
    """单档案的判别性检查标记（仅显示该档案声明了的检查）。"""
    parts = []
    if r.rank1_ok is not None:
        parts.append(f"rank1={'✓' if r.rank1_ok else '✗'}")
    if r.forbidden_topics_ok is not None:
        parts.append(f"fbT={'✓' if r.forbidden_topics_ok else '✗'}")
    if r.forbidden_atoms_ok is not None:
        parts.append(f"fbA={'✓' if r.forbidden_atoms_ok else '✗'}")
    if r.atom_recall is not None:
        parts.append(f"atomR={r.atom_recall:.2f}")
    return " ".join(parts)


def format_report(report: EvalReport, threshold: float = 0.70) -> str:
    lines: List[str] = []
    lines.append(f"{'prof':<5} {'kind':<4} {'recall':<7} {'checks':<26} {'expected -> got(top-k)'}")
    lines.append("-" * 96)
    for r in report.results:
        if r.is_negative:
            kind, val, detail = "neg", ("OK" if r.negative_ok else "FAIL"), f"[] -> {r.got_topics[:report.k]}"
        else:
            kind, val, detail = "pos", f"{r.recall:.2f}", f"{r.expected} -> {r.got_topics[:report.k]}"
        ro = "  ⚠READ-ONLY VIOLATED" if not r.read_only_ok else ""
        lines.append(f"{r.profile_id:<5} {kind:<4} {val:<7} {_flags(r):<26} {detail}{ro}")

    lines.append("-" * 96)
    lines.append(f"profiles            : {len(report.results)} "
                 f"(positive {len(report.positives)}, negative {len(report.negatives)})")
    lines.append(f"cluster Recall@{report.k}    : {report.mean_recall:.3f}  "
                 f"(threshold {threshold:.2f} -> {'PASS' if report.mean_recall >= threshold else 'FAIL'})")

    def _fmt(label: str, val: Optional[float]) -> Optional[str]:
        return None if val is None else f"{label:<20}: {val:.3f}"

    for line in (
        _fmt("weak_atom Recall@%d" % report.k, report.mean_atom_recall),
        _fmt("rank-1 accuracy", report.rank1_accuracy),
        _fmt("forbidden topics ok", report.forbidden_topics_rate),
        _fmt("forbidden atoms ok", report.forbidden_atoms_rate),
        _fmt("negative accuracy", report.negative_accuracy),
    ):
        if line:
            lines.append(line)
    lines.append(f"read-only invariant : {'HELD' if report.read_only_ok else 'VIOLATED'}")
    return "\n".join(lines)


def _main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="LearnForge Diagnosis 评测（Recall@K + 判别性检查）")
    parser.add_argument("--file", help="自定义数据集 JSON（默认内置 golden）")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="Recall@K 的 K（默认 5）")
    parser.add_argument("--threshold", type=float, default=0.70, help="通过门槛（默认 0.70）")
    args = parser.parse_args(argv)
    print(format_report(run_eval(args.file, k=args.k), threshold=args.threshold))


if __name__ == "__main__":
    _main()
