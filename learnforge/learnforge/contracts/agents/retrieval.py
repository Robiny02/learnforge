"""RetrievalAgent input/output（Design §3.6 / §7）。

共享混合检索：sqlite-vec 向量 + FTS5 关键词，RRF(k=60) 融合，不 rerank。
后端经 `backend` 参数抽象（云端留接口，Design §10a G1）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ..enums import RetrievalBackend, SourceType


class RetrievalFilters(BaseModel):
    topic: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source_type: Optional[SourceType] = None


class Chunk(BaseModel):
    """检索召回片段（Design §3.6 输出 / §7c 切片 metadata）。"""

    chunk_id: str
    text: str
    score: float = Field(default=0.0, description="融合分（RRF），降序返回。")
    source_type: SourceType = SourceType.ATOM
    metadata: Dict = Field(default_factory=dict)


class RetrievalInput(BaseModel):
    query: str
    filters: Optional[RetrievalFilters] = None
    top_k: int = Field(default=8, le=20)
    backend: RetrievalBackend = RetrievalBackend.LOCAL


class RetrievalOutput(BaseModel):
    chunks: List[Chunk] = Field(default_factory=list)
