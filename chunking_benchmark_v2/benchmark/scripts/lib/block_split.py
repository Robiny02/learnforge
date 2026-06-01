"""Split a Document into ordered pseudo-structure Blocks (Plan §3, §6 Phase 1).

Goal: expose the *structure* every chunker can exploit, and the boundaries whose
preservation we later measure (code fences, tables, image-OCR regions, Q/A lists,
comments, ad footers). This pass assigns a positional `structure_type` only;
semantic noise labels (`block_type`) are added later by block_classifier (Phase 3).

Design choices that matter for the metrics:
- A fenced ```code``` block is kept whole (atomic) so we can detect when a chunker
  splits it.
- An image marker `[图片N: ...]` opens an *image_ocr region* that absorbs its `OCR:`
  text (and internal blank lines) until the next image / heading / body-label /
  tags line. This keeps OCR with its context (ocr_context_separation metric).
- Consecutive `|`-lines form one atomic table block.
- Blank lines separate ordinary paragraphs; a numbered Q/A list with no blank lines
  between items therefore stays in one block (qa_pair metric).
"""

from __future__ import annotations

import re
from typing import List

from .schema import Block, Document, new_id

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_IMAGE_MARKER_RE = re.compile(r"^(\[图片|!\[)")
_TAGS_RE = re.compile(r"^#\S")  # hashtag line like "#字节 #Java后端" (no space after #)
_TABLE_RE = re.compile(r"^\s*\|")
_FENCE_RE = re.compile(r"^\s*```")
_COMMENT_RE = re.compile(r"^(评论\s*\d*|楼主回复|有人问)")
# Labels that mean "back to body text" — they close an open image-OCR region.
_BODY_LABELS = ("正文补充", "正文", "个人感受", "评论", "楼主更新", "楼主", "总结")
_AD_HINTS = ("广告", "推荐阅读", "版权", "公众号")


def _is_body_label(line: str) -> bool:
    s = line.strip()
    return any(s.startswith(lbl) for lbl in _BODY_LABELS)


def _heading_path(stack: List[str]) -> str:
    return " > ".join(stack)


def split_document(doc: Document) -> List[Block]:
    lines = doc.text.splitlines()
    blocks: List[Block] = []
    order = 0
    heading_stack: List[tuple] = []  # (level, text)

    # buffers
    buf: List[str] = []
    buf_kind = "paragraph"
    in_code = False
    in_image = False

    def cur_path() -> str:
        return _heading_path([t for _, t in heading_stack])

    def in_ad_section() -> bool:
        return any(any(h in t for h in _AD_HINTS) for _, t in heading_stack)

    def flush():
        nonlocal buf, buf_kind, order, in_image
        text = "\n".join(buf).strip("\n")
        buf = []
        in_image = False
        if not text.strip():
            buf_kind = "paragraph"
            return
        kind = buf_kind
        if kind == "paragraph" and in_ad_section():
            kind = "ad"
        blocks.append(
            Block(
                block_id=new_id(),
                doc_id=doc.doc_id,
                text=text,
                structure_type=kind,
                order=order,
                heading_path=cur_path(),
            )
        )
        order += 1
        buf_kind = "paragraph"

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # --- fenced code: capture verbatim until closing fence (atomic) ---
        if _FENCE_RE.match(line) and not in_code:
            flush()
            code_lines = [line]
            i += 1
            while i < n and not _FENCE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            if i < n:  # closing fence
                code_lines.append(lines[i])
                i += 1
            blocks.append(
                Block(
                    block_id=new_id(),
                    doc_id=doc.doc_id,
                    text="\n".join(code_lines),
                    structure_type="code",
                    order=order,
                    heading_path=cur_path(),
                )
            )
            order += 1
            continue

        # --- heading: closes everything, updates stack ---
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            text = m.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            kind = "ad" if any(h in text for h in _AD_HINTS) else "heading"
            blocks.append(
                Block(
                    block_id=new_id(),
                    doc_id=doc.doc_id,
                    text=line,
                    structure_type=kind,
                    order=order,
                    heading_path=cur_path(),
                )
            )
            order += 1
            i += 1
            continue

        # --- image marker opens an image-OCR region ---
        if _IMAGE_MARKER_RE.match(line):
            flush()
            in_image = True
            buf_kind = "image_ocr"
            buf.append(line)
            i += 1
            continue

        # inside an image region: absorb until a closing signal
        if in_image:
            if line.strip() == "" :
                buf.append(line)
                i += 1
                continue
            if _is_body_label(line) or _TAGS_RE.match(line) or _IMAGE_MARKER_RE.match(line):
                flush()  # close image region; re-handle this line next loop
                continue
            buf.append(line)
            i += 1
            continue

        # --- tags line ---
        if _TAGS_RE.match(line) and not _HEADING_RE.match(line):
            flush()
            blocks.append(
                Block(
                    block_id=new_id(),
                    doc_id=doc.doc_id,
                    text=line.strip(),
                    structure_type="tags",
                    order=order,
                    heading_path=cur_path(),
                )
            )
            order += 1
            i += 1
            continue

        # --- table: consecutive pipe lines ---
        if _TABLE_RE.match(line):
            flush()
            tbl = []
            while i < n and _TABLE_RE.match(lines[i]):
                tbl.append(lines[i])
                i += 1
            blocks.append(
                Block(
                    block_id=new_id(),
                    doc_id=doc.doc_id,
                    text="\n".join(tbl),
                    structure_type="table",
                    order=order,
                    heading_path=cur_path(),
                )
            )
            order += 1
            continue

        # --- blank line separates paragraphs ---
        if line.strip() == "":
            flush()
            i += 1
            continue

        # --- ordinary line: accumulate, tag comment if it looks like one ---
        if not buf and _COMMENT_RE.match(line.strip()):
            buf_kind = "comment"
        buf.append(line)
        i += 1

    flush()
    return blocks


def attach_blocks(doc: Document) -> Document:
    doc.blocks = split_document(doc)
    return doc


if __name__ == "__main__":  # smoke
    from collections import Counter

    from .loaders import load_all

    docs = load_all()
    total = Counter()
    for d in docs:
        for b in split_document(d):
            total[b.structure_type] += 1
    print("block structure_type distribution:")
    for k, v in total.most_common():
        print(f"  {k:12} {v}")
