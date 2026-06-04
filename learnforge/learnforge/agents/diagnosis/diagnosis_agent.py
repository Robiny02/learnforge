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

from ...contracts.agents.diagnosis import (
    Cluster,
    DiagnosisInput,
    DiagnosisResult,
    WeakAtom,
)
from ...contracts.enums import AgentId, EventType
from ...llm.client import LLM
from ...mcp import tools as _toolmod
from ...skills.registry import SKILL_REGISTRY
from ..base import BaseAgent
from ..react.loop import ReactRunner

# 弱点判定阈值与衰减常数
_RECENCY_LAMBDA = 0.05   # recency_weight = exp(-λ * age_days)
_WEAK_MASTERY_MAX = 0.6  # 有效掌握 < 0.6 视为候选弱点
_TOP_N = 5
_MIN_EVENTS_FULL_CONF = 12  # 达到该事件量给满置信

# 诊断工具(在 tools/ 框架真执行)给模型看的参数 schema——供 LLM 自主 tool-calling。
_DIAG_TOOLS = [
    "diagnosis.search_events", "diagnosis.get_mastery_snapshot",
    "diagnosis.search_qa_history", "diagnosis.search_mock_turns",
    "diagnosis.retrieve_knowledge", "diagnosis.analyze_code_static",
]
_toolmod.register_schema("diagnosis.search_events",
    {"type": "object", "properties": {
        "time_window": {"type": "string", "enum": ["7d", "30d", "all"]},
        "focus_topics": {"type": "array", "items": {"type": "string"}}}},
    "读取交互事件作为弱点证据(按时间窗/主题)。")
_toolmod.register_schema("diagnosis.get_mastery_snapshot",
    {"type": "object", "properties": {"atom_ids": {"type": "array", "items": {"type": "string"}}},
     "required": ["atom_ids"]}, "读取指定 atom 的有效掌握度。")
_toolmod.register_schema("diagnosis.search_qa_history",
    {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
     "required": ["query"]}, "检索本地问答历史作为证据。")
_toolmod.register_schema("diagnosis.search_mock_turns",
    {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
     "required": ["query"]}, "检索本地模拟面试记录作为证据。")
_toolmod.register_schema("diagnosis.retrieve_knowledge",
    {"type": "object", "properties": {"query": {"type": "string"},
        "scopes": {"type": "array", "items": {"type": "string"}}, "top_k": {"type": "integer"}},
     "required": ["query"]}, "检索共享/本地知识为诊断补充背景。")
_toolmod.register_schema("diagnosis.analyze_code_static",
    {"type": "object", "properties": {"code": {"type": "string"}, "language": {"type": "string"}},
     "required": ["code"]}, "对用户代码做确定性静态分析。")

