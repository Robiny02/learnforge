"""Compare retrieval quality across embedding models (Plan §5.B sensitivity).

Runs the same eval over every chunker for a list of embedding configs (plus a
BM25-only baseline) so the chunker ranking can be checked for robustness to the
embedding choice. Results are content-cached per (model, dim), so re-runs are cheap.

Run (provider/key via env — see .env.example):
    python -m scripts.compare_embeddings --k 5
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .eval_retrieval import _METRICS, eval_chunker, load_queries
from .lib.embeddings import make_embedder
from .lib.loaders import BENCHMARK_ROOT

REPORTS = BENCHMARK_ROOT / "reports"
CHUNKS_DIR = REPORTS / "chunks"

# (label, model, dim) — model=None means BM25-only baseline.
CONFIGS: List[Tuple[str, Optional[str], Optional[int]]] = [
    ("bm25", None, None),
    ("3-small@1024", "text-embedding-3-small", 1024),
    ("3-large@1024", "text-embedding-3-large", 1024),
    ("3-large@3072", "text-embedding-3-large", 3072),
    # NB: text-embedding-ada-002 is intentionally omitted — it does not accept the
    # `dimensions` parameter our provider sends, so it errors and silently degrades
    # to BM25. Use the text-embedding-3-* family for dimension control.
]


def _agg(rows: List[Dict], metric: str) -> Optional[float]:
    return round(statistics.mean(r[metric] for r in rows), 3) if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--metric", default="mrr", choices=list(_METRICS))
    ap.add_argument("--metric2", default="kw_recall@k_parent", choices=list(_METRICS))
    args = ap.parse_args()

    chunkers = sorted(p.name for p in CHUNKS_DIR.iterdir() if p.is_dir())
    queries = load_queries()

    # grid[config_label][chunker] = {metric: value, ...}
    grid: Dict[str, Dict[str, Dict]] = {}
    availability: Dict[str, bool] = {}
    for label, model, dim in CONFIGS:
        if model is None:
            emb, use_vector = None, False
            availability[label] = True
        else:
            emb = make_embedder(model=model, dim=dim)
            use_vector = emb.available
            availability[label] = emb.available
            if not emb.available:
                print(f"[skip] {label}: embedder unavailable (no key/SDK)")
        grid[label] = {}
        for ch in chunkers:
            res = eval_chunker(ch, queries, args.k, embedder=emb, use_vector=use_vector)
            if res.get("skipped"):
                continue
            rows = res["per_query"]
            grid[label][ch] = {m: _agg(rows, m) for m in _METRICS}
        print(f"[ok]   {label}: evaluated {len(grid[label])} chunkers")

    def render(metric: str) -> str:
        labels = [c[0] for c in CONFIGS if availability.get(c[0])]
        head = "| chunker | " + " | ".join(labels) + " |"
        sep = "|" + "---|" * (len(labels) + 1)
        lines = [f"### {metric.replace('@k', f'@{args.k}')}", "", head, sep]
        for ch in chunkers:
            cells = []
            for lab in labels:
                v = grid.get(lab, {}).get(ch, {}).get(metric)
                cells.append("–" if v is None else f"{v:.3f}")
            lines.append(f"| {ch} | " + " | ".join(cells) + " |")
        return "\n".join(lines)

    md = ["# Embedding Model Comparison\n",
          f"_k={args.k}; hybrid = BM25 + vector RRF. Each column is a different "
          f"embedding model/dim; `bm25` is the no-vector baseline._\n",
          render(args.metric), "", render(args.metric2)]
    out_md = REPORTS / "embedding_comparison.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    (REPORTS / "embedding_comparison.json").write_text(
        json.dumps({"k": args.k, "configs": [c[0] for c in CONFIGS], "grid": grid},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {out_md}\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
