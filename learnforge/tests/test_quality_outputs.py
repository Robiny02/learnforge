"""Quality guardrails for user-facing QA and plan artifacts."""

from learnforge.agents.qa.synthesizer import SynthesizerAgent
from learnforge.contracts.agents.qa import SynthesizerInput
from learnforge.contracts.agents.retrieval import Chunk
from learnforge.integrations import report


def test_report_generate_renders_rich_learning_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)

    out = report.report_generate_handler({
        "title": "Redis 面试复习计划",
        "summary": "先补持久化和缓存一致性，再用 mock 检查表达质量。",
        "days": {"0": ["Redis RDB/AOF", "缓存一致性"], "1": ["缓存穿透/击穿/雪崩"]},
        "tips": ["每天写一张 answer card", "两天后做 3 轮 mock"],
    })

    assert out["ok"] is True
    text = (tmp_path / out["path"].split("/")[-1]).read_text(encoding="utf-8")
    assert "## Overview" in text
    assert "## Priority Logic" in text
    assert "### Practice Tasks" in text
    assert "### Acceptance Criteria" in text
    assert "## Review Cadence" in text


def test_synthesizer_offline_fallback_uses_evidence():
    agent = SynthesizerAgent()
    chunk = Chunk(
        chunk_id="c1",
        text="B+ 树非叶子节点只存 key，扇出更大；叶子节点链表支持范围查询。",
        source_type="doc",
        score=1.0,
    )

    out = agent.run(SynthesizerInput(question="B+树为什么适合范围查询？", retrieved=[chunk]))

    assert "可复习版回答" in out.draft
    assert "检索到的关键依据" in out.draft
    assert "面试表达建议" in out.draft
