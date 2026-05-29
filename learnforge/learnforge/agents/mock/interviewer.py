"""InterviewerAgent —— 出题与追问（Design §3.9）。Phase 3：Sonnet 出题 + 题库兜底。

只出题/追问，不评分、不定策略。超时/无 LLM → 从候选 atom 取预置题兜底。
"""

from __future__ import annotations

from typing import List, Optional

from ...contracts.agents.mock import InterviewerInput, InterviewerOutput
from ...contracts.agents.retrieval import RetrievalInput
from ...contracts.enums import AgentId
from ..base import BaseAgent
from ..retrieval import RetrievalAgent


class InterviewerAgent(BaseAgent):
    agent_id = AgentId.INTERVIEWER

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self.retrieval = RetrievalAgent(db_path=db_path)

    def run(self, payload: InterviewerInput) -> InterviewerOutput:
        chunks = payload.retrieved
        if not chunks:
            try:
                chunks = self.retrieval.run(RetrievalInput(query=payload.topic, top_k=4)).chunks
            except Exception:
                chunks = []

        asked = [t.question for t in payload.turn_history]
        evidence = "\n".join(f"- {c.text[:200]}" for c in chunks) or "（无检索证据）"
        prompt = (
            f"主题：{payload.topic}\n难度(1-5)：{payload.difficulty}\n"
            f"已问过（勿重复）：{asked or '无'}\n参考资料：\n{evidence}\n"
            "出一道该难度的面试题，给出考点 expected_points 与相关 atom_refs。"
        )
        out = self.llm_structured(prompt, InterviewerOutput, max_tokens=512)
        if out is not None and out.question:
            return out

        # 兜底：题库 / 模板题（Design §3.9 Failure Modes）。
        atom_refs: List[str] = [c.chunk_id for c in chunks[:2]]
        n = len(payload.turn_history) + 1
        return InterviewerOutput(
            question=f"（第{n}题 · 难度{payload.difficulty}）请讲讲「{payload.topic}」的核心原理与典型应用场景。",
            expected_points=[f"{payload.topic} 的定义", "适用场景", "常见误区"],
            atom_refs=atom_refs,
        )
