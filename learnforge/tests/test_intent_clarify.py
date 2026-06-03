"""澄清门控回归：缺关键锚点 → 主动问一句；泛化请求 → 绝不打断（全离线）。

铁律两条，都在此固化：
  ① 用户暗示针对性意图却缺锚点 → clarification 非空、pending_slot 指明在等哪个槽位；
  ② 泛化请求（"考考我"）→ clarification 必须为空，直接开场。
并验证澄清的下一轮（resume_pending）把回复填进缺失槽位后产出可执行帧。
"""

from __future__ import annotations

import pytest

from learnforge.contracts.intent import Capability, MockAction
from learnforge.intent import IntentResolver


@pytest.fixture
def resolver():
    return IntentResolver()  # 无注入档案材料 → 拷打类缺料会索要


# ----------------------------------------------------------------- 触发澄清
def test_role_intent_without_role_asks(resolver):
    f = resolver.resolve("我想面试，但还没想好方向")
    assert f.clarification and "岗位" in f.clarification
    assert f.pending_slot == "target_role"
    assert "target_role" in f.missing_required


def test_resume_grill_without_material_asks(resolver):
    f = resolver.resolve("拿我简历狠狠拷打我")
    assert f.clarification
    assert f.pending_slot == "resume_claims"


def test_planning_without_goal_or_deadline_asks(resolver):
    f = resolver.resolve("帮我规划一下")
    assert f.capability == Capability.PLANNING
    assert f.clarification and f.pending_slot == "goal"


# ----------------------------------------------------------------- 不打断泛化请求
def test_generic_mock_not_clarified(resolver):
    assert resolver.resolve("考考我").clarification is None


def test_targeted_role_not_clarified(resolver):
    # 已给出岗位 → 不再追问。
    assert resolver.resolve("我面 RAG 算法实习").clarification is None


def test_jd_paste_not_clarified(resolver):
    jd = "岗位职责：负责 RAG 召回；任职要求：熟悉向量检索"
    assert resolver.resolve(jd).clarification is None


def test_topic_mock_not_clarified(resolver):
    assert resolver.resolve("面试我 redis").clarification is None


def test_qa_and_diagnosis_never_clarify(resolver):
    assert resolver.resolve("什么是 RDB").clarification is None
    assert resolver.resolve("诊断我的弱点").clarification is None


def test_resume_grill_with_profile_material_skips_ask():
    # 档案里已有简历材料 → 拷打类无需再索要。
    r = IntentResolver(profile_claims=["主导了一个 RAG 检索系统", "做过 rerank 调优"])
    f = r.resolve("拿我简历拷打我")
    assert f.clarification is None
    assert f.mock_slots().resume_claims  # 用上了档案材料


# ----------------------------------------------------------------- 澄清的下一轮
def test_resume_pending_fills_role(resolver):
    f1 = resolver.resolve("我想面试，但还没想好方向")
    assert f1.pending_slot == "target_role"
    f2 = resolver.resume_pending(f1.model_dump(), "RAG 算法实习")
    assert f2.capability == Capability.MOCK
    assert f2.action == MockAction.START.value
    assert f2.clarification is None
    assert f2.mock_slots().role_type == "rag"


def test_resume_pending_fills_resume_claims(resolver):
    f1 = resolver.resolve("拿我简历拷打我")
    f2 = resolver.resume_pending(f1.model_dump(), "做过 RAG 检索；做过 rerank 调优")
    assert f2.mock_slots().resume_claims == ["做过 RAG 检索", "做过 rerank 调优"]


def test_resume_pending_fills_plan_goal(resolver):
    f1 = resolver.resolve("帮我规划一下")
    f2 = resolver.resume_pending(f1.model_dump(), "两个月内补齐 redis 八股")
    assert f2.capability == Capability.PLANNING
    assert f2.clarification is None
    assert f2.plan_slots().goal
