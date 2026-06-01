"""Phase 2 retrieval evaluation (Plan §5.B, §6 Phase 2).

For each chunker: build a hybrid index over its leaf chunks, run every eval query,
and score retrieval quality. A chunk is *relevant* to a query if it comes from the
query's gold doc AND contains at least one gold keyword (normalised substring).

Metrics (mean over queries, overall and per source):
- top_doc_correct : top-1 chunk is from the gold doc
- hit@k           : any relevant chunk in top-k
- precision@k     : relevant fraction of top-k
- mrr             : 1 / rank of first relevant chunk
- kw_recall@k     : distinct gold keywords covered by top-k gold-doc chunks
- kw_recall@k_parent : same, but a matched child also contributes its parent
                       chunk's text (parent backfill — Plan: 父子分开评测)

Each row also carries `degraded` (True == BM25/FTS only; no vector path, because
no embedding provider/key is configured).

Run:
    python -m scripts.eval_retrieval [--k 5] [--chunkers all]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Dict, List, Optional

from .lib.loaders import BENCHMARK_ROOT
from .lib.retriever import HybridRetriever
from .lib.schema import Chunk, chunks_from_jsonl

REPORTS = BENCHMARK_ROOT / "reports"
CHUNKS_DIR = REPORTS / "chunks"
EVAL_QUERIES = BENCHMARK_ROOT / "data" / "eval_queries" / "eval_queries_seed.jsonl"

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub("", s.lower())


def load_queries() -> List[Dict]:
    rows = []
    with open(EVAL_QUERIES, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_chunker_chunks(chunker: str) -> List[Chunk]:
    out: List[Chunk] = []
    d = CHUNKS_DIR / chunker
    if not d.exists():
        return out
    for path in sorted(d.glob("*.jsonl")):
        out.extend(chunks_from_jsonl(str(path)))
    return out


def _keywords_in(text_norm: str, gold_norm: List[str]) -> set:
    return {g for g in gold_norm if g and g in text_norm}


def eval_chunker(
    chunker: str,
    queries: List[Dict],
    k: int,
    embedder: Optional[object] = None,
    use_vector: bool = True,
) -> Dict:
    all_chunks = load_chunker_chunks(chunker)
    leaves = [c for c in all_chunks if not c.is_parent]
    parent_text = {c.chunk_id: c.text for c in all_chunks if c.is_parent}
    if not leaves:
        return {"error": "no chunks", "skipped": True}

    retr = HybridRetriever(
        [c.text for c in leaves], use_vector=use_vector, embedder=embedder
    )
    degraded = retr.degraded

    per_query = []
    for q in queries:
        gold_doc = q["doc_id"]
        gold_norm = [_norm(g) for g in q.get("gold_keywords", [])]
        hits, _ = retr.search(q["query"], top_k=max(k, 10))
        ranked = [leaves[i] for i, _ in hits]
        topk = ranked[:k]

        def is_rel(c: Chunk) -> bool:
            return c.doc_id == gold_doc and bool(
                _keywords_in(_norm(c.text), gold_norm)
            )

        rel_topk = [c for c in topk if is_rel(c)]
        first_rel_rank = next((r for r, c in enumerate(ranked, 1) if is_rel(c)), 0)

        covered = set()
        covered_parent = set()
        for c in topk:
            if c.doc_id != gold_doc:
                continue
            covered |= _keywords_in(_norm(c.text), gold_norm)
            ptext = parent_text.get(c.parent_id or "", "")
            merged = _norm(c.text + " " + ptext)
            covered_parent |= _keywords_in(merged, gold_norm)

        denom = len(gold_norm) or 1
        per_query.append(
            {
                "query_id": q["query_id"],
                "source": q["source"],
                "difficulty": q.get("difficulty"),
                "top_doc_correct": 1.0 if ranked and ranked[0].doc_id == gold_doc else 0.0,
                "hit@k": 1.0 if rel_topk else 0.0,
                "precision@k": round(len(rel_topk) / k, 3),
                "mrr": round(1.0 / first_rel_rank, 3) if first_rel_rank else 0.0,
                "kw_recall@k": round(len(covered) / denom, 3),
                "kw_recall@k_parent": round(len(covered_parent) / denom, 3),
            }
        )
    return {"skipped": False, "degraded": degraded, "per_query": per_query}


_METRICS = (
    "top_doc_correct",
    "hit@k",
    "precision@k",
    "mrr",
    "kw_recall@k",
    "kw_recall@k_parent",
)


def _aggregate(rows: List[Dict]) -> Dict:
    if not rows:
        return {m: None for m in _METRICS}
    return {m: round(statistics.mean(r[m] for r in rows), 3) for m in _METRICS}


def build_report(results: Dict[str, Dict], k: int) -> Dict:
    table: Dict[str, Dict] = {}
    for chunker, res in results.items():
        if res.get("skipped"):
            continue
        rows = res["per_query"]
        sources = sorted({r["source"] for r in rows})
        table[f"{chunker}/ALL"] = {**_aggregate(rows), "degraded": res["degraded"]}
        for src in sources:
            srows = [r for r in rows if r["source"] == src]
            table[f"{chunker}/{src}"] = {**_aggregate(srows), "degraded": res["degraded"]}
    return table


def format_markdown(table: Dict[str, Dict], k: int, degraded_any: bool) -> str:
    cols = ["top_doc_correct", "hit@k", "precision@k", "mrr", "kw_recall@k", "kw_recall@k_parent"]
    head = "| key | " + " | ".join(c.replace("@k", f"@{k}") for c in cols) + " | degraded |"
    sep = "|" + "---|" * (len(cols) + 2)
    rows = [head, sep]
    for key in sorted(table):
        m = table[key]
        cells = ["–" if m.get(c) is None else f"{m[c]:.3f}" for c in cols]
        rows.append(f"| {key} | " + " | ".join(cells) + f" | {m.get('degraded')} |")
    mode = (
        "BM25-only on every row (no embedding key configured)"
        if degraded_any and all(m.get("degraded") for m in table.values())
        else "**hybrid (BM25 + vector RRF)** where `degraded=False`; BM25-only where `degraded=True`"
    )
    note = (
        f"\n\n_Retrieval mode: {mode}. Relevant = gold-doc chunk containing a gold "
        f"keyword. kw_recall@{k}_parent backfills the matched child's parent chunk._\n"
    )
    return "# Chunking Benchmark — Retrieval Eval (Phase 2)\n\n" + "\n".join(rows) + note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--chunkers", default="all")
    args = ap.parse_args()

    available = sorted(p.name for p in CHUNKS_DIR.iterdir() if p.is_dir()) if CHUNKS_DIR.exists() else []
    if not available:
        ap.error(f"no chunks found under {CHUNKS_DIR}; run scripts.run_chunking first")
    names = available if args.chunkers == "all" else [n.strip() for n in args.chunkers.split(",")]

    queries = load_queries()
    results = {name: eval_chunker(name, queries, args.k) for name in names}
    table = build_report(results, args.k)
    degraded_any = any(
        r.get("degraded") for r in results.values() if not r.get("skipped")
    )

    (REPORTS / "retrieval_eval.json").write_text(
        json.dumps({"k": args.k, "results": results, "table": table}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (REPORTS / "retrieval_eval.md").write_text(
        format_markdown(table, args.k, degraded_any), encoding="utf-8"
    )
    print(f"Evaluated {len(queries)} queries over chunkers: {names}")
    print(f"Wrote {REPORTS/'retrieval_eval.json'} and {REPORTS/'retrieval_eval.md'}")
    print("\n" + format_markdown(table, args.k, degraded_any))


if __name__ == "__main__":
    main()
