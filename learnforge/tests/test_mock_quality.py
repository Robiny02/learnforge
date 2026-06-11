"""Mock 回答质量改造的复杂场景测试（SOP+few-shot / 抗截断 / 降级可见性 / HYBRID 检索）。

对应改动：
- Interviewer/Coach skill 注入 progressive `sop` 段（编号步骤 + 质量门槛 + 好/烂 few-shot）。
- `load_instructions` 默认加载链接入 `sop`，激活原本是死代码的 SOP 注入。
- max_tokens 提升（Interviewer 512→768、Coach 1024→1600）抗 JSON 截断。
- LLM 可用却产出不可用时记 warning，模板/启发式兜底不再被当成「LLM 输出」静默端上。
- Interviewer 题材检索 FULLTEXT→HYBRID。

全部离线：用 monkeypatch 模拟 LLM「可用」并捕获实际下发的 system/prompt/max_tokens/model_tier，
断言结构契约与降级行为，不评判真实 LLM 输出质量。
"""

from __future__ import annotations

import logging
from typing import Dict, List

import pytest

import learnforge.llm.client as llm_client
from learnforge.agents.mock import CoachAgent, InterviewerAgent
from learnforge.contracts.agents.mock import (
    CoachInput,
    CoachReport,
    InterviewContext,
    InterviewerInput,
    InterviewerOutput,
    Score,
    Turn,
    Weakness,
)
from learnforge.contracts.agents.retrieval import Chunk, RetrievalInput, RetrievalOutput
from learnforge.contracts.enums import ModelTier, RetrievalMethod
from learnforge.contracts.message import TokenUsage


class _Result:
    """complete_structured 返回的成本/用量占位（base.py 只取 .cost_usd/.tokens）。"""

    cost_usd = 0.0
    tokens = TokenUsage()


