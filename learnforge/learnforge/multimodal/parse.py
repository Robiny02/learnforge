"""解析上传文件 → Attachment(确定性、可离线、优雅降级)。

策略(像 ChatGPT)：
- MD/TXT → 直接读文本(零依赖)。
- PDF → pypdf 抽文本；缺 pypdf / 抽取为空(扫描件) → degraded(可在 vision 阶段渲染页图，见 note)。
- 图片(png/jpg/...) → 保留 data URL 交 vision(原图喂多模态模型，非 OCR)。

输入 raw(前端内联)：{"filename","mime","data"}，data 为 base64 或 data URL。
任何单个文件解析失败都不抛——置 degraded + note，保证 "chain always passes"。
"""

from __future__ import annotations

import base64
import binascii
from typing import List, Optional
from uuid import uuid4

from ..contracts.attachment import Attachment

# 文本抽取预算(字符)：超出截断并标记，避免 PDF 撑爆上下文(后续可接 summarize)。
_TEXT_BUDGET = 12000

_IMAGE_MIMES = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_MD_EXTS = (".md", ".markdown")
_TEXT_EXTS = (".txt", ".text", ".log", ".csv")


def parse_attachments(raws: Optional[List[dict]]) -> List[Attachment]:
    return [parse_attachment(r) for r in (raws or []) if isinstance(r, dict)]


def parse_attachment(raw: dict) -> Attachment:
    filename = str(raw.get("filename") or "")
    mime = (raw.get("mime") or "").lower()
    data = raw.get("data") or ""
    kind = _detect_kind(filename, mime)
    att = Attachment(id=f"att-{uuid4().hex[:10]}", kind=kind, filename=filename, mime=mime)

    try:
        if kind == "image":
            att.image_data_url = _to_data_url(data, mime or "image/png")
            if not att.image_data_url:
                att.degraded, att.note = True, "图片数据为空"
        elif kind == "pdf":
            _parse_pdf(att, _decode_b64(data))
        else:  # markdown | text
            text = _decode_text(data)
            att.extracted_text, att.truncated = _budget(text)
    except Exception as e:  # noqa: BLE001 - 单文件失败不阻断
        att.degraded = True
        att.note = f"解析失败：{type(e).__name__}"
    return att


# ----------------------------------------------------------------- 类型判定
def _detect_kind(filename: str, mime: str) -> str:
    low = filename.lower()
    if mime in _IMAGE_MIMES or low.endswith(_IMAGE_EXTS):
        return "image"
    if mime == "application/pdf" or low.endswith(".pdf"):
        return "pdf"
    if mime == "text/markdown" or low.endswith(_MD_EXTS):
        return "markdown"
    return "text"


# ----------------------------------------------------------------- 解码
def _strip_data_url(data: str) -> str:
    """去掉 `data:...;base64,` 前缀，返回纯 base64。"""
    if data.startswith("data:") and "," in data:
        return data.split(",", 1)[1]
    return data


def _decode_b64(data: str) -> bytes:
    try:
        return base64.b64decode(_strip_data_url(data), validate=False)
    except (binascii.Error, ValueError):
        return b""


def _decode_text(data: str) -> str:
    """文本文件：可能是 data URL/base64，也可能前端直接传明文。"""
    if data.startswith("data:") or _looks_b64(data):
        raw = _decode_b64(data)
        if raw:
            return raw.decode("utf-8", errors="replace")
    return data


def _looks_b64(s: str) -> bool:
    if len(s) < 16 or len(s) % 4 != 0:
        return False
    return all(c.isalnum() or c in "+/=" for c in s[:64])


def _to_data_url(data: str, mime: str) -> str:
    if data.startswith("data:"):
        return data
    b64 = _strip_data_url(data)
    return f"data:{mime};base64,{b64}" if b64 else ""


# ----------------------------------------------------------------- PDF
def _parse_pdf(att: Attachment, raw: bytes) -> None:
    if not raw:
        att.degraded, att.note = True, "PDF 数据为空"
        return
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 - 缺依赖优雅降级
        att.degraded = True
        att.note = "未安装 pypdf，无法抽取 PDF 文本(可 pip install learnforge[multimodal])"
        return
    import io

    reader = PdfReader(io.BytesIO(raw))
    att.page_count = len(reader.pages)
    parts: List[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - 单页失败跳过
            continue
    text = "\n".join(p for p in parts if p.strip())
    att.extracted_text, att.truncated = _budget(text)
    if not att.extracted_text:
        att.degraded = True
        att.note = "PDF 抽取为空(可能是扫描件，后续可渲染页图走 vision)"


# ----------------------------------------------------------------- 预算
def _budget(text: str) -> tuple:
    text = (text or "").strip()
    if len(text) <= _TEXT_BUDGET:
        return text, False
    return text[:_TEXT_BUDGET] + "\n…(已截断)", True


def attachment_descriptors(atts: List[Attachment]) -> str:
    """给意图路由用的轻量描述符(图片不进 router，只给一句话存在性提示)。"""
    parts: List[str] = []
    for a in atts:
        tag = {"image": "图片", "pdf": "PDF", "markdown": "Markdown", "text": "文本"}.get(a.kind, a.kind)
        parts.append(f"[{tag}附件：{a.filename or a.id}]")
    return " ".join(parts)
