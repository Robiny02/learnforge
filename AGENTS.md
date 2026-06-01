# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project overview

LearnForge is a hierarchical multi-agent learning companion for programmers. The project consists of a Python package (`learnforge/`) that implements a 13-agent system built on LangGraph, FastAPI, SQLite (with FTS5 + sqlite-vec), and the Anthropic SDK. Phases 1-3 are complete; the system runs offline without `ANTHROPIC_API_KEY` via deterministic fallbacks.

## Commands

All commands run from `learnforge/` (the Python package root):

```bash
cd learnforge

# Install deps
pip install -e ".[dev]"

# Run all tests (offline — no API key needed)
pytest

# Run a single test file
pytest tests/test_diagnosis.py

# Run a single test by name
pytest tests/test_diagnosis.py::test_diagnosis_is_read_only

# Smoke-run the main graph (4 intent cases, no API key needed)
python -m learnforge.graph.main_graph

# Start the FastAPI server
uvicorn learnforge.app:api --reload

# Lint
ruff check learnforge tests
```

Model names can be overridden via env vars `LF_HAIKU_MODEL` and `LF_SONNET_MODEL`.

## Architecture

### Agent hierarchy

`ManagerAgent` is the **sole orchestrator and sole writer** of shared state. It owns a plan-and-execute loop (replan ≤ 2) and dispatches to four domain workers:

| Worker | Subagents | Role |
|---|---|---|
| `QAAgent` | Router → Synthesizer → Verifier | Question answering |
| `PlanningAgent` | — | Generate/modify learning paths |
| `MockInterviewAgent` | Interviewer, Judge, Strategist, Coach | Multi-turn mock interviews via LangGraph subgraph |
| `DiagnosisAgent` | — | Read-only weakness detection (ReAct 3-step) |

`RetrievalAgent` is a shared sub-capability and the **unified retrieval entry point** for a pluggable, two-layer RAG architecture, not directly scheduled by Manager. See "Knowledge base & retrieval" below.

### Knowledge base & retrieval (`knowledge/`)

Two-layer knowledge system selected via `KnowledgeScope`:
- **LOCAL** (本地用户库): personal data — `mock_turns`, `qa_history`.
- **SHARED** (共享知识库): reusable public content — `chunks` (course/blog/interview/doc slices) and `knowledge_atoms` (public knowledge points / question bank).

Pluggable components:
- `knowledge/sources/` — `KnowledgeSource` protocol + `LocalUserSource` / `LocalSharedSource` / `RemoteSharedSource` (remote KB via `LF_REMOTE_KB_URL`; offline → does not participate). Replaces the old `LocalBackend`/`CloudBackend`.
- `RetrievalMethod` enum — `keyword` / `fulltext` (FTS5 BM25) / `vector` (sqlite-vec KNN) / `hybrid` (FTS+vector RRF, default); `RetrievalFilters` adds metadata filtering (topic/source_type/kb_scope/difficulty/source_name).
- `llm/embeddings.py` — `EmbeddingProvider` (OpenAI/Voyage/Null), `EMBEDDINGS` singleton; env `LF_EMBEDDING_PROVIDER` ∈ {none,openai,voyage}, `LF_EMBEDDING_DIM` (default 1024, OpenAI uses `dimensions` to match vec0). No key → vector degrades to FTS.
- `knowledge/rerank.py` — `Reranker` protocol, `NoOpReranker` (default), optional `LLMReranker`.
- `knowledge/ingest.py` — ingestion pipeline (chunk → embed → write `chunks`/`chunk_fts`/`chunk_vectors`; `sync_atom_index()` for atoms). CLI: `python -m learnforge.knowledge.ingest --file docs.json | --sync-atoms`. Writes content/indexes only, never mastery.

`RetrievalAgent.run()` flow: rewrite → embed (if vector/hybrid) → query selected sources → cross-source RRF → optional rerank → top_k; output carries `method_used` + `degraded`. `RetrievalInput.backend` is kept for backward compat (maps to scopes via `effective_scopes()`).

