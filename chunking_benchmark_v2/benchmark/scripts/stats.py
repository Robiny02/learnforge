"""Intrinsic / structural metrics for a chunk set (Plan §5.A).

Given the source documents (with blocks) and a chunker's chunks, compute:
- counts & sizes: n_chunks, avg/median char_len, too-short / too-long ratios
- structure preservation: code / table / image-OCR / Q&A blocks that survive
  intact inside a single chunk (a block is "split" if no single chunk contains
  its full text)

Noise-contamination / technical-retention need block_type labels (Phase 3); they
are reported as null until the classifier runs.
"""

from __future__ import annotations

import re
import statistics
from typing import Dict, List, Optional

from .lib.schema import Block, Chunk, Document

_NUM_LINE = re.compile(r"^\s*\d+[\s\.、)）]")


def _is_qa_block(block: Block) -> bool:
    lines = [ln for ln in block.text.splitlines() if ln.strip()]
    numbered = sum(1 for ln in lines if _NUM_LINE.match(ln))
    return numbered >= 3


def _contained(text: str, chunks: List[Chunk]) -> bool:
    t = text.strip()
    if not t:
        return True
    return any(t in c.text for c in chunks)


def _safe_ratio(num: int, den: int) -> Optional[float]:
    if den == 0:
        return None
    return round(num / den, 3)


def compute(
    docs: List[Document], chunks: List[Chunk], min_chars: int = 40, max_chars: int = 820
) -> Dict:
    by_doc: Dict[str, List[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)

    # leaf chunks only for size stats (exclude parents to avoid double counting)
    leaves = [c for c in chunks if not c.is_parent]
    lens = [c.char_len for c in leaves] or [0]

    code_t = code_split = 0
    table_t = table_split = 0
    ocr_t = ocr_split = 0
    qa_t = qa_split = 0
    for doc in docs:
        dchunks = by_doc.get(doc.doc_id, [])
        for b in doc.blocks:
            if b.structure_type == "code":
                code_t += 1
                code_split += 0 if _contained(b.text, dchunks) else 1
            elif b.structure_type == "table":
                table_t += 1
                table_split += 0 if _contained(b.text, dchunks) else 1
            elif b.structure_type == "image_ocr":
                ocr_t += 1
                ocr_split += 0 if _contained(b.text, dchunks) else 1
            if _is_qa_block(b):
                qa_t += 1
                qa_split += 0 if _contained(b.text, dchunks) else 1

    return {
        "n_chunks": len(chunks),
        "n_leaf_chunks": len(leaves),
        "n_parent_chunks": sum(1 for c in chunks if c.is_parent),
        "avg_char_len": round(statistics.mean(lens), 1),
        "median_char_len": int(statistics.median(lens)),
        "max_char_len": max(lens),
        "too_short_ratio": _safe_ratio(sum(1 for x in lens if x < min_chars), len(lens)),
        "too_long_ratio": _safe_ratio(sum(1 for x in lens if x > max_chars), len(lens)),
        "code_block_split_rate": _safe_ratio(code_split, code_t),
        "table_split_rate": _safe_ratio(table_split, table_t),
        "ocr_context_separation_rate": _safe_ratio(ocr_split, ocr_t),
        "qa_pair_split_rate": _safe_ratio(qa_split, qa_t),
        "noise_contamination_rate": None,  # Phase 3 (needs block_type)
        "technical_retention_rate": None,  # Phase 3
        "_counts": {"code": code_t, "table": table_t, "image_ocr": ocr_t, "qa": qa_t},
    }


def format_markdown(stats_by_key: Dict[str, Dict]) -> str:
    """stats_by_key: {"chunker/source": metrics}. Renders a comparison table."""
    cols = [
        ("n_chunks", "n"),
        ("avg_char_len", "avg_len"),
        ("median_char_len", "med"),
        ("too_short_ratio", "short"),
        ("too_long_ratio", "long"),
        ("code_block_split_rate", "code_split"),
        ("table_split_rate", "tbl_split"),
        ("ocr_context_separation_rate", "ocr_sep"),
        ("qa_pair_split_rate", "qa_split"),
    ]
    header = "| key | " + " | ".join(label for _, label in cols) + " |"
    sep = "|" + "---|" * (len(cols) + 1)
    rows = [header, sep]
    for key in sorted(stats_by_key):
        m = stats_by_key[key]
        cells = []
        for field, _ in cols:
            v = m.get(field)
            cells.append("–" if v is None else str(v))
        rows.append(f"| {key} | " + " | ".join(cells) + " |")
    return "\n".join(rows)
