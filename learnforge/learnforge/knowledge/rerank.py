"""Reranker —— 召回后重排扩展位（默认 NoOp，可选 LLM rerank）。

RetrievalAgent 在 `rerank=True` 且 reranker 可用时，对融合后的候选做一次重排。
默认 `NoOpReranker` 不改变顺序；`LLMReranker` 用 Haiku 对候选打分（无 key 时降级 NoOp）。
未来可接 cross-encoder（如 bge-reranker）实现更强排序，只需新增一个实现。
"""

from __future__ import annotations

import json
from typing import List, Protocol

from ..contracts.agents.retrieval import Chunk
from ..contracts.enums import ModelTier
from ..llm.client import LLM, LLMUnavailable


class Reranker(Protocol):
    available: bool

    def rerank(self, query: str, chunks: List[Chunk], top_k: int) -> List[Chunk]:
        ...


class NoOpReranker:
    """默认重排器：原样返回前 top_k（不依赖任何外部能力）。"""

    available = True

    def rerank(self, query: str, chunks: List[Chunk], top_k: int) -> List[Chunk]:
        return chunks[:top_k]


class LLMReranker:
    """用 Haiku 对候选相关性打分后重排；无 LLM 时退化为 NoOp。"""

    @property
    def available(self) -> bool:
        return bool(getattr(LLM, "available", False))

    def rerank(self, query: str, chunks: List[Chunk], top_k: int) -> List[Chunk]:
        if not self.available or not chunks:
            return chunks[:top_k]
        listing = "\n".join(f"[{i}] {c.text[:200]}" for i, c in enumerate(chunks))
        prompt = (
            f"查询：{query}\n候选片段：\n{listing}\n\n"
            f"按与查询的相关性从高到低排序，只输出 JSON 数组形式的候选编号，"
            f"例如 [2,0,1]。最多返回前 {top_k} 个编号。"
        )
        try:
            result = LLM.complete(prompt, model_tier=ModelTier.HAIKU, max_tokens=128)
            order = json.loads(_extract_array(result.text))
        except (LLMUnavailable, Exception):  # noqa: BLE001 - 失败则降级
            return chunks[:top_k]
        seen = set()
        reranked: List[Chunk] = []
        for idx in order:
            if isinstance(idx, int) and 0 <= idx < len(chunks) and idx not in seen:
                seen.add(idx)
                reranked.append(chunks[idx])
        # 补齐未被模型提及的候选，保证不丢结果。
        for i, ch in enumerate(chunks):
            if i not in seen:
                reranked.append(ch)
        return reranked[:top_k]


def _extract_array(text: str) -> str:
    import re

    m = re.search(r"\[[^\]]*\]", text, re.DOTALL)
    return m.group(0) if m else "[]"


# 默认重排器单例（统一检索入口使用）。
DEFAULT_RERANKER: Reranker = NoOpReranker()
