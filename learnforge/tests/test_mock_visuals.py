"""离线单测：mock 输入意图分类 + 出图集成 + /ui/chat mock 辅助函数。

全程离线（不打网络）：
- gpt_image 在 pytest 下 available()=False（PYTEST 守卫），故不会真出图；
- classify_mock_input 用 fast_kind 快路 + monkeypatch 关 LLM 走启发式兜底。
"""

from __future__ import annotations

import learnforge.llm.client as llm_client
from learnforge.agents.mock.intent import (
    classify_exit_confirmation,
    classify_mock_input,
    fast_kind,
)
from learnforge.integrations import gpt_image as gi


# ----------------------------------------------------------------- 意图分类（#1 快路）
def test_fast_kind_clear_answer_skips_llm():
    # 无任何疑问信号 → 直接判作答（省 LLM 往返）。
    assert fast_kind("我用 AOF everysec 加 RDB 快照，并用 INFO persistence 监控") == "answer"


def test_fast_kind_explicit_side_lead():
    assert fast_kind("顺便问，什么是 RDB 持久化") == "side"
    assert fast_kind("插一句，AOF 重写怎么触发") == "side"


def test_fast_kind_ambiguous_returns_none():
    # 含疑问信号但可能是反问/边答边问 → 交 LLM。
    assert fast_kind("是不是应该用 AOF？") is None


def test_fast_kind_empty_is_answer():
    assert fast_kind("") == "answer"


def test_classify_falls_back_to_heuristic_offline(monkeypatch):
    # 关掉 LLM：歧义输入应走启发式（以问号结尾 → side）。
    monkeypatch.setattr(llm_client.LLM, "available", False)
    assert classify_mock_input("讲讲你的持久化方案", "是不是应该用 AOF？") == "side"


def test_classify_clear_answer_no_llm_needed(monkeypatch):
    # 即便 LLM 不可用，明显作答也能正确判定（快路兜底）。
    monkeypatch.setattr(llm_client.LLM, "available", False)
    assert classify_mock_input("讲讲持久化", "我负责了 AOF 配置和监控") == "answer"


# ----------------------------------------------------------------- #4 退出/暂停 + 退出确认（语义）
def test_fast_kind_exit_and_pause_leads():
    assert fast_kind("我们结束面试吧") == "exit"
    assert fast_kind("今天先到这") == "exit"
    assert fast_kind("暂停一下") == "pause"


def test_fast_kind_soft_exit_defers_to_llm():
    # 自然退出表达不在硬编码 leads 里 → 不能被快路当成作答，必须交 LLM（返回 None）。
    assert fast_kind("今天就先这样吧，有点累了") is None
    assert fast_kind("算了，不想继续了") is None
    # 对照：纯技术作答（无软退出 token）仍走快路省 LLM。
    assert fast_kind("我用 AOF everysec 加 RDB 快照并监控指标") == "answer"


def test_classify_four_way_offline(monkeypatch):
    monkeypatch.setattr(llm_client.LLM, "available", False)
    assert classify_mock_input("讲讲持久化", "不练了") == "exit"
    assert classify_mock_input("讲讲持久化", "稍等") == "pause"
    assert classify_mock_input("讲讲持久化", "顺便问，什么是 AOF") == "side"


def test_escalate_classification(monkeypatch):
    monkeypatch.setattr(llm_client.LLM, "available", False)
    assert fast_kind("帮我改下计划") == "escalate"
    assert classify_mock_input("讲讲持久化", "顺便帮我诊断一下我的弱点") == "escalate"
    assert classify_mock_input("讲讲持久化", "重新规划我的复习") == "escalate"


def test_build_handoff_summary():
    from learnforge.agents.mock.handoff import build_handoff_summary

    s = build_handoff_summary({
        "topic": "Redis",
        "turn_scores": [{"overall": 2}, {"overall": 4}],
        "topic_coverage": ["Redis", "缓存"],
        "current_question": "讲讲 AOF 和 RDB 的取舍",
        "turns": [{"user_answer": "AOF everysec"}],
    })
    assert "Redis" in s and "2 轮" in s and "3.0/5" in s
    assert "AOF" in s
    # 空状态也不报错。
    assert build_handoff_summary({}).startswith("模拟面试交接")


