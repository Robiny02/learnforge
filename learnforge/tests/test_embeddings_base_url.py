"""OpenAI 兼容 base_url（支持 OpenRouter 等代理跑 embeddings）—— 离线 mock，无网络。"""

from __future__ import annotations

import pytest


def test_openai_embedding_provider_honors_base_url(monkeypatch):
    import openai

    from learnforge.llm import embeddings as E

    captured = {}

    class _FakeEmbeddings:
        def create(self, model, input, **kw):
            data = [type("D", (), {"embedding": [0.1] * 8})() for _ in input]
            return type("R", (), {"data": data})()

    class _FakeClient:
        def __init__(self, **kw):
            captured.update(kw)
            self.embeddings = _FakeEmbeddings()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    monkeypatch.setattr(E, "OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

    p = E.OpenAIEmbeddingProvider(model="openai/text-embedding-3-small")
    assert p.available
    assert captured.get("base_url") == "https://openrouter.ai/api/v1"
    assert captured.get("api_key") == "sk-test"
    vecs = p.embed(["a", "b"])
    assert len(vecs) == 2 and len(vecs[0]) == 8


def test_openai_embedding_provider_no_base_url_by_default(monkeypatch):
    import openai

    from learnforge.llm import embeddings as E

    captured = {}

    class _FakeClient:
        def __init__(self, **kw):
            captured.update(kw)
            self.embeddings = type("E", (), {"create": lambda *a, **k: None})()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    monkeypatch.setattr(E, "OPENAI_BASE_URL", None)

    E.OpenAIEmbeddingProvider()
    assert "base_url" not in captured  # 默认走原生 OpenAI，不注入 base_url
