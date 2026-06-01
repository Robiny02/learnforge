"""LocalSharedSource —— 共享知识库（本地 SQLite），scope=SHARED。

检索 chunks（课程/博客/面经/文档切片）+ knowledge_atoms（公共知识点/题库），支持：
- KEYWORD/FULLTEXT：FTS5 BM25（含 LIKE 降级）。
- VECTOR：sqlite-vec KNN（需 embedding；不可用则降级到全文）。
- HYBRID：全文 + 向量并召回后 RRF 融合（默认）。
metadata 过滤（topic/source_type/kb_scope）下推到仓储层。
"""

from __future__ import annotations

from typing import List, Optional, Set

from ...contracts.agents.retrieval import Chunk, RetrievalFilters
from ...contracts.enums import KnowledgeScope, RetrievalMethod, SourceType
from ...storage.repositories import AtomRepository, ChunkRepository
from .base import rrf_fuse, scoped_filter


class LocalSharedSource:
    name = "local_shared"
    scope = KnowledgeScope.SHARED
    capabilities: Set[RetrievalMethod] = {
        RetrievalMethod.KEYWORD,
        RetrievalMethod.FULLTEXT,
        RetrievalMethod.VECTOR,
        RetrievalMethod.HYBRID,
    }
    available = True

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    @staticmethod
    def _include_atoms(filters: Optional[RetrievalFilters]) -> bool:
        # source_type 过滤为非 atom 类时，跳过 atom 源（题库）。
        return filters is None or filters.source_type in (None, SourceType.ATOM)

    def search(
        self,
        query: str,
        method: RetrievalMethod,
        filters: Optional[RetrievalFilters] = None,
        top_k: int = 8,
        embedding: Optional[List[float]] = None,
    ) -> List[Chunk]:
        try:
            chunk_repo = ChunkRepository(db_path=self._db_path)
            atom_repo = AtomRepository(conn=chunk_repo.conn)
        except Exception:
            return []

        fetch = top_k * 4
        ranked: List[List[Chunk]] = []
        want_vector = method in (RetrievalMethod.VECTOR, RetrievalMethod.HYBRID) and bool(embedding)
        want_text = method != RetrievalMethod.VECTOR or not embedding
        # 强制 kb_scope=shared：共享源只服务共享语料，个人 chunks(local) 不漏进 SHARED 检索。
        shared = scoped_filter(filters, KnowledgeScope.SHARED)

        try:
            if want_text:
                ranked.append(chunk_repo.fts_match(query, top_k=fetch, filters=shared))
                if self._include_atoms(filters):
                    ranked.append(atom_repo.fts_match(query, top_k=fetch, filters=filters))
            if want_vector:
                ranked.append(chunk_repo.vector_knn(embedding, top_k=fetch, filters=shared))
                if self._include_atoms(filters):
                    ranked.append(atom_repo.vector_knn(embedding, top_k=fetch, filters=filters))
        except Exception:
            return []

        fused = rrf_fuse([r for r in ranked if r], top_k=top_k)
        for ch in fused:
            ch.source_name = self.name
        return fused
