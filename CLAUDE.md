# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project overview

LearnForge is a learning companion for programmers, built as an **orchestrator + tools**
multi-agent system on LangGraph, FastAPI, and SQLite (FTS5 + sqlite-vec). One main agent
(`ManagerAgent`) plans and dispatches; each domain capability is a **tool** it calls
(agent-as-tool). The whole system runs **offline** — with no `OPENROUTER_API_KEY` every tool
falls back to a deterministic stub ("chain always passes").

Implemented tools: **qa**, **diagnose**, **plan**, **mock** (+ shared **retrieval**).
**research** is specced but not yet implemented (`learnforge/agents/research/CLAUDE.md`).

> Terminology: do not call this a "13-agent system". Router/Synthesizer/Judge/Coach etc. are
> *sub-steps inside a tool*, not standalone tools. The outward tools are the four above.

## Commands

All commands run from `learnforge/` (the Python package root):

```bash
cd learnforge
pip install -e ".[dev]"          # install deps
pytest                           # all tests (offline — no key needed)
pytest tests/test_diagnosis.py::test_diagnosis_is_read_only   # single test
python -m learnforge.graph.main_graph        # smoke-run the main graph
python tests/test_intent_accuracy.py         # live intent-classifier accuracy report
uvicorn learnforge.app:api --reload          # start the API server
ruff check learnforge tests                  # lint
```

Env: `OPENROUTER_API_KEY` enables LLMs (stored only in untracked `.env`). Model overrides:
`LF_HAIKU_MODEL` / `LF_SONNET_MODEL`; intent classifier `LF_INTENT_MODEL` (default `openai/gpt-5-mini`).

## Architecture

### Orchestrator + tools (`agents/`, `orchestration/`)

`ManagerAgent` (in `orchestration/manager.py`; `agents/manager.py` is a compat shim) is the
**sole orchestrator and sole writer** of shared state. It owns a plan-and-execute loop
(replan ≤ 2) and calls each tool as an agent-as-tool. Each tool lives in its own package under
`agents/` with its own `CLAUDE.md`:

| Tool | Dir | Role | Runtime tiers |
|---|---|---|---|
| qa | `agents/qa/` | Retrieval-augmented Q&A (Router→Synthesizer→Verifier) | Haiku/Sonnet |
| diagnose | `agents/diagnosis/` | Read-only weakness detection (ReAct 3-step) | Sonnet |
| plan | `agents/planning/` | Generate/modify learning paths (incremental PathDiff) | Sonnet |
| mock | `agents/mock/` | Multi-turn interview (Interviewer/Judge/Strategist/Coach) | Haiku/Sonnet |
| research | `agents/research/` | *not implemented* — first real tool-calling ReAct (read-only) | — |
| retrieval | `agents/retrieval/` | **shared capability**, not directly scheduled by Manager | Haiku |

**Each tool = three layers** (keep them distinct):
- **contract** — `contracts/agents/<tool>.py` (Input/Output Pydantic = the tool schema).
- **runtime persona** — its `Skill` in `skills/definitions/__init__.py` (system_prompt + tool perms + model_tier).
- **engineering doc** — the tool dir's `CLAUDE.md`. A `CLAUDE.md` is *not* the agent's system prompt.

See `agents/CLAUDE.md` for the layer overview and per-tool `CLAUDE.md` for each tool's contract,
invariants, and failure modes.

### Intent layer (`intent/`)

Natural language → routing. The production path is the lightweight **`Dispatcher`**
(`intent/dispatch.py`): L0 deterministic short-circuits → L1 one cheap LLM classification
(single vs multi intent + capability, 2 fields) → L3 offline rule fallback, then a `last_capability`
carry rule for continuation. Single intent → one tool; **multi intent → `orchestration/planner.py`
plan-as-tool-calls**. Slot-filling/clarification are pushed down into the tools (lazy), not done
up front. The older `IntentResolver` (heavier, multi-field) is retained for the CLI and tests.

Conversation context for routing/continuation is persisted in **`dialogue_turns`** (see Storage),
not in process memory; `DialogueTurnRepository.derive_context` recomputes `last_capability`
(with aside decay) + `active_mock` + `last_topic` from recent turns. Intent accuracy is gated by
`tests/test_intent_accuracy.py` (offline rules path) with a live LLM report on `__main__`.

In-interview turn classification (answer/side/exit/skip/hint/…) is separate: `intent/mock_turn.py`.

### Knowledge base & retrieval (`knowledge/`)

Two-layer knowledge selected via `KnowledgeScope`:
- **LOCAL** (本地用户库): personal data — `mock_turns`, `qa_history`.
- **SHARED** (共享知识库): public content — `chunks` (course/blog/interview/doc slices) + `knowledge_atoms`.

Pluggable: `knowledge/sources/` (`KnowledgeSource` protocol + Local/Remote sources, remote via
`LF_REMOTE_KB_URL`); `RetrievalMethod` ∈ keyword / fulltext (FTS5 BM25) / vector (sqlite-vec KNN) /
hybrid (FTS+vector RRF, default); `RetrievalFilters` for metadata; `llm/embeddings.py`
(`LF_EMBEDDING_PROVIDER` ∈ none/openai/voyage, no key → vector degrades to FTS); `knowledge/rerank.py`
(NoOp default, optional LLM); `knowledge/ingest.py` (chunk→embed→write chunks/fts/vectors; never mastery).
`RetrievalAgent.run()`: rewrite → embed → query sources → cross-source RRF → optional rerank → top_k,
output carries `method_used` + `degraded`. Details in `agents/retrieval/CLAUDE.md`.

### Data contracts (`contracts/`)

