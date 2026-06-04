"""把已解析的 Attachment 持久化进本地 SQLite 知识库（切片入库 + 去重 + 图片摘要）。

设计（压缩友好，见任务约束）：
- **真实内容/切片** → `chunks`（`kb_scope=local`），后续靠现有 retrieval 召回。
- **文档引用**（document_id/filename/summary）→ `AttachmentManifest` → 写进 dialogue_turns artifacts。
- **session_summary 只存 manifest**，绝不存全文。
- **图片**：当前轮仍直接喂 vision 作答（不在此破坏）；这里另外生成结构化 `ImageSummary`
  并把摘要文本入库，作为图片的可检索文本表示（截图/架构图/代码图/报错图等）。
- **去重**：按内容哈希判等，重复上传同一文件不重复入库（复用已有 document_id）。
- **优雅降级**：任何单文件失败/vision 失败都不抛——置 degraded + note，保证 "chain always passes"。

向量分路沿用 `knowledge.ingest`（无 embedding provider → 仅建 FTS 索引）。
"""

from __future__ import annotations

import hashlib
from typing import List, Optional, Tuple
from uuid import uuid4

from ..contracts.attachment import (
    Attachment,
    AttachmentManifest,
    DocumentRef,
    ImageSummary,
)
from ..contracts.enums import ModelTier
from ..knowledge.ingest import ingest_document
from ..llm.client import LLM, LLMStructuredError, LLMUnavailable
from ..storage.repositories import ChunkRepository

_ATTACHMENT_ORIGIN = "attachment"
_SUMMARY_BUDGET = 600  # manifest 摘要字符预算（轻量，不含全文）

_IMAGE_SUMMARY_SYSTEM = (
    "你是图片理解助手。看图并产出结构化摘要，供后续文本检索使用（非逐字 OCR）。"
    "重点判断图片类型（截图/架构图/代码图/报错图/示意图），并提炼主要内容与关键文字/标识。"
)


def content_hash_for(att: Attachment) -> str:
    """内容指纹：文本类按抽取文本，图片按 data URL。空内容退回文件名+类型。"""
    if att.kind == "image":
        basis = att.image_data_url or f"{att.filename}:{att.mime}"
    else:
        basis = att.extracted_text or f"{att.filename}:{att.mime}"
    return hashlib.sha256(basis.encode("utf-8", errors="replace")).hexdigest()[:32]


def summarize_image(att: Attachment) -> Tuple[ImageSummary, bool]:
    """对图片生成结构化摘要（vision）。失败/无 key → 降级摘要（不崩），返回 (summary, degraded)。"""
    fallback = ImageSummary(
        title=att.filename or "图片",
        image_kind="other",
        summary=(att.note or "未生成图片摘要（vision 不可用），仅保留文件引用。"),
    )
    if not att.is_image() or not LLM.available:
        return fallback, True
    prompt = (
        f"文件名：{att.filename or '(未命名)'}\n"
        "请描述这张图片：它是什么类型（截图/架构图/代码图/报错图/示意图），"
        "主要内容是什么，含哪些关键文字、组件、报错信息或代码标识。"
        "用于后续检索，所以要点要具体、可被关键词命中。"
    )
    try:
        obj, _ = LLM.complete_structured(
            prompt=prompt,
            schema=ImageSummary,
            model_tier=ModelTier.SONNET,
            system=_IMAGE_SUMMARY_SYSTEM,
            max_tokens=512,
            images=[att.image_data_url],
        )
        if obj and (obj.summary or obj.key_text):
            return obj, False
        return fallback, True
    except (LLMUnavailable, LLMStructuredError, Exception):  # noqa: BLE001 - 降级不阻断
        return fallback, True


def ingest_attachment(
    att: Attachment,
    *,
    session_id: str = "",
    db_path: Optional[str] = None,
    repo: Optional[ChunkRepository] = None,
    vision: bool = True,
) -> DocumentRef:
    """把单个附件入库并返回轻量 DocumentRef（失败也返回 degraded ref，不抛）。"""
    repo = repo or ChunkRepository(db_path=db_path)
    chash = content_hash_for(att)

    # 去重：同内容已入库 → 复用 document_id，不重复切片。
    try:
        existing = repo.find_document_by_hash(chash)
    except Exception:  # noqa: BLE001
        existing = None
    if existing:
        return DocumentRef(
            document_id=existing, attachment_id=att.id, filename=att.filename,
            kind=att.kind, content_hash=chash,
            chunk_count=repo.count_by_document(existing), reused=True,
            note="重复上传，已复用既有入库记录。",
        )

    document_id = f"doc-{uuid4().hex[:12]}"
    if att.kind == "image":
        return _ingest_image(att, document_id, chash, session_id, repo, vision)
    return _ingest_text(att, document_id, chash, session_id, repo)


