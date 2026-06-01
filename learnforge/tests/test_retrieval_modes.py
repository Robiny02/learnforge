"""Retrieval mode 对比：固定 workflow vs RetrievalAgent 内部 ReAct。"""

from __future__ import annotations

import pytest

from learnforge import config
from learnforge.agents.retrieval import RetrievalAgent
from learnforge.contracts.agents.retrieval import RetrievalInput
from learnforge.contracts.enums import KnowledgeScope, RetrievalMethod, RetrievalMode
from learnforge.eval.retrieval_mode_eval import run_eval
from learnforge.eval.retrieval_mode_eval import load_query_cases
from learnforge.knowledge.retrieval_aliases import expand_aliases, load_alias_rules
from learnforge.memory.daily import record


@pytest.fixture
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "mem"))
    return tmp_path / "mem"


def test_workflow_mode_remains_default(mem_dir, tmp_db):
    record(
        kind="qa",
        topic="payment",
        text="Q: payment callback failure 怎么处理？\nA: 幂等表 + 重试队列 + 对账补偿。",
        db_path=tmp_db,
    )
    out = RetrievalAgent(db_path=tmp_db).run(
        RetrievalInput(
            query="payment callback failure",
            scopes=[KnowledgeScope.LOCAL],
            method=RetrievalMethod.FULLTEXT,
        )
    )
    assert out.chunks
    assert out.trace == []


def test_react_mode_can_retry_with_trace(mem_dir, tmp_db):
    record(
        kind="qa",
        topic="auth",
        text="Q: JWT 怎么防篡改？\nA: 服务端用密钥对 header.payload 做签名校验。",
        db_path=tmp_db,
    )
    agent = RetrievalAgent(db_path=tmp_db)
    workflow = agent.run(
        RetrievalInput(
            query="怎么让登录态不被篡改",
            scopes=[KnowledgeScope.LOCAL],
            method=RetrievalMethod.FULLTEXT,
        )
    )
    react = agent.run(
        RetrievalInput(
            query="怎么让登录态不被篡改",
            scopes=[KnowledgeScope.LOCAL],
            method=RetrievalMethod.FULLTEXT,
            mode=RetrievalMode.REACT,
        )
    )
    assert not any("JWT" in c.text for c in workflow.chunks)
    assert any("JWT" in c.text for c in react.chunks)
    assert len(react.trace) >= 2
    assert {"thought", "action", "observation"} <= set(react.trace[0])
    assert react.metrics["llm_calls"] == 0
    assert react.metrics["while_rounds"] >= 1
    assert react.metrics["retrieval_calls"] == react.metrics["react_steps"]


def test_react_metrics_do_not_loop_when_first_search_hits(mem_dir, tmp_db):
    record(
        kind="qa",
        topic="auth",
        text="Q: JWT 怎么防篡改？\nA: 服务端用密钥对 header.payload 做签名校验。",
        db_path=tmp_db,
    )
    out = RetrievalAgent(db_path=tmp_db).run(
        RetrievalInput(
            query="JWT 防篡改",
            scopes=[KnowledgeScope.LOCAL],
            method=RetrievalMethod.FULLTEXT,
            mode=RetrievalMode.REACT,
        )
    )
    assert out.chunks
    assert out.metrics == {
        "react_steps": 1,
        "llm_calls": 0,
        "retrieval_calls": 1,
        "while_rounds": 0,
        "rewrite_count": 0,
        "early_stopped": True,
    }


def test_workflow_v2_retries_but_abstains_on_obvious_non_memory(mem_dir, tmp_db):
    record(
        kind="qa",
        topic="auth",
        text="Q: JWT 怎么防篡改？\nA: 服务端用密钥对 header.payload 做签名校验。",
        db_path=tmp_db,
    )
    agent = RetrievalAgent(db_path=tmp_db)
    hit = agent.run(
        RetrievalInput(
            query="怎么让登录态不被篡改",
            scopes=[KnowledgeScope.LOCAL],
            method=RetrievalMethod.FULLTEXT,
            mode=RetrievalMode.WORKFLOW_V2,
        )
    )
    miss = agent.run(
        RetrievalInput(
            query="1+1 等于几",
            scopes=[KnowledgeScope.LOCAL],
            method=RetrievalMethod.FULLTEXT,
            mode=RetrievalMode.WORKFLOW_V2,
        )
    )
    assert any("JWT" in c.text for c in hit.chunks)
    assert hit.trace
    assert miss.chunks == []
    assert miss.trace[0]["observation"] == "simple_math"


def test_retrieval_mode_eval_compares_both_modes(tmp_path):
    report = run_eval(
        db_path=str(tmp_path / "ab.db"),
        memory_root=str(tmp_path / "mem"),
    )
    assert report["metrics"]["react"]["hit@3"] >= report["metrics"]["workflow"]["hit@3"]
    assert "workflow_v2" in report["metrics"]
    assert "false_positives" in report["metrics"]["react"]


def test_alias_rules_are_config_driven():
    rules = load_alias_rules()
    assert any(r.rule_id == "jwt_tamper" for r in rules)
    workflow = expand_aliases("怎么让登录态不被篡改", "workflow_v2")
    react = expand_aliases("我哪里薄弱", "react")
    assert any("JWT" in r for r in workflow)
    assert any("薄弱点" in r for r in react)


def test_realistic_query_fixture_loads():
    cases = load_query_cases()
    names = {c.name for c in cases}
    assert "real-jwt-memory" in names
    assert "real-negative-rust" in names
    assert any(c.should_recall and c.gold for c in cases)
    assert any(not c.should_recall for c in cases)
