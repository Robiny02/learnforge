"""QAAgent —— 问答子系统外壳（Design §3.2）。Phase 2：动态链路 + 轻路径 + 后端选择。

链路：Router → (Retrieval) → Synthesizer → (Verifier)。
- chitchat 走轻路径，跳过检索/校验（Proposal R1）。
- 后端选择（用户澄清）：concept/temporal 走云端知识库（面经+博客，doc 生成）；
  project 走本地 RAG（FTS5 over SQLite，含 mock 记录）。
"""

from __future__ import annotations

from ...contracts.agents.qa import (
    Citation,
    QAInput,
    QAOutput,
    RouterInput,
    SynthesizerInput,
    VerifierInput,
)
from ...contracts.agents.retrieval import RetrievalInput
from ...contracts.enums import AgentId, QType, RetrievalBackend, Verdict
from ..base import BaseAgent
from ..retrieval import RetrievalAgent
from .router import RouterAgent
from .synthesizer import SynthesizerAgent
from .verifier import VerifierAgent

_VERDICT_CONFIDENCE = {
    Verdict.VERIFIED: 0.85,
    Verdict.UNCERTAIN: 0.5,
    Verdict.UNVERIFIED: 0.4,
}


class QAAgent(BaseAgent):
    agent_id = AgentId.QA

    def __init__(self) -> None:
        super().__init__()
        self.router = RouterAgent()
        self.retrieval = RetrievalAgent()
        self.synthesizer = SynthesizerAgent()
        self.verifier = VerifierAgent()

    @staticmethod
    def _pick_backend(q_type: QType) -> RetrievalBackend:
        # concept/temporal：外部语料（面经+博客）→ 云端；project：用户本地内容 → 本地。
        if q_type in (QType.CONCEPT, QType.TEMPORAL):
            return RetrievalBackend.CLOUD
        return RetrievalBackend.LOCAL

    def run(self, payload: QAInput) -> QAOutput:
        route = self.router.run(RouterInput(question=payload.question))

        chunks = []
        if route.need_retrieval:
            chunks = self.retrieval.run(
                RetrievalInput(query=payload.question, backend=self._pick_backend(route.q_type))
            ).chunks

        draft = self.synthesizer.run(
            SynthesizerInput(
                question=payload.question,
                retrieved=chunks,
                scoped_atoms=payload.scoped_atoms,
                project_context=payload.project_context_ref,
            )
        )

        if route.need_verifier:
            verified = self.verifier.run(
                VerifierInput(draft=draft.draft, claims=draft.claims, retrieved=chunks)
            )
            answer, verdict = verified.verified_answer, verified.verdict
        else:
            answer, verdict = draft.draft, Verdict.UNCERTAIN

        citations = [
            Citation(chunk_id=c.chunk_id, source_type=c.source_type, snippet=c.text[:120])
            for c in chunks
        ]
        # 成本聚合：QA 外壳本身不调 LLM，累计各子 agent 成本（供 trace/预算监控）。
        self.last_cost_usd = sum(
            getattr(a, "last_cost_usd", 0.0)
            for a in (self.router, self.retrieval, self.synthesizer, self.verifier)
        )
        # 信号话题：优先取检索片段 topic，否则回退到问题类型。
        topic = None
        for c in chunks:
            topic = c.metadata.get("topic") if isinstance(c.metadata, dict) else None
            if topic:
                break
        return QAOutput(
            answer=answer,
            citations=citations,
            verdict=verdict,
            confidence=_VERDICT_CONFIDENCE.get(verdict, 0.5),
            topic=topic or route.q_type.value,
        )
