"""Latency policy tests for QA retrieval.

These tests guard the cheap-path contract: concept QA should avoid vector retrieval
by default, and the online ReAct retrieval tool should use fulltext unless a more
expensive path is explicitly introduced.
"""

from learnforge.agents.qa import QAAgent
from learnforge.contracts.agents.qa import QAInput, SynthesizerOutput
from learnforge.contracts.enums import QType, RetrievalMethod


def test_concept_strategy_uses_fulltext_not_hybrid():
    qa = QAAgent()
    strategy = qa._select_strategy(QType.CONCEPT)
    _, method = qa._strategy_scopes(strategy, QType.CONCEPT)
    assert method == RetrievalMethod.FULLTEXT


def test_fast_concept_skips_router_retrieval_and_verifier(monkeypatch):
    import learnforge.llm.client as llm_client

    qa = QAAgent()
    monkeypatch.setattr(llm_client.LLM, "available", True)
    qa.router.run = lambda payload: (_ for _ in ()).throw(AssertionError("router should be skipped"))
    qa.retrieval.run = lambda payload: (_ for _ in ()).throw(AssertionError("retrieval should be skipped"))
    qa.verifier.run = lambda payload: (_ for _ in ()).throw(AssertionError("verifier should be skipped"))
    qa.synthesizer.run = lambda payload: SynthesizerOutput(
        draft="### 核心结论\nRedis 持久化主要是 RDB 和 AOF。",
        claims=[],
    )

    out = qa.run(QAInput(question="介绍一下redis的持久化机制"))

    assert out.answer.startswith("### 核心结论")
    assert out.topic == "concept_fast"


def test_redis_persistence_card_skips_all_llm_subagents(monkeypatch):
    import learnforge.llm.client as llm_client

    qa = QAAgent()
    monkeypatch.setattr(llm_client.LLM, "available", True)
    qa.router.run = lambda payload: (_ for _ in ()).throw(AssertionError("router should be skipped"))
    qa.retrieval.run = lambda payload: (_ for _ in ()).throw(AssertionError("retrieval should be skipped"))
    qa.verifier.run = lambda payload: (_ for _ in ()).throw(AssertionError("verifier should be skipped"))
    qa.synthesizer.run = lambda payload: (_ for _ in ()).throw(AssertionError("synthesizer should be skipped"))

    out = qa.run(QAInput(question="介绍一下redis的持久化机制"))

    assert out.topic == "concept_fast"
    assert out.confidence >= 0.8
    assert "### 混合持久化" in out.answer
    assert "appendfsync" in out.answer
    assert "AOF rewrite" in out.answer
    assert "面试追问" in out.answer


def test_rocketmq_reliability_card_uses_role_chain(monkeypatch):
    import learnforge.llm.client as llm_client

    qa = QAAgent()
    monkeypatch.setattr(llm_client.LLM, "available", True)
    qa.router.run = lambda payload: (_ for _ in ()).throw(AssertionError("router should be skipped"))
    qa.retrieval.run = lambda payload: (_ for _ in ()).throw(AssertionError("retrieval should be skipped"))
    qa.verifier.run = lambda payload: (_ for _ in ()).throw(AssertionError("verifier should be skipped"))
    qa.synthesizer.run = lambda payload: (_ for _ in ()).throw(AssertionError("synthesizer should be skipped"))

    out = qa.run(QAInput(question="rockmq可靠性怎么保证？"))

    assert out.topic == "concept_fast"
    assert "Producer" in out.answer
    assert "Broker" in out.answer
    assert "Consumer" in out.answer
    assert "DLQ" in out.answer
    assert "幂等" in out.answer
    assert "至少一次" in out.answer


def test_spring_boot_startup_card_uses_lifecycle_chain(monkeypatch):
    import learnforge.llm.client as llm_client

    qa = QAAgent()
    monkeypatch.setattr(llm_client.LLM, "available", True)
    qa.router.run = lambda payload: (_ for _ in ()).throw(AssertionError("router should be skipped"))
    qa.retrieval.run = lambda payload: (_ for _ in ()).throw(AssertionError("retrieval should be skipped"))
    qa.verifier.run = lambda payload: (_ for _ in ()).throw(AssertionError("verifier should be skipped"))
    qa.synthesizer.run = lambda payload: (_ for _ in ()).throw(AssertionError("synthesizer should be skipped"))

    out = qa.run(QAInput(question="springboot怎么启动？"))

    assert out.topic == "concept_fast"
    assert "SpringApplication.run" in out.answer
    assert "Environment" in out.answer
    assert "ApplicationContext" in out.answer
    assert "refresh()" in out.answer
    assert "自动配置" in out.answer
    assert "Tomcat" in out.answer
    assert "ApplicationRunner" in out.answer


