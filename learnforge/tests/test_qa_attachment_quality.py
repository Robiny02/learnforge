"""QA 质量/延迟回归：附件文档讲解走结构化深度 prompt + 概念问句免 LLM 路由短路。

锁住：
- 自包含概念问句（什么是/介绍一下…）在 dispatcher 走确定性短路，不触发 LLM 路由（降延迟）。
- 带能力关键词的问句（如「什么是模拟面试」）不被概念短路误判。
- 上传文档问答用「文档讲解」结构化 prompt（概览/能力/流程/示例/边界），不套八股模板。
全离线、确定性。
"""

from __future__ import annotations

from learnforge.contracts.agents.qa import SynthesizerInput


# ----------------------------------------------------------------- 延迟：概念短路
def test_concept_question_shortcuts_without_llm(monkeypatch):
    from learnforge.intent.dispatch import Dispatcher
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", True, raising=False)
    # LLM 标记可用，但概念短路应在 L0 命中 → 不会真的调用分类器。
    monkeypatch.setattr(
        Dispatcher, "_classify",
        staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用 LLM 路由"))),
    )
    r = Dispatcher().route("什么是 TCP 三次握手")
    assert r.capability == "qa" and "concept_shortcut" in r.signals


def test_concept_shortcut_excludes_capability_keywords(monkeypatch):
    from learnforge.intent.dispatch import Dispatcher
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    # 「什么是模拟面试」带 mock 关键词 → 不走概念短路（交规则/LLM 判，避免误当概念）。
    r = Dispatcher().route("什么是模拟面试")
    assert "concept_shortcut" not in r.signals


# ----------------------------------------------------------------- 质量：文档讲解 prompt
def _capture_complete(monkeypatch):
    from learnforge.llm.client import LLM, LLMResult, TokenUsage
    from learnforge.contracts.enums import ModelTier
    seen = {}

    def fake_complete(prompt, model_tier=ModelTier.SONNET, system=None, max_tokens=1024,
                      timeout_s=None, response_format=None, model=None, images=None):
        seen["prompt"] = prompt
        return LLMResult(text='{"draft":"### 这是什么\\n数据分析 skill。","claims":[]}',
                         tokens=TokenUsage(prompt=0, completion=0), cost_usd=0.0,
                         model_tier=model_tier)

    monkeypatch.setattr(LLM, "available", True, raising=False)
    monkeypatch.setattr(LLM, "complete", fake_complete, raising=False)
    return seen


def test_attachment_uses_document_explanation_prompt(monkeypatch):
    from learnforge.agents.qa.synthesizer import SynthesizerAgent
    seen = _capture_complete(monkeypatch)
    out = SynthesizerAgent().run(SynthesizerInput(
        question="介绍一下这个skill讲了什么",
        attachment_text="【附件：SKILL.md】\n数据分析技能：用 DuckDB 对 Excel/CSV 跑 SQL，支持多表 join 与导出。",
    ))
    p = seen["prompt"]
    # 走文档讲解结构（覆盖能力/流程/边界），且把完整文档作为依据注入。
    assert "核心能力" in p and "使用流程" in p and "注意事项与边界" in p
    assert "DuckDB" in p                      # 完整文档内容进了 prompt
    assert "至少覆盖 7 层" not in p             # 不再套用概念八股模板的固定 7 层结构
    assert out.draft.startswith("### 这是什么")


def test_attachment_synth_offline_degrades(monkeypatch):
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    from learnforge.agents.qa.synthesizer import SynthesizerAgent
    out = SynthesizerAgent().run(SynthesizerInput(
        question="讲讲这个文档", attachment_text="【附件：a.md】\n关键内容 ABC"))
    assert "ABC" in out.draft  # 无 key 也把文档要点摘出来，不崩
