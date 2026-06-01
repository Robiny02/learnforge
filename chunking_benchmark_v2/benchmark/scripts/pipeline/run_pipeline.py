"""End-to-end pipeline demo / CLI.

    python -m scripts.pipeline.run_pipeline                 # ingest + 3 demo questions
    python -m scripts.pipeline.run_pipeline --ask "Redis 为什么快？"
    python -m scripts.pipeline.run_pipeline --trace         # show agent-call trace

Runs offline (BM25 + extractive QA) or hybrid+LLM when OPENAI_API_KEY is set.
"""

from __future__ import annotations

import argparse

from .manager import PipelineManager

_DEMO = [
    "Redis 为什么这么快？不要把广告当答案。",
    "字节 Java 后端面经里 Redis 被问了哪些点？忽略个人状态。",
    "Claude Code 的 MCP 实现里连接管理和认证缓存怎么做的？",
]


def _print_qa(res, show_trace: bool) -> None:
    print(f"\nQ: {res.question}")
    flags = f"[grounded={res.grounded} llm={res.llm_used} retrieval_degraded={res.degraded_retrieval}]"
    print(f"   {flags}")
    print("A:", res.answer)
    if res.citations:
        print("Citations:")
        for i, c in enumerate(res.citations, 1):
            print(f"  [{i}] {c.doc_id} ({c.source_type}): {c.snippet}")
    if show_trace and res.trace is not None:
        print(res.trace.render())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", default=None)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--strategy", default=None, help="force a chunker for all docs")
    ap.add_argument("--trace", action="store_true")
    args = ap.parse_args()

    mgr = PipelineManager()
    print(f"LLM available: {mgr.llm_available}")
    ingest = mgr.ingest_all(strategy_override=args.strategy)
    print(
        f"Ingested {ingest.n_docs} docs → {ingest.n_chunks} chunks; "
        f"roles={ingest.role_counts}"
    )
    if args.trace:
        print(mgr._ingest_trace.render())

    questions = [args.ask] if args.ask else _DEMO
    for q in questions:
        _print_qa(mgr.ask(q, top_k=args.top_k), show_trace=args.trace)


if __name__ == "__main__":
    main()
