"""QAAgent —— 问答子系统外壳（Design §3.2）。Phase 2：动态链路 + 轻路径 + 知识源选择。

链路：Router → (Retrieval) → Synthesizer → (Verifier)。
- chitchat 走轻路径，跳过检索/校验（Proposal R1）。
- 知识源/方式选择：concept/temporal 走共享知识库（课程/博客/面经，混合检索）；
  project 同时查本地用户库 + 共享库，召回个人上下文与公共知识。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ...config import AGENT_BOUNDS
from ...contracts.agents.qa import (
    Citation,
    QAInput,
    QAOutput,
    RouterInput,
    SynthesizerInput,
    VerifierInput,
)
from ...contracts.agents.retrieval import RetrievalInput
from ...contracts.enums import AgentId, KnowledgeScope, ModelTier, QType, RetrievalMethod, Verdict
from ...llm.client import LLM
from ...mcp import tools as _toolmod
from ..base import BaseAgent
from ..react.loop import ReactRunner
from ..retrieval import RetrievalAgent
from .concept_cards import answer_for as local_concept_answer
from .router import RouterAgent
from .synthesizer import SynthesizerAgent
from .verifier import VerifierAgent

_VERDICT_CONFIDENCE = {
    Verdict.VERIFIED: 0.85,
    Verdict.UNCERTAIN: 0.5,
    Verdict.UNVERIFIED: 0.4,
}

_FAST_CONCEPT_CUES = (
    "什么是", "介绍一下", "解释一下", "讲讲", "说说", "区别", "原理",
    "机制", "流程", "为什么", "如何理解", "怎么理解", "怎么选",
    "怎么保证", "如何保证", "可靠性", "一致性", "可用性", "幂等", "顺序",
    "怎么启动", "启动流程", "生命周期",
)
_EXTERNAL_OR_COMPLEX_CUES = (
    "最新", "今天", "现在", "版本", "官网", "源码", "项目", "repo",
    "github", "论文", "链接", "url", "http",
)
_LOCAL_CONTEXT_CUES = (
    "我的", "我之前", "上次", "以前", "之前", "记忆", "记录", "本地",
    "文件", "报告", "笔记", "学习计划", "复盘", "memory", "md",
)
_NON_QA_CUES = (
    "诊断", "弱点", "薄弱", "计划", "规划", "路径", "mock", "模拟面试", "出题",
)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_FILE_ROOTS = ("docs", "data/memory")

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
    "目标不是只给一句定义，而是帮助用户形成可复习、可面试口述、可落到工程判断的理解。\n"
    "【快路径】简单的概念/常识/八股问题：如果你有把握，**不要调用任何工具**，但回答仍要结构化、充分：\n"
    "1) 先给一句核心结论；2) 解释机制/流程；3) 给适用场景与取舍；4) 点出常见误区；"
    "5) 给一段面试可口述版本或小例子。用户明确要求“一句话/简短”时再压缩。\n"
    "【取证路径】仅当问题涉及：用户自己的项目代码、最新/版本/时效信息、或你不确定的事实时，"
    "才调用 retrieval.search 获取证据（可多次），然后**基于证据**作答，不要编造引用。"
)


class FastQARouteDecision(BaseModel):
    """Tiny LLM decision for fast QA evidence routing."""

    use_memory: bool = Field(
        default=False,
        description="是否需要读取个人长期/短期记忆，例如上次记录、我的薄弱点、历史 QA、mock 反馈。",
    )
    use_files: bool = Field(
        default=False,
        description="是否需要读取本地 markdown 文件，例如 docs/reports/data/memory 下的报告、计划、笔记。",
    )
    answer_directly: bool = Field(
        default=True,
        description="不需要本地上下文时，是否可以直接回答。",
    )
    direct_answer: Optional[str] = Field(
        default=None,
        description="当 answer_directly=true 且无需本地上下文时，直接给用户的 Markdown 答案。",
    )
    reason: str = ""
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class QAAgent(BaseAgent):
    agent_id = AgentId.QA

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self._db_path = db_path
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
        # 带附件(图片/文档)→ 直接走 synthesizer(文档/图片即证据)，跳过 router/retrieval/verifier：
        # verifier 会把分节结构压成一段(实测让「介绍这个文档」退化成一句话)，且多 3 次 LLM 往返。
        if payload.attachment_text or payload.image_data_urls:
            return self._run_attachment(payload)
        if self._is_fast_concept(payload):
            return self._run_fast_concept(payload)
        if LLM.available and self.skill is not None:
            out = self._run_react(payload)
            if out is not None:
                return out
        return self._run_chain(payload)

    # ---------------- 明确普通八股/概念题：一次合成，跳过 router/retrieval/verifier ----------------
    @staticmethod
    def is_fast_concept_request(payload: QAInput) -> bool:
        return QAAgent._is_fast_concept(payload)

    @staticmethod
    def _is_fast_concept(payload: QAInput) -> bool:
        q = (payload.question or "").strip().lower()
        if not q or payload.project_context_ref or payload.scoped_atoms:
            return False
        if any(cue in q for cue in _NON_QA_CUES):
            return False
        if any(cue in q for cue in _EXTERNAL_OR_COMPLEX_CUES):
            return False
        has_fast_cue = any(cue in q for cue in _FAST_CONCEPT_CUES)
        has_local_cue = any(cue in q for cue in _LOCAL_CONTEXT_CUES)
        return len(q) <= 120 and (has_fast_cue or has_local_cue)

    def _run_fast_concept(self, payload: QAInput) -> QAOutput:
        self.last_cost_usd = 0.0
        route = self._decide_fast_route(payload)
        if route and (route.use_memory or route.use_files):
            return self._run_fast_with_local_context(payload, route)

        local = local_concept_answer(payload.question)
        if local:
            return QAOutput(
                answer=local,
                citations=[],
                verdict=Verdict.UNCERTAIN,
                confidence=0.82,
                topic="concept_fast",
            )

        if route and route.answer_directly and self._usable_direct_answer(route.direct_answer):
            self.last_cost_usd = getattr(self, "last_cost_usd", 0.0)
            return QAOutput(
                answer=route.direct_answer.strip(),
                citations=[],
                verdict=Verdict.UNCERTAIN,
                confidence=max(0.68, min(route.confidence, 0.86)),
                topic="concept_fast",
            )

        answer = self._fast_direct_answer(payload.question)
        if answer:
            return QAOutput(
                answer=answer,
                citations=[],
                verdict=Verdict.UNCERTAIN,
                confidence=0.74,
                topic="concept_fast",
            )

        if not LLM.available:
            return self._run_chain(payload)

        self.require_tool("agent.synthesizer")
        draft = self.synthesizer.run(
            SynthesizerInput(
                question=payload.question,
                retrieved=[],
                scoped_atoms=payload.scoped_atoms,
                project_context=payload.project_context_ref,
                session_context=payload.session_context,
            )
        )
        self.last_cost_usd = getattr(self.synthesizer, "last_cost_usd", 0.0)
        return QAOutput(
            answer=draft.draft,
            citations=[],
            verdict=Verdict.UNCERTAIN,
            confidence=0.72,
            topic="concept_fast",
        )

    def _decide_fast_route(self, payload: QAInput) -> Optional[FastQARouteDecision]:
        """Use a tiny LLM call to decide whether fast QA needs local evidence.

        This call is intentionally outside the full Manager/Router/RAG chain: it only
        chooses local evidence sources and may provide a direct answer when no local
        context is needed.
        """
        if not LLM.available:
            return self._fallback_fast_route(payload.question)
        self.require_tool("llm.complete_structured")
        prompt = (
            f"用户问题：{payload.question}\n\n"
            "判断这个 QA 问题是否需要读取本地上下文。只考虑两类本地上下文：\n"
            "- memory: 用户个人记忆，包括 MEMORY.md、daily memory、历史 QA、mock 反馈、复盘。\n"
            "- files: 本地 markdown 文件，包括 docs/reports/data/memory 下的计划、报告、笔记。\n\n"
            "规则：\n"
            "1. 如果问题只是通用技术概念/八股，use_memory=false,use_files=false，并直接给一个较完整的 Markdown 答案。\n"
            "2. 如果用户说“我的/上次/之前/根据记录/复盘/本地文件/报告/笔记”，优先打开对应本地上下文。\n"
            "3. 不要因为问题是技术概念就默认查库；只有用户需要个人上下文或本地文件时才查。\n"
            "4. 如果能直接回答，答案要有学习价值：核心结论、机制、细节、取舍、误区、面试口述版。\n"
            "5. 如果是 MQ/中间件可靠性问题，必须按 Producer、Broker、Consumer、集群/故障切换、"
            "事务/顺序/幂等这些角色链路拆解，不要只罗列机制名词。\n"
            "6. 如果是框架启动/生命周期问题，必须按入口、环境准备、容器创建、refresh、自动配置、"
            "内嵌服务启动、Runner/事件回调拆解，不要只贴 main 方法。\n"
        )
        try:
            obj, result = LLM.complete_structured(
                prompt=prompt,
                schema=FastQARouteDecision,
                model_tier=ModelTier.HAIKU,
                system="你是 LearnForge QA 的快速证据路由器。只决定是否读取本地上下文；必要时直接回答。",
                max_tokens=1400,
                timeout_s=min(2.5, AGENT_BOUNDS.get(AgentId.QA, {}).get("timeout_s", 8.0)),
                retries=0,
            )
            self.last_cost_usd = result.cost_usd
            return obj
        except Exception:
            return self._fallback_fast_route(payload.question)

    @staticmethod
    def _fallback_fast_route(question: str) -> FastQARouteDecision:
        q = (question or "").lower()
        return FastQARouteDecision(
            use_memory=any(cue in q for cue in ("我的", "上次", "之前", "记忆", "记录", "复盘")),
            use_files=any(cue in q for cue in ("本地", "文件", "报告", "笔记", "md")),
            answer_directly=True,
            direct_answer=None,
            reason="heuristic_fallback",
            confidence=0.5,
        )

    @staticmethod
    def _usable_direct_answer(answer: Optional[str]) -> bool:
        text = (answer or "").strip()
        return len(text) >= 80 and ("###" in text or "\n-" in text or "\n1." in text)

    def _run_fast_with_local_context(
        self, payload: QAInput, route: FastQARouteDecision
    ) -> QAOutput:
        context, chunks = self._collect_local_context(payload.question, route)
        answer = self._fast_direct_answer(payload.question, context=context)
        citations = [
            Citation(chunk_id=c.chunk_id, source_type=c.source_type, snippet=(c.text or "")[:120])
            for c in chunks[:5]
        ]
        if not answer:
            if context:
                answer = (
                    "### 核心结论\n\n"
                    "我读取了本地上下文，但这次没有成功生成完整回答。先给你摘出最相关的依据：\n\n"
                    f"{context[:1200]}"
                )
            else:
                answer = self._fast_direct_answer(payload.question) or "本地上下文没有找到明确依据。"
        return QAOutput(
            answer=answer,
            citations=citations,
            verdict=Verdict.UNCERTAIN,
            confidence=0.7 if context else 0.55,
            topic="concept_fast_local" if context else "concept_fast",
        )

    def _collect_local_context(
        self, question: str, route: FastQARouteDecision
    ) -> Tuple[str, List]:
        parts: List[str] = []
        chunks: List = []
        if route.use_memory:
            try:
                from ...memory.base import MEMORY

                stable = MEMORY.memory_prefix(db_path=getattr(self, "_db_path", None)).strip()
                if stable:
                    parts.append("【MEMORY.md 稳定记忆】\n" + stable[:1200])
            except Exception:
                pass
            try:
                self.require_tool("retrieval.search")
                out = self.retrieval.run(
                    RetrievalInput(
                        query=question,
                        scopes=[KnowledgeScope.LOCAL],
                        method=RetrievalMethod.FULLTEXT,
                        top_k=4,
                        rerank=False,
                    )
                )
                chunks = out.chunks
                if chunks:
                    body = "\n".join(
                        f"- [{c.chunk_id}] {(c.text or '').strip()[:320]}" for c in chunks[:4]
                    )
                    parts.append("【长期记忆召回】\n" + body)
            except Exception:
                pass
        if route.use_files:
            file_hits = _search_local_markdown(question)
            if file_hits:
                body = "\n".join(
                    f"- [{hit['path']}] {hit['excerpt']}" for hit in file_hits[:4]
                )
                parts.append("【本地 markdown 文件】\n" + body)
        return "\n\n".join(parts).strip(), chunks

    def _fast_direct_answer(self, question: str, context: str = "") -> Optional[str]:
        if not LLM.available:
            return None
        self.require_tool("llm.complete")
        context_part = f"\n\n本地上下文：\n{context[:2200]}\n" if context else ""
        prompt = (
            f"问题：{question}{context_part}\n"
            "请直接回答。要求：\n"
            "- 用 Markdown 分节，避免整段堆在一起。\n"
            "- 默认给可复习、可面试、可落地的深度：核心结论、机制/流程、关键细节、场景取舍、常见误区、面试口述版。\n"
            "- 如果问题是 MQ/消息中间件可靠性，必须按 Producer → Broker → Consumer → 集群/故障切换 → 事务/顺序/幂等 的链路回答；说明每一段解决哪类丢失/重复/乱序风险。\n"
            "- 如果问题是框架启动/生命周期，必须按 main 入口 → SpringApplication 构建 → Environment 准备 → ApplicationContext 创建 → refresh → 自动配置/Bean 生命周期 → 内嵌 WebServer → Runner/事件 的链路回答。\n"
            "- 如果给了本地上下文，优先基于本地上下文；没有依据的地方明确说是通用补充。\n"
            "- 不要编造本地文件或记忆中不存在的事实。"
        )
        try:
            result = LLM.complete(
                prompt=prompt,
                model_tier=ModelTier.SONNET,
                system="你是 LearnForge 的快速 QA 答案生成器，回答要深入但不要走完整 RAG 链。",
                max_tokens=1800,
                timeout_s=5.0,
            )
            self.last_cost_usd += result.cost_usd
            text = result.text.strip()
            return text or None
        except Exception:
            return None

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
                    RetrievalInput(query=q, scopes=scopes, method=RetrievalMethod.FULLTEXT)
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

    # ---------------- 附件问答（文档/图片即证据，单次合成，保结构化深度）----------------
    def _run_attachment(self, payload: QAInput) -> QAOutput:
        """上传文档/图片的问答：附件本身就是证据，直接交 synthesizer 一次合成。

        不走 router/retrieval/verifier——附件内容无需对共享库核验，verifier 还会把
        结构化分节压扁成一段；单次合成既保深度又省 3 次 LLM 往返(降延迟)。
        """
        self.require_tool("agent.synthesizer")
        draft = self.synthesizer.run(
            SynthesizerInput(
                question=payload.question,
                retrieved=[],
                scoped_atoms=payload.scoped_atoms,
                project_context=payload.project_context_ref,
                session_context=payload.session_context,
                attachment_text=payload.attachment_text,
                image_data_urls=payload.image_data_urls,
            )
        )
        self.last_cost_usd = getattr(self.synthesizer, "last_cost_usd", 0.0)
        return QAOutput(
            answer=draft.draft,
            citations=[],
            verdict=Verdict.UNCERTAIN,
            confidence=0.7,
            topic="attachment",
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
                session_context=payload.session_context,
                attachment_text=payload.attachment_text,
                image_data_urls=payload.image_data_urls,
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


def _search_local_markdown(question: str, limit: int = 4) -> List[Dict[str, str]]:
    """Lightweight local markdown search for QA fast lane.

    This is deliberately not an indexer: it only scans a bounded set of local
    markdown files that LearnForge itself produces or owns.
    """
    terms = _query_terms(question)
    if not terms:
        return []
    hits: List[Tuple[int, str, str]] = []
    for rel in _LOCAL_FILE_ROOTS:
        root = _PROJECT_ROOT / rel
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md"))[:120]:
            if path.name == "MEMORY.md":
                continue
            try:
                text = path.read_text(encoding="utf-8")[:50000]
            except OSError:
                continue
            lower = text.lower()
            score = sum(lower.count(t) for t in terms)
            if score <= 0:
                continue
            pos = min((lower.find(t) for t in terms if lower.find(t) >= 0), default=0)
            start = max(0, pos - 120)
            end = min(len(text), pos + 360)
            excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
            hits.append((score, str(path), excerpt))
    hits.sort(key=lambda x: x[0], reverse=True)
    return [{"path": p, "excerpt": e} for _, p, e in hits[:limit]]


def _query_terms(question: str) -> List[str]:
    raw = (question or "").lower()
    # Keep ASCII words and longer CJK runs; single CJK chars are too broad for file scanning.
    terms = re.findall(r"[a-z0-9_+#.-]{2,}|[\u4e00-\u9fff]{2,}", raw)
    stop = {
        "介绍一下", "解释一下", "讲讲", "说说", "什么是", "为什么", "如何理解",
        "我的", "上次", "之前", "本地", "文件", "报告", "笔记", "记忆", "记录",
    }
    return [t for t in terms if t not in stop][:8]