_DIAG_REACT_SYSTEM = (
    "你是学习诊断师（只读，绝不改路径/atom）。用工具自主收集证据后给复习建议：\n"
    "先 diagnosis.search_events 取信号；按需 get_mastery_snapshot/search_mock_turns/"
    "search_qa_history/retrieve_knowledge 补证据；用户给了代码可 analyze_code_static。\n"
    "最后用 ≤3 条按优先级、简短、可执行的中文复习建议作答。弱点打分由系统确定性计算，你只需聚证据+给建议。"
)


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
        selected = SKILL_REGISTRY.select(
            self.agent_id,
            intent="diagnose",
            event=payload.trigger.value,
            text=" ".join(payload.focus_topics),
        )
        if selected:
            self.skill = selected[0]
        # LLM 自主 ReAct（模型自己决定调哪些诊断工具）；降级/不可用 → 确定性兜底。
        if LLM.available and self.skill is not None:
            out = self._run_react(payload)
            if out is not None:
                return out
        return self._run_deterministic(payload)

    # ------------------------------- LLM 自主 ReAct（驱动 tools/ 诊断工具）
    def _run_react(self, payload: DiagnosisInput) -> Optional[DiagnosisResult]:
        events_sink: List[dict] = []

        def _mk(name: str):
            def handler(args: dict) -> dict:
                a = dict(args)
                a.setdefault("db_path", self._db_path)
                tr = self.call_tool(name, a)  # 经 tool_runtime（含权限门）执行 tools/ 工具
                if name == "diagnosis.search_events" and isinstance(tr.data, dict):
                    events_sink.extend(tr.data.get("events") or [])
                out = {"ok": tr.ok, "observation": tr.observation}
                if isinstance(tr.data, dict):
                    out.update(tr.data)
                return out
            return handler

        tools = [t for t in _DIAG_TOOLS if self.has_tool(t)]
        handlers = {t: _mk(t) for t in tools}
        win = payload.time_window.value
        focus = "、".join(payload.focus_topics) if payload.focus_topics else "（全部）"
        system = (self.skill.spec.system_prompt if self.skill else "") or _DIAG_REACT_SYSTEM
        user_prompt = f"诊断学习薄弱点。时间窗={win}，关注主题={focus}。先取证据再给复习建议。"
        # 按需检索（只读）：读 mock 记录/上传材料里的目标要求，辅助判断弱点取向。不写任何 state。
        materials = self._recall_materials(payload)
        if materials:
            user_prompt += f"\n\n【参考材料(只读，勿据此编造掌握度)】\n{materials}"
        res = ReactRunner(max_steps=4).run(
            self,
            user_prompt=user_prompt,
            tool_names=tools, system=system, handlers=handlers,
        )
        if res.degraded and not res.text:
            return None  # 降级 → 确定性兜底

        # 关键打分用确定性计算（可靠）：用 ReAct 采到的 events（或确定性补载）聚类。
        trace: List[_ReActStep] = []
        events = events_sink or self._act_load_events(payload, trace)
        if not events:
            return DiagnosisResult(
                weak_atoms=[], clusters=[],
                recommendations=["数据不足：近窗内无交互事件，建议先做一次模拟面试或问答采集信号。"],
                confidence=0.0,
            )
        topic_stats, atom_stats = self._act_join_mastery(events, trace)
        weak_atoms, clusters = self._act_cluster_rank(topic_stats, atom_stats, trace)
        # 建议用确定性结构化生成（clean ≤3 条 + 模板兜底），不直接抓模型自由文本（会带 markdown 标题/前言）。
        recs = self._reason_recommendations(clusters, weak_atoms)
        self.last_react_trace = res.trace
        return DiagnosisResult(weak_atoms=weak_atoms, clusters=clusters,
                               recommendations=recs, confidence=self._estimate_confidence(events, clusters))

    def _recall_materials(self, payload: DiagnosisInput) -> str:
        """只读地检索上传材料/历史记录（local），辅助诊断。无 query/命中 → 空串（非强制）。"""
        from ...contracts.enums import KnowledgeScope

        q = "、".join(payload.focus_topics).strip() or "薄弱点 复习 要求"
        try:
            return self.recall(q, scopes=[KnowledgeScope.LOCAL], top_k=3).text
        except Exception:  # noqa: BLE001 - 检索失败不影响只读诊断
            return ""

    @staticmethod
    def _parse_recs(text: str) -> List[str]:
        """从 ReAct 最终文本抽 ≤3 条建议（去掉项目符号/编号）。"""
        import re as _re
        out: List[str] = []
        for line in (text or "").splitlines():
            s = _re.sub(r"^\s*([-*•]|\d+[.)、])\s*", "", line).strip()
            if len(s) >= 4:
                out.append(s)
        return out[:3]

    # ------------------------------- 确定性兜底（原 3 段式）
    def _run_deterministic(self, payload: DiagnosisInput) -> DiagnosisResult:
        trace: List[_ReActStep] = []
        events = self._act_load_events(payload, trace)
        if not events:
            self.last_react_trace = [s.as_dict() for s in trace]
            return DiagnosisResult(
                weak_atoms=[], clusters=[],
                recommendations=["数据不足：近窗内无交互事件，建议先做一次模拟面试或问答采集信号。"],
                confidence=0.0,
            )
        topic_stats, atom_stats = self._act_join_mastery(events, trace)
        weak_atoms, clusters = self._act_cluster_rank(topic_stats, atom_stats, trace)
        recommendations = self._reason_recommendations(clusters, weak_atoms)
        confidence = self._estimate_confidence(events, clusters)
        self.last_react_trace = [s.as_dict() for s in trace]
        return DiagnosisResult(weak_atoms=weak_atoms, clusters=clusters,
                               recommendations=recommendations, confidence=confidence)

    # ----------------------------------------------------------- 段① events
    def _act_load_events(self, payload: DiagnosisInput, trace: List[_ReActStep]) -> List[dict]:
        events: List[dict] = []
        try:
            result = self.call_tool(
                "diagnosis.search_events",
                {
                    "db_path": self._db_path,
                    "time_window": payload.time_window.value,
                    "focus_topics": payload.focus_topics,
                },
            )
            events = list((result.data or {}).get("events", []))
        except Exception:
            events = []
        trace.append(_ReActStep(
            thought=f"需要 {payload.time_window.value} 窗内的弱点信号，按 focus_topics 过滤。",
            action=f"tool:diagnosis.search_events(time_window={payload.time_window.value})",
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
            result = self.call_tool(
                "diagnosis.get_mastery_snapshot",
                {"db_path": self._db_path, "atom_ids": list(atom_stats.keys())},
            )
            mastery_rows = (result.data or {}).get("atoms", {})
        except Exception:
            mastery_rows = {}
        for aid, st in atom_stats.items():
            eff = 0.0
            atom = mastery_rows.get(aid)
            if atom is not None:
                eff = float(atom.get("effective_mastery", 0.0))
                st["topic"] = atom.get("topic") or st["topic"]
                joined += 1
            st["effective_mastery"] = eff

        trace.append(_ReActStep(
            thought="对涉及 atom 读掌握度并计 effective（时间遗忘）；缺失则仅按频次。",
            action=f"tool:diagnosis.get_mastery_snapshot x{len(atom_stats)}",
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
