"""Typed IO contracts for the pipeline agents (mirrors learnforge contracts/).

Defined before agents so every agent has an explicit Input/Output shape and the
Manager can wire them by contract, not by reaching into internals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..lib.schema import Chunk, Document


# --- agent-call tracing (so "链路是通的" is observable) ----------------------
@dataclass
class TraceEvent:
    agent: str
    action: str
    detail: str = ""
    ms: float = 0.0


class Tracer:
    """Records the chain of agent-to-agent calls for one request."""

    def __init__(self) -> None:
        self.events: List[TraceEvent] = []

    def log(self, agent: str, action: str, detail: str = "", ms: float = 0.0) -> None:
        self.events.append(TraceEvent(agent, action, detail, ms))

    def render(self) -> str:
        lines = ["agent call trace:"]
        for e in self.events:
            t = f" ({e.ms:.0f}ms)" if e.ms else ""
            lines.append(f"  {e.agent} · {e.action}{t}{(' — ' + e.detail) if e.detail else ''}")
        return "\n".join(lines)


class _Timed:
    """`with tracer.timed(agent, action) as t: ...; t.detail = '...'`"""

    def __init__(self, tracer: Tracer, agent: str, action: str) -> None:
        self.tracer, self.agent, self.action, self.detail = tracer, agent, action, ""

    def __enter__(self):
        self._t0 = time.time()
        return self

    def __exit__(self, *exc):
        self.tracer.log(self.agent, self.action, self.detail, (time.time() - self._t0) * 1000)
        return False


def timed(tracer: Tracer, agent: str, action: str) -> _Timed:
    return _Timed(tracer, agent, action)


# --- ingestion ----------------------------------------------------------------
@dataclass
class IngestRequest:
    documents: List[Document]
    strategy_override: Optional[str] = None  # force a chunker for all docs


@dataclass
class IndexedChunk:
    chunk: Chunk
    index_role: str  # main | downweighted | isolated
    block_types: List[str] = field(default_factory=list)


@dataclass
class KnowledgeStore:
    """The shared KB the RetrievalAgent reads (mirrors learnforge knowledge base)."""

    chunks: List[IndexedChunk] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)

    def by_role(self, *roles: str) -> List[IndexedChunk]:
        return [c for c in self.chunks if c.index_role in roles]


@dataclass
class IngestResult:
    store: KnowledgeStore
    n_docs: int
    n_chunks: int
    role_counts: Dict[str, int]


# --- retrieval ----------------------------------------------------------------
@dataclass
class RetrievalQuery:
    query: str
    top_k: int = 5
    include_downweighted: bool = True  # downweighted joins index at lower weight


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float
    index_role: str


@dataclass
class RetrievalResult:
    query: str
    hits: List[RetrievedChunk]
    method_used: str
    degraded: bool


# --- QA -----------------------------------------------------------------------
@dataclass
class QARequest:
    question: str
    top_k: int = 5


@dataclass
class Citation:
    doc_id: str
    source_type: str
    snippet: str


@dataclass
class QAResult:
    question: str
    answer: str
    citations: List[Citation]
    grounded: bool          # True == answer synthesised from retrieved evidence
    llm_used: bool          # False == extractive fallback (no key)
    degraded_retrieval: bool
    trace: Optional[Tracer] = None
