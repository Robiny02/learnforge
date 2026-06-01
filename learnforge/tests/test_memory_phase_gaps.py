"""个人记忆系统需求补全（REQUIREMENTS G1/G3/G4/G5/G6）回归测试，全离线。"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.contracts.agents.retrieval import RetrievalFilters, RetrievalInput
from learnforge.contracts.enums import KnowledgeScope, RetrievalMethod
from learnforge.memory import decay
from learnforge.memory.daily import record
from learnforge.memory.files import append_root_memory, read_root_memory


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    return tmp_path / "mem"


def _recall(db_path, query, filters=None):
    from learnforge.agents.retrieval import RetrievalAgent

    out = RetrievalAgent(db_path=db_path).run(
        RetrievalInput(query=query, scopes=[KnowledgeScope.LOCAL],
                       method=RetrievalMethod.FULLTEXT, top_k=8, filters=filters)
    )
    return out.chunks


# ---------------- G4：重要性元信息 ----------------
def test_importance_ranking_and_kinds():
    # 决策/薄弱点比普通问答重要；缺省 kind 取默认值。
    assert decay.importance_for("decision") > decay.importance_for("qa")
    assert decay.importance_for("weak") > decay.importance_for("note")
    assert decay.importance_for(None) == config.MEMORY_IMPORTANCE_DEFAULT
    # progress/decision 进入慢衰减档（G8）。
    assert "progress" in config.MEMORY_STICKY_KINDS
    assert "decision" in config.MEMORY_STICKY_KINDS


def test_recorded_note_carries_importance_metadata(mem_dir, tmp_db):
    record(kind="decision", text="决定先攻并发再攻 Redis。", topic="plan", db_path=tmp_db)
    from learnforge.storage.repositories import ChunkRepository

    rows = ChunkRepository(db_path=tmp_db).conn.execute(
        "SELECT metadata FROM chunks WHERE kb_scope='local'"
    ).fetchall()
    assert any('"importance":' in r["metadata"] and '"kind": "decision"' in r["metadata"]
               for r in rows)


# ---------------- G5：按类型（kind）过滤召回 ----------------
def test_recall_filter_by_kind(mem_dir, tmp_db):
    record(kind="qa", text="Kafka 顺序消费要点。", topic="mq", db_path=tmp_db)
    record(kind="weak", text="Kafka 分区再均衡薄弱。", topic="mq", db_path=tmp_db)
    only_weak = _recall(tmp_db, "Kafka", filters=RetrievalFilters(kind="weak"))
    assert only_weak
    assert all((c.metadata or {}).get("kind") == "weak" for c in only_weak)


# ---------------- G6：写入去重 ----------------
def test_record_dedup_skips_duplicate(mem_dir, tmp_db):
    n1 = record(kind="qa", text="Q: 什么是乐观锁？\nA: 基于版本号。", topic="db", db_path=tmp_db)
    n2 = record(kind="qa", text="Q: 什么是乐观锁？\nA: 基于版本号。", topic="db", db_path=tmp_db)
    assert not n1.get("duplicate")
    assert n2.get("duplicate") is True
    from learnforge.storage.repositories import ChunkRepository

    rows = ChunkRepository(db_path=tmp_db).conn.execute(
        "SELECT 1 FROM chunks WHERE kb_scope='local'"
    ).fetchall()
    assert len(rows) == 1  # 重复内容只索引一次


# ---------------- G1：稳定规则层受控写入 MEMORY.md ----------------
def test_append_root_memory_writes_and_dedups(mem_dir):
    assert append_root_memory("回答尽量用中文，给可核验要点。") is True
    text = read_root_memory()
    assert "用户补充偏好" in text and "回答尽量用中文" in text
    # 已存在的同条目不重复堆积。
    assert append_root_memory("回答尽量用中文，给可核验要点。") is False


def test_maybe_remember_rule_only_on_explicit_trigger(mem_dir):
    from learnforge.agents.manager import ManagerAgent

    mgr = ManagerAgent()
    # 明确"记住…"→ 写入并抽取规则正文。
    rule = mgr.maybe_remember_rule("请记住我主攻后端面试，偏好简洁回答")
    assert rule and "后端" in rule
    assert "后端" in read_root_memory()
    # 非记住意图 → 不写。
    assert mgr.maybe_remember_rule("什么是 Redis？") is None
    # 过短的"记住了"不视为稳定偏好。
    assert mgr.maybe_remember_rule("记住了，谢谢") is None


# ---------------- G3：decision/progress 写入 daily ----------------
def test_remember_decision_and_progress(mem_dir, tmp_db):
    from learnforge.agents.manager import ManagerAgent

    mgr = ManagerAgent(db_path=tmp_db)
    assert mgr.remember_decision("学习路径已调整（modify）。") is not None
    assert mgr.remember_progress("本周完成并发模块。", topic="concurrency") is not None
    from learnforge.storage.repositories import ChunkRepository

    rows = ChunkRepository(db_path=tmp_db).conn.execute(
        "SELECT metadata FROM chunks WHERE kb_scope='local'"
    ).fetchall()
    kinds = {r["metadata"] for r in rows}
    assert any('"kind": "decision"' in m for m in kinds)
    assert any('"kind": "progress"' in m for m in kinds)
