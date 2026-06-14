"""记忆日志面板（memory/log.py + 流水线接线）测试，全离线。"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.memory import daily
from learnforge.memory.log import INJECT, READ, WRITE, MemoryLog


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    return tmp_path / "mem"


# ---------------- 收集器本身 ----------------
def test_log_collects_and_summarizes():
    log = MemoryLog()
    log.record(READ, "加载稳定记忆", "已加载 MEMORY.md（5 行）")
    log.record(INJECT, "注入稳定记忆", "进入 prompt", count=1)
    log.record(READ, "搜索长期记忆", "命中 3 条")
    log.record(INJECT, "注入长期记忆", "采用 2 条", count=2)
    log.record(WRITE, "提取候选记忆", "类型 qa")
    log.record(WRITE, "保存记忆", "成功，类型为 qa")

    s = log.summary()
    assert s["loaded_memory_md"] is True
    assert s["searched_daily"] is True
    assert s["injected_count"] == 3  # 1 + 2
    assert s["extracted_candidate"] is True
    assert s["saved"] is True
    # 渲染行带时间与原因。
    lines = log.render_lines()
    assert any("搜索长期记忆：命中 3 条" in ln for ln in lines)


def test_log_reset_and_empty():
    log = MemoryLog()
    log.record(READ, "加载稳定记忆", "为空，跳过")
    log.reset()
    assert log.events == []
    assert log.render_lines() == ["（本轮无记忆操作）"]
    assert log.summary()["loaded_memory_md"] is False


# ---------------- 接线：daily.record 写入面板 ----------------
def test_record_logs_save_then_skip_duplicate(mem_dir, tmp_db):
    from learnforge.memory.log import MEMORY_LOG

    MEMORY_LOG.reset()
    daily.record(kind="weak", text="薄弱点：AQS 理解不牢。", topic="concurrency", db_path=tmp_db)
    daily.record(kind="weak", text="薄弱点：AQS 理解不牢。", topic="concurrency", db_path=tmp_db)
    actions = [(e.action, e.result) for e in MEMORY_LOG.events]
    assert ("保存记忆", "成功，类型为 weak") in actions
    assert any(a == "跳过保存" and "重复" in r for a, r in actions)


# ---------------- 接线：begin_memory_turn 读取流水线 ----------------
def test_begin_memory_turn_logs_read_pipeline(mem_dir, tmp_db):
    from learnforge.agents.manager import ManagerAgent
    from learnforge.memory.files import append_root_memory
    from learnforge.memory.log import MEMORY_LOG
    from learnforge.storage.repositories import SessionStateRepository

    append_root_memory("回答用中文，给可核验要点。")
    SessionStateRepository(db_path=tmp_db).upsert(
        "s1", "早期摘要", {}, [{"user": "hi", "reply": "ok"}]
    )
    mgr = ManagerAgent(db_path=tmp_db)
    mgr.begin_memory_turn("乐观锁和悲观锁？", "s1")

    # begin_memory_turn：读取阶段——加载 MEMORY.md（注入）+ 读会话记忆（仅 READ，不在此注入）。
    s = MEMORY_LOG.summary()
    assert s["loaded_memory_md"] is True
    assert s["loaded_session"] is True
    assert s["injected_count"] >= 1  # 稳定记忆已注入

    # 会话上下文的**真实**注入发生在 execute_dynamic 的 build_session_context（不再在 begin 谎报，§7）。
    mgr.build_session_context("s1")
    assert MEMORY_LOG.summary()["injected_count"] >= 2  # 稳定 + 会话上下文


def test_retrieval_local_logs_search_and_inject(mem_dir, tmp_db):
    from learnforge.agents.retrieval import RetrievalAgent
    from learnforge.contracts.agents.retrieval import RetrievalInput
    from learnforge.contracts.enums import KnowledgeScope, RetrievalMethod
    from learnforge.memory.log import MEMORY_LOG

    daily.record(kind="qa", text="Q: Redis 为什么快？\nA: 纯内存 + 单线程。",
                 topic="redis", db_path=tmp_db)
    MEMORY_LOG.reset()
    RetrievalAgent(db_path=tmp_db).run(
        RetrievalInput(query="Redis", scopes=[KnowledgeScope.LOCAL],
                       method=RetrievalMethod.FULLTEXT, top_k=3)
    )
    assert MEMORY_LOG.summary()["searched_daily"] is True
    assert any(e.category == INJECT and "长期记忆" in e.action for e in MEMORY_LOG.events)


# ---------------- 演示脚本冒烟 ----------------
def test_demo_panel_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    from learnforge.memory import demo_panel

    demo_panel.main(["--db", str(tmp_path / "demo.db")])  # 不抛异常即通过
