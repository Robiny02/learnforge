"""阶段一回归：会话上下文（pinned/summary/recent/最近调用/附件引用）真正进入 QA prompt。

修复前的 bug：pinned/summary/recent 这套会话记忆已实现，但产物从未进入子 agent 的 prompt——
manager_plan 读出来写进 state['conversation_summary'] 后，execute_dynamic/dispatch/QAInput 都没消费。

本文件守护阶段一的接线 + 预算 + 面板诚实度。全离线、确定性（无 API key）。
"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.agents.manager import ManagerAgent
from learnforge.agents.qa.synthesizer import SynthesizerAgent
from learnforge.contracts.agents.qa import QAInput, SynthesizerInput
from learnforge.contracts.dialogue import DialogueTurn, ToolCall
from learnforge.memory.context_assembler import assemble_prompt
from learnforge.memory.session_context import build_session_context
from learnforge.memory.tokens import count_tokens
from learnforge.storage.repositories import (
    DialogueTurnRepository,
    SessionStateRepository,
)

DIAG = "你的薄弱点是并发与锁机制：可重入锁与 CAS 还没吃透"


@pytest.fixture
def small_thresholds(monkeypatch):
    monkeypatch.setattr(config, "SESSION_COMPACTION_THRESHOLD_TOKENS", 200)
    monkeypatch.setattr(config, "SESSION_COMPACTION_TARGET_TOKENS", 100)
    monkeypatch.setattr(config, "SESSION_MIN_RECENT_ROUNDS", 2)


# ----------------------------- 装配器：内容齐全 -----------------------------

def test_build_session_context_includes_pinned_summary_recent(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    sid = "sc-1"
    mgr.record_turn(sid, "诊断我的弱点", DIAG, important=True)      # → pinned
    mgr.record_turn(sid, "再问一句", "好的，普通回答")              # → recent
    SessionStateRepository(db_path=tmp_db).upsert(                  # 注入一条早期摘要
        sid, "目标：准备后端面试", _state(tmp_db, sid)["active_task"],
        _state(tmp_db, sid)["recent_messages"])

    sctx = build_session_context(sid, db_path=tmp_db)
    kinds = {s.kind for s in sctx.sections}
    assert {"pinned", "summary", "recent"} <= kinds
    rendered = sctx.render()
    assert "并发与锁" in rendered          # pinned 结论在装配结果里
    assert "准备后端面试" in rendered      # 早期摘要在装配结果里


# ------------------- 核心回归：compaction 后 pinned 仍进入 QA -------------------

def test_pinned_survives_compaction_and_reaches_context(small_thresholds, tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    sid = "sc-2"
    mgr.record_turn(sid, "诊断我的弱点", DIAG, important=True)       # 重要结果 → pinned
    for i in range(8):                                              # 灌满触发 compaction
        mgr.record_turn(sid, f"问题{i}" + "啊" * 120, f"答{i}" + "哦" * 120)

    rendered = build_session_context(sid, db_path=tmp_db).render()
    assert "并发与锁" in rendered  # 压缩后重要诊断结论仍可被后续轮引用


def test_session_context_reaches_qa_input(small_thresholds, tmp_db, monkeypatch):
    """证明会话上下文真的抵达 QA 边界：捕获 dispatch 传给 qa.run 的 QAInput。"""
    mgr = ManagerAgent(db_path=tmp_db)
    sid = "sc-3"
    mgr.record_turn(sid, "诊断我的弱点", DIAG, important=True)

    captured = {}
    real_run = mgr.qa.run

    def _spy(payload: QAInput):
        captured["session_context"] = payload.session_context
        return real_run(payload)

    monkeypatch.setattr(mgr.qa, "run", _spy)
    mgr.execute_dynamic("什么是数据库索引", session_id=sid)

    assert "session_context" in captured, "QA 未被调用"
    assert "并发与锁" in captured["session_context"]  # pinned 结论进入了 QAInput.session_context


def test_session_context_reaches_planning_input(tmp_db, monkeypatch):
    """planning 也消费会话上下文：捕获 dispatch 传给 planning.run 的 PlanningInput。"""
    mgr = ManagerAgent(db_path=tmp_db)
    captured = {}
    real_run = mgr.planning.run

    def _spy(payload):
        captured["session_context"] = payload.session_context
        return real_run(payload)

    monkeypatch.setattr(mgr.planning, "run", _spy)
    mgr.dispatch("planning", "生成学习计划", {"session_context": "【重要结果·固定】并发与锁是薄弱点"})

    assert "并发与锁" in captured.get("session_context", "")


def test_session_context_reaches_synthesizer_prompt():
    """证明会话上下文进入实际 LLM prompt 串：经 _session_block → assemble_prompt 的 volatile 尾部。"""
    payload = SynthesizerInput(
        question="什么是索引",
        session_context="【重要结果·固定】\n· 结论：并发与锁机制是薄弱点",
    )
    block = SynthesizerAgent._session_block(payload)
    assert "并发与锁" in block

    assembled = assemble_prompt(handoff_summary=block, user_input="什么是索引")
    assert "并发与锁" in assembled.volatile_suffix()  # 在送进模型的串里
    assert "并发与锁" not in assembled.cacheable_prefix()  # 不污染可缓存 prefix（§6c）


# ------------------- 最近 tool/子 agent 投影 + 附件引用 -------------------

def test_recent_projection_reflects_called_agents_and_attachments(tmp_db):
    repo = DialogueTurnRepository(db_path=tmp_db)
    sid = "sc-4"
    repo.add_turn(DialogueTurn(
        turn_id="t1", session_id=sid, seq=1, role="user", text="诊断我的弱点",
        tool_calls=[ToolCall(agent="diagnosis")]))
    repo.add_turn(DialogueTurn(
        turn_id="t2", session_id=sid, seq=2, role="user", text="看看我的简历",
        tool_calls=[ToolCall(agent="qa")],
        artifacts=[{"kind": "document", "ref": "doc-abc123", "filename": "resume.md"}]))

    rendered = build_session_context(sid, db_path=tmp_db).render()
    assert "diagnosis" in rendered            # 最近调用过的子 agent
    assert "resume.md" in rendered            # 可引用附件文件名
    assert "doc-abc123" in rendered           # 以及 document_id（供"刚才那个文件"跨轮定位）


# ----------------------------- 预算：不无限增长 -----------------------------

def test_session_context_budget_bounded(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "SESSION_CONTEXT_MAX_TOKENS", 300)
    repo = SessionStateRepository(db_path=tmp_db)
    sid = "sc-5"
    huge_pins = [{"user": "x" * 400, "reply": "并发" * 400} for _ in range(8)]
    repo.upsert(sid, "目标" * 2000, {"pinned": huge_pins},
                [{"user": "q" * 400, "reply": "a" * 400} for _ in range(10)])

    sctx = build_session_context(sid, db_path=tmp_db)
    # 整体不超全局天花板（容许 marker 余量）；逐段也各自被裁过。
    assert count_tokens(sctx.render()) <= config.SESSION_CONTEXT_MAX_TOKENS + 8


# ----------------------------- 面板诚实度（§7）-----------------------------

def test_empty_session_no_phantom_injection(tmp_db):
    """无历史 → 装配为空、不渲染、不会谎报注入；面板也无 session 来源。"""
    from learnforge.memory.log import prompt_load_overview

    sctx = build_session_context("nobody", db_path=tmp_db)
    assert sctx.is_empty()
    assert sctx.render() == ""

    sources = prompt_load_overview("nobody", db_path=tmp_db)
    assert not any(s["name"].startswith("session:") for s in sources)


def test_panel_reports_real_sections_with_tokens(tmp_db):
    """有历史 → 面板逐段（pinned/recent…）报告，且 token > 0（真实进入 prompt 的来源）。"""
    from learnforge.memory.log import prompt_load_overview

    mgr = ManagerAgent(db_path=tmp_db)
    sid = "sc-6"
    mgr.record_turn(sid, "诊断我的弱点", DIAG, important=True)

    sources = {s["name"]: s for s in prompt_load_overview(sid, db_path=tmp_db)}
    assert "session:pinned" in sources
    assert sources["session:pinned"]["tokens"] > 0


# ----------------------------- Step G：面板镜像固定 8 层 Prompt Stack -----------------------------

def test_panel_mirrors_eight_layer_stack(tmp_db):
    """面板逐层对应 assembler 的固定 Prompt Stack：System/Project Guide + 本轮 agent 的 Active Skill/Tool Registry。"""
    from learnforge.contracts.dialogue import DialogueTurn, ToolCall
    from learnforge.memory.log import prompt_load_overview

    repo = DialogueTurnRepository(db_path=tmp_db)
    sid = "sc-g1"
    repo.add_turn(DialogueTurn(turn_id="g1", session_id=sid, seq=1, role="user",
                               text="什么是索引", capability="qa",
                               tool_calls=[ToolCall(agent="qa")]))

    rows = prompt_load_overview(sid, db_path=tmp_db, user_input="什么是索引")
    by_layer = {r["layer"]: r for r in rows}
    assert by_layer[1]["kind"] == "System"
    assert by_layer[2]["kind"] == "Project Guide" and by_layer[2]["tokens"] > 0
    # 本轮跑的是 qa → Active Skill / Tool Registry 取 qa 的 skill。
    assert by_layer[4]["kind"] == "Active Skill" and by_layer[4]["name"] == "qa.shell.v1"
    assert by_layer[5]["kind"] == "Tool Registry"
    assert by_layer[8]["kind"] == "User Input" and by_layer[8]["tokens"] > 0
    # 每行都带 token 与层序，且按层序单调（可在前端按 stack 顺序渲染）。
    layers = [r["layer"] for r in rows]
    assert layers == sorted(layers)
    assert all(isinstance(r["tokens"], int) for r in rows)


def test_panel_active_skill_omitted_without_agent(tmp_db):
    """无可判定子 agent（无 dialogue 轮）→ 不臆造 Active Skill/Tool Registry，但稳定层仍在。"""
    from learnforge.memory.log import prompt_load_overview

    rows = prompt_load_overview("sc-g2", db_path=tmp_db)
    kinds = {r["kind"] for r in rows}
    assert "System" in kinds and "Project Guide" in kinds
    assert "Active Skill" not in kinds and "Tool Registry" not in kinds  # 不臆造


def _state(db_path, sid):
    return SessionStateRepository(db_path=db_path).get(sid) or {"active_task": {}, "recent_messages": []}
