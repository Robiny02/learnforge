"""daily 记忆：record→index→hybrid 召回 + 时间衰减（全离线，FTS 路径）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from learnforge import config
from learnforge.contracts.agents.retrieval import RetrievalInput
from learnforge.contracts.enums import KnowledgeScope, RetrievalMethod
from learnforge.memory import decay
from learnforge.memory.daily import record, reindex


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    return tmp_path / "mem"


def _recall(db_path, query):
    from learnforge.agents.retrieval import RetrievalAgent

    out = RetrievalAgent(db_path=db_path).run(
        RetrievalInput(query=query, scopes=[KnowledgeScope.LOCAL],
                       method=RetrievalMethod.FULLTEXT, top_k=8)
    )
    return out.chunks


def test_record_writes_file_and_indexes_chunk(mem_dir, tmp_db):
    note = record(kind="qa", text="Q: 什么是 Redis？\nA: 内存数据库，常用于缓存。",
                  topic="redis", db_path=tmp_db)
    assert (mem_dir / f"{note['date'][:10]}.md").exists()
    # chunks 出现 kb_scope='local' / origin='daily' 行。
    from learnforge.storage.repositories import ChunkRepository

    rows = ChunkRepository(db_path=tmp_db).conn.execute(
        "SELECT metadata FROM chunks WHERE kb_scope='local'"
    ).fetchall()
    assert any('"origin": "daily"' in r["metadata"] for r in rows)


def test_hybrid_recall_finds_recorded_note(mem_dir, tmp_db):
    record(kind="qa", text="Q: JWT 怎么防篡改？\nA: 用签名校验。", topic="auth", db_path=tmp_db)
    chunks = _recall(tmp_db, "JWT")
    assert chunks and any("JWT" in c.text for c in chunks)


def test_decay_weight_recency_and_stickiness():
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    old = (now - timedelta(days=20)).isoformat()
    # 越旧权重越低。
    assert decay.decay_weight(now.isoformat(), "note", now=now) > decay.decay_weight(
        old, "note", now=now
    )
    # 同龄：weak 衰减慢 → 权重高于普通 note。
    assert decay.decay_weight(old, "weak", now=now) > decay.decay_weight(old, "note", now=now)
    # 无日期不衰减。
    assert decay.decay_weight(None, "note", now=now) == 1.0


def test_recall_prefers_recent_note(mem_dir, tmp_db):
    now = datetime.now(timezone.utc)
    record(kind="note", text="CAS 旧笔记：比较并交换。", topic="concurrency",
           date=(now - timedelta(days=60)).isoformat(), db_path=tmp_db)
    record(kind="note", text="CAS 新笔记：比较并交换，今天复习。", topic="concurrency",
           date=now.isoformat(), db_path=tmp_db)
    chunks = _recall(tmp_db, "CAS")
    assert chunks
    assert "新笔记" in chunks[0].text  # 衰减后新者排前


def test_recall_weak_outranks_old_note_same_age(mem_dir, tmp_db):
    now = datetime.now(timezone.utc)
    aged = (now - timedelta(days=25)).isoformat()
    record(kind="note", text="Kafka 普通笔记。", topic="mq", date=aged, db_path=tmp_db)
    record(kind="weak", text="Kafka 薄弱点：分区与顺序消费。", topic="mq", date=aged,
           db_path=tmp_db)
    chunks = _recall(tmp_db, "Kafka")
    assert chunks
    assert chunks[0].metadata.get("kind") == "weak"  # 慢衰减的 weak 排前


def test_reindex_rebuilds_from_files(mem_dir, tmp_db):
    record(kind="qa", text="Q: 什么是 Redis？\nA: 缓存。", topic="redis", db_path=tmp_db)
    from learnforge.storage.repositories import ChunkRepository

    conn = ChunkRepository(db_path=tmp_db).conn
    # 清掉索引但保留 markdown 文件，再 reindex 应恢复召回。
    conn.execute("DELETE FROM chunks WHERE kb_scope='local'")
    conn.execute("DELETE FROM chunk_fts")
    conn.commit()
    assert not _recall(tmp_db, "Redis")
    n = reindex(db_path=tmp_db)
    assert n >= 1
    assert _recall(tmp_db, "Redis")
