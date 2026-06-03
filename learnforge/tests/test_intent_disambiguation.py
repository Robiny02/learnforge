"""顶层低置信消歧：意义不明 → 澄清 or 走 qa（离线确定性路径）。

固化三条：
  ① 普通问句 / 抽象题 / 噪声闲聊 → 照常 qa，不打扰（且不花 LLM）；
  ② 含糊求助（想做事没说清）→ 无 key 时反问一句，pending_slot=intent；
  ③ 反问的下一轮把回复整句重解析，路由到正确能力。
高置信路由（关键词/信号）不受影响。
"""

from __future__ import annotations

import pytest

from learnforge.contracts.intent import Capability
from learnforge.intent import IntentResolver


@pytest.fixture
def resolver():
    return IntentResolver()


@pytest.fixture(autouse=True)
def llm_off(monkeypatch):
    # 强制无 LLM，走确定性兜底，让"含糊→反问"可回归（反转后 resolve 默认 LLM 主判）。
    from learnforge.llm import client

    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    return client.LLM


# ----------------------------------------------------------------- ① 不打扰：问句/噪声 → qa
@pytest.mark.parametrize("text", [
    "学习的本质是什么", "怎么才能学好编程", "什么是 RAG", "redis 持久化怎么配",
    "在吗", "你好", "test",
])
def test_clear_or_noise_stays_qa(resolver, text):
    f = resolver.resolve(text)
    assert f.capability == Capability.QA
    assert f.clarification is None


# ----------------------------------------------------------------- ② 含糊求助 → 反问
@pytest.mark.parametrize("text", ["帮帮我", "不知道学啥", "我想变强", "随便", "怎么办", "帮我弄一下"])
def test_vague_request_clarifies_offline(resolver, llm_off, text):
    f = resolver.resolve(text)
    assert f.capability == Capability.QA      # 兜底能力仍是 qa
    assert f.clarification                     # 但主动反问
    assert f.pending_slot == "intent"
    assert "intent" in f.missing_required


def test_vague_clarification_lists_directions(resolver, llm_off):
    f = resolver.resolve("帮帮我")
    # 反问应给出可选方向，帮用户把意图说清。
    assert "计划" in f.clarification and "面试" in f.clarification


# ----------------------------------------------------------------- ③ 反问下一轮 → 重解析
@pytest.mark.parametrize("reply,cap", [
    ("我哪里薄弱", Capability.DIAGNOSIS),
    ("帮我排个 RAG 复习计划", Capability.PLANNING),
    ("考考我 redis", Capability.MOCK),
    ("什么是向量检索", Capability.QA),
])
def test_resume_after_intent_clarify_reroutes(resolver, llm_off, reply, cap):
    f1 = resolver.resolve("帮帮我")
    assert f1.pending_slot == "intent"
    f2 = resolver.resume_pending(f1.model_dump(), reply)
    assert f2.capability == cap


# ----------------------------------------------------------------- 高置信不受影响
@pytest.mark.parametrize("text,cap", [
    ("诊断我的弱点", Capability.DIAGNOSIS),
    ("帮我做个学习计划", Capability.PLANNING),
    ("考考我", Capability.MOCK),
    ("我面 RAG 算法实习", Capability.MOCK),
])
def test_high_confidence_routes_unchanged(resolver, text, cap):
    assert resolver.resolve(text).capability == cap


def test_llm_capability_returns_none_when_unavailable(llm_off):
    assert IntentResolver._llm_capability("帮帮我") is None
