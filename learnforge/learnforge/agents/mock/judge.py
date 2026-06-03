"""JudgeAgent —— 每轮独立评分（Design §3.10，与出题分离）。Phase 3：Haiku rubric + 启发式兜底。

只依据 expected_points 与回答评分，不出题、不调难度、不补正确答案给用户。
解析失败/无 LLM → 启发式打分（考点命中率），低置信兜底，保证策略环可用。
"""

from __future__ import annotations

from ...contracts.agents.mock import JudgeInput, Score, ScoreDims
from ...contracts.enums import AgentId
from . import interview_skill as IS
from ..base import BaseAgent


class JudgeAgent(BaseAgent):
    agent_id = AgentId.JUDGE

    def run(self, payload: JudgeInput) -> Score:
        prompt = (
            f"题目：{payload.question}\n考点 expected_points：{payload.expected_points}\n"
            f"考生回答：{payload.user_answer}\n"
            "按 rubric 评分：overall(0-5)、dims{correctness,depth,clarity}(各0-5)、missed_points、confidence。\n"
            "另外标注 risk_flags ⊆ {overclaim, no_evidence, vague}：夸大无证据/声称做过却无证据链/含糊过短。"
        )
        out = self.llm_structured(prompt, Score, max_tokens=320)
        if out is None:
            out = self._heuristic(payload)
        # 风险标签恒以确定性规则兜底补全（接入 LLMInternSkill 真实性边界）。
        if not out.risk_flags:
            out.risk_flags = IS.risk_flags_for_answer(payload.user_answer, payload.expected_points)
        return out

    @staticmethod
    def _heuristic(payload: JudgeInput) -> Score:
        """考点命中率启发式（无 LLM 兜底）；confidence 低，不强当真值。"""
        answer = (payload.user_answer or "").strip()
        if not answer:
            return Score(overall=0, dims=ScoreDims(), missed_points=payload.expected_points,
                         confidence=0.2)
        points = payload.expected_points or []
        if not points:
            # 无考点可比，按长度粗估。
            overall = 3 if len(answer) > 60 else 2
            return Score(overall=overall,
                         dims=ScoreDims(correctness=overall, depth=overall - 1, clarity=overall),
                         missed_points=[], confidence=0.2)
        hit = [p for p in points if any(tok and tok in answer for tok in p.split())]
        missed = [p for p in points if p not in hit]
        ratio = len(hit) / len(points)
        overall = max(0, min(5, round(ratio * 5)))
        return Score(
            overall=overall,
            dims=ScoreDims(
                correctness=overall,
                depth=max(0, overall - 1),
                clarity=min(5, 3 + (1 if len(answer) > 80 else 0)),
            ),
            missed_points=missed,
            confidence=0.3,
        )
