"""memory 文件层（MEMORY.md + daily markdown）单测。"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.memory import files


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    d = tmp_path / "mem"
    monkeypatch.setattr(config, "MEMORY_DIR", str(d))
    return d


def test_read_root_memory_creates_template(mem_dir):
    text = files.read_root_memory()
    assert "MEMORY" in text
    assert (mem_dir / "MEMORY.md").exists()


def test_append_daily_writes_and_parses_back(mem_dir):
    note = files.append_daily(kind="qa", text="Q: 什么是 Redis？\nA: 内存数据库。", topic="redis")
    # 文件按当日命名生成。
    day = note["date"][:10]
    assert (mem_dir / f"{day}.md").exists()
    # note 块可被 iter_daily_notes 解析回。
    parsed = files.iter_daily_notes()
    assert len(parsed) == 1
    assert parsed[0]["kind"] == "qa"
    assert parsed[0]["topic"] == "redis"
    assert "Redis" in parsed[0]["text"]


def test_iter_handles_multiple_notes_and_days(mem_dir):
    files.append_daily(kind="qa", text="关于 CAS 的结论。", topic="concurrency",
                       date="2026-05-01")
    files.append_daily(kind="weak", text="JWT 薄弱。", topic="auth", date="2026-05-02")
    files.append_daily(kind="qa", text="第二条 CAS 笔记。", topic="concurrency",
                       date="2026-05-02")
    kinds = sorted(n["kind"] for n in files.iter_daily_notes())
    assert kinds == ["qa", "qa", "weak"]
