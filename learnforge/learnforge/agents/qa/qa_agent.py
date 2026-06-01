"""QAAgent —— 问答子系统外壳（Design §3.2）。Phase 2：动态链路 + 轻路径 + 知识源选择。

链路：Router → (Retrieval) → Synthesizer → (Verifier)。
- chitchat 走轻路径，跳过检索/校验（Proposal R1）。
- 知识源/方式选择：concept/temporal 走共享知识库（课程/博客/面经，混合检索）；
  project 同时查本地用户库 + 共享库，召回个人上下文与公共知识。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ...contracts.agents.qa import (
    Citation,
    QAInput,
    QAOutput,
    RouterInput,
    SynthesizerInput,
    VerifierInput,
)
from ...contracts.agents.retrieval import RetrievalInput
from ...contracts.enums import AgentId, KnowledgeScope, QType, RetrievalMethod, Verdict
from ...llm.client import LLM
from ...mcp import tools as _toolmod
from ..base import BaseAgent
from ..react.loop import ReactRunner
from ..retrieval import RetrievalAgent
from .router import RouterAgent
from .synthesizer import SynthesizerAgent
from .verifier import VerifierAgent

_VERDICT_CONFIDENCE = {
    Verdict.VERIFIED: 0.85,
    Verdict.UNCERTAIN: 0.5,
    Verdict.UNVERIFIED: 0.4,
}

# 给模型看的 retrieval.search 参数 schema（真执行由 QA 按调用注入带 db_path 的 handler）。
_toolmod.register_schema(
    "retrieval.search",
    {"type": "object",
     "properties": {"query": {"type": "string", "description": "检索关键词或问题"}},
     "required": ["query"]},
    "检索本地用户库/共享知识库，返回相关片段（只读，用于项目/时效/不确定的事实）。",
)

_QA_REACT_SYSTEM = (
    "你是面向程序员的问答助手。\n"
    "【快路径】简单的概念/常识/八股问题：直接、简洁作答，**不要调用任何工具**。\n"
    "【取证路径】仅当问题涉及：用户自己的项目代码、最新/版本/时效信息、或你不确定的事实时，"
    "才调用 retrieval.search 获取证据（可多次），然后**基于证据**作答，不要编造引用。"
)


class QAAgent(BaseAgent):
    agent_id = AgentId.QA

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self.router = RouterAgent()
        # db_path 透传到检索：否则 QA 永远查 config.DB_PATH，忽略 Manager 注入的库。
        self.retrieval = RetrievalAgent(db_path=db_path)
        self.synthesizer = SynthesizerAgent()
        self.verifier = VerifierAgent()

    def _select_strategy(self, q_type: QType) -> dict:
        """由 QA shell skill 选择内部策略，而不是硬编码固定 workflow。"""
        if self.skill is None:
            return {}
        strategy = self.skill.strategy_for(q_type.value, default=QType.CONCEPT.value)
        for tool in self.skill.required_tools_for_strategy(q_type.value, default=QType.CONCEPT.value):
            self.require_tool(tool)
        return strategy

    @staticmethod
    def _strategy_scopes(strategy: dict, q_type: QType) -> Tuple[List[KnowledgeScope], RetrievalMethod]:
        # strategy_rules 是 skill 的轻量声明，使用字符串以便未来从 YAML/Markdown skill 读取。
        raw_scopes = strategy.get("scopes")
        if raw_scopes is None:
            raw_scopes = ["shared"] if q_type in (QType.CONCEPT, QType.TEMPORAL) else ["local", "shared"]
        scopes = [KnowledgeScope(s) for s in raw_scopes]
        method = RetrievalMethod(strategy.get("method", RetrievalMethod.HYBRID.value))
        return scopes, method

    def run(self, payload: QAInput) -> QAOutput:
        """默认 ReAct（简单题快答 / 项目·research 才调工具）；无 LLM 时回退固定链。"""
        if LLM.available and self.skill is not None:
            out = self._run_react(payload)
            if out is not None:
                return out
        return self._run_chain(payload)

    # ---------------- ReAct 模式（模型自主决定是否取证）----------------
    def _run_react(self, payload: QAInput) -> Optional[QAOutput]:
        chunks_sink: List = []

        def retrieval_handler(args: dict) -> dict:
            q = (args.get("query") or payload.question or "").strip()
            raw = args.get("scopes") or ["shared", "local"]
            try:
                scopes = [KnowledgeScope(s) for s in raw if s in ("shared", "local")] \
                    or [KnowledgeScope.SHARED, KnowledgeScope.LOCAL]
            except Exception:
                scopes = [KnowledgeScope.SHARED, KnowledgeScope.LOCAL]
            try:
                got = self.retrieval.run(
                    RetrievalInput(query=q, scopes=scopes, method=RetrievalMethod.HYBRID)
                ).chunks
            except Exception:
                got = []
            chunks_sink.extend(got)
            return {"chunks": [
                {"chunk_id": c.chunk_id, "text": (c.text or "")[:300],
                 "source_type": getattr(c, "source_type", ""),
                 "topic": (c.metadata.get("topic") if isinstance(c.metadata, dict) else None)}
                for c in got[:5]
            ]}

        res = ReactRunner(max_steps=3).run(
            self, user_prompt=payload.question, tool_names=["retrieval.search"],
            system=_QA_REACT_SYSTEM, handlers={"retrieval.search": retrieval_handler},
        )
        if res.degraded and not res.text:
            return None  # 降级回固定链

        citations = [
            Citation(chunk_id=c.chunk_id, source_type=c.source_type, snippet=(c.text or "")[:120])
            for c in chunks_sink[:5]
        ]
        used_tool = bool(res.tool_results)
        if chunks_sink:
            verdict, confidence = Verdict.UNCERTAIN, 0.6   # 有证据但未正式校验
        elif used_tool:
            verdict, confidence = Verdict.UNCERTAIN, 0.5   # 查了但没召回
        else:
            verdict, confidence = Verdict.UNCERTAIN, 0.7   # 简单题直接答（快路径）
        self.last_cost_usd = getattr(self.retrieval, "last_cost_usd", 0.0)
        topic = None
        for c in chunks_sink:
            topic = c.metadata.get("topic") if isinstance(c.metadata, dict) else None
            if topic:
                break
        return QAOutput(
            answer=res.text or "（未能生成回答）", citations=citations,
            verdict=verdict, confidence=confidence, topic=topic or "general",
        )

    # ---------------- 固定链（离线兜底，原 Phase 2 行为）----------------
    def _run_chain(self, payload: QAInput) -> QAOutput:
        self.require_tool("agent.router")
        route = self.router.run(RouterInput(question=payload.question))
        strategy = self._select_strategy(route.q_type)

        chunks = []
        need_retrieval = bool(strategy.get("retrieve", route.need_retrieval))
        if need_retrieval:
            self.require_tool("retrieval.search")
            scopes, method = self._strategy_scopes(strategy, route.q_type)
            chunks = self.retrieval.run(
                RetrievalInput(query=payload.question, scopes=scopes, method=method)
            ).chunks

        self.require_tool("agent.synthesizer")
        draft = self.synthesizer.run(
            SynthesizerInput(
                question=payload.question,
                retrieved=chunks,
                scoped_atoms=payload.scoped_atoms,
                project_context=payload.project_context_ref,
            )
        )

        need_verifier = bool(strategy.get("verify", route.need_verifier))
        if need_verifier:
            self.require_tool("agent.verifier")
            verified = self.verifier.run(
                VerifierInput(draft=draft.draft, claims=draft.claims, retrieved=chunks)
            )
            answer, verdict = verified.verified_answer, verified.verdict
        else:
            answer, verdict = draft.draft, Verdict.UNCERTAIN

        citations = [
            Citation(chunk_id=c.chunk_id, source_type=c.source_type, snippet=c.text[:120])
            for c in chunks
        ]
        # 成本聚合：QA 外壳本身不调 LLM，累计各子 agent 成本（供 trace/预算监控）。
        self.last_cost_usd = sum(
            getattr(a, "last_cost_usd", 0.0)
            for a in (self.router, self.retrieval, self.synthesizer, self.verifier)
        )
        # 信号话题：优先取检索片段 topic，否则回退到问题类型。
        topic = None
        for c in chunks:
            topic = c.metadata.get("topic") if isinstance(c.metadata, dict) else None
            if topic:
                break
        return QAOutput(
            answer=answer,
            citations=citations,
            verdict=verdict,
            confidence=_VERDICT_CONFIDENCE.get(verdict, 0.5),
            topic=topic or route.q_type.value,
        )
