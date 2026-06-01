"""RetrievalAgent input/output（Design §3.6 / §7，双层知识库统一检索）。

统一检索入口：从多知识源（本地用户库 / 共享知识库 / 远程共享库）召回，支持
关键词 / 全文 / 向量 / 混合 / metadata 过滤，跨源 RRF(k=60) 融合，可选 rerank。

选择维度：
- `method`：检索方式（RetrievalMethod，默认 HYBRID）。
- `scopes`：知识层级（KnowledgeScope，默认 [LOCAL, SHARED]）。
- `backend`：历史字段，保留向后兼容；不传 `scopes` 时按它推导。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ..enums import KnowledgeScope, RetrievalBackend, RetrievalMethod, RetrievalMode, SourceType


class RetrievalFilters(BaseModel):
    """metadata 过滤条件（各知识源按能力尽量应用，缺失字段视为不过滤）。"""

    topic: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source_type: Optional[SourceType] = None
    kb_scope: Optional[KnowledgeScope] = None
    difficulty: Optional[int] = None
    source_name: Optional[str] = None
    kind: Optional[str] = Field(
        default=None,
        description="按记忆类型过滤（daily memory 的 metadata.kind，如 qa/weak/mock/progress/decision）。",
    )


class Chunk(BaseModel):
    """检索召回片段（Design §3.6 输出 / §7c 切片 metadata）。"""

    chunk_id: str
    text: str
    score: float = Field(default=0.0, description="融合分（RRF），降序返回。")
    source_type: SourceType = SourceType.ATOM
    scope: KnowledgeScope = Field(
        default=KnowledgeScope.SHARED, description="片段所属知识层级（溯源）。"
    )
    source_name: Optional[str] = Field(
        default=None, description="产出该片段的知识源名（溯源）。"
    )
    metadata: Dict = Field(default_factory=dict)


class RetrievalInput(BaseModel):
    query: str
    filters: Optional[RetrievalFilters] = None
    top_k: int = Field(default=8, le=20)
    method: RetrievalMethod = RetrievalMethod.HYBRID
    mode: RetrievalMode = Field(
        default=RetrievalMode.WORKFLOW,
        description="检索运行模式：workflow 固定流水线；workflow_v2 受控改写；react 观察后重试。",
    )
    scopes: Optional[List[KnowledgeScope]] = Field(
        default=None,
        description="检索的知识层级；None 时按 backend 推导（默认 [LOCAL, SHARED]）。",
    )
    rerank: bool = Field(default=False, description="是否启用 rerank 重排（可用时）。")
    # 历史字段：保留向后兼容，仅在未显式给出 scopes 时用于推导。
    backend: RetrievalBackend = RetrievalBackend.LOCAL

    def effective_scopes(self) -> List[KnowledgeScope]:
        """解析最终检索层级：优先 scopes，否则由 backend 推导。"""
        if self.scopes:
            return self.scopes
        if self.backend == RetrievalBackend.CLOUD:
            return [KnowledgeScope.SHARED]
        return [KnowledgeScope.LOCAL, KnowledgeScope.SHARED]


class RetrievalOutput(BaseModel):
    chunks: List[Chunk] = Field(default_factory=list)
    method_used: RetrievalMethod = RetrievalMethod.HYBRID
    degraded: bool = Field(
        default=False, description="是否发生降级（如向量不可用退化到 FTS、远程源缺省）。"
    )
    metrics: Dict = Field(
        default_factory=dict,
        description="检索运行指标，如 react_steps/llm_calls/retrieval_calls/while_rounds。",
    )
    trace: List[Dict] = Field(
        default_factory=list,
        description="可选检索轨迹；workflow_v2/react 记录检索决策。",
    )
