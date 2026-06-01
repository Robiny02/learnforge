"""RemoteSharedSource —— 远程共享知识库（可插拔），scope=SHARED。

对接外部知识库服务（面经/博客/题库等大语料）。通过 env 配置：
- `LF_REMOTE_KB_URL`：检索端点（POST JSON）。未配置则 `available=False`，返回 []（离线降级）。
- `LF_REMOTE_KB_TOKEN`：可选 Bearer 鉴权。

请求体：{"query","method","top_k","filters"}；期望响应：{"chunks":[{chunk_id,text,score,...}]}。
仅用 stdlib urllib，避免引入额外依赖；任何异常都吞掉并返回 []（保持"链路永远通"）。
替代旧 CloudBackend。
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import List, Optional, Set

from ...contracts.agents.retrieval import Chunk, RetrievalFilters
from ...contracts.enums import KnowledgeScope, RetrievalMethod, SourceType


class RemoteSharedSource:
    name = "remote_shared"
    scope = KnowledgeScope.SHARED
    capabilities: Set[RetrievalMethod] = {
        RetrievalMethod.FULLTEXT,
        RetrievalMethod.VECTOR,
        RetrievalMethod.HYBRID,
    }

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None,
                 timeout_s: float = 1.5) -> None:
        self._url = url or os.getenv("LF_REMOTE_KB_URL")
        self._token = token or os.getenv("LF_REMOTE_KB_TOKEN")
        self._timeout_s = timeout_s
        self.available = bool(self._url)

    def search(
        self,
        query: str,
        method: RetrievalMethod,
        filters: Optional[RetrievalFilters] = None,
        top_k: int = 8,
        embedding: Optional[List[float]] = None,
    ) -> List[Chunk]:
        if not self.available or not self._url:
            return []
        body = json.dumps({
            "query": query,
            "method": method.value,
            "top_k": top_k,
            "filters": filters.model_dump(mode="json") if filters else None,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            req = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []
        return [self._to_chunk(item) for item in data.get("chunks", []) if item.get("text")]

    def _to_chunk(self, item: dict) -> Chunk:
        try:
            source_type = SourceType(item.get("source_type", "doc"))
        except ValueError:
            source_type = SourceType.DOC
        return Chunk(
            chunk_id=item.get("chunk_id", ""),
            text=item["text"],
            score=float(item.get("score", 0.0)),
            source_type=source_type,
            scope=KnowledgeScope.SHARED,
            source_name=self.name,
            metadata=item.get("metadata", {}),
        )
