"""PipelineManager — the orchestrator (mirrors learnforge ManagerAgent).

Sole entry point that wires the worker agents and owns the shared KnowledgeStore:
    ingest_all()  : IngestionAgent → store          (build the KB once)
    ask(question) : QAAgent → RetrievalAgent → LLM  (answer over the KB)

Every call threads a Tracer so the agent-to-agent chain is observable.
"""

from __future__ import annotations

from typing import List, Optional

from ..lib.loaders import load_all
from ..lib.schema import Document
from .agent_base import LLM
from .contracts import IngestRequest, IngestResult, QARequest, QAResult, Tracer
from .ingestion_agent import IngestionAgent
from .qa_agent import QAAgent
from .retrieval_agent import RetrievalAgent


class PipelineManager:
    name = "manager"

    def __init__(self) -> None:
        self.ingestion = IngestionAgent()
        self.store = None
        self.retrieval: Optional[RetrievalAgent] = None
        self.qa: Optional[QAAgent] = None
        self.last_ingest: Optional[IngestResult] = None

    def ingest_all(
        self, documents: Optional[List[Document]] = None, strategy_override: Optional[str] = None
    ) -> IngestResult:
        tracer = Tracer()
        docs = documents if documents is not None else load_all()
        tracer.log(self.name, "plan", f"ingest {len(docs)} docs (override={strategy_override})")
        result = self.ingestion.run(
            IngestRequest(documents=docs, strategy_override=strategy_override), tracer=tracer
        )
        self.store = result.store
        self.retrieval = RetrievalAgent(self.store)
        self.qa = QAAgent(self.retrieval)
        self.last_ingest = result
        self._ingest_trace = tracer
        return result

    def ask(self, question: str, top_k: int = 5) -> QAResult:
        if self.qa is None:
            self.ingest_all()
        tracer = Tracer()
        tracer.log(self.name, "route", f"QA: '{question[:40]}'")
        assert self.qa is not None
        return self.qa.run(QARequest(question=question, top_k=top_k), tracer=tracer)

    @property
    def llm_available(self) -> bool:
        return LLM.available
