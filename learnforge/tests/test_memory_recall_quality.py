"""记忆召回：不同质量 query + 边界情况（全离线，FTS + 衰减）。

复用 memory_recall_demo 的语料夹具；断言"召回正确性 + 衰减排序 + 边界鲁棒性"，
不断言具体分值（离线 FTS 受 tokenizer 影响，断言结构/顺序更稳）。
"""

from __future__ import annotations

import pytest
from memory_recall_demo import build_corpus, recall

from learnforge import config
from learnforge.contracts.enums import RetrievalMethod


@pytest.fixture
def corpus_db(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    build_corpus(tmp_db, tmp_path / "mem")
    return tmp_db


def _texts(out):
    return " || ".join(c.text for c in out.chunks)


# --- 高质量：精确关键词应命中对应记忆 -------------------------------------
@pytest.mark.parametrize(
    "query, expect",
    [("Redis", "Redis"), ("JWT", "JWT"), ("CAS", "CAS"), ("Kafka", "Kafka")],
)
def test_exact_keyword_recall(corpus_db, query, expect):
    out = recall(corpus_db, query)
    assert out.chunks, f"{query} 应有召回"
    assert expect in _texts(out)


# --- 中等质量：中文相关概念（LIKE 降级）也应命中 --------------------------
@pytest.mark.parametrize(
    "query, expect",
    [("缓存过期", "Redis"), ("并发控制", "乐观锁"), ("分布式 事务", "分布式事务")],
)
def test_medium_quality_recall(corpus_db, query, expect):
    assert expect in _texts(recall(corpus_db, query))


# --- 时间衰减：新近性 + 黏性 ----------------------------------------------
def test_decay_prefers_recent_same_keyword(corpus_db):
    # 两条 Redis（今天 vs 40 天前）→ 今天的排在前。
    top = recall(corpus_db, "Redis").chunks[0]
    assert "缓存过期" in top.text  # 今天那条


def test_decay_stickiness_weak_outranks_newer_note(corpus_db):
    # "索引"：note(8天,快衰) vs weak(25天,慢衰) → 慢衰减的 weak 排前。
    top = recall(corpus_db, "索引").chunks[0]
    assert top.metadata.get("kind") == "weak"


# --- 仅语义 query：离线 FTS 难命中（暴露需向量召回的局限）-----------------
@pytest.mark.parametrize("query", ["怎么让登录态不被篡改", "读多写少的并发方案"])
def test_semantic_only_misses_offline(corpus_db, query):
    out = recall(corpus_db, query)
    # 关键词不重叠 → FTS 召不回（有向量时才好）。这里断言"语义靶点未命中"。
    assert "JWT" not in _texts(out) and "乐观锁" not in _texts(out)


# --- 边界情况：鲁棒性（不得抛异常，召回可空）-----------------------------
@pytest.mark.parametrize(
    "query",
    [
        "",                       # 空
        "     ",                  # 纯空白
        "？？？",                  # 纯标点
        "GraphQL 量子纠缠",        # 不存在
        'redis AND OR "(',        # FTS 特殊符号/注入
        "锁",                     # 单 CJK 字
        "REDIS",                  # 大小写
        "缓存" * 60,              # 超长(>120)
    ],
)
def test_edge_cases_do_not_crash(corpus_db, query):
    out = recall(corpus_db, query)
    assert isinstance(out.chunks, list)  # 永不抛异常


def test_empty_and_nonexistent_return_no_results(corpus_db):
    assert recall(corpus_db, "").chunks == []
    assert recall(corpus_db, "GraphQL 量子纠缠").chunks == []


def test_fts_special_chars_still_recall_valid_term(corpus_db):
    # 注入式特殊符号被 sanitize 后，仍能凭 redis 命中且不崩。
    out = recall(corpus_db, 'redis AND OR "(')
    assert "Redis" in _texts(out)


def test_case_insensitive(corpus_db):
    assert "Redis" in _texts(recall(corpus_db, "REDIS"))


# --- MEMORY.md 不进索引：查其内容词应召不回 ------------------------------
def test_memory_md_not_in_recall(corpus_db):
    assert recall(corpus_db, "回答用中文").chunks == []


# --- 方式无关：HYBRID 离线降级 FTS 同样不崩 ------------------------------
def test_hybrid_offline_degrades(corpus_db):
    out = recall(corpus_db, "Redis", method=RetrievalMethod.HYBRID)
    assert "Redis" in _texts(out)
