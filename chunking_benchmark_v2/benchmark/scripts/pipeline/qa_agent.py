"""QAAgent — retrieval-augmented answer with citations (mirrors learnforge QAAgent).

Chain: RetrievalAgent → Synthesizer (LLM) → Verifier-lite. Calls RetrievalAgent
(agent-to-agent), grounds the answer in retrieved chunks, cites doc_ids. With no
API key it degrades to an extractive answer (top snippets), so the chain still
returns something verifiable. Never fabricates citations — they come from hits.
"""

from __future__ import annotations

from typing import List, Optional

from .agent_base import PipelineAgent
from .contracts import (
    Citation,
    QARequest,
    QAResult,
    RetrievalQuery,
    RetrievalResult,
    Tracer,
)
from .retrieval_agent import RetrievalAgent

_SYS = (
    "你是严谨的程序员面试知识助手。只依据提供的【证据片段】回答，"
    "不要编造；证据不足就明说。在关键结论后用 [n] 标注引用的证据编号。"
    "回答简洁、分点、用中文。"
)


class QAAgent(PipelineAgent):
    name = "qa"
    capabilities = ["qa.answer", "retrieval.search", "llm.complete"]

    def __init__(self, retrieval: RetrievalAgent) -> None:
        super().__init__()
        self.retrieval = retrieval

    def _evidence_block(self, hits) -> str:
        out = []
        for i, h in enumerate(hits, 1):
            tag = "" if h.index_role == "main" else f" ({h.index_role})"
            out.append(f"[{i}]{tag} 来源={h.chunk.doc_id}\n{h.chunk.text[:600]}")
        return "\n\n".join(out)

    def _extractive(self, hits) -> str:
        if not hits:
            return "未检索到相关证据，无法作答。"
        lines = ["（无 LLM key，给出抽取式证据摘要）"]
        for i, h in enumerate(hits, 1):
            snippet = " ".join(h.chunk.text.split())[:160]
            lines.append(f"[{i}] {snippet}")
        return "\n".join(lines)

    def run(self, req: QARequest, tracer: Optional[Tracer] = None) -> QAResult:
        self.require("qa.answer")
        rq = RetrievalQuery(query=req.question, top_k=req.top_k)
        rr: RetrievalResult = self.retrieval.run(rq, tracer=tracer)  # agent-to-agent

        citations = [
            Citation(doc_id=h.chunk.doc_id, source_type=h.chunk.source_type,
                     snippet=" ".join(h.chunk.text.split())[:120])
            for h in rr.hits
        ]
        llm_used = False
        if self.llm.available and rr.hits:
            self.require("llm.complete")
            user = f"问题：{req.question}\n\n【证据片段】\n{self._evidence_block(rr.hits)}"
            ans = self.llm.chat(_SYS, user, max_tokens=600)
            if ans:
                answer, llm_used = ans, True
            else:
                answer = self._extractive(rr.hits)
        else:
            answer = self._extractive(rr.hits)

        if tracer is not None:
            tracer.log(self.name, "answer",
                       f"{'LLM' if llm_used else 'extractive'}; {len(citations)} citations")
        return QAResult(
            question=req.question,
            answer=answer,
            citations=citations,
            grounded=bool(rr.hits),
            llm_used=llm_used,
            degraded_retrieval=rr.degraded,
            trace=tracer,
        )
