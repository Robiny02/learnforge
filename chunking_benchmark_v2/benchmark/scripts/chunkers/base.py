"""Shared helpers for chunkers (Plan §4).

Keeps a single char budget across all strategies so comparisons are fair
(Plan §5: "固定切片参数预算" — avoid "切得碎=Recall 虚高").
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

from ..lib.schema import Block, Chunk, new_id

# Fair, source-agnostic char budget shared by every chunker.
TARGET_CHARS = 512
OVERLAP_CHARS = 128
# A block longer than this is considered "oversized" and may be windowed
# (unless it is atomic — code / table / image_ocr).
MAX_CHARS = int(TARGET_CHARS * 1.6)
MIN_CHARS = 40
ATOMIC_TYPES = ("code", "table", "image_ocr")


def window_chars(
    text: str, size: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS
) -> List[str]:
    """Sliding char window. Generalises learnforge's CJK fallback in
    `learnforge/knowledge/ingest.py::chunk_text` to all sources, giving a fair
    char budget for CJK and ASCII alike."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    out: List[str] = []
    for i in range(0, len(text), step):
        piece = text[i : i + size]
        if piece:
            out.append(piece)
        if i + size >= len(text):
            break
    return out


def try_import_learnforge_chunk_text():
    """Reuse learnforge's chunk_text when importable; else None (offline-safe).

    Importing the module can cascade into optional deps; we guard and degrade.
    """
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "learnforge")
    )
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from learnforge.knowledge.ingest import chunk_text  # type: ignore

        return chunk_text
    except Exception:
        return None


def make_chunk(
    doc_id: str,
    source_type: str,
    text: str,
    chunker_name: str,
    *,
    level: int = 0,
    parent_id: Optional[str] = None,
    is_parent: bool = False,
    structure_type: str = "other",
    block_types: Optional[List[str]] = None,
    index_role: str = "main",
    heading_path: str = "",
    metadata: Optional[dict] = None,
) -> Chunk:
    return Chunk(
        chunk_id=new_id(),
        doc_id=doc_id,
        text=text,
        chunker_name=chunker_name,
        source_type=source_type,
        level=level,
        parent_id=parent_id,
        is_parent=is_parent,
        structure_type=structure_type,
        block_types=block_types or [],
        index_role=index_role,
        heading_path=heading_path,
        metadata=metadata or {},
    )


def block_to_child_chunks(
    block: Block, doc, chunker_name: str, parent_id: Optional[str] = None
) -> List[Chunk]:
    """Turn one block into one or more child chunks; atomic blocks never split."""
    pieces: List[str]
    if block.structure_type in ATOMIC_TYPES or block.char_len <= MAX_CHARS:
        pieces = [block.text]
    else:
        pieces = window_chars(block.text)
    out = []
    for p in pieces:
        out.append(
            make_chunk(
                doc.doc_id,
                doc.source_type,
                p,
                chunker_name,
                level=0,
                parent_id=parent_id,
                structure_type=block.structure_type,
                heading_path=block.heading_path,
                metadata={"block_id": block.block_id},
            )
        )
    return out
