"""Common data contracts for the chunking benchmark (Plan §4).

Defined before any chunker so all strategies emit the same `Chunk` shape and can
be compared in one table. Pure stdlib dataclasses — Python 3.9 compatible
(`typing.Optional/List`, no `X | None`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# --- block-level classification labels (docs/ocr_and_noise_policy_v2.md) ------
# Phase 1 only assigns the structural label below; semantic noise labels
# (technical_core / irrelevant_ocr / ...) are layered on in Phase 3.
BLOCK_TYPES = (
    "technical_core",
    "useful_context",
    "career_noise",
    "off_topic_job",
    "useful_ocr",
    "irrelevant_ocr",
    "ad_or_promo",
    "empty_or_garbled",
)

# Where a chunk lands in the eval index. Phase 1 keeps everything in "main";
# Phase 3 routes blocks to downweighted / isolated by their block_type.
INDEX_ROLES = ("main", "downweighted", "isolated")


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Block:
    """A pseudo-structure unit of a document (heading section, OCR block,
    single interview question, one comment, a code fence, the ad footer, ...).

    `structure_type` is the *positional* role (set by block_split); `block_type`
    is the *semantic* noise label (set by block_classifier in Phase 3, else None).
    """

    block_id: str
    doc_id: str
    text: str
    structure_type: str  # heading | paragraph | code | table | image_ocr | comment | tags | ad | other
    order: int
    heading_path: str = ""
    block_type: Optional[str] = None  # one of BLOCK_TYPES, filled in Phase 3
    metadata: Dict = field(default_factory=dict)

    @property
    def char_len(self) -> int:
        return len(self.text)


@dataclass
class Document:
    """One source file: raw text + parsed blocks + source metadata."""

    doc_id: str
    source_type: str  # github_md | xiaohongshu | xiaolin_blog
    text: str
    title: str = ""
    blocks: List[Block] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


@dataclass
class Chunk:
    """Unified output of every chunker."""

    chunk_id: str
    doc_id: str
    text: str
    chunker_name: str
    source_type: str
    level: int = 0  # 0 = leaf/child, 1 = parent (section)
    parent_id: Optional[str] = None
    is_parent: bool = False
    structure_type: str = "other"
    block_types: List[str] = field(default_factory=list)
    index_role: str = "main"
    heading_path: str = ""
    metadata: Dict = field(default_factory=dict)

    @property
    def char_len(self) -> int:
        return len(self.text)

    def to_json(self) -> str:
        d = asdict(self)
        d["char_len"] = self.char_len
        return json.dumps(d, ensure_ascii=False)


def chunks_from_jsonl(path: str) -> List[Chunk]:
    """Reload chunks written by run_chunking (used by eval scripts)."""
    out: List[Chunk] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            d.pop("char_len", None)
            out.append(Chunk(**d))
    return out
