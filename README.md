# LearnForge

> A learning companion for programmers — built as an **orchestrator + tools** multi-agent
> system on LangGraph, FastAPI, and SQLite (FTS5 + sqlite-vec).

LearnForge turns scattered study signals — questions you ask, mock-interview answers, learning
goals — into a durable, queryable state that drives diagnosis and planning. One main agent
(`ManagerAgent`) plans and dispatches; each domain capability is a **tool** it calls.

The whole system runs **offline**: with no `OPENROUTER_API_KEY`, every tool falls back to a
deterministic stub, so the chain always passes and tests need no network.

## Capabilities

| Tool | Role |
|---|---|
| **qa** | Retrieval-augmented Q&A (Router → Synthesizer → Verifier), citation-backed answers |
| **diagnose** | Read-only weakness detection over mastery + interaction signals (ReAct) |
| **plan** | Generate / modify learning paths as incremental `PathDiff` |
| **mock** | Multi-turn mock interview (Interviewer / Judge / Strategist / Coach) |
| **retrieval** | Shared RAG capability: keyword / FTS5 BM25 / sqlite-vec KNN / hybrid (RRF) |

> `research` is specced but not yet implemented. Router / Synthesizer / Judge / Coach are
> *sub-steps inside a tool*, not standalone tools.

## Architecture

```
User request
     │
     ▼
LangGraph main graph
  START → manager_plan → manager_execute → manager_aggregate → END
                              │
                              ▼
                        ManagerAgent  (sole orchestrator + sole writer of shared state)
                              │
        ┌──────────┬──────────┼──────────┬──────────┐
        ▼          ▼          ▼          ▼          ▼
       qa      diagnose      plan       mock     retrieval (shared)
```

- **ManagerAgent** owns a plan-and-execute loop (replan ≤ 2) and is the *only* writer of
  mastery and learning paths. `DiagnosisAgent` is strictly read-only.
- **Intent layer** (`intent/`) routes natural language: deterministic short-circuits → one cheap
  LLM classification → offline rule fallback. Single intent → one tool; multi-intent → planner.
- **Two-layer knowledge base**: LOCAL (personal data) + SHARED (public course/blog/interview
  slices), retrieved through a pluggable RAG pipeline with optional rerank and a remote KB source.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture and [`docs/`](docs/) for design notes.

## Quick start

```bash
cd learnforge
pip install -e ".[dev]"                       # Python 3.11+

pytest                                         # full suite, fully offline
python -m learnforge.graph.main_graph          # smoke-run the main graph
python -m learnforge.cli                        # interactive CLI
uvicorn learnforge.app:api --reload            # start the API server
```

Enable LLMs by setting `OPENROUTER_API_KEY` in an untracked `.env` (see [`.env.example`](.env.example)).
Without it, the system degrades gracefully to deterministic stubs.

## Repository layout

```
learnforge/            Python package (agents, graph, knowledge, storage, intent, …)
docs/                  Design notes, architecture, UI design, archived proposals
chunking_benchmark_v2/ Standalone RAG chunking-strategy benchmark
scripts/               Dev tooling (diagram generation, MCP helpers)
CLAUDE.md              Engineering guide / architecture reference
```

## Design invariants

- **Manager is the only writer** of mastery and learning paths.
- **The chain always passes** — no API key required; every tool has a deterministic stub.
- All `*_id` are UUID strings; timestamps are ISO-8601 UTC text in SQLite.

## License

See repository for license details.
