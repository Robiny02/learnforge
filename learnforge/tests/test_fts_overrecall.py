"""FTS/LIKE fallback 过召回回归（REQUIREMENTS 测试 §7.1）。

单字符词（ASCII 如 "1"、CJK 如 "加"）不应驱动 LIKE 子串召回，否则闲聊/算术类
负样本会误召含该字的记忆（实测 "1 加 1 等于几" 曾误召含 "增加" 的 chunking 记忆）。
"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.contracts.agents.retrieval import RetrievalInput
from learnforge.contracts.enums import KnowledgeScope, RetrievalMethod
from learnforge.memory.daily import record


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    return tmp_path / "mem"


def _recall(db_path, query):
    from learnforge.agents.retrieval import RetrievalAgent

    return RetrievalAgent(db_path=db_path).run(
        RetrievalInput(query=query, scopes=[KnowledgeScope.LOCAL],
                       method=RetrievalMethod.FULLTEXT, top_k=5)
    ).chunks


def test_single_char_query_terms_do_not_overrecall(mem_dir, tmp_db):
    record(kind="weak", topic="chunking",
           text="薄弱点：chunking 需要增加边界检查与上下文保留。", db_path=tmp_db)
    # 含单字 "加"/"1" 的闲聊算术不应召回(单字 LIKE 子串过宽)。
    assert _recall(tmp_db, "1 加 1 等于几") == []
    assert _recall(tmp_db, "a b c") == []
    # 合法多字查询仍能召回。
    assert _recall(tmp_db, "chunking 边界")
