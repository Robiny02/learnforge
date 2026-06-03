"""多轮承接/切换：靠上文判断意图（跨能力 topic 切换/延续）——离线确定性路径。

固化：
  ① 无自身关键词但承接/切换上文 → 借上一轮能力（planning 省略追加、qa 换主题、mock 续接）；
  ② 自身有关键词 / 明显新问题 → 不被上文劫持；无上下文 → 不回归（仍 qa）；
  ③ server 端按借用到的能力显式路由（多轮端到端）。
"""

from __future__ import annotations

import pytest

from learnforge.contracts.intent import Capability
from learnforge.intent import IntentResolver
from learnforge.intent import slots as S


@pytest.fixture
def resolver():
    return IntentResolver()


@pytest.fixture(autouse=True)
def llm_off(monkeypatch):
    # 强制无 LLM，走确定性规则借用，可回归。
    from learnforge.llm import client

    monkeypatch.setattr(client.LLM, "available", False, raising=False)


# ----------------------------------------------------------------- 承接/切换检测
def test_continuation_and_switch_helpers():
    assert S.is_continuation("再加上并发") and S.is_continuation("那 rerank 呢")
    assert not S.is_continuation("什么是 RAG")
    switched, topic = S.detect_topic_switch("换成 kafka")
    assert switched and topic == "kafka"
    assert S.extract_known_topic("聊聊 redis 持久化") == "redis"
    assert S.extract_known_topic("随便说点啥") is None  # 不返回剥词残渣


# ----------------------------------------------------------------- 借用上文
@pytest.mark.parametrize("text,ctx,cap", [
    ("再加上并发", {"last_capability": "planning"}, Capability.PLANNING),
    ("改成 kafka", {"last_capability": "planning"}, Capability.PLANNING),
    ("那 rerank 呢", {"last_capability": "qa"}, Capability.QA),
    ("再细看一下", {"last_capability": "diagnosis"}, Capability.DIAGNOSIS),
    ("换 kafka", {"active_mock": True}, Capability.MOCK),
    ("再来一题", {"active_mock": True}, Capability.MOCK),
])
def test_carry_from_context(resolver, text, ctx, cap):
    f = resolver.resolve(text, ctx)
    assert f.capability == cap
    assert "context_carry" in f.signals


def test_topic_switch_injects_topic(resolver):
    f = resolver.resolve("改成 kafka", {"last_capability": "planning"})
    assert f.slots.get("topic") == "kafka"
    assert "topic_switch" in f.signals


def test_carry_uses_last_topic_when_message_has_none(resolver):
    f = resolver.resolve("再深入讲讲", {"last_capability": "qa", "last_topic": "redis"})
    assert f.capability == Capability.QA
    assert f.slots.get("topic") == "redis"


# ----------------------------------------------------------------- 不被劫持 / 不回归
def test_own_keyword_beats_context(resolver):
    # 自带能力关键词 → 走自己的，不被上文劫持。
    f = resolver.resolve("诊断我的弱点", {"last_capability": "qa"})
    assert f.capability == Capability.DIAGNOSIS


def test_clear_question_not_hijacked(resolver):
    f = resolver.resolve("什么是 RAG", {"last_capability": "planning"})
    assert f.capability == Capability.QA
    assert "context_carry" not in f.signals


def test_no_context_no_regression(resolver):
    # 无上下文时承接句仍落 qa（不凭空借用）。
    f = resolver.resolve("再加上并发")
    assert f.capability == Capability.QA
    assert "context_carry" not in f.signals


# ----------------------------------------------------------------- server 端到端多轮
def _client():
    from fastapi.testclient import TestClient

    from learnforge.app.server import api
    return TestClient(api)


def test_server_multiturn_planning_then_ellipsis():
    c = _client()
    sid = "mt-plan"
    c.post("/ui/chat", json={"text": "帮我做个 redis 复习计划", "mode": "qa", "session_id": sid})
    r = c.post("/ui/chat", json={"text": "再加上并发", "mode": "qa", "session_id": sid}).json()
    assert r["plan"][-1]["agent"] == "planning"


def test_server_multiturn_diagnosis_then_carry():
    c = _client()
    sid = "mt-diag"
    c.post("/ui/chat", json={"text": "诊断我的弱点", "mode": "qa", "session_id": sid})
    r = c.post("/ui/chat", json={"text": "再细看一下", "mode": "qa", "session_id": sid}).json()
    assert r["plan"][-1]["agent"] == "diagnosis"


def test_server_multiturn_qa_followup_stays_qa():
    c = _client()
    sid = "mt-qa"
    c.post("/ui/chat", json={"text": "什么是RAG", "mode": "qa", "session_id": sid})
    r = c.post("/ui/chat", json={"text": "那 rerank 呢", "mode": "qa", "session_id": sid}).json()
    assert r["plan"][-1]["agent"] == "qa"
