"""证据约束式拷打接入测试（LLMInternSkill → mock interview）。

分两层：
1) 纯规则层 interview_skill：角色识别 / 风险标签 / 降级 / answer card / 证据式追问（离线确定性）。
2) 接入层：Judge 补 risk_flags、Coach 出 answer_cards、Interviewer 用上下文做证据式追问、
   MockInput.context 经子图整场可用。

全部离线（无 ANTHROPIC_API_KEY），断言结构契约与确定性兜底，不评判 LLM 输出质量。
"""

from learnforge.agents.mock import interview_skill as IS
from learnforge.agents.mock import CoachAgent, InterviewerAgent, JudgeAgent, MockInterviewAgent
from learnforge.contracts.agents.retrieval import Chunk, RetrievalOutput
from learnforge.contracts.enums import RetrievalMethod
from learnforge.contracts.agents.mock import (
    CoachInput,
    InterviewContext,
    InterviewerInput,
    JudgeInput,
    MockInput,
    Score,
    Turn,
)


# ---------------------------------------------------------------- 纯规则层
def test_detect_role_type_picks_strongest():
    assert IS.detect_role_type(jd_text="负责 RAG 召回、rerank 与 citation 评估") == "rag"
    assert IS.detect_role_type(target_role="搜索排序 NDCG 相关性实习") == "search-ranking"
    # 无信号 → None（退回纯主题模式）。
    assert IS.detect_role_type(jd_text="", target_role=None) is None


def test_risk_flags_overclaim_and_vague():
    # 夸大无证据 → overclaim；含糊/过短 → vague。
    flags = IS.risk_flags_for_answer("我主导上线了系统，显著提升了效果")
    assert "overclaim" in flags
    assert IS.risk_flags_for_answer("感觉差不多吧") == ["vague"] or "vague" in IS.risk_flags_for_answer("感觉差不多吧")
    # 空回答 → 无证据且含糊。
    assert set(IS.risk_flags_for_answer("")) == {"no_evidence", "vague"}


def test_risk_flags_reward_evidence():
    # 有证据词（指标/对比/bad case）即使较短也不应判 overclaim。
    ans = "我用固定 query 集对比 BM25 与 rerank 的 NDCG@10，并记录了 bad case 类型"
    assert "overclaim" not in IS.risk_flags_for_answer(ans)


def test_downgrade_maps_unsafe_to_safe():
    assert IS.downgrade("我主导了整个系统") is not None
    assert IS.downgrade("上线到生产环境") is not None
    assert IS.downgrade("我读了点资料") is None


def test_build_answer_card_has_three_tiers():
    card = IS.build_answer_card(
        question="你说提升了搜索排序，指标是什么？",
        expected_points=["NDCG/MRR 口径", "baseline 对比"],
        user_answer="感觉排序效果好多了",
        role_type="search-ranking",
    )
    assert card["dangerous"] and card["passable"] and card["strong"]
    assert card["why_risky"]
    assert card["evidence_needed"]


def test_grounded_followup_targets_overclaim():
    f = IS.grounded_followup(
        topic="RAG", difficulty=3, last_question="讲讲你的 RAG 项目",
        last_answer="我主导上线了企业级知识库，显著提升了准确率",
        claims=["做过企业知识库 RAG"], role_type="rag", turn_index=1,
    )
    # 追问应逼问真实边界/证据，而非泛泛复述。
    assert "证明" in f["question"] or "亲手" in f["question"]
    assert f["expected_points"]


# ---------------------------------------------------------------- 接入层（agent）
def test_judge_attaches_risk_flags_offline():
    j = JudgeAgent()
    score = j.run(JudgeInput(
        question="你优化了什么？", expected_points=["chunk", "rerank"],
        user_answer="我主导上线了系统，大幅提升效果",
    ))
    assert isinstance(score, Score)
    assert "overclaim" in score.risk_flags


def test_interviewer_followup_uses_context_offline():
    iv = InterviewerAgent()
    ctx = InterviewContext(target_role="RAG 实习", resume_claims=["做过文档问答 RAG demo"])
    out = iv.run(InterviewerInput(
        topic="RAG", difficulty=3, context=ctx,
        last_question="讲讲你的 RAG", last_answer="我主导上线，显著提升准确率",
        turn_index=1,
    ))
    assert out.question
    # 证据式追问应触及真实边界/证据，而不是模板题。
    assert "核心原理与典型应用场景" not in out.question


def test_interviewer_caches_topic_retrieval(monkeypatch):
    import learnforge.llm.client as llm_client

    monkeypatch.setattr(llm_client.LLM, "available", False)
    iv = InterviewerAgent()
    calls = []

    def fake_run(payload):
        calls.append(payload)
        return RetrievalOutput(
            chunks=[
                Chunk(
                    chunk_id="redis-cache",
                    text="Redis persistence question bank.",
                    source_type="doc",
                    score=1.0,
                )
            ],
            method_used=RetrievalMethod.FULLTEXT,
        )

    monkeypatch.setattr(iv.retrieval, "run", fake_run)
    first = iv.run(InterviewerInput(topic="Redis", difficulty=3))
    second = iv.run(InterviewerInput(topic="Redis", difficulty=3, turn_index=1))
    assert first.question and second.question
    assert len(calls) == 1


def test_coach_emits_answer_cards_for_risky_turns():
    c = CoachAgent()
    turns = [
        Turn(turn_index=0, question="你优化了什么？", user_answer="我主导上线，显著提升",
             score=Score(overall=2, missed_points=["指标口径"], risk_flags=["overclaim"])),
        Turn(turn_index=1, question="再讲讲指标", user_answer="感觉还行",
             score=Score(overall=2, risk_flags=["vague"])),
    ]
    out = c.run(CoachInput(
        turn_scores=[t.score for t in turns],
        topic_coverage=["RAG"],
        context=InterviewContext(role_type="rag"),
        turns=turns,
    ))
    assert out.report.answer_cards, "高风险轮应产出 answer card"
    card = out.report.answer_cards[0]
    assert card.passable and card.strong


def test_mock_context_threads_through_subgraph():
    m = MockInterviewAgent()
    ctx = InterviewContext(
        target_role="搜索排序实习",
        jd_text="负责召回与 rerank，关注 NDCG/MRR",
        resume_claims=["做过 mini search demo"],
    )
    out = m.run(MockInput(topic="搜索排序", session_id="t-ctx", context=ctx))
    assert out.status == "active"
    assert out.question
    # 答一题后风险标签应随评分落地。
    ans = m.answer("t-ctx", user_answer="我主导上线了排序系统，显著提升了效果")
    assert ans.turn_scores
    assert "overclaim" in ans.turn_scores[-1].risk_flags
