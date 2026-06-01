"""Phase 1 orchestrator (Plan §6).

Load all source docs → attach blocks → run each chunker → write
`reports/chunks/{chunker}/{doc_id}.jsonl` and `reports/stats.{json,md}`
(intrinsic/structural metrics per chunker × source).

Run:
    python -m scripts.run_chunking --chunkers all
    python -m scripts.run_chunking --chunkers fixed,structural,semantic
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Dict, List

from . import stats as stats_mod
from .chunkers import fixed, llamaindex_chunker, semantic, structural
from .lib.block_split import attach_blocks
from .lib.loaders import BENCHMARK_ROOT, load_all
from .lib.schema import Chunk, Document

REPORTS = BENCHMARK_ROOT / "reports"
CHUNKS_DIR = REPORTS / "chunks"

CHUNKERS: Dict[str, Callable[[Document], List[Chunk]]] = {
    fixed.NAME: fixed.chunk,
    structural.NAME: structural.chunk,
    semantic.NAME: semantic.chunk,
    llamaindex_chunker.NAME: llamaindex_chunker.chunk,
}


def _write_chunks(chunker_name: str, chunks_by_doc: Dict[str, List[Chunk]]) -> None:
    out_dir = CHUNKS_DIR / chunker_name
    out_dir.mkdir(parents=True, exist_ok=True)
    for doc_id, chunks in chunks_by_doc.items():
        path = out_dir / f"{doc_id}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for c in chunks:
                fh.write(c.to_json() + "\n")


def run(chunker_names: List[str]) -> Dict:
    docs = [attach_blocks(d) for d in load_all()]
    sources = sorted({d.source_type for d in docs})
    summary: Dict[str, Dict] = {}
    stats_by_key: Dict[str, Dict] = {}

    for name in chunker_names:
        fn = CHUNKERS[name]
        all_chunks: List[Chunk] = []
        chunks_by_doc: Dict[str, List[Chunk]] = {}
        skipped = False
        error = None
        for d in docs:
            try:
                cs = fn(d)
            except ImportError as exc:
                skipped, error = True, str(exc)
                break
            chunks_by_doc[d.doc_id] = cs
            all_chunks.extend(cs)

        if skipped:
            summary[name] = {"skipped": True, "reason": error}
            print(f"[SKIP] {name}: {error}")
            continue

        _write_chunks(name, chunks_by_doc)
        # overall + per-source intrinsic stats
        stats_by_key[f"{name}/ALL"] = stats_mod.compute(docs, all_chunks)
        for src in sources:
            sdocs = [d for d in docs if d.source_type == src]
            schunks = [c for c in all_chunks if c.source_type == src]
            stats_by_key[f"{name}/{src}"] = stats_mod.compute(sdocs, schunks)
        summary[name] = {
            "skipped": False,
            "n_docs": len(docs),
            "n_chunks": len(all_chunks),
        }
        print(f"[OK]   {name}: {len(all_chunks)} chunks over {len(docs)} docs")

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "stats.json").write_text(
        json.dumps({"summary": summary, "stats": stats_by_key}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = ["# Chunking Benchmark — Intrinsic Stats (Phase 1)\n"]
    md.append(stats_mod.format_markdown(stats_by_key))
    md.append("\n\n_Note: noise_contamination / technical_retention require Phase 3 block labels._\n")
    (REPORTS / "stats.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {REPORTS/'stats.json'} and {REPORTS/'stats.md'}")
    return {"summary": summary, "stats": stats_by_key}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--chunkers",
        default="all",
        help="comma-separated subset of: " + ", ".join(CHUNKERS) + " (or 'all')",
    )
    args = ap.parse_args()
    if args.chunkers.strip() == "all":
        names = list(CHUNKERS)
    else:
        names = [n.strip() for n in args.chunkers.split(",") if n.strip()]
        unknown = [n for n in names if n not in CHUNKERS]
        if unknown:
            ap.error(f"unknown chunkers: {unknown}; available: {list(CHUNKERS)}")
    run(names)


if __name__ == "__main__":
    main()
