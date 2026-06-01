# Agentic RAG Pipeline (LearnForge-style)

A hierarchical, single-responsibility agent chain over the chunking benchmark —
mirrors LearnForge's `ManagerAgent → workers` design. Every agent degrades
gracefully, so the chain runs with or without an API key.

## Agents & call graph

```
PipelineManager (orchestrator, owns KnowledgeStore + Tracer)
├── ingest_all()
│     └── IngestionAgent
│           ├── BlockClassifierAgent   # blocks → 8 labels → index_role
│           └── ChunkingAgent          # source_type → best chunker (fallback-safe)
└── ask(question)
      └── QAAgent
            ├── RetrievalAgent         # hybrid BM25+vector RRF over main(+downweighted)
            └── LLM synthesis          # cited answer; extractive fallback offline
```

Agent-to-agent calls are explicit and **traced** (`--trace` prints the chain).

## Index roles (noise routing, not deletion)

| role | block labels | retrieval |
|---|---|---|
| `main` | technical_core, useful_ocr, useful_context | primary index |
| `downweighted` | career_noise, off_topic_job | joins at 0.6× score |
| `isolated` | irrelevant_ocr, ad_or_promo, empty_or_garbled | excluded |

A chunk that touches **any** `main` block stays `main` (technical content is never
demoted); a chunk made only of noise is isolated.

## Run

```bash
cd benchmark
# offline (BM25 + extractive QA) — always works
python -m scripts.pipeline.run_pipeline --trace

# full hybrid + LLM synthesis
export LF_EMBEDDING_PROVIDER=openai OPENAI_API_KEY=... \
       OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
       LF_EMBEDDING_MODEL=text-embedding-3-small LF_EMBEDDING_DIM=1024
python -m scripts.pipeline.run_pipeline --ask "字节 Java 后端面经里 Redis 被问了哪些点？"
```

`PIPELINE_LLM_MODEL` (default `openai/gpt-4o-mini`) selects the synthesis model.
```
