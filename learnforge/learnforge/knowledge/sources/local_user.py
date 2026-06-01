"""LocalUserSource —— 本地用户库（个人学习数据），scope=LOCAL。

检索个人数据三源：
- mock 历史记录（mock_turns，关键词）；
- 历史问答（qa_history，FTS5）——**导入/seed 的结构化历史问答**落点；
- 个人长期记忆（chunks 且 kb_scope=local，FTS5 + 向量）——**运行时 QA/mock/进展/决策** 经
  Manager 写 daily memory 的落点。

「历史问答」边界（REQUIREMENTS G7）：运行时 QA 由 Manager.remember_qa 写 daily（→ chunks(local)），
seed/导入的历史问答写 qa_history；二者互不重复（同一条 QA 不会同时存于两处），均在本源召回层汇合。

mock/qa 属轻量关键词召回；chunks(local) 支持向量，故本源整体提供 VECTOR/HYBRID 能力
（无 embedding 时在源内降级为全文）。kb_scope 强制为 local，个人数据不外漏到共享检索。
"""

from __future__ import annotations

from typing import List, Optional, Set

from ...contracts.agents.retrieval import Chunk, RetrievalFilters
from ...contracts.enums import KnowledgeScope, RetrievalMethod
from ...memory.decay import decay_weight, importance_for
from ...storage.repositories import (
    ChunkRepository,
    MockTurnRepository,
    QAHistoryRepository,
)
from .base import rrf_fuse, scoped_filter


class LocalUserSource:
    name = "local_user"
    scope = KnowledgeScope.LOCAL
    capabilities: Set[RetrievalMethod] = {
        RetrievalMethod.KEYWORD,
        RetrievalMethod.FULLTEXT,
        RetrievalMethod.VECTOR,
        RetrievalMethod.HYBRID,
    }
    available = True

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    def search(
        self,
        query: str,
        method: RetrievalMethod,
        filters: Optional[RetrievalFilters] = None,
        top_k: int = 8,
        embedding: Optional[List[float]] = None,
    ) -> List[Chunk]:
        try:
            mock_repo = MockTurnRepository(db_path=self._db_path)
            conn = mock_repo.conn
            qa_repo = QAHistoryRepository(conn=conn)
            chunk_repo = ChunkRepository(conn=conn)
            local = scoped_filter(filters, KnowledgeScope.LOCAL)
            want_vector = (
                method in (RetrievalMethod.VECTOR, RetrievalMethod.HYBRID) and bool(embedding)
            )
            want_text = method != RetrievalMethod.VECTOR or not embedding

            mock = mock_repo.fts_match(query, top_k=top_k * 2)
            qa = qa_repo.fts_match(query, top_k=top_k * 2)
            ctext = (
                chunk_repo.fts_match(query, top_k=top_k * 2, filters=local) if want_text else []
            )
            cvec = (
                chunk_repo.vector_knn(embedding, top_k=top_k * 2, filters=local)
                if want_vector
                else []
            )
        except Exception:
            return []
        lists = [mock, qa, ctext, cvec]
        for lst in lists:
            for ch in lst:
                ch.source_name = self.name
        fused = rrf_fuse([lst for lst in lists if lst], top_k=top_k * 2)
        return self._apply_time_decay(fused)[:top_k]

    @staticmethod
    def _apply_time_decay(chunks: List[Chunk]) -> List[Chunk]:
        """按 note 年龄(衰减)与类型(重要性)重排（MEMORY.md 不在索引内，天然豁免）。

        综合排序 = 相关性(score) × 时间衰减(decay_weight) × 重要性(0.5+0.5·importance)：
        相关性主导，时间与重要性作温和修正（REQUIREMENTS R7.3）。重要性以温和系数介入，
        避免压过相关性。无日期的命中（mock_turns/qa_history）decay_weight=1.0，不受影响。
        """
        rescored = []
        for ch in chunks:
            meta = ch.metadata or {}
            kind = meta.get("kind")
            factor = decay_weight(meta.get("date"), kind) * (0.5 + 0.5 * importance_for(kind))
            rescored.append(ch.model_copy(update={"score": ch.score * factor}))
        rescored.sort(key=lambda c: c.score, reverse=True)
        return rescored
