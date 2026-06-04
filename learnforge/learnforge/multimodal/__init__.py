"""多模态输入：把上传的本地文件(图片/PDF/MD/文本)解析成 Attachment。

公开入口：
    from learnforge.multimodal import parse_attachments
"""

from __future__ import annotations

from .ingest import ingest_attachment, ingest_attachments, summarize_image
from .parse import parse_attachment, parse_attachments

__all__ = [
    "parse_attachment",
    "parse_attachments",
    "ingest_attachment",
    "ingest_attachments",
    "summarize_image",
]