def test_ui_fast_qa_bypasses_main_graph(monkeypatch):
    import learnforge.app.server as server
    import learnforge.llm.client as llm_client

    monkeypatch.setattr(llm_client.LLM, "available", True)
    monkeypatch.setattr(
        server,
        "_invoke",
        lambda text, session_id: (_ for _ in ()).throw(AssertionError("main graph should be skipped")),
    )

    out = server._ui_route("qa", "介绍一下redis的持久化机制", "test-session")

    assert out["plan"] == [{"agent": "qa", "fast": True}]
    assert out["status"] == "ok"
    assert "### 核心结论" in out["reply_text"]
    assert "AOF rewrite" in out["reply_text"]


def test_uncarded_concept_uses_llm_route_direct_answer(monkeypatch):
    import learnforge.agents.qa.qa_agent as qa_mod
    import learnforge.llm.client as llm_client

    qa = QAAgent()
    monkeypatch.setattr(llm_client.LLM, "available", True)
    qa.router.run = lambda payload: (_ for _ in ()).throw(AssertionError("router should be skipped"))
    qa.retrieval.run = lambda payload: (_ for _ in ()).throw(AssertionError("retrieval should be skipped"))
    qa.synthesizer.run = lambda payload: (_ for _ in ()).throw(AssertionError("synthesizer should be skipped"))

    def fake_structured(**kwargs):
        return qa_mod.FastQARouteDecision(
            use_memory=False,
            use_files=False,
            answer_directly=True,
            direct_answer=(
                "### 核心结论\n\nB+树适合范围查询，因为叶子节点有序并通过链表连接。\n\n"
                "### 机制/流程\n\n- 从根节点定位到区间起点。\n- 沿叶子节点顺序扫描。"
                "\n\n### 场景与取舍\n\n它适合数据库索引，因为磁盘 IO 更少、区间扫描更顺。"
            ),
            confidence=0.8,
        ), type("Result", (), {"cost_usd": 0.001})()

    monkeypatch.setattr(llm_client.LLM, "complete_structured", fake_structured)

    out = qa.run(QAInput(question="介绍一下B+树为什么适合范围查询"))

    assert out.topic == "concept_fast"
    assert "B+树适合范围查询" in out.answer


def test_fast_route_reads_local_memory_when_llm_requests_it(monkeypatch):
    import learnforge.agents.qa.qa_agent as qa_mod
    import learnforge.llm.client as llm_client

    qa = QAAgent()
    seen = []
    monkeypatch.setattr(llm_client.LLM, "available", True)

    def fake_structured(**kwargs):
        return qa_mod.FastQARouteDecision(
            use_memory=True,
            use_files=False,
            answer_directly=False,
            reason="用户询问之前记录",
            confidence=0.82,
        ), type("Result", (), {"cost_usd": 0.001})()

    def fake_complete(**kwargs):
        seen.append(kwargs["prompt"])
        return type("Result", (), {
            "text": "### 核心结论\n\n根据本地记忆，你之前 Redis AOF rewrite 边界没说清。",
            "cost_usd": 0.002,
        })()

    monkeypatch.setattr(llm_client.LLM, "complete_structured", fake_structured)
    monkeypatch.setattr(llm_client.LLM, "complete", fake_complete)
    monkeypatch.setattr(
        qa.retrieval,
        "run",
        lambda payload: seen.append(payload) or type("Out", (), {
            "chunks": [
                type("Chunk", (), {
                    "chunk_id": "m1",
                    "source_type": "doc",
                    "text": "mock 反馈：Redis AOF rewrite 期间增量写入边界没讲清。",
                })()
            ]
        })(),
    )

    out = qa.run(QAInput(question="根据我的记忆，介绍一下 redis 持久化"))

    assert out.topic == "concept_fast_local"
    assert out.citations and out.citations[0].chunk_id == "m1"
    assert "AOF rewrite" in out.answer
    assert any("本地上下文" in p for p in seen if isinstance(p, str))


def test_react_retrieval_tool_uses_fulltext_for_evidence_question(monkeypatch):
    import learnforge.agents.qa.qa_agent as qa_mod
    import learnforge.llm.client as llm_client

    class FakeRunner:
        def __init__(self, max_steps=3):
            pass

        def run(self, agent, user_prompt, tool_names, system=None, handlers=None):
            handlers["retrieval.search"]({"query": "Redis"})
            return type("Result", (), {
                "degraded": False,
                "text": "answer",
                "tool_results": [{"tool": "retrieval.search", "result": {}}],
            })()

    qa = QAAgent()
    seen = []
    monkeypatch.setattr(llm_client.LLM, "available", True)
    monkeypatch.setattr(qa_mod, "ReactRunner", FakeRunner)
    monkeypatch.setattr(qa.retrieval, "run", lambda payload: seen.append(payload) or type("Out", (), {"chunks": []})())

    out = qa.run(QAInput(question="最新 Redis 持久化文档怎么说？"))
    assert out.answer == "answer"
    assert seen and seen[0].method == RetrievalMethod.FULLTEXT