### Data contracts (`contracts/`)

All contracts are defined before implementations. Key types:
- `contracts/enums.py` — all enums (AgentId, Status, MasteryTier, etc.)
- `contracts/state.py` — `MainState` (LangGraph TypedDict) + `UserProfile`, `LearningPath`, `MockState`
- `contracts/agents/*.py` — per-agent Input/Output Pydantic models
- `contracts/message.py` — `ResponsePayload`, `EventPayload`, `ContextEnvelope`

### LangGraph graphs (`graph/`)

- `main_graph.py`: `START → manager_plan → manager_execute → manager_aggregate → END`
- `mock_graph.py`: S1_INIT → S2_INTERVIEWER → S3_AWAIT(interrupt) → S4_JUDGE → S5_STRATEGIST → (loop or S6_COACH → S7_SETTLE → END). `interrupt()` at S3 and S_PAUSE; resume via `Command(resume=...)`.

### Mastery algorithm (`mastery.py`)

Pure functions, no side effects. Used by DiagnosisAgent (read-only) and Manager (write path):
- `update_mastery(old, signal, source)` — exponential moving average; α: mock=0.5, qa=0.3, self=0.15
- `effective_mastery(score, decay_rate, last_reviewed_at)` — time decay via `exp(-λ·days)`
- `to_tier(score)` → `MasteryTier` (5 levels: unknown/learning/familiar/proficient/mastered)

### Storage (`storage/`)

- `schema.sql` — full DDL: `knowledge_atoms`, `chunks` (+ `kb_scope`), `atom_vectors`/`chunk_vectors` (vec0, dim=`EMBEDDING_DIM`), `atom_fts`/`chunk_fts` (FTS5), `qa_history`/`qa_history_fts`, `interaction_events`, `mock_sessions`/`mock_turns`, `diagnosis_reports`, `agent_traces`, `user_profile`, `learning_paths`
- `db.py` — `init_db(path)`, `get_connection(path)`; injects `config.EMBEDDING_DIM` into vec0 DDL; FTS5-only degradation when sqlite-vec absent
- `repositories.py` — `AtomRepository` (+ `fts_match`/`vector_knn`/`sync_index`), `ChunkRepository` (`fts_match`/`vector_knn`/`upsert`), `MockTurnRepository`, `QAHistoryRepository`, `EventRepository`, `TraceRepository`, `LearningPathRepository`

### LLM client (`llm/client.py`)

Singleton `LLM = LLMClient()`. When `ANTHROPIC_API_KEY` is absent, `LLM.available = False` and all agents fall back to deterministic stubs. `complete_structured()` calls the model and parses JSON output into a Pydantic schema (1 retry).

### Skills (`skills/`)

`SkillRegistry` maps each `AgentId` to a `Skill` (system_prompt + model_tier + workflow config). `BaseAgent.llm_structured()` uses the agent's registered skill; returns `None` on failure so callers can fall back.

### Key design invariants

- **Manager is the only writer** of `knowledge_atoms` mastery scores and `learning_paths`. DiagnosisAgent is strictly read-only (enforced by `test_diagnosis_is_read_only`).
- **"Chain always passes"**: no `ANTHROPIC_API_KEY` required; all agents have deterministic fallback stubs.
- **Composite workflow (§5.6)**: "prepare for interview" → diagnosis → planning.modify. If diagnosis is empty, skip modify and suggest mock instead. After modify, suggest mock but do not auto-start it.
- **mock settlement**: on `status=review`, `ManagerAgent.settle_mock()` commits mastery (唯一写者) then auto-triggers a read-only post-mock diagnosis.
- All `*_id` fields are `str(uuid4)`; timestamps are ISO8601 UTC text in SQLite.
- Source typing: use `typing.Optional/List/Union` (not `X | None`) for Python 3.9 compatibility.

## Tests

Tests run fully offline. Fixtures `tmp_db` (empty DB) and `seeded_db` (2 topics, 5 events) are in `conftest.py`. Tests assert structural contracts and read-only invariants — not LLM output quality.
