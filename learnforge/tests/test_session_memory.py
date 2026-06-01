"""短期 session_memory：最近 N 轮原文 + 溢出压缩 + memory_prefix（全离线）。"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.agents.manager import ManagerAgent
from learnforge.memory.base import MEMORY
from learnforge.storage.repositories import SessionStateRepository


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    return tmp_path / "mem"


def test_recent_rounds_capped_and_overflow_compressed(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    for i in range(7):
        mgr.record_turn("s1", f"问题{i}", f"回答{i}")
    st = SessionStateRepository(db_path=tmp_db).get("s1")
    assert len(st["recent_messages"]) == config.SESSION_RECENT_ROUNDS  # 仅留最近 6 轮
    assert st["summary"]  # 溢出轮已压成摘要（离线走 JSON 回退）
    assert "问题0" in st["summary"]  # 最早一轮进了摘要


def test_load_session_memory_renders_summary_and_recent(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    mgr.record_turn("s2", "什么是乐观锁？", "基于版本号的并发控制。")
    rendered = mgr.load_session_memory("s2")
    assert rendered and "乐观锁" in rendered


def test_load_session_memory_none_when_absent(tmp_db):
    assert ManagerAgent(db_path=tmp_db).load_session_memory("nope") is None


def test_memory_prefix_reads_memory_md(mem_dir):
    prefix = MEMORY.memory_prefix()
    assert "MEMORY" in prefix  # 读到 MEMORY.md（缺失时由模板创建）
    assert (mem_dir / "MEMORY.md").exists()
