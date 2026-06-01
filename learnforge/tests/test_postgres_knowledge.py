"""PostgreSQL 知识库测试。

PG 不可用（未装 psycopg 或未配置 LF_POSTGRES_DSN）时整模块 skip，不影响现有 pytest。
启用方式：把 LF_POSTGRES_DSN 指向一个**可丢弃的测试库**（fixture 会 TRUNCATE 这 4 张表），
并 `pip install 'learnforge[postgres]'`，DB 侧需装 pgvector（BM25 可选 pg_search）。
"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.contracts.agents.retrieval import RetrievalFilters
from learnforge.contracts.enums import RetrievalMethod
from learnforge.knowledge.postgres.db import pg_available
from learnforge.knowledge.postgres.ingest import ingest_documents_pg
from learnforge.knowledge.postgres.migrate import migrate
from learnforge.knowledge.postgres.repository import PgChunkRepository
from learnforge.knowledge.postgres.source import PostgresSharedSource

pytestmark = pytest.mark.skipif(
    not pg_available(), reason="LF_POSTGRES_DSN / psycopg 不可用，跳过 PG 知识库测试"
)

DIM = config.EMBEDDING_DIM


def _vec(hot: int) -> list:
    """确定性单位向量：仅 hot 维为 1，便于断言最近邻（无需 API key）。"""
    v = [0.0] * DIM
    v[hot % DIM] = 1.0
    return v


def _truncate(repo: PgChunkRepository) -> None:
    conn = repo._conn(autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "TRUNCATE source_errors, ingest_runs, document_chunks, source_documents "
            "RESTART IDENTITY CASCADE"
        )
        cur.close()
    finally:
        conn.close()


@pytest.fixture
def repo() -> PgChunkRepository:
    migrate()  # 幂等：建扩展/表/索引
    r = PgChunkRepository()
    _truncate(r)
    yield r
    _truncate(r)


# --------------------------------------------------------------------- 入库
def test_chunk_ingest(repo):
    docs = [
        {"title": "Optimistic lock", "topic": "concurrency", "source_type": "doc",
         "source_url": "x", "text": "optimistic lock uses a version column and CAS"},
        {"title": "MVCC", "topic": "database", "source_type": "course",
         "source_url": "y", "text": "mvcc keeps multiple row versions for readers"},
    ]
    stats = ingest_documents_pg(docs, repo=repo, source="test")
    assert stats["documents"] == 2
    assert stats["chunks"] >= 2
    assert stats["errors"] == 0
    assert repo.count_chunks() == stats["chunks"]


# ----------------------------------------------------------------- 关键词检索
def test_keyword_search(repo):
    ingest_documents_pg(
        [{"title": "MVCC", "topic": "database", "source_type": "doc", "source_url": "y",
          "text": "mvcc keeps multiple row versions for concurrent readers"}],
        repo=repo,
    )
    hits = repo.keyword_search("mvcc", top_k=5)
    assert hits, "keyword search 应召回 mvcc 文档"
    assert any("mvcc" in h.text.lower() for h in hits)
    # 真实反映后端：BM25 或 FTS 降级，绝不混淆。
    assert hits[0].metadata["keyword_backend"] in ("bm25", "postgres_fts")


# ------------------------------------------------------------------- 向量检索
def test_vector_search_returns_nearest(repo):
    repo.upsert_chunk(chunk_id="a", content="alpha doc", topic="t", embedding=_vec(1))
    repo.upsert_chunk(chunk_id="b", content="beta doc", topic="t", embedding=_vec(500))
    hits = repo.vector_search(_vec(1), top_k=2)
    assert hits and hits[0].chunk_id == "a"


def test_vector_search_fallback_without_embedding(repo):
    repo.upsert_chunk(chunk_id="a", content="alpha", topic="t", embedding=_vec(1))
    # 无 embedding（NullEmbedding 场景）→ 向量检索退化为空，不报错。
    assert repo.vector_search(None, top_k=5) == []
    src = PostgresSharedSource()
    assert src.search("anything", RetrievalMethod.VECTOR, None, 5, embedding=None) == []


# --------------------------------------------------------------------- hybrid
def test_hybrid_rrf_merges_keyword_and_vector(repo):
    repo.upsert_chunk(chunk_id="kw", content="optimistic lock with cas", topic="t",
                      embedding=_vec(10))
    repo.upsert_chunk(chunk_id="vec", content="totally unrelated prose", topic="t",
                      embedding=_vec(20))
    hits = repo.hybrid_search("optimistic", _vec(20), top_k=5)
    ids = {h.chunk_id for h in hits}
    assert "kw" in ids   # 关键词臂命中
    assert "vec" in ids  # 向量臂命中


# ----------------------------------------------------------------- metadata filter
def test_metadata_filter_topic(repo):
    repo.upsert_chunk(chunk_id="c1", content="lock mechanism", topic="concurrency")
    repo.upsert_chunk(chunk_id="c2", content="lock mechanism", topic="database")
    hits = repo.keyword_search("lock", RetrievalFilters(topic="concurrency"), 5)
    assert {h.chunk_id for h in hits} == {"c1"}
    assert all(h.metadata["topic"] == "concurrency" for h in hits)
