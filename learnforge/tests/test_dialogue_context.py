"""意图层上下文选择：anchors(①) + summary(④) 取代纯"最近 N 轮"窗口。

锁住"早于近窗的关键转折不丢"：转折轮作为 anchor 被钉住(主线存活时)，更旧上下文进 summary。
全部确定性、无 LLM——意图热路径可回归。
"""

from __future__ import annotations

import pytest

from learnforge.contracts.dialogue import DialogueTurn, ToolCall
from learnforge.storage.db import get_connection, init_db
from learnforge.storage.repositories import DialogueTurnRepository, SessionStateRepository


@pytest.fixture
def repo(tmp_path):
    p = str(tmp_path / "d.db")
    init_db(p)
    return DialogueTurnRepository(conn=get_connection(p))


def _add(repo, text, cap, topic=None, artifacts=None, status="completed"):
    s = repo.next_seq("s")
    repo.add_turn(DialogueTurn(
        turn_id=f"t{s}", session_id="s", seq=s, role="user", text=text,
        capability=cap, topic=topic, status=status,
        tool_calls=[ToolCall(agent=cap)] if cap else [],
        artifacts=artifacts or [],
    ))


def test_anchor_preserves_pre_window_pivot(repo):
    """主线存活时，早于近 4 窗的转折轮被钉为 anchor(不丢)。"""
    _add(repo, "帮我排个RAG两周计划", "planning", "rag")   # t1 转折
    _add(repo, "什么是rerank", "qa", "rerank")
    _add(repo, "继续完善计划", "planning", "rag")
    _add(repo, "那HNSW呢", "qa")
    _add(repo, "TCP三次握手", "qa")                        # t5
    ctx = repo.derive_context("s")
    assert ctx["last_capability"] == "planning"
    anchor_texts = [a["text"] for a in ctx["anchors"]]
    recent_texts = [h["text"] for h in ctx["history"]]
    assert "帮我排个RAG两周计划" in anchor_texts            # 转折被钉住
    assert "帮我排个RAG两周计划" not in recent_texts        # 且确实在近窗之外
    assert ctx["anchors"][0]["kind"] == "thread_start"


def test_summary_digest_fallback(repo):
    """无 session_state 摘要时，summary 回退为更旧轮的确定性 digest(主题/能力)。"""
    _add(repo, "帮我排个RAG计划", "planning", "rag")   # 早于近 4 窗
    _add(repo, "考考我kafka", "mock", "kafka")          # 早于近 4 窗
    for q in ("什么是A", "什么是B", "什么是C", "什么是D"):  # 近 4 窗占满
        _add(repo, q, "qa")
    ctx = repo.derive_context("s")
    assert "planning" in ctx["summary"] and "mock" in ctx["summary"]
    assert "rag" in ctx["summary"] and "kafka" in ctx["summary"]


def test_session_summary_preferred_over_digest(repo):
    """有 session_state.summary(LLM 折叠)时，优先用它而非 digest。"""
    _add(repo, "帮我排个RAG计划", "planning", "rag")
    for q in ("什么是A", "什么是B", "什么是C", "什么是D"):
        _add(repo, q, "qa")
    SessionStateRepository(conn=repo.conn).upsert("s", "用户在准备RAG算法实习面试")
    ctx = repo.derive_context("s")
    assert ctx["summary"] == "用户在准备RAG算法实习面试"


def test_anchor_expires_when_thread_decays(repo):
    """主线被 aside 衰减(≥3 连续旁支)后，不再钉 thread_start anchor；但 summary 仍留痕。"""
    _add(repo, "帮我排个RAG计划", "planning", "rag")   # 转折(早于近窗)
    for q in ("什么是A", "什么是B", "什么是C", "什么是D"):  # 4 连续旁支 → 主线失效
        _add(repo, q, "qa")
    ctx = repo.derive_context("s")
    assert ctx.get("last_capability") is None
    assert all(a["kind"] != "thread_start" for a in ctx["anchors"])
    assert "planning" in ctx["summary"]               # 仍能从概要看出做过 planning
