"""Embedding provider —— 可插拔的文本向量化（外部 API，离线优雅降级）。

设计要点（对齐 llm/client.py 的降级哲学）：
- provider 经 env 选择（`LF_EMBEDDING_PROVIDER` ∈ {none, openai, voyage}）。
- 无 key / 无 SDK / provider=none 时 `available=False`，向量检索分路自动跳过，
  统一检索降级到 FTS/关键词——"链路永远通"。
- 维度统一为 `config.EMBEDDING_DIM`（默认 1024），与 vec0 schema 对齐；
  OpenAI text-embedding-3-* 通过 `dimensions` 参数截断到该维度。
"""

from __future__ import annotations

import os
from typing import List, Optional, Protocol

from ..config import EMBEDDING_DIM, EMBEDDING_MODEL, EMBEDDING_PROVIDER, OPENAI_BASE_URL


class EmbeddingProvider(Protocol):
    available: bool
    dim: int

    def embed(self, texts: List[str]) -> List[List[float]]:
        """把一批文本编码为定长向量；不可用时应返回空列表。"""
        ...


class NullEmbeddingProvider:
    """离线占位：永不可用，向量检索分路据此跳过。"""

    available = False
    dim = EMBEDDING_DIM

    def embed(self, texts: List[str]) -> List[List[float]]:
        return []


class OpenAIEmbeddingProvider:
    """OpenAI text-embedding-3-*（支持 dimensions 截断到 EMBEDDING_DIM）。"""

    def __init__(self, model: Optional[str] = None, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim
        self.model = model or EMBEDDING_MODEL or "text-embedding-3-small"
        self._client = None
        self.available = False
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            try:
                import openai  # type: ignore

                # base_url 可选：支持 OpenAI 兼容代理（如 OpenRouter）跑 embeddings。
                kwargs = {"api_key": api_key}
                if OPENAI_BASE_URL:
                    kwargs["base_url"] = OPENAI_BASE_URL
                self._client = openai.OpenAI(**kwargs)
                self.available = True
            except Exception:
                self.available = False

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self.available or self._client is None or not texts:
            return []
        try:
            resp = self._client.embeddings.create(
                model=self.model, input=texts, dimensions=self.dim
            )
            return [d.embedding for d in resp.data]
        except Exception:
            return []


class VoyageEmbeddingProvider:
    """Voyage AI embeddings（voyage-3 默认 1024 维）。"""

    def __init__(self, model: Optional[str] = None, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim
        self.model = model or EMBEDDING_MODEL or "voyage-3"
        self._client = None
        self.available = False
        api_key = os.getenv("VOYAGE_API_KEY")
        if api_key:
            try:
                import voyageai  # type: ignore

                self._client = voyageai.Client(api_key=api_key)
                self.available = True
            except Exception:
                self.available = False

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self.available or self._client is None or not texts:
            return []
        try:
            result = self._client.embed(texts, model=self.model)
            return list(result.embeddings)
        except Exception:
            return []


def _build_provider() -> EmbeddingProvider:
    if EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbeddingProvider()
    if EMBEDDING_PROVIDER == "voyage":
        return VoyageEmbeddingProvider()
    return NullEmbeddingProvider()


# 全局单例。
EMBEDDINGS: EmbeddingProvider = _build_provider()


def embed_query(query: str) -> Optional[List[float]]:
    """编码单条 query；不可用或失败时返回 None（调用方据此降级）。"""
    if not getattr(EMBEDDINGS, "available", False):
        return None
    vecs = EMBEDDINGS.embed([query])
    return vecs[0] if vecs else None
