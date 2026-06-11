"""会话级上下文压缩（Claude Code 式 compaction）：turn 结束按 token 阈值触发，pinned 重要结果永不压。

全离线、确定性（无 key → 字符估算 + JSON 回退摘要）。覆盖：
  - tokens.count_tokens / truncate_to_tokens
  - record_turn 的 token-阈值触发（不按轮数）、MIN_RECENT 下限
  - 重要结果 pin 保护：永不折叠、置顶注入、FIFO cap
  - turn_is_important 判定信号
"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.agents.manager import ManagerAgent
from learnforge.memory.tokens import count_tokens, truncate_to_tokens
from learnforge.storage.repositories import SessionStateRepository


@pytest.fixture
def small_thresholds(monkeypatch):
    """把阈值调小，方便用少量内容触发 compaction。"""
    monkeypatch.setattr(config, "SESSION_COMPACTION_THRESHOLD_TOKENS", 200)
    monkeypatch.setattr(config, "SESSION_COMPACTION_TARGET_TOKENS", 100)
    monkeypatch.setattr(config, "SESSION_MIN_RECENT_ROUNDS", 2)


def _pinned(tmp_db, sid):
    st = SessionStateRepository(db_path=tmp_db).get(sid) or {}
    return (st.get("active_task") or {}).get("pinned") or []


# ----------------------------- tokens 基元 -----------------------------

def test_count_tokens_empty_and_monotonic():
    assert count_tokens("") == 0
    assert count_tokens("hello world") > 0
    assert count_tokens("a" * 100) <= count_tokens("a" * 200)


def test_truncate_to_tokens_respects_budget():
    text = "word " * 500
    out = truncate_to_tokens(text, 50)
    assert count_tokens(out) <= 50
    assert out.endswith("〕")  # 带截断标记
    assert truncate_to_tokens("just a few words", 1000) == "just a few words"


# ------------------------- 会话级 compaction 触发 -------------------------

def test_compaction_triggers_on_token_threshold(small_thresholds, tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    for i in range(6):
        mgr.record_turn("s1", f"问题{i}" + "啊" * 200, f"回答{i}" + "哦" * 200)
    st = SessionStateRepository(db_path=tmp_db).get("s1")
    assert st["summary"]  # 触发了压缩（离线走 JSON 回退摘要）
    # 按 token 折叠：recent 数被压到 < 灌入轮数，但不低于 MIN_RECENT 下限。
    assert config.SESSION_MIN_RECENT_ROUNDS <= len(st["recent_messages"]) < 6


def test_no_compaction_below_threshold(tmp_db):
    # 默认阈值 ≈10 万（上限 128K 的 80%）；两轮短内容远不触发 → 无摘要、原文全留。
    mgr = ManagerAgent(db_path=tmp_db)
    mgr.record_turn("s5", "什么是乐观锁？", "基于版本号的并发控制。")
    mgr.record_turn("s5", "悲观锁呢？", "加锁阻塞。")
    st = SessionStateRepository(db_path=tmp_db).get("s5")
    assert not st["summary"]
    assert len(st["recent_messages"]) == 2


def test_min_recent_rounds_preserved(small_thresholds, tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    for i in range(10):
        mgr.record_turn("s4", f"问题{i}" + "啊" * 300, f"答{i}" + "哦" * 300)
    st = SessionStateRepository(db_path=tmp_db).get("s4")
    assert len(st["recent_messages"]) >= config.SESSION_MIN_RECENT_ROUNDS


# --------------------------- 重要结果 pin 保护 ---------------------------

def test_pinned_important_survives_compaction(small_thresholds, tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    mgr.record_turn("s2", "诊断我的弱点", "你的弱点是并发与锁机制", important=True)
    # 之后大量闲聊把普通轮压走。
    for i in range(8):
        mgr.record_turn("s2", f"闲聊{i}" + "啊" * 200, f"回应{i}" + "哦" * 200)
    pinned = _pinned(tmp_db, "s2")
    assert any("并发" in p.get("reply", "") for p in pinned)  # 重要结果在 pinned 区
    rendered = mgr.load_session_memory("s2")
    assert "并发" in rendered and "重要结果·固定" in rendered  # 渲染置顶、未被压缩掉


def test_pinned_capped_fifo(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "SESSION_MAX_PINNED", 3)
    mgr = ManagerAgent(db_path=tmp_db)
    for i in range(5):
        mgr.record_turn("s3", f"重要{i}", f"结果{i}", important=True)
    pinned = _pinned(tmp_db, "s3")
    assert len(pinned) == 3
    assert "结果4" in pinned[-1]["reply"]                       # 保留最新
    assert all("结果0" not in p["reply"] for p in pinned)       # 最旧被 FIFO 丢弃


def test_non_important_turn_not_pinned(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    mgr.record_turn("s6", "普通问题", "普通回答")  # important 默认 False
    assert _pinned(tmp_db, "s6") == []


# --------------------------- turn_is_important 判定 ---------------------------

def test_turn_is_important_signals():
    assert ManagerAgent.turn_is_important(citations=[{"id": 1}]) is True
    assert ManagerAgent.turn_is_important(citations=[], plan=[{"agent": "diagnosis"}]) is True
    assert ManagerAgent.turn_is_important(citations=[], plan=[{"agent": "planning"}]) is True
    assert ManagerAgent.turn_is_important(citations=[], plan=[{"agent": "mock"}]) is True
    assert ManagerAgent.turn_is_important(citations=[], plan=[{"agent": "qa"}]) is False
    assert ManagerAgent.turn_is_important() is False