def test_exit_confirmation_heuristic_offline(monkeypatch):
    monkeypatch.setattr(llm_client.LLM, "available", False)
    assert classify_exit_confirmation("确定，结束吧") == "confirm"
    assert classify_exit_confirmation("先别，再来一题") == "continue"
    # "不结束" 含"结束"，但 continue 线索优先 → 不误判为确认。
    assert classify_exit_confirmation("不，继续") == "continue"
    # 歧义 → 默认不结束。
    assert classify_exit_confirmation("呃") == "continue"


# ----------------------------------------------------------------- 出图集成
def test_gpt_image_offline_in_tests():
    # 不变量：测试环境绝不真出图（即使 .env 有 key）。
    assert gi.available() is False
    assert gi.auto_enabled() is False


def test_asset_url_conversion():
    assert gi.asset_url("/x/docs/assets/plan-123.png") == "/assets/plan-123.png"
    assert gi.asset_url(None) is None


def test_generate_helpers_degrade_without_key():
    # available()=False 时高层封装直接返回 not-ok，不抛、不打网络。
    res = gi.generate_plan_infographic("计划", {0: ["Redis"]})
    assert res.get("ok") is False


def test_build_plan_prompt_carries_data():
    p = gi.build_plan_prompt("我的计划", {0: ["Redis 持久化"], 1: ["哨兵"]}, summary="复习")
    assert "我的计划" in p and "Day 1" in p and "Redis 持久化" in p
    assert "1536x1024" in p  # house style 画幅约束


def test_build_diagnosis_prompt_sorts_by_severity():
    p = gi.build_diagnosis_prompt(
        [{"topic": "Redis", "severity": 0.8}, {"topic": "MySQL", "severity": 0.3}],
        weak_atoms=[{"topic": "MVCC", "mastery": 0.4}],
    )
    # 高严重度在前。
    assert p.index("Redis") < p.index("MySQL")
    assert "MVCC" in p


# ----------------------------------------------------------------- server mock 辅助
def test_server_mock_helpers():
    from learnforge.app import server as S

    assert S._wants_start_mock("来一场模拟面试") is True
    # 复合"准备面试"（诊断→改计划）不应被当成开 mock。
    assert S._wants_start_mock("帮我准备面试") is False
    assert S._wants_start_mock("诊断我的弱点并据此调整复习计划") is False
    assert S._extract_mock_topic("模拟面试 Redis") == "Redis"
    assert S._extract_mock_topic("考考我") == "综合技术面试"


def test_mock_response_active_branch():
    from learnforge.app import server as S
    from learnforge.contracts.agents.mock import MockOutput

    out = MockOutput(session_id="s1", status="active", question="请解释 MVCC？", turn_index=1)
    body = S._mock_response(mgr=None, out=out, sid="s1")  # active 分支不用 mgr
    assert body["mock_active"] is True
    assert body["mock_session_id"] == "s1"
    assert body["reply_text"] == "请解释 MVCC？"


def test_mock_response_review_settles_and_adds_image_url():
    from learnforge.app import server as S
    from learnforge.contracts.agents.mock import MockOutput

    class FakeMgr:
        def settle_mock(self, sid):
            return {"mastery_committed": 2, "diagnosis": {"clusters": [{"topic": "Redis", "severity": 0.7}]},
                    "diagnosis_cost_usd": 0.0, "diagnosis_image_path": None}

    out = MockOutput(session_id="s2", status="review", turn_index=3)
    body = S._mock_response(mgr=FakeMgr(), out=out, sid="s2")
    assert body["mock_active"] is False
    assert "settlement" in body
    # 出图按需：无 path → url 为 None；前端据 diagnosis.clusters 给"生成"按钮。
    assert body["settlement"]["diagnosis_image_url"] is None
    assert body["settlement"]["diagnosis"]["clusters"]