Contracts are defined before implementations:
- `contracts/enums.py` — all enums (AgentId, Status, MasteryTier, …).
- `contracts/state.py` — `MainState` (LangGraph TypedDict) + `UserProfile`/`LearningPath`/`MockState`.
- `contracts/agents/*.py` — per-tool Input/Output models (the tool schemas).
- `contracts/intent.py` — `IntentFrame`, `Capability`, `MockAction`; `intent/dispatch.py` adds `Route`.
- `contracts/dialogue.py` — `DialogueTurn` / `ToolCall` / `DialogueContext` (conversation ledger).
- `contracts/message.py` — `ResponsePayload`, `EventPayload`, `ContextEnvelope`.

### LangGraph graphs (`graph/`)

- `main_graph.py`: `START → manager_plan → manager_execute → manager_aggregate → END`.
- `mock_graph.py`: S1_INIT → S2_INTERVIEWER → S3_AWAIT(interrupt) → S4_JUDGE → S5_STRATEGIST →
  (loop or S6_COACH → S7_SETTLE → END). `interrupt()` at S3 and S_PAUSE; resume via `Command(resume=...)`.

### Interview grilling (`agents/mock/interview_skill.py`)

Evidence-bound methodology distilled from the open-source **LLMInternSkill** (MIT) into a pure,
offline, deterministic rubric (attribution in `agents/mock/skill_pack/NOTICE.md`). Powers both LLM
prompts and no-key fallbacks. Pure functions: `detect_role_type`, `role_focus`, `extract_claims`,
`pick_grill_round` (truth_boundary→technical_depth→jd_deep_dive→scenario), `risk_flags_for_answer`,
`build_answer_card`. `MockInput.context: InterviewContext` (all optional) carries
target_role/jd_text/role_type/resume_claims/projects. Only the grilling rubric was vendored —
resume-polish / JD-tailoring / LaTeX / project-scout intentionally left out
(`docs/interview-skill-integration.md`). Full notes in `agents/mock/CLAUDE.md`.

### Mastery algorithm (`mastery.py`)

Pure, side-effect-free. Used by DiagnosisAgent (read) and Manager (write):
- `update_mastery(old, signal, source)` — EMA; α: mock=0.5, qa=0.3, self=0.15.
- `effective_mastery(score, decay_rate, last_reviewed_at)` — time decay `exp(-λ·days)`.
- `to_tier(score)` → `MasteryTier` (unknown/learning/familiar/proficient/mastered).

### Storage (`storage/`)

- `schema.sql` — full DDL: `knowledge_atoms`, `chunks` (+ `kb_scope`), `atom_vectors`/`chunk_vectors`
  (vec0, dim=`EMBEDDING_DIM`), `atom_fts`/`chunk_fts` (FTS5), `qa_history`(+fts), `interaction_events`,
  `mock_sessions`/`mock_turns`, `diagnosis_reports`, `agent_traces`, `user_profile`, `learning_paths`,
  `session_state`, **`dialogue_turns`** (conversation ledger: role/capability/route_mode/topic/tool_calls/artifacts).
- `db.py` — `init_db(path)`, `get_connection(path)`; injects `EMBEDDING_DIM` into vec0 DDL; FTS5-only
  degradation when sqlite-vec absent.
- `repositories.py` — `Atom`/`Chunk`/`MockTurn`/`QAHistory`/`Event`/`Trace`/`LearningPath`/`SessionState`/
  **`DialogueTurn`** repositories. `DialogueTurnRepository` self-heals its table (IF NOT EXISTS) to
  migrate older DBs; offers `recent` / `recent_projection` / `derive_context`.

### LLM client (`llm/client.py`)

Singleton `LLM = LLMClient()` over **OpenRouter** (OpenAI-compatible). With no `OPENROUTER_API_KEY`
(or `OPENAI_API_KEY`) → `LLM.available = False` and every tool falls back to a deterministic stub.
`complete_structured()` calls the model and parses JSON into a Pydantic schema (1 retry); a per-call
`model=` override lets the intent layer use a stronger reasoning model.

### Skills (`skills/`)

`SkillRegistry` maps each `AgentId` to a `Skill` (system_prompt + model_tier + workflow). This is the
**runtime-persona** layer — `BaseAgent.llm_structured()` uses the agent's skill and returns `None` on
failure so callers fall back. Editing how a tool *answers* means editing its skill, not its CLAUDE.md.

## Key design invariants

- **Manager is the only writer** of `knowledge_atoms` mastery and `learning_paths`. DiagnosisAgent is
  strictly read-only (`test_diagnosis_is_read_only`); a future research tool must be read-only too.
- **"Chain always passes"**: no `OPENROUTER_API_KEY` required; every tool has a deterministic stub.
- **Composite workflow (§5.6)**: "prepare for interview" → diagnosis → planning.modify. Empty diagnosis
  → skip modify, suggest mock. After modify, suggest mock but do not auto-start it.
- **Mock settlement**: on `status=review`, `ManagerAgent.settle_mock()` commits mastery (sole writer)
  then auto-triggers a read-only post-mock diagnosis.
- All `*_id` are `str(uuid4)`; timestamps are ISO8601 UTC text in SQLite.
- Python 3.9 compatible: use `typing.Optional/List/Union`, not `X | None`.

## Tests

Tests run fully offline. Fixtures `tmp_db` (empty) and `seeded_db` (2 topics, 5 events) are in
`conftest.py`. Tests assert structural contracts and read-only invariants — not LLM output quality.
Two pre-existing failures in `tests/test_embeddings_base_url.py` require the optional `openai` package
and are unrelated to core logic.
