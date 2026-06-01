"""Hybrid retriever for the benchmark (Plan §5.B, §6 Phase 2).

- BM25 (pure Python, deterministic) with a CJK-aware tokenizer (ASCII words +
  Chinese unigrams & bigrams — no external segmenter needed).
- Optional dense vector path via learnforge's `EMBEDDINGS` singleton; when no
  provider/key is configured it is unavailable and we degrade to pure BM25.
- Fusion via Reciprocal Rank Fusion (RRF), mirroring learnforge's hybrid design.

`search()` returns ranked (index, score) and a `degraded` flag (True == FTS/BM25
only, no vector path). Zero third-party deps required for the default path.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Optional, Sequence, Tuple

_ASCII = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[一-鿿]+")
_RRF_K = 60


def tokenize(text: str) -> List[str]:
    text = text.lower()
    toks = _ASCII.findall(text)
    for run in _CJK_RUN.findall(text):
        toks.extend(list(run))  # unigrams
        toks.extend(run[i : i + 2] for i in range(len(run) - 1))  # bigrams
    return toks


class BM25:
    def __init__(self, docs_tokens: Sequence[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = list(docs_tokens)
        self.N = len(self.docs)
        self.doc_len = [len(d) for d in self.docs]
        self.avgdl = (sum(self.doc_len) / self.N) if self.N else 0.0
        self.freqs = [Counter(d) for d in self.docs]
        df: Counter = Counter()
        for d in self.docs:
            for term in set(d):
                df[term] += 1
        self.idf = {
            t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()
        }

    def scores(self, query_tokens: List[str]) -> List[float]:
        out = [0.0] * self.N
        q = [t for t in query_tokens if t in self.idf]
        for i in range(self.N):
            f = self.freqs[i]
            dl = self.doc_len[i] or 1
            s = 0.0
            for t in q:
                ft = f.get(t, 0)
                if not ft:
                    continue
                idf = self.idf[t]
                denom = ft + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                s += idf * (ft * (self.k1 + 1)) / denom
            out[i] = s
        return out


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return num / (na * nb)


def _rank_map(scores: List[float]) -> dict:
    """index -> 1-based rank (1 = highest score)."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return {idx: r + 1 for r, idx in enumerate(order)}


def _load_embedder() -> Optional[object]:
    """Benchmark embedding interface (see lib/embeddings.py): provider+key via env,
    disk-cached, degrades to None (BM25-only) when unavailable."""
    try:
        from .embeddings import get_embedder

        emb = get_embedder()
        return emb if getattr(emb, "available", False) else None
    except Exception:
        return None


class HybridRetriever:
    """Index a list of texts; query with BM25 (+ optional vector) via RRF."""

    def __init__(
        self, texts: Sequence[str], use_vector: bool = True, embedder: Optional[object] = None
    ):
        self.texts = list(texts)
        self.bm25 = BM25([tokenize(t) for t in self.texts])
        if not use_vector:
            self.embedder = None
        elif embedder is not None:
            self.embedder = embedder if getattr(embedder, "available", False) else None
        else:
            self.embedder = _load_embedder()
        self.degraded = self.embedder is None
        self.doc_vecs: Optional[List[List[float]]] = None
        if self.embedder is not None and self.texts:
            try:
                self.doc_vecs = self.embedder.embed(list(self.texts))
                if not self.doc_vecs:
                    self.embedder, self.degraded = None, True
            except Exception:
                self.embedder, self.degraded = None, True

    def search(self, query: str, top_k: int = 10) -> Tuple[List[Tuple[int, float]], bool]:
        bm = self.bm25.scores(tokenize(query))
        if self.embedder is None or self.doc_vecs is None:
            ranked = sorted(range(len(bm)), key=lambda i: bm[i], reverse=True)
            return [(i, bm[i]) for i in ranked[:top_k]], True
        qv = self.embedder.embed([query])[0]
        vec = [_cosine(qv, dv) for dv in self.doc_vecs]
        rb, rv = _rank_map(bm), _rank_map(vec)
        fused = {
            i: 1.0 / (_RRF_K + rb[i]) + 1.0 / (_RRF_K + rv[i]) for i in range(len(bm))
        }
        ranked = sorted(fused, key=lambda i: fused[i], reverse=True)
        return [(i, fused[i]) for i in ranked[:top_k]], False
