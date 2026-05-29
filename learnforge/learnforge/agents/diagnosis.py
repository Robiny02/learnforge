"""DiagnosisAgent —— 只读弱点诊断（Design §3.13 / §5.5）。Phase 3：ReAct 三段式聚合。

严格只读（Proposal §5 决策 5）：只 join `interaction_events` + `knowledge_atoms` 掌握度，
经 RetrievalAgent 可选补证据，产出弱点结论 + 行动建议；**不写任何 state**（写经 Manager→Planning）。

ReAct 三段式（Thought→Action→Observation，每段聚合一类信号）：
  ① LOAD_EVENTS  ：按 time_window/focus_topics 读事件 → 观测 error_freq、recency。
  ② JOIN_MASTERY ：对涉及的 atom 读掌握度（含时间遗忘 effective）→ 观测 (1-mastery)。
  ③ CLUSTER_RANK ：弱点 score = (1-mastery)*recency_weight*error_freq，聚簇、排序、给建议。
数据不足时不硬凑：confidence<0.5 + 声明（Design §3.13 Failure Modes）。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ..contracts.agents.diagnosis import (
    Cluster,
    DiagnosisInput,
    DiagnosisResult,
    WeakAtom,
)
from ..contracts.enums import AgentId, EventType
from ..mastery import effective_mastery
from .base import BaseAgent

# 弱点判定阈值与衰减常数
_RECENCY_LAMBDA = 0.05   # recency_weight = exp(-λ * age_days)
_WEAK_MASTERY_MAX = 0.6  # 有效掌握 < 0.6 视为候选弱点
_TOP_N = 5
_MIN_EVENTS_FULL_CONF = 12  # 达到该事件量给满置信


class _ReActStep:
    """单步 ReAct 轨迹（供 agent_traces / 调试观测，不外传给用户）。"""

    __slots__ = ("thought", "action", "observation")

    def __init__(self, thought: str, action: str, observation: str) -> None:
        self.thought = thought
        self.action = action
        self.observation = observation

    def as_dict(self) -> Dict[str, str]:
        return {"thought": self.thought, "action": self.action, "observation": self.observation}


class DiagnosisAgent(BaseAgent):
    agent_id = AgentId.DIAGNOSIS

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self._db_path = db_path
        self.last_react_trace: List[Dict[str, str]] = []

    # ------------------------------------------------------------------ run
    def run(self, payload: DiagnosisInput) -> DiagnosisResult:
        trace: List[_ReActStep] = []

        # ── 段①：LOAD_EVENTS ───────────────────────────────────────────
        events = self._act_load_events(payload, trace)
        if not events:
            # 数据不足：如实声明低置信（Design §3.13 / §5.5 异常①）。
            self.last_react_trace = [s.as_dict() for s in trace]
            return DiagnosisResult(
                weak_atoms=[], clusters=[],
                recommendations=["数据不足：近窗内无交互事件，建议先做一次模拟面试或问答采集信号。"],
                confidence=0.0,
            )

        # ── 段②：JOIN_MASTERY ─────────────────────────────────────────
        topic_stats, atom_stats = self._act_join_mastery(events, trace)

        # ── 段③：CLUSTER_RANK ─────────────────────────────────────────
        weak_atoms, clusters = self._act_cluster_rank(topic_stats, atom_stats, trace)

        recommendations = self._reason_recommendations(clusters, weak_atoms)
        confidence = self._estimate_confidence(events, clusters)

        self.last_react_trace = [s.as_dict() for s in trace]
        return DiagnosisResult(
            weak_atoms=weak_atoms,
            clusters=clusters,
            recommendations=recommendations,
            confidence=confidence,
        )

    # ----------------------------------------------------------- 段① events
    def _act_load_events(self, payload: DiagnosisInput, trace: List[_ReActStep]) -> List[dict]:
        events: List[dict] = []
        try:
            from ..storage.repositories import EventRepository

            repo = EventRepository(db_path=self._db_path)
            events = repo.list_window_dicts(payload.time_window.value)
        except Exception:
            events = []
        if payload.focus_topics:
            focus = set(payload.focus_topics)
            events = [e for e in events if e.get("topic") in focus]
        trace.append(_ReActStep(
            thought=f"需要 {payload.time_window.value} 窗内的弱点信号，按 focus_topics 过滤。",
            action=f"EventRepository.list_window_dicts({payload.time_window.value})",
            observation=f"取得 {len(events)} 条事件。",
        ))
        return events

    # --------------------------------------------------------- 段② mastery
    def _act_join_mastery(
        self, events: List[dict], trace: List[_ReActStep]
    ) -> Tuple[Dict[str, dict], Dict[str, dict]]:
        """聚合 per-topic 与 per-atom 的 error_freq/recency，并 join 掌握度。"""
        now = datetime.now(timezone.utc)
        topic_stats: Dict[str, dict] = {}
        atom_stats: Dict[str, dict] = {}

        for e in events:
            if not _is_weak_signal(e):
                continue
            rw = _recency_weight(e.get("created_at"), now)
            topic = e.get("topic") or "general"
            ts = topic_stats.setdefault(topic, {"error_freq": 0.0, "recency": 0.0, "count": 0})
            ts["error_freq"] += 1.0
            ts["recency"] += rw
            ts["count"] += 1
            for aid in e.get("atom_refs") or []:
                as_ = atom_stats.setdefault(
                    aid, {"topic": topic, "error_freq": 0.0, "recency": 0.0, "count": 0}
                )
                as_["error_freq"] += 1.0
                as_["recency"] += rw
                as_["count"] += 1

        # join 掌握度（缺失则按"仅频次"，effective=0 → (1-eff)=1，Design §5.5 异常②）。
        joined = 0
        try:
            from ..storage.repositories import AtomRepository

            atom_repo: Optional[AtomRepository] = AtomRepository(db_path=self._db_path)
        except Exception:
            atom_repo = None
        for aid, st in atom_stats.items():
            eff = 0.0
            if atom_repo is not None:
                try:
                    atom = atom_repo.get(aid)
                except Exception:
                    atom = None
                if atom is not None:
                    eff = effective_mastery(
                        atom.mastery_score, atom.decay_rate, atom.last_reviewed_at, now
                    )
                    st["topic"] = atom.topic
                    joined += 1
            st["effective_mastery"] = eff

        trace.append(_ReActStep(
            thought="对涉及 atom 读掌握度并计 effective（时间遗忘）；缺失则仅按频次。",
            action=f"AtomRepository.get x{len(atom_stats)}",
            observation=f"弱点话题 {len(topic_stats)} 个、atom {len(atom_stats)} 个，命中掌握度 {joined} 个。",
        ))
        return topic_stats, atom_stats

    # ---------------------------------------------------- 段③ cluster & rank
    def _act_cluster_rank(
        self, topic_stats: Dict[str, dict], atom_stats: Dict[str, dict], trace: List[_ReActStep]
    ) -> Tuple[List[WeakAtom], List[Cluster]]:
        # 弱点 atom：score = (1-effective)*recency*error_freq（Design §3.13）。
        scored_atoms: List[Tuple[float, WeakAtom]] = []
        for aid, st in atom_stats.items():
            eff = st.get("effective_mastery", 0.0)
            score = (1.0 - eff) * st["recency"] * st["error_freq"]
            if eff < _WEAK_MASTERY_MAX and score > 0:
                scored_atoms.append((
                    score,
                    WeakAtom(atom_id=aid, topic=st["topic"], mastery=eff, evidence_count=st["count"]),
                ))
        scored_atoms.sort(key=lambda x: x[0], reverse=True)
        weak_atoms = [wa for _, wa in scored_atoms[:_TOP_N]]

        # 话题簇：severity = 归一化的话题弱点强度。
        raw: Dict[str, float] = {}
        for topic, st in topic_stats.items():
            raw[topic] = st["recency"] * st["error_freq"]
        max_raw = max(raw.values()) if raw else 1.0
        clusters = [
            Cluster(topic=t, severity=_clamp(v / max_raw if max_raw else 0.0))
            for t, v in sorted(raw.items(), key=lambda x: x[1], reverse=True)[:_TOP_N]
        ]

        trace.append(_ReActStep(
            thought="按 (1-mastery)*recency*error_freq 排序 atom，话题归一化得 severity。",
            action="cluster_and_rank(top_n=%d)" % _TOP_N,
            observation=f"产出弱点 atom {len(weak_atoms)} 个、簇 {len(clusters)} 个。",
        ))
        return weak_atoms, clusters

    # --------------------------------------------------------- 建议（LLM 可选）
    def _reason_recommendations(
        self, clusters: List[Cluster], weak_atoms: List[WeakAtom]
    ) -> List[str]:
        if not clusters:
            return ["近窗内未见明显弱点，可按现有计划继续巩固。"]

        out = self.llm_structured(
            "基于以下弱点簇给出 3 条以内可执行的复习建议（每条 ≤40 字，按优先级）：\n"
            + "\n".join(f"- {c.topic} (severity={c.severity:.2f})" for c in clusters),
            _Recommendations,
            max_tokens=256,
        )
        if out is not None and out.items:
            return out.items[:3]
        # 回退：按 severity 模板化建议（链路永远通）。
        return [f"加强「{c.topic}」：定向刷题 + 复盘错点（severity {c.severity:.2f}）"
                for c in clusters[:3]]

    @staticmethod
    def _estimate_confidence(events: List[dict], clusters: List[Cluster]) -> float:
        """置信随数据量上升；事件少或无簇时 <0.5（Design §3.13）。"""
        vol = min(1.0, len(events) / _MIN_EVENTS_FULL_CONF)
        base = 0.3 + 0.6 * vol
        if not clusters:
            base = min(base, 0.45)
        return round(_clamp(base), 3)


# --- LLM 建议结构化输出 schema ---
from pydantic import BaseModel, Field  # noqa: E402


class _Recommendations(BaseModel):
    items: List[str] = Field(default_factory=list)


# --- helpers ---
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _is_weak_signal(event: dict) -> bool:
    """事件是否表征弱点：mock_weakness 恒是；qa_signal 看 signal 是否标注未掌握。"""
    et = event.get("event_type")
    if et == EventType.MOCK_WEAKNESS.value:
        return True
    if et == EventType.QA_SIGNAL.value:
        sig = event.get("signal") or {}
        if sig.get("correct") is False:
            return True
        if sig.get("verdict") in ("unverified", "uncertain"):
            return True
        if sig.get("weak") is True:
            return True
    return False


def _recency_weight(created_at_iso: Optional[str], now: datetime) -> float:
    if not created_at_iso:
        return 1.0
    try:
        ts = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return 1.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return math.exp(-_RECENCY_LAMBDA * age_days)
