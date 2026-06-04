"""Prompt 拼装：assembler 的 retrieved/session 槽位已接通（§6c 有序加载 + 缓存边界）。

锁住"接通"不回退：证据/会话上下文经 assembler 有序槽位注入 **volatile 尾部**（稳定 prefix 之后），
而不是各 agent 手工拼进 user prompt（避免漂移与重复计费）。
"""

from __future__ import annotations

from learnforge.contracts.agents.qa import SynthesizerInput
from learnforge.contracts.agents.retrieval import Chunk
from learnforge.contracts.enums import ModelTier
from learnforge.memory.context_assembler import assemble_prompt


def test_assembler_orders_volatile_slots():
    """volatile 尾部顺序固定：retrieved → session → user_input。"""
    ap = assemble_prompt(retrieved="EVID", handoff_summary="SESS", user_input="ASK")
    vs = ap.volatile_suffix()
    assert vs.index("EVID") < vs.index("SESS") < vs.index("ASK")


def test_stable_prefix_excludes_volatile():
    """稳定 prefix（缓存段）只含 system/skill/constitution/memory，不含证据/会话/输入。"""
    ap = assemble_prompt(constitution="CONST", memory_summary="MEM",
                         retrieved="EVID", handoff_summary="SESS", user_input="ASK")
    prefix = ap.cacheable_prefix()
    assert "CONST" in prefix and "MEM" in prefix
    assert "EVID" not in prefix and "SESS" not in prefix and "ASK" not in prefix


def _fake_llm(monkeypatch, captured):
    from learnforge.llm import client
    from learnforge.llm.client import LLMResult, TokenUsage

    def fake_cs(prompt, schema, model_tier=ModelTier.SONNET, system=None, max_tokens=1024, **kw):
        captured["prompt"] = prompt
        captured["system"] = system
        return (schema(draft="ok", claims=[]),
                LLMResult(text="", tokens=TokenUsage(prompt=0, completion=0),
                          cost_usd=0.0, model_tier=model_tier))

    monkeypatch.setattr(client.LLM, "available", True, raising=False)
    monkeypatch.setattr(client.LLM, "complete_structured", fake_cs, raising=False)


def test_synthesizer_routes_evidence_through_slot(monkeypatch):
    """证据/项目上下文经槽位进 volatile（恰好一次）；稳定 prefix 进 system。"""
    from learnforge.agents.qa.synthesizer import SynthesizerAgent

    captured: dict = {}
    _fake_llm(monkeypatch, captured)
    SynthesizerAgent().run(SynthesizerInput(
        question="什么是RAG",
        retrieved=[Chunk(chunk_id="c1", text="RAG 是检索增强生成")],
        project_context="我在做客服问答系统",
    ))
    prompt, system = captured["prompt"], captured["system"]
    assert prompt.count("RAG 是检索增强生成") == 1           # 不重复拼接
    assert "客服问答系统" in prompt                           # session 槽位
    assert "Agent boundary" in (system or "")                # 稳定 prefix 在 system（缓存段）
    # 证据应排在问题之前（retrieved → user_input 顺序）。
    assert prompt.index("RAG 是检索增强生成") < prompt.index("什么是RAG")
