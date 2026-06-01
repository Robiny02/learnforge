"""PostgresSharedSource —— 把 PG 知识库接入现有 KnowledgeSource 协议（Design §7）。

与 LocalSharedSource 同形：实现统一 search()，由 RetrievalAgent 调度并跨源 RRF。
scope = SHARED（共享库）。VECTOR/HYBRID 缺 embedding 时自然退化（与 SQLite 路径一致）。
"""

from __future__ import annotations

from typing import List, Optional

from ...contracts.agents.retrieval import Chunk, RetrievalFilters
from ...contracts.enums import KnowledgeScope, RetrievalMethod
from .repository import PgChunkRepository


class PostgresSharedSource:
    scope = KnowledgeScope.SHARED

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._repo = PgChunkRepository(dsn=dsn)

    def search(
        self,
        query: str,
        method: RetrievalMethod,
        filters: Optional[RetrievalFilters],
        top_k: int,
        embedding: Optional[List[float]] = None,
    ) -> List[Chunk]:
        # KEYWORD 与 FULLTEXT 在 PG 上同走关键词路径（BM25 或 FTS 降级，由 repo 决定）。
        if method in (RetrievalMethod.KEYWORD, RetrievalMethod.FULLTEXT):
            return self._repo.keyword_search(query, filters, top_k)
        if method == RetrievalMethod.VECTOR:
            return self._repo.vector_search(embedding, filters, top_k)
        # HYBRID：keyword + vector → RRF。
        return self._repo.hybrid_search(query, embedding, filters, top_k)
