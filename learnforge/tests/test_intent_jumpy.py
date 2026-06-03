"""用户"跳脱"鲁棒性：突然插入八股 / 让生成 md，意图仍准、主线不被污染（离线确定性）。

固化：
  ① 生成 md/笔记/文档 = 自包含 note 请求，识别并路由到笔记链路；
  ② 自包含八股问句（哪怕带承接词"再问下"）不被多轮上下文劫持，照常 QA；
  ③ 旁支插入不污染主线意图（粘住 last_capability），连续多次旁支后主线才失效；
  ④ 真正的承接句仍正确借用主线。
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
    from learnforge.llm import client

    monkeypatch.setattr(client.LLM, "available", False, raising=False)


# ----------------------------------------------------------------- ① 生成 md/笔记
@pytest.mark.parametrize("text", [
    "帮我生成一个 redis 的 md 笔记", "写一份并发的学习笔记", "整理成文档", "总结成 markdown",
])
def test_note_request_recognized(resolver, text):
    assert S.is_note_request(text)
    f = resolver.resolve(text)
    assert "note_gen" in f.signals and f.action == "note"


@pytest.mark.parametrize("text", ["做个学习计划", "诊断我的弱点", "什么是 markdown"])
def test_not_note_request(text):
    assert not S.is_note_request(text)


# ----------------------------------------------------------------- ② 自包含判定
def test_is_self_contained():
    assert S.is_self_contained("什么是一致性哈希")
    assert S.is_self_contained("为什么用 AOF")
    assert S.is_self_contained("生成一份 md 笔记")
    assert not S.is_self_contained("再深入讲讲")     # 省略式承接，非自包含
    assert not S.is_self_contained("再加上并发")      # 追加，非自包含


# ----------------------------------------------------------------- ③ 八股插入不被劫持
@pytest.mark.parametrize("text", [
    "什么是一致性哈希", "为什么用 AOF 不用 RDB", "再问下，HTTP 和 RPC 的区别",
    "生成一份 redis 笔记",
])
def test_insertion_not_hijacked_by_thread(resolver, text):
    f = resolver.resolve(text, {"last_capability": "planning", "last_topic": "redis"})
    assert "context_carry" not in f.signals          # 不借用主线
    assert f.capability == Capability.QA             # 照常问答/笔记


# ----------------------------------------------------------------- ④ 承接仍生效
def test_genuine_continuation_still_carries(resolver):
    f = resolver.resolve("再加上并发", {"last_capability": "planning"})
    assert f.capability == Capability.PLANNING and "context_carry" in f.signals


# ----------------------------------------------------------------- server：旁支不污染主线
def _client():
    from fastapi.testclient import TestClient

    from learnforge.app.server import api
    return TestClient(api)


def _agent(r):
    return (r.get("plan") or [{}])[-1].get("agent")


def test_server_insertion_does_not_pollute_thread(monkeypatch):
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    c = _client()
    sid = "jumpy-1"

    def post(t):
        return c.post("/ui/chat", json={"text": t, "mode": "qa", "session_id": sid}).json()

    assert _agent(post("帮我做 redis 复习计划")) == "planning"   # 开主线
    assert _agent(post("什么是一致性哈希")) == "qa"               # 插入八股（旁支）
    assert _agent(post("生成一份 md 笔记")) == "qa"               # 插入生成（旁支）
    assert _agent(post("再加上并发")) == "planning"              # 承接主线 → 仍 planning


def test_server_thread_decays_after_aside_streak(monkeypatch):
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    c = _client()
    sid = "jumpy-2"

    def post(t):
        return c.post("/ui/chat", json={"text": t, "mode": "qa", "session_id": sid}).json()

    post("帮我做 kafka 复习计划")
    for q in ("什么是分区", "什么是副本", "什么是 ISR"):   # 连续 3 个旁支
        post(q)
    assert _agent(post("再加上消费者")) == "qa"           # 主线已失效 → 不再误带 planning
