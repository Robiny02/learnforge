"""RetrievalAgent —— 统一检索入口（双层知识库 + 可扩展 RAG，Design §3.6 / §7）。

职责：只做"召回 + 排序"，不合成、不写任何学习状态（Proposal §5 决策 6）。

统一流程：
1. 条件改写（query 过长/模糊 → Haiku 改写一次，Design §3.6）。
2. 按 `method` 决定是否需要 embedding（VECTOR/HYBRID），不可用则降级到 FTS。
3. 按 `scopes` 选择知识源（SourceRegistry），各源按能力执行检索。
4. 跨源 RRF(k=60) 融合。
5. 可选 rerank 重排。
6. 截断 top_k 返回，并标注 `method_used` / `degraded`。

可插拔点：KnowledgeSource（知识源）、EmbeddingProvider（向量化）、Reranker（重排）。
向后兼容：保留 `rrf_fuse` 名称与 `RetrievalInput.backend` 字段映射。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from ..contracts.agents.retrieval import Chunk, RetrievalFilters, RetrievalInput, RetrievalOutput
from ..contracts.enums import AgentId, KnowledgeScope, ModelTier, RetrievalMethod, RetrievalMode
from ..knowledge.retrieval_aliases import expand_aliases
from ..knowledge.rerank import DEFAULT_RERANKER, LLMReranker, Reranker
from ..knowledge.sources import (
    KnowledgeSource,
    build_default_sources,
    rrf_fuse,
)
from ..llm.client import LLM, LLMUnavailable
from ..llm.embeddings import embed_query
from .base import BaseAgent

__all__ = ["RetrievalAgent", "rrf_fuse"]


class RetrievalAgent(BaseAgent):
    agent_id = AgentId.RETRIEVAL

    def __init__(
        self,
        db_path: Optional[str] = None,
        sources: Optional[List[KnowledgeSource]] = None,
        reranker: Optional[Reranker] = None,
    ) -> None:
        super().__init__()
        # PG 接入点：无 DSN 时与历史一致（SQLite 三源），有 DSN 时追加 PG 共享源。
        self._sources: List[KnowledgeSource] = sources or build_default_sources(db_path)
        self._reranker = reranker or DEFAULT_RERANKER
        self._llm_reranker = LLMReranker()

    def register_source(self, source: KnowledgeSource) -> None:
        """运行时注册新知识源（扩展位）。"""
        self._sources.append(source)

    def _sources_for(self, scopes: List[KnowledgeScope]) -> List[KnowledgeSource]:
        return [s for s in self._sources if s.scope in scopes]

    def _maybe_rewrite(self, query: str) -> str:
        """长(>120)或模糊 query → Haiku 改写一次（Design §3.6）。"""
        if len(query) <= 120 or not LLM.available or self.skill is None:
            return query
        self.require_tool("llm.complete")
        try:
            result = LLM.complete(
                prompt=f"改写检索 query：{query}",
                model_tier=ModelTier.HAIKU,
                system=self.skill.spec.system_prompt,
                max_tokens=64,
            )
            return result.text.strip() or query
        except LLMUnavailable:
            return query

    def run(self, payload: RetrievalInput) -> RetrievalOutput:
        self.require_tool("retrieval.search")
        if payload.mode == RetrievalMode.WORKFLOW_V2:
            return self._run_workflow_v2(payload)
        if payload.mode == RetrievalMode.REACT:
            return self._run_react(payload)
        return self._run_workflow(payload)

    def _run_workflow(
        self,
        payload: RetrievalInput,
        query_override: Optional[str] = None,
        skip_rewrite: bool = False,
    ) -> RetrievalOutput:
        query = query_override if query_override is not None else payload.query
        if not skip_rewrite:
            query = self._maybe_rewrite(query)
        scopes = payload.effective_scopes()
        sources = self._sources_for(scopes)

        degraded = False
        # 向量/混合需 embedding；不可用则降级（仍走全文）。
        embedding: Optional[List[float]] = None
        method = payload.method
        if method in (RetrievalMethod.VECTOR, RetrievalMethod.HYBRID):
            embedding = embed_query(query)
            if embedding is None:
                degraded = True
                if method == RetrievalMethod.VECTOR:
                    method = RetrievalMethod.FULLTEXT

        # 未配置的远程源属正常离线状态，不参与即可，不算降级。
        ranked_lists: List[List[Chunk]] = []
        for source in sources:
            try:
                hits = source.search(
                    query=query,
                    method=method,
                    filters=payload.filters,
                    top_k=payload.top_k,
                    embedding=embedding,
                )
            except Exception:
                hits = []
                degraded = True
            if hits:
                ranked_lists.append(hits)

        cap = payload.top_k * 2 if payload.rerank else payload.top_k
        if len(ranked_lists) == 1:
            # 单源（如仅本地长期记忆）：保留源内 decay/重要性真实分，不二次 RRF 抹平
            # （REQUIREMENTS 测试 §8.3/§14-8）。跨源才用 RRF 融合不同分数体系。
            fused = sorted(ranked_lists[0], key=lambda c: c.score, reverse=True)[:cap]
        else:
            fused = rrf_fuse(ranked_lists, top_k=cap)

        hit_count = sum(len(lst) for lst in ranked_lists)
        if payload.rerank:
            reranker = self._llm_reranker if self._llm_reranker.available else self._reranker
            fused = reranker.rerank(query, fused, top_k=payload.top_k)
        else:
            fused = fused[: payload.top_k]

        self._log_local_memory(scopes, hit_count, len(fused))
        return RetrievalOutput(chunks=fused, method_used=method, degraded=degraded)

    def _run_workflow_v2(self, payload: RetrievalInput) -> RetrievalOutput:
        """固定检索 workflow 的保守增强版。

        v2 不做自由 ReAct；它只是把“失败后改写”和“结果采纳闸门”显式化：
        1. 明显非记忆 query 直接 abstain；
        2. 原 query 先跑，可信则直接返回；
        3. 空结果/可疑结果才尝试受控 rewrite；
        4. 只有通过 intent gate 的候选才会被采纳。
        """
        trace: List[Dict[str, object]] = []
        query = (payload.query or "").strip()
        no_recall = _obvious_no_recall(query)
        if no_recall:
            trace.append(_react_step("query 不像记忆检索请求，直接 abstain。", "abstain", no_recall))
            return RetrievalOutput(chunks=[], method_used=payload.method, degraded=False, trace=trace)

        search_payload = _intent_filtered_payload(payload)
        first = self._run_workflow(search_payload)
        ok, reason = _accept_retrieval(query, query, first.chunks)
        trace.append(_react_step(
            thought="先跑原 query；只有结果通过 intent gate 才采纳。",
            action=f"workflow_v2.original(query={query!r})",
            observation=f"hits={len(first.chunks)}, accept={ok}, reason={reason}",
        ))
        if ok:
            first.trace = trace
            return first

        accepted_lists: List[List[Chunk]] = []
        degraded = first.degraded
        method_used = first.method_used
        for rewritten in _workflow_v2_rewrites(query):
            if rewritten == query:
                continue
            retry_payload = search_payload.model_copy(
                update={"method": RetrievalMethod.FULLTEXT, "rerank": False}
            )
            out = self._run_workflow(retry_payload, query_override=rewritten, skip_rewrite=True)
            degraded = degraded or out.degraded
            method_used = out.method_used
            ok, reason = _accept_retrieval(query, rewritten, out.chunks)
            trace.append(_react_step(
                thought="首轮未采纳，尝试受控 query rewrite，并对结果做 intent gate。",
                action=f"workflow_v2.rewrite(query={rewritten!r})",
                observation=f"hits={len(out.chunks)}, accept={ok}, reason={reason}",
            ))
            if ok:
                accepted_lists.append(out.chunks)

        if not accepted_lists:
            return RetrievalOutput(chunks=[], method_used=method_used, degraded=degraded, trace=trace)
        if len(accepted_lists) == 1:
            chunks = sorted(accepted_lists[0], key=lambda c: c.score, reverse=True)[: payload.top_k]
        else:
            chunks = rrf_fuse(accepted_lists, top_k=payload.top_k)
        return RetrievalOutput(chunks=chunks, method_used=method_used, degraded=degraded, trace=trace)

    def _run_react(self, payload: RetrievalInput) -> RetrievalOutput:
        """小步 ReAct 检索：先跑固定 workflow，再根据 observation 做有限重试。

        ReAct 只负责“检索策略调整”，仍然不合成答案、不写状态。无 LLM key 时使用确定性
        query expansion；有 key 时仍可复用 `_maybe_rewrite()` 的 Haiku 改写。
        """
        trace: List[Dict[str, object]] = []
        retrieval_calls = 0
        while_rounds = 0

        first = self._run_workflow(payload)
        retrieval_calls += 1
        trace.append(_react_step(
            thought="先按输入的 scopes/method 执行固定检索 workflow，观察是否已有足够候选。",
            action=f"workflow(query={payload.query!r}, method={payload.method.value})",
            observation=f"hits={len(first.chunks)}, method_used={first.method_used.value}, degraded={first.degraded}",
        ))
        if first.chunks:
            first.trace = trace
            first.metrics = _react_metrics(trace, llm_calls=0, retrieval_calls=retrieval_calls,
                                           while_rounds=while_rounds, rewrite_count=0,
                                           early_stopped=True)
            return first

        best = first
        rewrites, llm_calls = self._react_rewrites_with_stats(payload.query)
        for rewritten in rewrites:
            if rewritten == payload.query:
                continue
            while_rounds += 1
            retry_payload = payload.model_copy(
                update={"method": RetrievalMethod.FULLTEXT, "rerank": False}
            )
            out = self._run_workflow(retry_payload, query_override=rewritten, skip_rewrite=True)
            retrieval_calls += 1
            trace.append(_react_step(
                thought="首轮无命中，尝试把用户表述改写成可能出现在个人/共享知识里的检索词。",
                action=f"workflow(query={rewritten!r}, method=fulltext)",
                observation=f"hits={len(out.chunks)}, method_used={out.method_used.value}, degraded={out.degraded}",
            ))
            if len(out.chunks) > len(best.chunks):
                best = out
            if out.chunks:
                out.trace = trace
                out.metrics = _react_metrics(trace, llm_calls=llm_calls,
                                             retrieval_calls=retrieval_calls,
                                             while_rounds=while_rounds,
                                             rewrite_count=len(rewrites), early_stopped=True)
                return out

        # 仅当调用方没有显式 scopes 时，ReAct 才扩大到默认双层；显式 scopes 代表上游有边界意图。
        if payload.scopes is None:
            broaden = payload.model_copy(
                update={
                    "scopes": [KnowledgeScope.LOCAL, KnowledgeScope.SHARED],
                    "method": RetrievalMethod.FULLTEXT,
                    "rerank": False,
                }
            )
            out = self._run_workflow(broaden)
            retrieval_calls += 1
            trace.append(_react_step(
                thought="改写仍无命中，且上游未显式限定 scope，扩大到 local+shared 做最后一次检索。",
                action="workflow(scopes=local+shared, method=fulltext)",
                observation=f"hits={len(out.chunks)}, method_used={out.method_used.value}, degraded={out.degraded}",
            ))
            if len(out.chunks) > len(best.chunks):
                best = out

        best.trace = trace
        best.metrics = _react_metrics(trace, llm_calls=llm_calls,
                                      retrieval_calls=retrieval_calls,
                                      while_rounds=while_rounds,
                                      rewrite_count=len(rewrites), early_stopped=False)
        return best

    @staticmethod
    def _react_rewrites(query: str) -> List[str]:
        return RetrievalAgent._react_rewrites_with_stats(query)[0]

    @staticmethod
    def _react_rewrites_with_stats(query: str) -> tuple:
        """离线 ReAct 的保守 query expansion。

        这不是替代 embedding 的语义检索，只是让无 key 环境也能比较“观察后重试”的收益。
        词表保持很小，并聚焦通用学习/面试表述，避免把 recall agent 变成另一个知识库。
        """
        q = (query or "").strip()
        if not q:
            return [], 0
        rewrites: List[str] = []
        llm_rewrites, llm_calls = _openrouter_rewrites_with_stats(q)
        rewrites.extend(llm_rewrites)
        rewrites.extend(f"{q} {expansion}" for expansion in expand_aliases(q, RetrievalMode.REACT.value))
        words = [w for w in q.replace("？", " ").replace("?", " ").split() if len(w) > 1]
        if words and " ".join(words) != q:
            rewrites.append(" ".join(words))
        # 去重且保序。
        return _dedupe_nonempty(rewrites), llm_calls

    @staticmethod
    def _log_local_memory(scopes: List[KnowledgeScope], hits: int, used: int) -> None:
        """LOCAL 检索 = 搜索长期(个人)记忆 → 记忆日志面板（REQUIREMENTS §3.1/§3.2）。"""
        if KnowledgeScope.LOCAL not in scopes:
            return
        from ..memory.log import INJECT, READ, MEMORY_LOG

        MEMORY_LOG.record(READ, "搜索长期记忆", f"命中 {hits} 条")
        MEMORY_LOG.record(INJECT, "注入长期记忆", f"采用 {used} 条", count=used)


def _react_step(thought: str, action: str, observation: str) -> Dict[str, str]:
    return {"thought": thought, "action": action, "observation": observation}


def _react_metrics(
    trace: List[Dict[str, object]],
    llm_calls: int,
    retrieval_calls: int,
    while_rounds: int,
    rewrite_count: int,
    early_stopped: bool,
) -> Dict[str, object]:
    return {
        "react_steps": len(trace),
        "llm_calls": llm_calls,
        "retrieval_calls": retrieval_calls,
        "while_rounds": while_rounds,
        "rewrite_count": rewrite_count,
        "early_stopped": early_stopped,
    }


def _workflow_v2_rewrites(query: str) -> List[str]:
    """Workflow v2 的受控改写：真实模型优先，小词表兜底。

    与 ReAct 的 `_react_rewrites` 不同，这里不加入“哪里弱/上次”这种泛化 weak alias，
    以免近域负样本被拉进已有薄弱点。
    """
    q = (query or "").strip()
    if not q:
        return []
    rewrites: List[str] = []
    rewrites.extend(_openrouter_rewrites(q))
    rewrites.extend(
        f"{q} {expansion}" for expansion in expand_aliases(q, RetrievalMode.WORKFLOW_V2.value)
    )
    return _dedupe_nonempty(rewrites)


def _obvious_no_recall(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "empty_query"
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", q):
        return "punctuation_only"
    if re.fullmatch(r"\s*\d+\s*[-+*/]\s*\d+\s*(等于几|=|是多少)?\s*", q):
        return "simple_math"
    cjk = re.findall(r"[\u4e00-\u9fff]", q)
    ascii_terms = re.findall(r"[A-Za-z][A-Za-z0-9_+-]{1,}", q)
    if len(cjk) <= 1 and not ascii_terms:
        return "too_short"
    if "AND OR" in q.upper() or q.count('"') % 2 == 1:
        return "fts_operator_like_input"
    return ""


def _accept_retrieval(original_query: str, retrieval_query: str, chunks: List[Chunk]) -> tuple:
    if not chunks:
        return False, "no_hits"
    query_terms = _specific_terms(original_query)
    rewrite_terms = _specific_terms(retrieval_query)
    haystack = " ".join(c.text for c in chunks[:3]).lower()
    external_missing = [t for t in _external_terms(original_query) if t not in haystack]
    if len(external_missing) >= 2:
        return False, f"missing_external_terms={external_missing[:3]}"

    # 如果用户问了很具体的外部技术词，而候选完全不含这些词，倾向 abstain。
    missing = [t for t in query_terms if t not in haystack]
    if len(query_terms) >= 2 and len(missing) >= 2:
        # rewrite 命中的技术词可以救回语义改写场景，例如“快照读”→ MVCC/read view。
        matched_rewrite = [t for t in rewrite_terms if t in haystack]
        if len(matched_rewrite) < 2:
            return False, f"missing_specific_terms={missing[:3]}"

    if _is_memory_intent(original_query) and _memory_anchor_hits(retrieval_query, chunks):
        return True, "memory_intent_anchor"

    if not _has_overlap_or_rewrite_anchor(original_query, retrieval_query, haystack):
        return False, "no_query_or_rewrite_anchor"
    return True, "accepted"


def _specific_terms(query: str) -> List[str]:
    q = (query or "").lower()
    stop = {
        "and", "or", "the", "how", "what", "why", "with", "without", "where",
        "does", "did", "之前", "怎么", "什么", "哪里", "处理", "区别",
    }
    terms = [t for t in re.findall(r"[a-z][a-z0-9_+-]{2,}", q) if t not in stop]
    phrases = [
        "登录态", "篡改", "读多写少", "分布式锁", "续期", "快照读", "行版本",
        "索引", "消息队列", "顺序", "消费者", "重复", "扣款", "突发", "限速",
        "线程池", "队列", "跨服务", "一致性", "退款", "风控", "拒付", "争议",
        "昨天", "上次", "最近", "模拟面试", "没答好", "缺点", "薄弱点", "复盘",
        "反馈", "不清晰", "不稳定",
    ]
    terms.extend(p.lower() for p in phrases if p in q)
    return _dedupe_nonempty(terms)


def _external_terms(query: str) -> List[str]:
    q = (query or "").lower()
    allow_missing = {
        "sql", "explain", "db", "token", "core", "max", "queue", "reject",
        "read", "view", "gc", "roots", "stw", "mock",
    }
    terms = [t for t in re.findall(r"[a-z][a-z0-9_+-]{2,}", q) if t not in allow_missing]
    return _dedupe_nonempty(terms)


def _has_overlap_or_rewrite_anchor(original_query: str, retrieval_query: str, haystack: str) -> bool:
    original_terms = _specific_terms(original_query)
    rewrite_terms = _specific_terms(retrieval_query)
    if any(t in haystack for t in original_terms):
        return True
    return sum(1 for t in rewrite_terms if t in haystack) >= 1


def _is_memory_intent(query: str) -> bool:
    q = query or ""
    event_terms = ("昨天", "上次", "最近", "之前", "当时", "mock", "模拟面试")
    recall_terms = ("缺点", "薄弱点", "没答好", "复盘", "回忆", "记录")
    return any(t in q for t in event_terms) and any(t in q for t in recall_terms)


def _is_mock_intent(query: str) -> bool:
    q = (query or "").lower()
    return "mock" in q or "模拟面试" in q


def _intent_filtered_payload(payload: RetrievalInput) -> RetrievalInput:
    if payload.filters is not None:
        return payload
    if _is_mock_intent(payload.query):
        return payload.model_copy(update={"filters": RetrievalFilters(kind="mock")})
    return payload


def _memory_anchor_hits(retrieval_query: str, chunks: List[Chunk]) -> bool:
    rq = retrieval_query.lower()
    haystack = " ".join(c.text for c in chunks[:3]).lower()
    metas = [c.metadata or {} for c in chunks[:3]]
    if any(m.get("kind") in ("weak", "mock") for m in metas):
        return True
    anchors = ("mock", "薄弱点", "反馈", "不清晰", "不稳定", "缺点")
    return any(a in rq for a in anchors) and any(a in haystack for a in anchors)


def _dedupe_nonempty(items: List[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out


def _openrouter_rewrites(query: str) -> List[str]:
    return _openrouter_rewrites_with_stats(query)[0]


def _openrouter_rewrites_with_stats(query: str) -> tuple:
    """可选真实模型改写。

    默认关闭；只有设置 LF_RETRIEVAL_REACT_LLM=1 且 OPENROUTER_API_KEY 存在时才调用网络。
    这里用标准库 HTTP，避免把 OpenAI SDK 变成测试/运行时硬依赖。
    """
    if os.getenv("LF_RETRIEVAL_REACT_LLM") != "1":
        return [], 0
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return [], 0

    model = os.getenv("LF_OPENROUTER_MODEL", "openai/gpt-4o-mini")
    prompt = (
        "You are a retrieval query rewriter for a programmer learning-memory system. "
        "Return JSON only: {\"queries\":[...]} with 1 to 3 short keyword-style queries. "
        "Do not answer the user. Preserve uncertainty; do not invent private facts. "
        "Prefer technical terms likely found in notes. User query:\n"
        f"{query}"
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 120,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://learnforge.local",
            "X-Title": "LearnForge Retrieval Eval",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - explicit API endpoint
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError):
        return [], 1
    try:
        text = payload["choices"][0]["message"]["content"]
        data = json.loads(_extract_json_object(text))
        queries = data.get("queries") or []
    except Exception:
        return [], 1
    out: List[str] = []
    for q in queries[:3]:
        s = str(q).strip()
        if s:
            out.append(s)
    return out, 1


def _extract_json_object(text: str) -> str:
    raw = (text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start:end + 1]
    return raw
