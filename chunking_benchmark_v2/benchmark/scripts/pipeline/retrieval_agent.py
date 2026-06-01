"""RetrievalAgent — hybrid search over the role-routed store (mirrors learnforge).

Only `main` chunks form the primary index; `downweighted` chunks (off-topic /
career-noise) join at a score penalty so they surface only when nothing better
exists; `isolated` chunks (irrelevant OCR / ads) never enter retrieval. Reports
`method_used` + `degraded`, exactly like learnforge's RetrievalAgent.
"""

from __future__ import annotations

from typing import List, Optional

from ..lib.embeddings import get_embedder
from ..lib.retriever import HybridRetriever
from .agent_base import PipelineAgent
from .contracts import (
    KnowledgeStore,
    RetrievalQuery,
    RetrievalResult,
    RetrievedChunk,
    Tracer,
)

_DOWNWEIGHT = 0.6


class RetrievalAgent(PipelineAgent):
    name = "retrieval"
    capabilities = ["retrieval.search"]

    def __init__(self, store: KnowledgeStore) -> None:
        super().__init__()
        self.store = store
        self._indexed: List = []
        self._retr: Optional[HybridRetriever] = None
        self._built_for = None

    def _build(self, include_downweighted: bool) -> None:
        roles = ("main", "downweighted") if include_downweighted else ("main",)
        self._indexed = self.store.by_role(*roles)
        texts = [ic.chunk.text for ic in self._indexed]
        self._retr = HybridRetriever(texts, use_vector=True, embedder=get_embedder())
        self._built_for = include_downweighted

    def run(self, q: RetrievalQuery, tracer: Optional[Tracer] = None) -> RetrievalResult:
        self.require("retrieval.search")
        if self._retr is None or self._built_for != q.include_downweighted:
            self._build(q.include_downweighted)
        assert self._retr is not None

        raw, degraded = self._retr.search(q.query, top_k=q.top_k * 2)
        scored = []
        for idx, score in raw:
            ic = self._indexed[idx]
            s = score * (_DOWNWEIGHT if ic.index_role == "downweighted" else 1.0)
            scored.append((ic, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        hits = [
            RetrievedChunk(chunk=ic.chunk, score=round(s, 4), index_role=ic.index_role)
            for ic, s in scored[: q.top_k]
        ]
        method = "hybrid (bm25+vector RRF)" if not degraded else "bm25"
        if tracer is not None:
            tracer.log(self.name, "search",
                       f"'{q.query[:30]}' → {len(hits)} hits via {method}")
        return RetrievalResult(query=q.query, hits=hits, method_used=method, degraded=degraded)
