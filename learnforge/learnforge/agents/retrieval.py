"""RetrievalAgent —— 共享混合检索（Design §3.6 / §7，含用户澄清的双后端语义）。

后端（Design §10a-G1 的 backend 参数落到具体语义）：
- **LocalBackend**（本地 RAG = FTS5 over SQLite）：检索 sqlite 内已有内容——chunks（atoms/doc）
  与 mock_turns（历史 mock 记录）。两路 FTS 结果用 RRF(k=60) 融合。
- **CloudBackend**（云端知识库）：面经 + 技术博客等大语料，用于"生成文档/答案"。MVP 留接口
  前向兼容（不接真实服务，Proposal §2 Non-Goal），返回空并标注。

条件改写（Design §3.6）：query 过长或模糊时用 Haiku 改写一次，否则原样。
RetrievalAgent 只做"召回+排序"，不合成（Proposal §5 决策 6）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from ..config import RRF_K
from ..contracts.agents.retrieval import Chunk, RetrievalInput, RetrievalOutput
from ..contracts.enums import AgentId, ModelTier, RetrievalBackend
from ..llm.client import LLM, LLMUnavailable
from ..storage.repositories import ChunkRepository, MockTurnRepository
from .base import BaseAgent


def rrf_fuse(ranked_lists: List[List[Chunk]], k: int = RRF_K, top_k: int = 8) -> List[Chunk]:
    """Reciprocal Rank Fusion（Design §7b）：score = Σ 1/(k + rank)。"""
    scores: Dict[str, float] = {}
    by_id: Dict[str, Chunk] = {}
    for chunks in ranked_lists:
        for rank, ch in enumerate(chunks):
            scores[ch.chunk_id] = scores.get(ch.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            by_id.setdefault(ch.chunk_id, ch)
    fused = []
    for cid, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]:
        ch = by_id[cid].model_copy(update={"score": sc})
        fused.append(ch)
    return fused


class _Backend(Protocol):
    def search(self, payload: RetrievalInput) -> List[Chunk]: ...


class LocalBackend:
    """本地 RAG：FTS5 over SQLite（chunks + mock_turns），RRF 融合。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    def search(self, payload: RetrievalInput) -> List[Chunk]:
        try:
            chunk_repo = ChunkRepository(db_path=self._db_path)
            mock_repo = MockTurnRepository(conn=chunk_repo.conn)
            kw = chunk_repo.fts_match(payload.query, top_k=payload.top_k * 4)
            mock = mock_repo.fts_match(payload.query, top_k=payload.top_k * 4)
        except Exception:
            # 向量/FTS 全失败 → 由调用方降级（Design §2c-C / §8c）。
            return []
        return rrf_fuse([kw, mock], top_k=payload.top_k)


class CloudBackend:
    """云端知识库（面经 + 技术博客）。MVP 留接口，不接真实服务（Non-Goal / G1）。"""

    def search(self, payload: RetrievalInput) -> List[Chunk]:
        # 未来：hybrid_sort + cross_encoder_rerank(top_k*4 -> top_k)。
        # MVP：返回空 + 标注，保持接口前向兼容。
        return []


class RetrievalAgent(BaseAgent):
    agent_id = AgentId.RETRIEVAL

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self._backends: Dict[RetrievalBackend, _Backend] = {
            RetrievalBackend.LOCAL: LocalBackend(db_path=db_path),
            RetrievalBackend.CLOUD: CloudBackend(),
        }

    def _maybe_rewrite(self, query: str) -> str:
        """长(>120)或模糊 query → Haiku 改写一次（Design §3.6）。"""
        if len(query) <= 120 or not LLM.available or self.skill is None:
            return query
        try:
            result = LLM.complete(
                prompt=f"改写检索 query：{query}",
                model_tier=ModelTier.HAIKU,
                system=self.skill.spec.system_prompt,
                max_tokens=64,
            )
            return result.text.strip() or query
        except LLMUnavailable:
            return query

    def run(self, payload: RetrievalInput) -> RetrievalOutput:
        query = self._maybe_rewrite(payload.query)
        effective = payload.model_copy(update={"query": query})
        backend = self._backends[payload.backend]
        chunks = backend.search(effective)
        return RetrievalOutput(chunks=chunks)
