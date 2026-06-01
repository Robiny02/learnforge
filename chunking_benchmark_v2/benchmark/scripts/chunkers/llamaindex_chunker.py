"""LlamaIndex chunker (Plan §4.4) — framework baseline.

Markdown sources (github_md / xiaolin_blog) use `MarkdownNodeParser`; the weakly
structured 小红书 posts use `SentenceSplitter`. Nodes are mapped back to the
common `Chunk` contract so all strategies sit in one comparison table.

LlamaIndex is a declared dependency. If it is not importable, `chunk()` raises
ImportError with an install hint; run_chunking records the strategy as skipped
rather than crashing the whole run.
"""

from __future__ import annotations

from typing import List

from ..lib.schema import Chunk, Document
from .base import OVERLAP_CHARS, TARGET_CHARS, make_chunk

NAME = "llamaindex"

INSTALL_HINT = "LlamaIndex not installed. Run: pip install llama-index"

_MARKDOWN_SOURCES = ("github_md", "xiaolin_blog")


def _imports():
    from llama_index.core import Document as LIDocument  # type: ignore
    from llama_index.core.node_parser import (  # type: ignore
        MarkdownNodeParser,
        SentenceSplitter,
    )

    return LIDocument, MarkdownNodeParser, SentenceSplitter


def available() -> bool:
    try:
        _imports()
        return True
    except Exception:
        return False


def _heading_from_meta(meta: dict) -> str:
    headers = [v for k, v in meta.items() if str(k).lower().startswith("header")]
    return " > ".join(str(h) for h in headers)


def chunk(doc: Document) -> List[Chunk]:
    try:
        LIDocument, MarkdownNodeParser, SentenceSplitter = _imports()
    except Exception as exc:  # pragma: no cover - depends on env
        raise ImportError(INSTALL_HINT) from exc

    li_doc = LIDocument(text=doc.text, metadata={"doc_id": doc.doc_id})
    if doc.source_type in _MARKDOWN_SOURCES:
        parser = MarkdownNodeParser()
    else:
        # ~chars→tokens rough conversion keeps it in the same ballpark budget.
        parser = SentenceSplitter(
            chunk_size=max(64, TARGET_CHARS // 2),
            chunk_overlap=max(8, OVERLAP_CHARS // 2),
        )
    nodes = parser.get_nodes_from_documents([li_doc])

    out: List[Chunk] = []
    for node in nodes:
        text = node.get_content().strip()
        if not text:
            continue
        out.append(
            make_chunk(
                doc.doc_id,
                doc.source_type,
                text,
                NAME,
                structure_type="llamaindex_node",
                heading_path=_heading_from_meta(getattr(node, "metadata", {}) or {}),
                metadata={"framework": "llama_index"},
            )
        )
    return out
