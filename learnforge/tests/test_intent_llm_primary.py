"""反转后的 LLM 主判路径：resolve() 优先用 LLM 判意图（强模型多字段），失败/无 key 退回规则。

LLM 判 capability + is_continuation + is_aside + topic/role + 澄清；槽位细节走确定性层。
用 monkeypatch 把 `_llm_judge` 换成固定返回，**不打真实网络**、可回归。覆盖：
  ① capability 正确映射成 IntentFrame（带 llm_intent 信号 + 确定性槽位）；
  ② 承接(is_continuation)→context_carry、旁支(is_aside)→aside、note/composite/unclear；
  ③ LLM 给的 topic/role/clarification 生效，needs_clarification=false 清掉规则反问；
  ④ LLM 判不出(_llm_judge→None) 时退回确定性规则链；
  ⑤ 显式命令 / pending 澄清仍走快路，不调 LLM。
"""

from __future__ import annotations

import pytest

from learnforge.contracts.intent import Capability
from learnforge.intent import IntentResolver
from learnforge.intent.resolver import _IntentJudgment


@pytest.fixture
def llm_on(monkeypatch):
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", True, raising=False)


def _judge(monkeypatch, fn):
    monkeypatch.setattr(IntentResolver, "_llm_judge", staticmethod(fn))


# ----------------------------------------------------------------- ① 映射 + 槽位
def test_llm_mock_with_role(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(capability="mock", target_role="RAG 实习"))
    f = IntentResolver().resolve("拿我项目练一练")
    assert f.capability == Capability.MOCK and "llm_intent" in f.signals
    assert f.slots.get("target_role") == "RAG 实习"


def test_llm_diagnosis(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(capability="diagnosis"))
    assert IntentResolver().resolve("帮我复盘下").capability == Capability.DIAGNOSIS


# ----------------------------------------------------------------- ② 多轮关系 + 特殊能力
def test_llm_continuation_sets_context_carry(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(capability="planning", is_continuation=True))
    f = IntentResolver().resolve("再加上并发", {"last_capability": "planning"})
    assert f.capability == Capability.PLANNING and "context_carry" in f.signals


def test_llm_aside_signal(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(capability="qa", is_aside=True))
    f = IntentResolver().resolve("顺便问什么是一致性哈希", {"last_capability": "planning"})
    assert "aside" in f.signals and "context_carry" not in f.signals


def test_llm_note(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(capability="note", topic="redis"))
    f = IntentResolver().resolve("把这些整理成 md")
    assert f.capability == Capability.QA and f.action == "note"
    assert "note_gen" in f.signals and f.slots.get("topic") == "redis"


def test_llm_composite(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(capability="composite"))
    f = IntentResolver().resolve("快面试了帮我冲刺")
    assert f.capability == Capability.COMPOSITE and f.handoffs == ["diagnosis", "planning"]


def test_llm_unclear_uses_its_clarification(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(
        capability="unclear", needs_clarification=True, clarification="你想做点什么？"))
    f = IntentResolver().resolve("emmm")
    assert f.clarification == "你想做点什么？" and f.pending_slot == "intent"


# ----------------------------------------------------------------- ③ 澄清以 LLM 为准
def test_llm_no_clarification_clears_rule_gate(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(capability="mock", needs_clarification=False))
    f = IntentResolver().resolve("我想面试但没说方向")
    assert f.clarification is None


# ----------------------------------------------------------------- ④ 失败退回规则
def test_llm_none_falls_back_to_rules(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: None)
    f = IntentResolver().resolve("诊断我的弱点")
    assert f.capability == Capability.DIAGNOSIS and "llm_intent" not in f.signals


def test_llm_invalid_capability_falls_back(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: _IntentJudgment(capability="garbage"))
    f = IntentResolver().resolve("考考我")  # 非法能力 → None → 规则关键词 → mock
    assert f.capability == Capability.MOCK and "llm_intent" not in f.signals


# ----------------------------------------------------------------- ⑤ 快路不被接管
def test_explicit_command_bypasses_llm(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: pytest.fail("显式命令不应调用 LLM"))
    f = IntentResolver().resolve("/plan 两周补齐 redis")
    assert f.capability == Capability.PLANNING and "explicit" in f.signals


def test_pending_clarify_bypasses_llm(monkeypatch, llm_on):
    _judge(monkeypatch, lambda t, c: pytest.fail("澄清续接不应调用 LLM"))
    pending = {"capability": "planning", "pending_slot": "goal", "slots": {}}
    f = IntentResolver().resolve("两周内补齐 redis", {"pending_intent": pending})
    assert f.capability == Capability.PLANNING
