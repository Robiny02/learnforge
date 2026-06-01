"""Structural + parent/child chunker (Plan §4.2).

Uses the blocks from block_split: groups blocks into sections (parents) by
heading_path, emits each block as a child (atomic code/table/image_ocr never
split). For weakly-structured 小红书 posts (a single `#` title) the post becomes
one parent and body / each image-OCR / comments / tags become children — the
"伪结构" path. Keeps code, tables and image-OCR context intact.
"""

from __future__ import annotations

from typing import Dict, List

from ..lib.block_split import split_document
from ..lib.schema import Chunk, Document
from .base import MIN_CHARS, block_to_child_chunks, make_chunk

NAME = "structural"


def _group_key(block) -> str:
    return block.heading_path or "(root)"


def chunk(doc: Document) -> List[Chunk]:
    blocks = doc.blocks or split_document(doc)
    if not blocks:
        return []

    # Preserve first-seen order of sections.
    groups: Dict[str, list] = {}
    for b in blocks:
        groups.setdefault(_group_key(b), []).append(b)

    out: List[Chunk] = []
    for key, gblocks in groups.items():
        parent_text = "\n\n".join(b.text for b in gblocks).strip()
        if not parent_text:
            continue
        parent = make_chunk(
            doc.doc_id,
            doc.source_type,
            parent_text,
            NAME,
            level=1,
            is_parent=True,
            structure_type="section",
            heading_path=key,
            metadata={"n_blocks": len(gblocks)},
        )
        out.append(parent)
        for b in gblocks:
            # Skip standalone heading lines as children (kept in the parent).
            if b.structure_type == "heading" and b.char_len < MIN_CHARS:
                continue
            out.extend(block_to_child_chunks(b, doc, NAME, parent_id=parent.chunk_id))
    return out
