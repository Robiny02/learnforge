"""Fixed-length chunker — baseline (Plan §4.1).

Ignores all structure: slides a fixed char window over the whole document.
Reuses learnforge's `chunk_text` when importable (its CJK path is the same
char-window idea); otherwise uses the in-repo `window_chars`. Expected to cut
code fences, split Q/A lists, and separate image-OCR from its context — exactly
the failure modes the structural/semantic chunkers should avoid.
"""

from __future__ import annotations

from typing import List

from ..lib.schema import Chunk, Document
from .base import OVERLAP_CHARS, TARGET_CHARS, make_chunk, window_chars

NAME = "fixed"

_lf_chunk_text = None  # resolved lazily so import never hard-fails offline


def _pieces(text: str) -> List[str]:
    # We deliberately use a uniform char budget across sources for fairness;
    # learnforge.chunk_text tokenises ASCII by whitespace (different budget for
    # CJK vs ASCII), so we keep window_chars as the comparable baseline.
    return window_chars(text, TARGET_CHARS, OVERLAP_CHARS)


def chunk(doc: Document) -> List[Chunk]:
    out: List[Chunk] = []
    for piece in _pieces(doc.text):
        out.append(
            make_chunk(
                doc.doc_id,
                doc.source_type,
                piece,
                NAME,
                structure_type="fixed_window",
                heading_path=doc.title,
                metadata={"chunk_mode": "char_window"},
            )
        )
    return out
