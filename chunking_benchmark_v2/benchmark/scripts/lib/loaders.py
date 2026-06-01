"""Load benchmark source documents into the common `Document` contract (Plan §2, §6 Phase 1).

Three sources:
- xiaohongshu_interview / xiaolin_blog: one `.md` + sibling `.metadata.json` per doc.
- github_md: empty in-package; we reuse the cached crawl at
  `learnforge/data/sources/liuup_claude-code-analysis.md.jsonl` (one record == one md file).
  If a populated `data/raw/github_md/*.md` exists (e.g. after running fetch script), it wins.

Loading only reads text + metadata; block splitting happens in block_split.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .schema import Document

# benchmark/  (this file is benchmark/scripts/lib/loaders.py)
BENCHMARK_ROOT = Path(__file__).resolve().parents[2]
# repo root holding the learnforge package with the cached github crawl.
REPO_ROOT = BENCHMARK_ROOT.parents[1]
CACHED_GITHUB_JSONL = (
    REPO_ROOT / "learnforge" / "data" / "sources" / "liuup_claude-code-analysis.md.jsonl"
)

RAW = BENCHMARK_ROOT / "data" / "raw"

SOURCE_TYPE_BY_DIR = {
    "xiaohongshu_interview": "xiaohongshu",
    "xiaolin_blog": "xiaolin_blog",
    "github_md": "github_md",
}


def _read_metadata(md_path: Path) -> Dict:
    meta_path = md_path.with_suffix(".metadata.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _load_md_dir(dir_name: str) -> List[Document]:
    src_dir = RAW / dir_name
    source_type = SOURCE_TYPE_BY_DIR[dir_name]
    docs: List[Document] = []
    if not src_dir.exists():
        return docs
    for md_path in sorted(src_dir.glob("*.md")):
        meta = _read_metadata(md_path)
        doc_id = meta.get("doc_id") or md_path.stem
        docs.append(
            Document(
                doc_id=doc_id,
                source_type=source_type,
                text=md_path.read_text(encoding="utf-8"),
                title=meta.get("title", doc_id),
                metadata=meta,
            )
        )
    return docs


def _load_github_cached(jsonl_path: Optional[Path] = None) -> List[Document]:
    path = jsonl_path or CACHED_GITHUB_JSONL
    docs: List[Document] = []
    if not path.exists():
        return docs
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            doc_id = rec.get("title") or rec.get("heading_path") or f"github_{len(docs)}"
            docs.append(
                Document(
                    doc_id=str(doc_id),
                    source_type="github_md",
                    text=rec.get("text", ""),
                    title=str(rec.get("title", doc_id)),
                    metadata={
                        "heading_path": rec.get("heading_path"),
                        "source_url": rec.get("source_url"),
                        **(rec.get("metadata") or {}),
                    },
                )
            )
    return docs


def load_github_md() -> List[Document]:
    """Prefer locally-fetched raw md; otherwise reuse the cached crawl."""
    local = _load_md_dir("github_md")
    if local:
        return local
    return _load_github_cached()


def load_xiaohongshu() -> List[Document]:
    return _load_md_dir("xiaohongshu_interview")


def load_xiaolin_blog() -> List[Document]:
    return _load_md_dir("xiaolin_blog")


def load_all() -> List[Document]:
    return load_github_md() + load_xiaohongshu() + load_xiaolin_blog()


def load_by_source(source_type: str) -> List[Document]:
    return [d for d in load_all() if d.source_type == source_type]


if __name__ == "__main__":  # quick smoke
    from collections import Counter

    docs = load_all()
    by_src = Counter(d.source_type for d in docs)
    print(f"loaded {len(docs)} docs: {dict(by_src)}")
    for d in docs[:3]:
        print(f"  [{d.source_type}] {d.doc_id} ({len(d.text)} chars)")
