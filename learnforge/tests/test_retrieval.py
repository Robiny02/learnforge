"""统一检索与双层知识库测试（离线）。

断言结构/契约/降级/只读性，不断言 LLM 输出质量：
- 知识源选择（scopes）与跨源召回。
- metadata 过滤。
- 离线无 embedding 时向量分路降级仍可召回（FTS）。
- backend 向后兼容映射。
- RetrievalAgent 只读：检索不写任何用户学习状态。
"""

from __future__ import annotations

from learnforge.agents.retrieval import RetrievalAgent, rrf_fuse
from learnforge.contracts.agents.retrieval import (
    Chunk,
    RetrievalFilters,
    RetrievalInput,
)
from learnforge.contracts.enums import (
    KnowledgeScope,
    RetrievalBackend,
    RetrievalMethod,
    SourceType,
)
from learnforge.storage.repositories import _Base


def _counts(db_path: str) -> dict:
    conn = _Base(db_path=db_path).conn
    tables = ["knowledge_atoms", "interaction_events", "learning_paths",
              "path_items", "mock_turns", "diagnosis_reports"]
    return {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables}


def test_rrf_fuse_dedup_and_order():
    a = Chunk(chunk_id="x", text="a")
    b = Chunk(chunk_id="y", text="b")
    fused = rrf_fuse([[a, b], [b, a]], top_k=2)
    ids = {c.chunk_id for c in fused}
    assert ids == {"x", "y"}
    assert all(c.score > 0 for c in fused)


def test_effective_scopes_backward_compat():
    assert RetrievalInput(query="q", backend=RetrievalBackend.CLOUD).effective_scopes() == [
        KnowledgeScope.SHARED
    ]
    assert RetrievalInput(query="q", backend=RetrievalBackend.LOCAL).effective_scopes() == [
        KnowledgeScope.LOCAL,
        KnowledgeScope.SHARED,
    ]
    assert RetrievalInput(query="q", scopes=[KnowledgeScope.SHARED]).effective_scopes() == [
        KnowledgeScope.SHARED
    ]


def test_shared_source_returns_chunks(seeded_db):
    agent = RetrievalAgent(db_path=seeded_db)
    out = agent.run(
        RetrievalInput(query="乐观锁", scopes=[KnowledgeScope.SHARED],
                       method=RetrievalMethod.FULLTEXT)
    )
    ids = {c.chunk_id for c in out.chunks}
    assert "c0" in ids
    assert all(c.scope == KnowledgeScope.SHARED for c in out.chunks)


def test_local_user_source_returns_qa_history(seeded_db):
    agent = RetrievalAgent(db_path=seeded_db)
    out = agent.run(
        RetrievalInput(query="乐观锁", scopes=[KnowledgeScope.LOCAL],
                       method=RetrievalMethod.FULLTEXT)
    )
    assert any(c.chunk_id == "qa:q0" for c in out.chunks)
    assert all(c.scope == KnowledgeScope.LOCAL for c in out.chunks)


def test_metadata_filter_source_type(seeded_db):
    agent = RetrievalAgent(db_path=seeded_db)
    out = agent.run(
        RetrievalInput(
            query="索引 乐观锁 数据库",
            scopes=[KnowledgeScope.SHARED],
            method=RetrievalMethod.FULLTEXT,
            filters=RetrievalFilters(source_type=SourceType.BLOG),
        )
    )
    assert all(c.source_type == SourceType.BLOG for c in out.chunks)
    assert any(c.chunk_id == "c1" for c in out.chunks)


def test_vector_method_degrades_offline(seeded_db):
    # 离线无 embedding：VECTOR 应降级到 FULLTEXT，degraded=True，仍能召回。
    agent = RetrievalAgent(db_path=seeded_db)
    out = agent.run(
        RetrievalInput(query="乐观锁", scopes=[KnowledgeScope.SHARED],
                       method=RetrievalMethod.VECTOR)
    )
    assert out.degraded is True
    assert out.method_used == RetrievalMethod.FULLTEXT
    assert any(c.chunk_id == "c0" for c in out.chunks)


def test_hybrid_default_offline_returns_results(seeded_db):
    agent = RetrievalAgent(db_path=seeded_db)
    out = agent.run(RetrievalInput(query="乐观锁"))  # 默认 HYBRID + [LOCAL, SHARED]
    ids = {c.chunk_id for c in out.chunks}
    assert "c0" in ids or "qa:q0" in ids


def test_retrieval_is_read_only(seeded_db):
    before = _counts(seeded_db)
    agent = RetrievalAgent(db_path=seeded_db)
    agent.run(RetrievalInput(query="乐观锁 数据库 索引"))
    after = _counts(seeded_db)
    assert before == after
