"""KnowledgeSource 协议与跨/源内 RRF 融合（Design §7b）。

每个知识源声明：`name`、`scope`（KnowledgeScope）、`capabilities`（支持的检索方式集合），
并实现统一的 `search(query, method, filters, top_k, embedding)`。RetrievalAgent 按
`scopes` 选择源、按 `method` 与源能力决定检索方式，跨源用 RRF 融合。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol, Set, runtime_checkable

from ...config import RRF_K
from ...contracts.agents.retrieval import Chunk, RetrievalFilters
from ...contracts.enums import KnowledgeScope, RetrievalMethod


def scoped_filter(
    filters: Optional[RetrievalFilters], scope: KnowledgeScope
) -> RetrievalFilters:
    """强制把 chunks 检索限制在某 kb_scope，避免本地/共享互相串源（个人记忆不漏进 SHARED）。"""
    if filters is None:
        return RetrievalFilters(kb_scope=scope)
    return filters.model_copy(update={"kb_scope": scope})


def rrf_fuse(ranked_lists: List[List[Chunk]], k: int = RRF_K, top_k: int = 8) -> List[Chunk]:
    """Reciprocal Rank Fusion（Design §7b）：score = Σ 1/(k + rank)。

    按 chunk_id 去重；最终 score 为 RRF 融合分，降序返回前 top_k。
    """
    scores: Dict[str, float] = {}
    by_id: Dict[str, Chunk] = {}
    for chunks in ranked_lists:
        for rank, ch in enumerate(chunks):
            scores[ch.chunk_id] = scores.get(ch.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            by_id.setdefault(ch.chunk_id, ch)
    fused: List[Chunk] = []
    for cid, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]:
        fused.append(by_id[cid].model_copy(update={"score": sc}))
    return fused


@runtime_checkable
class KnowledgeSource(Protocol):
    name: str
    scope: KnowledgeScope
    capabilities: Set[RetrievalMethod]
    available: bool

    def search(
        self,
        query: str,
        method: RetrievalMethod,
        filters: Optional[RetrievalFilters] = None,
        top_k: int = 8,
        embedding: Optional[List[float]] = None,
    ) -> List[Chunk]:
        """从该源召回片段；不支持的方式应在源内优雅降级，失败返回空列表。"""
        ...
