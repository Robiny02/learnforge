"""统一意图层 resolver/slots 的表驱动回归（全离线，无 API key）。

只断言**结构契约**：每条意图短语 → 期望的 capability / action / 关键槽位，以及
自然语言里抽取的面试上下文（岗位/JD/简历/难度/轮次）。不评判 LLM 输出质量。
"""

from __future__ import annotations

import pytest

from learnforge.contracts.intent import Capability
from learnforge.intent import IntentResolver
from learnforge.intent import slots as S


@pytest.fixture(autouse=True)
def _llm_off(monkeypatch):
    # 反转后 resolve() 默认 LLM 主判；此处关掉以测确定性规则兜底链（本文件断言的就是它）。
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)


@pytest.fixture
def resolver():
    return IntentResolver()


# ----------------------------------------------------------------- 能力路由
@pytest.mark.parametrize("text,cap", [
    ("考考我", Capability.MOCK),
    ("面试我 Python 并发", Capability.MOCK),
    ("我面 RAG 算法实习，拿我项目拷打我", Capability.MOCK),   # 无 mock 关键词，靠信号
    ("应聘后端开发岗，模拟一场", Capability.MOCK),
    ("给我来5道难一点的题", Capability.MOCK),                 # 出题配置信号
    ("诊断我的弱点", Capability.DIAGNOSIS),
    ("帮我做个学习计划", Capability.PLANNING),
    ("准备面试", Capability.COMPOSITE),                       # 复合：诊断→规划
    ("什么是 AOF 重写", Capability.QA),
])
def test_capability_routing(resolver, text, cap):
    assert resolver.resolve(text).capability == cap


def test_composite_carries_ordered_handoffs(resolver):
    f = resolver.resolve("快面试了帮我准备一下")
    assert f.capability == Capability.COMPOSITE
    assert f.handoffs == ["diagnosis", "planning"]


def test_explicit_command_wins(resolver):
    assert resolver.resolve("/plan 两个月内补齐分布式").capability == Capability.PLANNING
    assert resolver.resolve("/mock redis").capability == Capability.MOCK


# ----------------------------------------------------------------- 面试槽位抽取
def test_role_and_role_type_extracted(resolver):
    f = resolver.resolve("我面 RAG 算法实习")
    ms = f.mock_slots()
    assert ms.role_type == "rag"
    assert ms.target_role and "RAG" in ms.target_role
    assert ms.focus  # role_focus 回填


def test_jd_paste_detected_as_context(resolver):
    jd = "岗位职责：负责 RAG 召回与 rerank；任职要求：熟悉向量检索与评测"
    ms = resolver.resolve(jd).mock_slots()
    assert ms.jd_text == jd
    assert ms.role_type == "rag"


def test_difficulty_and_turns_slots(resolver):
    ms = resolver.resolve("给我来5道难一点的题").mock_slots()
    assert ms.difficulty == 4
    assert ms.max_turns == 5
    assert ms.topic is None  # 不把"5道难一点的题"当主题


def test_topic_hint_extracted(resolver):
    assert resolver.resolve("面试我 redis 持久化").mock_slots().topic == "redis"


def test_generic_mock_has_no_targeting(resolver):
    ms = resolver.resolve("考考我").mock_slots()
    assert ms.target_role is None and ms.jd_text is None
    assert ms.to_interview_context() is None  # 退回纯主题行为


def test_target_role_regex_not_overcapturing(resolver):
    # "面试我 Python 并发" 不应把"试我 Python 并发"误当岗位。
    assert resolver.resolve("面试我 Python 并发").mock_slots().target_role is None


# ----------------------------------------------------------------- 槽位 → InterviewContext
def test_mock_slots_to_interview_context_roundtrip():
    ms = S.build_mock_slots("我面 agent 工程师")
    ic = ms.to_interview_context()
    assert ic is not None
    assert ic.role_type == "agent"


# ----------------------------------------------------------------- 全部离线确定，可回归
def test_resolve_is_pure(resolver):
    a = resolver.resolve("我面 RAG 算法实习，拿我项目拷打我")
    b = resolver.resolve("我面 RAG 算法实习，拿我项目拷打我")
    assert a.model_dump() == b.model_dump()
