"""Seed data for personal local memory and session_memory.

These tests intentionally stay inside the existing memory/session_state paths:
daily markdown is the long-term source of truth, and session_state is short-term
conversation context that must not enter the daily/chunks index.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from learnforge import config
from learnforge.orchestration.manager import ManagerAgent
from learnforge.contracts.agents.retrieval import RetrievalFilters, RetrievalInput
from learnforge.contracts.enums import KnowledgeScope, RetrievalMethod
from learnforge.memory.daily import reindex
from learnforge.memory.files import iter_daily_notes
from learnforge.storage.db import get_connection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_MEMORY_DIR = PROJECT_ROOT / "data" / "memory"
SESSION_SEED_PATH = PROJECT_ROOT / "data" / "session_memory" / "session_state_seed.json"
SEED_DATES = {"2026-05-24", "2026-05-25", "2026-05-26", "2026-05-28", "2026-05-29"}


def _seed_notes(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(SEED_MEMORY_DIR))
    return [n for n in iter_daily_notes() if str(n.get("date", ""))[:10] in SEED_DATES]


def _recall(db_path, query, filters=None, top_k=8):
    from learnforge.agents.retrieval import RetrievalAgent

    return RetrievalAgent(db_path=db_path).run(
        RetrievalInput(
            query=query,
            scopes=[KnowledgeScope.LOCAL],
            method=RetrievalMethod.FULLTEXT,
            top_k=top_k,
            filters=filters,
        )
    ).chunks


def _load_session_seed() -> dict:
    return json.loads(SESSION_SEED_PATH.read_text(encoding="utf-8"))


def _insert_session_seed(db_path: str, seed: dict) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """INSERT INTO session_state
             (session_id, summary, recent_messages, active_task, updated_at)
           VALUES (?,?,?,?,?)""",
        (
            seed["session_id"],
            seed["summary"],
            json.dumps(seed["recent_messages"], ensure_ascii=False),
            json.dumps(seed["active_task"], ensure_ascii=False),
            seed["updated_at"],
        ),
    )
    conn.commit()
    conn.close()


def test_seed_daily_files_parse_and_keep_kind_topic_importance(monkeypatch):
    notes = _seed_notes(monkeypatch)
    per_day = Counter(str(n.get("date", ""))[:10] for n in notes)
    assert all(per_day[d] >= 20 for d in SEED_DATES)
    assert {p.name for p in SEED_MEMORY_DIR.glob("2026-05-2*.md")} >= {
        f"{d}.md" for d in SEED_DATES
    }
    assert {n["kind"] for n in notes} >= {"qa", "weak", "mock", "progress", "decision"}
    assert {n["topic"] for n in notes} >= {
        "memory_architecture",
        "ragas",
        "chunking",
        "embedding",
        "redis",
        "payment",
        "java",
        "agent",
        "retrieval_eval",
    }
    assert all(isinstance(n.get("importance"), float) for n in notes)
    assert all(n.get("content") == n.get("text") and n.get("text") for n in notes)


def test_seed_daily_reindex_rebuilds_local_daily_index(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "MEMORY_DIR", str(SEED_MEMORY_DIR))
    rebuilt = reindex(db_path=tmp_db)
    assert rebuilt >= 100

    conn = get_connection(tmp_db)
    rows = conn.execute(
        "SELECT topic, metadata FROM chunks WHERE kb_scope='local' "
        "AND json_extract(metadata,'$.origin')='daily'"
    ).fetchall()
    conn.close()
    assert len(rows) >= 15
    metadata = [json.loads(r["metadata"]) for r in rows]
    per_day = Counter(str(m.get("date", ""))[:10] for m in metadata)
    assert all(per_day[d] >= 20 for d in SEED_DATES)
    assert all("kind" in m and "importance" in m for m in metadata)
    assert any(r["topic"] == "payment" for r in rows)


def test_seed_kind_and_topic_filters_are_usable(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "MEMORY_DIR", str(SEED_MEMORY_DIR))
    reindex(db_path=tmp_db)

    payment_weak = _recall(
        tmp_db,
        "payment 幂等 重复扣款",
        filters=RetrievalFilters(topic="payment", kind="weak"),
    )
    assert payment_weak
    assert all(c.metadata.get("kind") == "weak" and c.metadata.get("topic") == "payment"
               for c in payment_weak)

    java_in_redis = _recall(tmp_db, "Java 线程池", filters=RetrievalFilters(topic="redis"))
    assert java_in_redis == []


def test_seed_negative_sample_does_not_recall_unrelated_memory(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "MEMORY_DIR", str(SEED_MEMORY_DIR))
    reindex(db_path=tmp_db)
    assert _recall(tmp_db, "quantum superconducting compiler") == []


def test_session_memory_seed_renders_summary_and_recent_messages(tmp_db):
    seed = _load_session_seed()
    assert {"session_id", "summary", "recent_messages", "active_task", "updated_at"} <= set(seed)
    _insert_session_seed(tmp_db, seed)

    rendered = ManagerAgent(db_path=tmp_db).load_session_memory(seed["session_id"])
    assert rendered
    assert "[早期会话摘要]" in rendered
    assert "personal local memory" in rendered
    assert "daily memory 要覆盖" in rendered
    assert seed["active_task"]["negative_index_marker"] not in rendered


def test_session_memory_seed_does_not_enter_long_term_index(monkeypatch, tmp_db):
    monkeypatch.setattr(config, "MEMORY_DIR", str(SEED_MEMORY_DIR))
    seed = _load_session_seed()
    _insert_session_seed(tmp_db, seed)
    reindex(db_path=tmp_db)

    assert _recall(tmp_db, seed["active_task"]["negative_index_marker"]) == []
    assert _recall(tmp_db, seed["session_id"]) == []
