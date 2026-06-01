"""ChunkingAgent — routes each document to the best chunker (Plan §9 findings).

Routing policy comes from the benchmark results:
- github_md   → llamaindex (MarkdownNodeParser: 0 code/table split, strong recall)
- xiaolin_blog→ llamaindex (keeps code; ad footer isolated downstream)
- xiaohongshu → structural (pseudo-structure: isolates OCR / off-topic blocks)

Any chunker failure falls back to structural → fixed, so ingestion never breaks.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ..chunkers import fixed, llamaindex_chunker, semantic, structural
from ..lib.schema import Chunk, Document
from .agent_base import PipelineAgent
from .contracts import Tracer, timed

_REGISTRY: Dict[str, Callable[[Document], List[Chunk]]] = {
    "fixed": fixed.chunk,
    "structural": structural.chunk,
    "semantic": semantic.chunk,
    "llamaindex": llamaindex_chunker.chunk,
}

ROUTE = {
    "github_md": "llamaindex",
    "xiaolin_blog": "llamaindex",
    "xiaohongshu": "structural",
}
_FALLBACK_ORDER = ["structural", "fixed"]


class ChunkingAgent(PipelineAgent):
    name = "chunking"
    capabilities = ["chunk.document"]

    def strategy_for(self, doc: Document, override: Optional[str]) -> str:
        return override or ROUTE.get(doc.source_type, "structural")

    def run(
        self, doc: Document, override: Optional[str] = None, tracer: Optional[Tracer] = None
    ) -> List[Chunk]:
        self.require("chunk.document")
        chosen = self.strategy_for(doc, override)
        order = [chosen] + [s for s in _FALLBACK_ORDER if s != chosen]
        for strat in order:
            try:
                chunks = _REGISTRY[strat](doc)
                if tracer is not None:
                    tracer.log(
                        self.name,
                        "chunk",
                        f"{doc.doc_id} [{doc.source_type}] → {strat}: {len(chunks)} chunks"
                        + ("" if strat == chosen else f" (fallback from {chosen})"),
                    )
                for c in chunks:
                    c.metadata.setdefault("strategy", strat)
                return chunks
            except Exception as exc:  # try next fallback
                if tracer is not None:
                    tracer.log(self.name, "chunk_failed", f"{strat}: {exc}")
        return []