class _LLMCapture:
    """捕获最后一次 LLM 调用的 kwargs，并按 schema 返回一个有效对象。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, object]] = []

    def __call__(self, **kwargs):  # 匹配 base.py 的关键字调用
        self.calls.append(kwargs)
        schema = kwargs["schema"]
        if schema is InterviewerOutput:
            obj = InterviewerOutput(
                question="LLM-GENERATED-Q", expected_points=["p1", "p2"], atom_refs=["a1"]
            )
        elif schema is CoachReport:
            obj = CoachReport(
                summary="LLM-SUMMARY",
                weaknesses=[Weakness(topic="RAG", evidence="第1轮")],
                answer_cards=[],
            )
        else:  # pragma: no cover - 防御性
            obj = schema()
        return obj, _Result()

    @property
    def last(self) -> Dict[str, object]:
        return self.calls[-1]

    def text(self) -> str:
        """最后一次调用 system + prompt 合并，便于断言内容到没到模型。"""
        c = self.last
        return f"{c.get('system', '')}\n{c.get('prompt', '')}"


@pytest.fixture
def llm_on(monkeypatch):
    """让 LLM 表现为「可用」并捕获下发内容（不触网）。"""
    cap = _LLMCapture()
    monkeypatch.setattr(llm_client.LLM, "available", True)
    monkeypatch.setattr(llm_client.LLM, "complete_structured", cap)
    return cap


# --------------------------------------------------------------------------- #
# 1. Interviewer：复杂证据式拷打场景下，SOP+few-shot 进 system、复杂上下文进 prompt
# --------------------------------------------------------------------------- #
def test_interviewer_injects_sop_and_threads_complex_context(llm_on):
    iv = InterviewerAgent()
    ctx = InterviewContext(
        jd_text="负责 RAG 召回、rerank 与 citation 评估，关注 NDCG/MRR",  # → role_type 推断为 rag
        resume_claims=["主导上线企业级知识库 RAG", "把准确率从 60% 提到 90%"],
        projects=["文档问答系统：FAISS + BM25 混排"],
    )
    history = [
        Turn(turn_index=0, question="讲讲你的 RAG 项目", user_answer="我主导上线，显著提升准确率"),
        Turn(turn_index=1, question="召回怎么做的", user_answer="就是向量加全文"),
    ]
    out = iv.run(
        InterviewerInput(
            topic="RAG",
            difficulty=4,
            turn_history=history,
            context=ctx,
            last_question="召回怎么做的",
            last_answer="我主导上线了整个系统，显著提升了效果",  # 夸大无证据 → overclaim
            turn_index=2,  # → technical_depth 轮
            retrieved=[Chunk(chunk_id="c1", text="RAG 评估题库", source_type="doc", score=1.0)],
        )
    )
    # 走的是 LLM 路径（非模板兜底）
    assert out.question == "LLM-GENERATED-Q"

    system = str(llm_on.last["system"])
    prompt = str(llm_on.last["prompt"])
    # SOP + few-shot 确实注入 system（cacheable prefix）
    assert "出题 SOP" in system
    assert "few-shot" in system
    assert "好题" in system and "烂题" in system  # 好/烂对照样例
    # 复杂运行时上下文进入 prompt（grilling brief）：claim、上一轮回答、风险点、拷打轮次
    assert "主导上线企业级知识库 RAG" in prompt
    assert "我主导上线了整个系统" in prompt
    assert "overclaim" in prompt  # risk_flags_for_answer 命中夸大
    assert "拷打轮次" in prompt
    # 强模型档 + 抗截断 max_tokens
    assert llm_on.last["model_tier"] == ModelTier.SONNET
    assert llm_on.last["max_tokens"] == 768


# --------------------------------------------------------------------------- #
# 2. Interviewer：题材检索用 HYBRID（中文召回兜底），而非 FULLTEXT
# --------------------------------------------------------------------------- #
def test_interviewer_uses_hybrid_retrieval(monkeypatch):
    monkeypatch.setattr(llm_client.LLM, "available", False)  # 无 LLM → 必走检索取题材
    iv = InterviewerAgent()
    seen: List[RetrievalInput] = []

    def fake_run(payload: RetrievalInput) -> RetrievalOutput:
        seen.append(payload)
        return RetrievalOutput(
            chunks=[Chunk(chunk_id="k1", text="缓存击穿题库", source_type="doc", score=1.0)],
            method_used=RetrievalMethod.HYBRID,
        )

    monkeypatch.setattr(iv.retrieval, "run", fake_run)
    # 中文主题：FTS5 不切中文，必须靠 HYBRID 的向量分路兜底
    out = iv.run(InterviewerInput(topic="缓存穿透与雪崩", difficulty=3))
    assert out.question
    assert len(seen) == 1
    assert seen[0].method == RetrievalMethod.HYBRID


# --------------------------------------------------------------------------- #
# 3. Coach：LLM「可用但失败」时记 warning，且启发式兜底仍出结果（链路永远通）
# --------------------------------------------------------------------------- #
def test_coach_warns_and_falls_back_when_llm_returns_none(monkeypatch, caplog):
    monkeypatch.setattr(llm_client.LLM, "available", True)
    # 模拟截断/解析失败：complete_structured 抛 LLMStructuredError → llm_structured 返回 None
    def boom(**kwargs):
        raise llm_client.LLMStructuredError("simulated truncation/parse failure")

    monkeypatch.setattr(llm_client.LLM, "complete_structured", boom)

    c = CoachAgent()
    turns = [
        Turn(turn_index=0, question="高可用怎么做的", user_answer="加了多个实例就高可用了",
             score=Score(overall=2, missed_points=["故障转移"], risk_flags=["overclaim"])),
        Turn(turn_index=1, question="主挂了怎么切", user_answer="不太清楚",
             score=Score(overall=1, risk_flags=["no_evidence", "vague"])),
        Turn(turn_index=2, question="探活怎么做", user_answer="健康检查 + 摘流，给过演练记录",
             score=Score(overall=4)),
    ]
    with caplog.at_level(logging.WARNING, logger="learnforge.agents.mock.coach"):
        out = c.run(CoachInput(
            turn_scores=[t.score for t in turns],
            topic_coverage=["高可用"],
            context=InterviewContext(role_type="backend"),
            turns=turns,
        ))

    # 降级不再静默：有 warning 记录
    assert any("Coach LLM produced no usable report" in r.message for r in caplog.records)
    # 仍出结构化结果（启发式兜底）：弱点带具体轮次证据
    assert out.report.summary
    assert out.report.weaknesses
    assert any("第1轮" in w.evidence or "第2轮" in w.evidence for w in out.report.weaknesses)
    # 高风险/低分轮仍补 answer cards（确定性规则恒补）
    assert out.report.answer_cards


# --------------------------------------------------------------------------- #
# 4. Coach：复杂多轮聚合下，SOP+few-shot 进 system、逐轮证据进 prompt、max_tokens=1600
# --------------------------------------------------------------------------- #
def test_coach_injects_sop_and_aggregates_complex_turns(llm_on):
    c = CoachAgent()
    # 6 轮混合：低分+风险、稳定、含糊，跨轮重复失分模式
    scores = [
        Score(overall=2, missed_points=["指标口径"], risk_flags=["overclaim"]),
        Score(overall=1, risk_flags=["no_evidence", "vague"]),
        Score(overall=4),
        Score(overall=2, missed_points=["边界条件"], risk_flags=["vague"]),
        Score(overall=5),
        Score(overall=3),
    ]
    turns = [Turn(turn_index=i, question=f"Q{i}", user_answer=f"A{i}", score=s)
             for i, s in enumerate(scores)]
    out = c.run(CoachInput(
        turn_scores=scores,
        topic_coverage=["RAG", "搜索排序"],
        context=InterviewContext(target_role="搜索排序实习", jd_text="召回 rerank NDCG"),
        turns=turns,
    ))
    assert out.report.summary == "LLM-SUMMARY"  # 走 LLM 路径

    system = str(llm_on.last["system"])
    prompt = str(llm_on.last["prompt"])
    assert "复盘 SOP" in system
    assert "answer_card 质量门槛" in system
    assert "passable" in system and "strong" in system
    # 逐轮证据进入 prompt
    assert "第1轮" in prompt and "overall=" in prompt
    assert "risk=" in prompt
    # 抗截断 + 强模型档
    assert llm_on.last["max_tokens"] == 1600
    assert llm_on.last["model_tier"] == ModelTier.SONNET


# --------------------------------------------------------------------------- #
# 5. Coach：纯离线复杂场景——证据绑定弱点、answer card 限 3、弱点事件回流
# --------------------------------------------------------------------------- #
def test_coach_offline_complex_evidence_and_caps(monkeypatch):
    monkeypatch.setattr(llm_client.LLM, "available", False)  # 纯启发式兜底
    c = CoachAgent()
    # 5 个低分高风险轮（应触发 >3 张潜在 card）+ 1 个高分轮
    risky = [
        Turn(turn_index=i, question=f"危险问题{i}", user_answer=f"夸大回答{i}",
             score=Score(overall=1, missed_points=[f"考点{i}"], risk_flags=["overclaim"]))
        for i in range(5)
    ]
    good = Turn(turn_index=5, question="扎实问题", user_answer="给了指标和 bad case",
                score=Score(overall=5))
    turns = risky + [good]
    out = c.run(CoachInput(
        turn_scores=[t.score for t in turns],
        topic_coverage=["分布式"],
        context=InterviewContext(role_type="backend"),
        turns=turns,
    ))
    # 每条弱点必带「第N轮」证据
    assert out.report.weaknesses
    assert all("第" in w.evidence and "轮" in w.evidence for w in out.report.weaknesses)
    # answer cards 限 3 张
    assert 0 < len(out.report.answer_cards) <= 3
    # 高分轮进 strengths，不应被当弱点
    assert out.report.strengths
    # 弱点事件回流（供诊断消费），每条带 evidence
    assert out.events
    assert all(e.signal.get("source") == "mock" and e.signal.get("evidence") for e in out.events)


# --------------------------------------------------------------------------- #
# 6. 样本不足：<2 轮有效评分 → 如实声明不下结论，不硬凑（不调用 LLM）
# --------------------------------------------------------------------------- #
def test_coach_insufficient_sample_is_honest(llm_on):
    c = CoachAgent()
    turns = [Turn(turn_index=0, question="Q0", user_answer="A0", score=Score(overall=3))]
    out = c.run(CoachInput(
        turn_scores=[t.score for t in turns], topic_coverage=["x"], turns=turns
    ))
    assert "样本不足" in out.report.summary
    assert not out.report.weaknesses
    assert llm_on.calls == []  # 样本不足时根本不该触发 LLM
