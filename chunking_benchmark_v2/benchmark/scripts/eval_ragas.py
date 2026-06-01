"""RAGAS retrieval evaluation with an LLM judge (Plan §5.B, stronger metrics).

For each chunker we run our hybrid retriever, take the top-k contexts it would
feed an LLM, and score them with RAGAS:
- context_precision (LLMContextPrecisionWithReference): are the relevant contexts
  ranked near the top?
- context_recall (LLMContextRecall): is the reference answer's content actually
  present in the retrieved contexts?

These are LLM-judged (not keyword-overlap), so they're more convincing than the
substring metrics in eval_retrieval. Reference answers are synthesised from each
query's gold_section + gold_keywords.

Judge + embeddings go through the same OpenRouter key (env). Run:
    export OPENAI_API_KEY=...  OPENAI_BASE_URL=https://openrouter.ai/api/v1
    python -m scripts.eval_ragas --k 5 [--chunkers all] [--judge openai/gpt-4o-mini]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import warnings
from pathlib import Path
from typing import Dict, List

warnings.filterwarnings("ignore")

from .eval_retrieval import load_chunker_chunks, load_queries
from .lib.embeddings import get_embedder
from .lib.loaders import BENCHMARK_ROOT
from .lib.retriever import HybridRetriever

REPORTS = BENCHMARK_ROOT / "reports"
CHUNKS_DIR = REPORTS / "chunks"


def _reference(q: Dict) -> str:
    kws = "；".join(q.get("gold_keywords", []))
    sec = q.get("gold_section", "")
    return f"关于「{sec}」的关键点：{kws}。"


def _build_judge(model: str):
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    key = os.getenv("OPENAI_API_KEY")
    base = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    llm = ChatOpenAI(model=model, api_key=key, base_url=base, temperature=0)
    emb = OpenAIEmbeddings(
        model=os.getenv("LF_EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=key,
        base_url=base,
        dimensions=int(os.getenv("LF_EMBEDDING_DIM", "1024")),
    )
    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(emb)


def _retrieve_contexts(chunker: str, queries: List[Dict], k: int) -> Dict[str, List[str]]:
    all_chunks = load_chunker_chunks(chunker)
    leaves = [c for c in all_chunks if not c.is_parent]
    parents = {c.chunk_id: c.text for c in all_chunks if c.is_parent}
    retr = HybridRetriever([c.text for c in leaves], use_vector=True, embedder=get_embedder())
    out = {}
    for q in queries:
        hits, _ = retr.search(q["query"], top_k=k)
        ctxs = []
        for i, _ in hits:
            c = leaves[i]
            # Backfill parent so a tiny child carries its section context.
            ptext = parents.get(c.parent_id or "", "")
            ctxs.append((c.text + ("\n\n" + ptext if ptext else "")).strip())
        out[q["query_id"]] = ctxs
    return out


def eval_chunker_ragas(chunker: str, queries: List[Dict], k: int, judge_llm, judge_emb) -> Dict:
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall
    from ragas.run_config import RunConfig

    ctx_by_q = _retrieve_contexts(chunker, queries, k)
    samples = [
        SingleTurnSample(
            user_input=q["query"],
            retrieved_contexts=ctx_by_q[q["query_id"]],
            reference=_reference(q),
        )
        for q in queries
    ]
    ds = EvaluationDataset(samples=samples)
    metrics = [LLMContextPrecisionWithReference(), LLMContextRecall()]
    result = evaluate(
        dataset=ds,
        metrics=metrics,
        llm=judge_llm,
        embeddings=judge_emb,
        run_config=RunConfig(max_workers=4, timeout=180),
        show_progress=False,
    )
    df = result.to_pandas()
    # column names: 'llm_context_precision_with_reference', 'context_recall'
    cols = {c: c for c in df.columns}
    prec_col = next((c for c in df.columns if "precision" in c), None)
    rec_col = next((c for c in df.columns if "recall" in c), None)
    per_query = []
    for _, row in df.iterrows():
        per_query.append(
            {
                "precision": None if prec_col is None else _num(row[prec_col]),
                "recall": None if rec_col is None else _num(row[rec_col]),
            }
        )
    return {"per_query": per_query, "sources": [q["source"] for q in queries]}


def _num(v):
    try:
        f = float(v)
        return None if f != f else round(f, 3)  # NaN guard
    except Exception:
        return None


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.mean(vals), 3) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--chunkers", default="all")
    ap.add_argument("--judge", default=os.getenv("RAGAS_JUDGE_MODEL", "openai/gpt-4o-mini"))
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        ap.error("OPENAI_API_KEY not set — RAGAS needs an LLM judge. See .env.example.")

    available = sorted(p.name for p in CHUNKS_DIR.iterdir() if p.is_dir())
    names = available if args.chunkers == "all" else [n.strip() for n in args.chunkers.split(",")]
    queries = load_queries()
    judge_llm, judge_emb = _build_judge(args.judge)

    table: Dict[str, Dict] = {}
    raw: Dict[str, Dict] = {}
    for name in names:
        res = eval_chunker_ragas(name, queries, args.k, judge_llm, judge_emb)
        pq, srcs = res["per_query"], res["sources"]
        raw[name] = res
        table[f"{name}/ALL"] = {
            "context_precision": _mean([p["precision"] for p in pq]),
            "context_recall": _mean([p["recall"] for p in pq]),
        }
        for src in sorted(set(srcs)):
            idx = [i for i, s in enumerate(srcs) if s == src]
            table[f"{name}/{src}"] = {
                "context_precision": _mean([pq[i]["precision"] for i in idx]),
                "context_recall": _mean([pq[i]["recall"] for i in idx]),
            }
        print(f"[ok] {name}: {table[f'{name}/ALL']}")

    lines = [
        "# Chunking Benchmark — RAGAS Retrieval Eval",
        "",
        f"_Judge: `{args.judge}`; k={args.k}; contexts = top-{args.k} retrieved (child + "
        "parent backfill). context_precision = relevant contexts ranked high; "
        "context_recall = reference answer covered by contexts._",
        "",
        "| key | context_precision | context_recall |",
        "|---|---|---|",
    ]
    for key in sorted(table):
        m = table[key]
        p = "–" if m["context_precision"] is None else f"{m['context_precision']:.3f}"
        r = "–" if m["context_recall"] is None else f"{m['context_recall']:.3f}"
        lines.append(f"| {key} | {p} | {r} |")
    md = "\n".join(lines)
    (REPORTS / "ragas_eval.md").write_text(md, encoding="utf-8")
    (REPORTS / "ragas_eval.json").write_text(
        json.dumps({"judge": args.judge, "k": args.k, "table": table}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {REPORTS/'ragas_eval.md'}\n\n{md}")


if __name__ == "__main__":
    main()
