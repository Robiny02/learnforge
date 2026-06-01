"""Reserved embedding interface for the benchmark (Plan §5.B hybrid path).

You provide a key + pick a provider via env vars; nothing else needs editing.
Reuses learnforge's provider classes (OpenAI text-embedding-3-* / Voyage voyage-3),
adds a disk cache so re-running the eval doesn't re-bill the API, and degrades to
"unavailable" (→ BM25-only retrieval) when no key/SDK is present.

Configure (one of):
    export LF_EMBEDDING_PROVIDER=openai
    export OPENAI_API_KEY=sk-...
    # optional: export LF_EMBEDDING_MODEL=text-embedding-3-large
    # optional: export LF_EMBEDDING_DIM=1024
or:
    export LF_EMBEDDING_PROVIDER=voyage
    export VOYAGE_API_KEY=pa-...

The relevant SDK must be installed in the venv:
    pip install openai          # for provider=openai
    pip install voyageai        # for provider=voyage

Verify your key end-to-end:
    python -m scripts.lib.embeddings --selftest
"""

from __future__ import annotations

import hashlib
import os
import pickle
import sys
from pathlib import Path
from typing import List, Optional

_BENCH_ROOT = Path(__file__).resolve().parents[2]
_CACHE_DIR = _BENCH_ROOT / "reports" / ".cache"

PROVIDER = os.getenv("LF_EMBEDDING_PROVIDER", "none").strip().lower()
MODEL = os.getenv("LF_EMBEDDING_MODEL") or None
DIM = int(os.getenv("LF_EMBEDDING_DIM", "1024"))
_BATCH = int(os.getenv("LF_EMBEDDING_BATCH", "128"))
# Embed only a safe prefix: keeps oversized nodes (e.g. a large LlamaIndex
# markdown section) under the model's ~8191-token input limit.
_MAX_EMBED_CHARS = int(os.getenv("LF_EMBEDDING_MAX_CHARS", "6000"))


def _learnforge_on_path() -> None:
    repo = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "learnforge")
    )
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _build_raw(provider: str, model: Optional[str], dim: int):
    """Instantiate a learnforge provider with explicit config.

    We import the provider classes (not the learnforge singleton) so the benchmark
    controls provider/model/dim independently.
    """
    if provider not in ("openai", "voyage"):
        return None
    try:
        _learnforge_on_path()
        from learnforge.llm.embeddings import (  # type: ignore
            OpenAIEmbeddingProvider,
            VoyageEmbeddingProvider,
        )

        if provider == "openai":
            return OpenAIEmbeddingProvider(model=model, dim=dim)
        return VoyageEmbeddingProvider(model=model, dim=dim)
    except Exception:
        return None


class CachingEmbedder:
    """Wraps a raw provider with a content-addressed disk cache.

    Defaults come from env (LF_EMBEDDING_*), but provider/model/dim can be passed
    explicitly so several models can be compared in one process.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        dim: Optional[int] = None,
    ) -> None:
        provider = (provider or PROVIDER).strip().lower()
        model = model if model is not None else MODEL
        dim = dim if dim is not None else DIM
        self._raw = _build_raw(provider, model, dim)
        self.available = bool(getattr(self._raw, "available", False))
        self.provider = provider if self.available else "none"
        self.model = getattr(self._raw, "model", model)
        self.dim = getattr(self._raw, "dim", dim)
        self._cache: dict = {}
        self._cache_path: Optional[Path] = None
        if self.available:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tag = f"{self.provider}_{self.model}_{self.dim}".replace("/", "-")
            self._cache_path = _CACHE_DIR / f"emb_{tag}.pkl"
            if self._cache_path.exists():
                try:
                    self._cache = pickle.loads(self._cache_path.read_bytes())
                except Exception:
                    self._cache = {}

    def _key(self, text: str) -> str:
        h = hashlib.sha1()
        h.update(f"{self.provider}|{self.model}|{self.dim}|".encode("utf-8"))
        h.update(text.encode("utf-8"))
        return h.hexdigest()

    def _persist(self) -> None:
        if self._cache_path is not None:
            try:
                self._cache_path.write_bytes(pickle.dumps(self._cache))
            except Exception:
                pass

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self.available or not texts:
            return []
        keys = [self._key(t) for t in texts]
        missing_idx = [i for i, k in enumerate(keys) if k not in self._cache]
        # Batch to stay under provider per-request input limits (OpenAI: 2048).
        for start in range(0, len(missing_idx), _BATCH):
            batch_idx = missing_idx[start : start + _BATCH]
            fresh = self._raw.embed(
                [texts[i][:_MAX_EMBED_CHARS] for i in batch_idx]
            )  # type: ignore
            if not fresh or len(fresh) != len(batch_idx):
                return []  # provider failed → caller degrades to BM25
            for j, i in enumerate(batch_idx):
                self._cache[keys[i]] = fresh[j]
            self._persist()
        return [self._cache[k] for k in keys]


# Singleton used by the retriever (env-configured).
EMBEDDER = CachingEmbedder()


def get_embedder() -> CachingEmbedder:
    return EMBEDDER


def make_embedder(
    model: Optional[str] = None, dim: Optional[int] = None, provider: str = "openai"
) -> CachingEmbedder:
    """Build a fresh embedder for an explicit model/dim (used by compare_embeddings)."""
    return CachingEmbedder(provider=provider, model=model, dim=dim)


def _selftest() -> int:
    e = get_embedder()
    print(f"provider={e.provider} model={e.model} dim={e.dim} available={e.available}")
    if not e.available:
        print(
            "NOT available. Set LF_EMBEDDING_PROVIDER + the matching *_API_KEY and "
            "install the SDK (pip install openai | voyageai). Retrieval will use BM25 only."
        )
        return 1
    vecs = e.embed(["redis 为什么快", "MySQL MVCC 版本链"])
    if not vecs:
        print("provider returned no vectors — check key/SDK/network.")
        return 2
    print(f"OK: embedded {len(vecs)} texts, vector dim={len(vecs[0])} (cached at {e._cache_path})")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else _selftest())
