"""IngestionAgent — orchestrates document → indexed knowledge store (Plan §6 Phase 3).

Per document: split into blocks → BlockClassifierAgent labels them → ChunkingAgent
chunks → each chunk inherits an index_role from the blocks it covers
(conservative: a chunk that touches ANY main block stays main, so technical
content is never demoted; a chunk made only of isolated noise is isolated).

This is the agent that calls other agents — the heart of "agent 之间的调用".
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..lib.block_split import split_document
from ..lib.schema import Block, Chunk, Document
from .agent_base import PipelineAgent
from .block_classifier import BlockClassifierAgent
from .chunking_agent import ChunkingAgent
from .contracts import IndexedChunk, IngestRequest, IngestResult, KnowledgeStore, Tracer

_ROLE_RANK = {"main": 0, "downweighted": 1, "isolated": 2}  # lower = keep harder


class IngestionAgent(PipelineAgent):
    name = "ingestion"
    capabilities = ["ingest.run", "chunk.document", "classify.block"]

    def __init__(
        self,
        classifier: Optional[BlockClassifierAgent] = None,
        chunker: Optional[ChunkingAgent] = None,
    ) -> None:
        super().__init__()
        self.classifier = classifier or BlockClassifierAgent()
        self.chunker = chunker or ChunkingAgent()

    def _role_for_chunk(self, chunk: Chunk, blocks: List[Block]) -> tuple:
        """Return (index_role, block_types) by overlap with classified blocks."""
        ctext = chunk.text
        head = ctext.strip()[:40]
        best_role, types = "isolated", []
        # Structural children carry their originating block_id directly.
        bid = chunk.metadata.get("block_id")
        touched: List[Block] = []
        for b in blocks:
            if bid and b.block_id == bid:
                touched = [b]
                break
            bh = b.text.strip()[:40]
            if bh and (bh in ctext or head in b.text):
                touched.append(b)
        if not touched:
            return "main", []  # default keep when we can't map (whole-doc windows)
        for b in touched:
            role = (b.metadata.get("index_role")) or "main"
            types.append(b.block_type or "useful_context")
            if _ROLE_RANK[role] < _ROLE_RANK[best_role]:
                best_role = role
        return best_role, sorted(set(types))

    def run(self, req: IngestRequest, tracer: Optional[Tracer] = None) -> IngestResult:
        self.require("ingest.run")
        store = KnowledgeStore()
        role_counts: Dict[str, int] = {"main": 0, "downweighted": 0, "isolated": 0}

        for doc in req.documents:
            doc.blocks = split_document(doc)
            self.classifier.run(doc)  # agent-to-agent call → labels blocks
            chunks = self.chunker.run(doc, override=req.strategy_override, tracer=tracer)
            for c in chunks:
                if c.is_parent:
                    role, types = "main", []  # parents are backfill context, keep
                else:
                    role, types = self._role_for_chunk(c, doc.blocks)
                c.index_role = role
                c.block_types = types
                store.chunks.append(IndexedChunk(chunk=c, index_role=role, block_types=types))
                role_counts[role] += 1

        store.stats = {
            "n_docs": len(req.documents),
            "n_chunks": len(store.chunks),
            "role_counts": role_counts,
        }
        if tracer is not None:
            tracer.log(self.name, "ingest_done",
                       f"{len(req.documents)} docs → {len(store.chunks)} chunks {role_counts}")
        return IngestResult(
            store=store,
            n_docs=len(req.documents),
            n_chunks=len(store.chunks),
            role_counts=role_counts,
        )
