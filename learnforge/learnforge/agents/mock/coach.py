"""CoachAgent —— 终场复盘 + 弱点事件（Design §3.12）。Phase 3：Sonnet 聚合 + 事件 emit。

聚合逐轮评分为可行动复盘，每条 weakness 必带 evidence（引哪一轮）；
产出 interaction_event(mock_weakness) 供诊断消费。样本 <2 轮 → "样本不足"简报。
"""

from __future__ import annotations

from typing import List

from ...contracts.agents.mock import CoachInput, CoachOutput, CoachReport, Weakness
from ...contracts.enums import AgentId, EventType
from ...contracts.message import EventPayload
from ..base import BaseAgent


class CoachAgent(BaseAgent):
    agent_id = AgentId.COACH

    def run(self, payload: CoachInput) -> CoachOutput:
        scored = [s for s in payload.turn_scores if s.overall is not None]
        if len(scored) < 2:
            return CoachOutput(
                report=CoachReport(summary="样本不足（<2 轮有效评分），暂不下结论，建议多练几轮。"),
                events=[],
            )

        out = self.llm_structured(self._build_prompt(payload), CoachReport, max_tokens=1024)
        report = out if out is not None else self._heuristic_report(payload)
        events = self._weakness_events(report)
        return CoachOutput(report=report, events=events)

    @staticmethod
    def _build_prompt(payload: CoachInput) -> str:
        lines = []
        for i, s in enumerate(payload.turn_scores):
            lines.append(f"  第{i + 1}轮 overall={s.overall} missed={s.missed_points}")
        return (
            "对一场模拟面试做终场复盘。逐轮评分：\n" + "\n".join(lines) + "\n"
            f"覆盖话题：{payload.topic_coverage}\n"
            "输出 summary / strengths / weaknesses(每条带 evidence 引哪一轮) / next_steps。"
        )

    @staticmethod
    def _heuristic_report(payload: CoachInput) -> CoachReport:
        """无 LLM 兜底：低分轮抽成 weakness，引用轮次为 evidence。"""
        overalls = [s.overall for s in payload.turn_scores if s.overall is not None]
        avg = sum(overalls) / len(overalls) if overalls else 0
        weaknesses: List[Weakness] = []
        strengths: List[str] = []
        topics = payload.topic_coverage or ["本场主题"]
        primary_topic = topics[0]
        for i, s in enumerate(payload.turn_scores):
            if s.overall is None:
                continue
            if s.overall <= 2:
                miss = "、".join(s.missed_points) if s.missed_points else "作答不充分"
                weaknesses.append(Weakness(topic=primary_topic,
                                           evidence=f"第{i + 1}轮 overall={s.overall}：{miss}"))
            elif s.overall >= 4:
                strengths.append(f"第{i + 1}轮表现稳定（overall={s.overall}）")
        return CoachReport(
            summary=f"共 {len(overalls)} 轮，平均 {avg:.1f}/5。"
                    + ("整体扎实。" if avg >= 3.5 else "存在明显薄弱点，需针对性巩固。"),
            strengths=strengths[:3],
            weaknesses=weaknesses[:5],
            next_steps=[f"针对「{w.topic}」复盘失分点并定向刷题" for w in weaknesses[:3]],
        )

    @staticmethod
    def _weakness_events(report: CoachReport) -> List[EventPayload]:
        return [
            EventPayload(
                event_type=EventType.MOCK_WEAKNESS,
                topic=w.topic,
                atom_refs=[],
                signal={"evidence": w.evidence, "weak": True, "source": "mock"},
            )
            for w in report.weaknesses
        ]