def _ingest_text(
    att: Attachment, document_id: str, chash: str, session_id: str, repo: ChunkRepository
) -> DocumentRef:
    text = (att.extracted_text or "").strip()
    if not text:
        return DocumentRef(
            document_id=document_id, attachment_id=att.id, filename=att.filename,
            kind=att.kind, content_hash=chash, degraded=True,
            note=att.note or "附件无可入库文本（可能是扫描件/空文件）。",
        )
    topic_hint = _topic_hint(att.filename, text)
    metadata = {
        "origin": _ATTACHMENT_ORIGIN,
        "document_id": document_id,
        "content_hash": chash,
        "filename": att.filename,
        "mime": att.mime,
        "attachment_kind": att.kind,
        "session_id": session_id,
    }
    try:
        chunk_ids = ingest_document(
            text=text, source_type="doc", topic=topic_hint, kb_scope="local",
            metadata=metadata, repo=repo,
        )
    except Exception as e:  # noqa: BLE001 - 入库失败不阻断对话
        return DocumentRef(
            document_id=document_id, attachment_id=att.id, filename=att.filename,
            kind=att.kind, content_hash=chash, degraded=True,
            note=f"入库失败：{type(e).__name__}",
        )
    return DocumentRef(
        document_id=document_id, attachment_id=att.id, filename=att.filename,
        kind=att.kind, content_hash=chash, chunk_count=len(chunk_ids),
        topic_hint=topic_hint, summary=text[:_SUMMARY_BUDGET], degraded=att.degraded,
    )


def _ingest_image(
    att: Attachment, document_id: str, chash: str, session_id: str,
    repo: ChunkRepository, vision: bool,
) -> DocumentRef:
    summary, degraded = (summarize_image(att) if vision else (ImageSummary(
        title=att.filename or "图片"), True))
    topic_hint = (summary.topics[0] if summary.topics else None) or _topic_hint(att.filename, summary.summary)
    metadata = {
        "origin": _ATTACHMENT_ORIGIN,
        "document_id": document_id,
        "content_hash": chash,
        "filename": att.filename,
        "mime": att.mime,
        "attachment_kind": "image",
        "image_kind": summary.image_kind,
        "vision_degraded": degraded,
        "session_id": session_id,
    }
    chunk_count = 0
    try:
        chunk_ids = ingest_document(
            text=summary.as_chunk_text(att.filename), source_type="doc",
            topic=topic_hint, kb_scope="local", metadata=metadata, repo=repo,
        )
        chunk_count = len(chunk_ids)
    except Exception as e:  # noqa: BLE001
        return DocumentRef(
            document_id=document_id, attachment_id=att.id, filename=att.filename,
            kind="image", content_hash=chash, image_kind=summary.image_kind,
            degraded=True, note=f"图片摘要入库失败：{type(e).__name__}",
        )
    return DocumentRef(
        document_id=document_id, attachment_id=att.id, filename=att.filename,
        kind="image", content_hash=chash, chunk_count=chunk_count,
        topic_hint=topic_hint, summary=(summary.summary or summary.title)[:_SUMMARY_BUDGET],
        image_kind=summary.image_kind, degraded=degraded,
        note="vision 摘要降级（仍保留文件引用）。" if degraded else "",
    )


def ingest_attachments(
    atts: List[Attachment],
    *,
    session_id: str = "",
    db_path: Optional[str] = None,
    vision: bool = True,
) -> AttachmentManifest:
    """批量入库一组附件，返回 manifest（document 引用列表）。"""
    if not atts:
        return AttachmentManifest()
    try:
        repo = ChunkRepository(db_path=db_path)
    except Exception:  # noqa: BLE001 - DB 不可用：返回空 manifest，不阻断对话
        return AttachmentManifest()
    docs = [
        ingest_attachment(att, session_id=session_id, db_path=db_path, repo=repo, vision=vision)
        for att in atts
    ]
    return AttachmentManifest(documents=docs)


def _topic_hint(filename: str, text: str) -> Optional[str]:
    """很轻量的话题线索：优先文件名主干，否则取正文首个非空行前若干字。"""
    stem = (filename or "").rsplit(".", 1)[0].strip()
    if stem:
        return stem[:40]
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:40]
    return None
