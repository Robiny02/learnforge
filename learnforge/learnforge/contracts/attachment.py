"""多模态附件契约：用户上传的本地文件(图片/PDF/MD/文本)的结构化表示。

设计(像 ChatGPT)：图片走 vision(原图喂多模态模型，非 OCR)；文本类(MD/PDF文本)抽成文本。
解析层产出 `Attachment`(带来源)；在 tool 边界拍平成 (attachment_text, image_data_urls)：
- 文本 → 经 assembler `retrieved` 证据槽注入(不污染 user_query)；
- 图片 → 作为 vision content part(image_url) 挂到消息上。

Python 3.9 兼容：typing.Optional/List/Dict。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """一个已解析的附件。`kind` 决定注入方式(文本进证据槽 / 图片走 vision)。"""

    id: str
    kind: str                              # image | pdf | markdown | text
    filename: str = ""
    mime: str = ""
    extracted_text: str = ""               # MD/PDF文本(图片留空，由 vision 读)
    image_data_url: Optional[str] = None   # data:image/png;base64,...（vision 用）
    page_count: Optional[int] = None
    truncated: bool = False                # 文本超预算被截断
    degraded: bool = False                 # 该解析却失败/缺依赖(如 PDF 无 pypdf、图片无 key)
    note: str = ""                         # 降级/错误说明(供 UI 与提示)
    meta: Dict = Field(default_factory=dict)

    def is_image(self) -> bool:
        return self.kind == "image" and bool(self.image_data_url)

    def as_evidence(self) -> str:
        """文本附件渲染成带来源的证据块(进 retrieved 槽)。

        借鉴 Claude Code：超预算被截断时**带指针**——告知完整内容已入库、可按文件名/document_id
        检索取回，避免模型以为眼前就是全文（而非默默吞掉剩余部分）。
        """
        if not self.extracted_text:
            return ""
        head = f"【附件：{self.filename or self.kind}】"
        if self.truncated:
            ref = self.meta.get("document_id")
            head += (f"（已截断；完整内容已入库，可按文件名「{self.filename or self.kind}」"
                     + (f"或 document_id={ref} " if ref else "") + "检索取回）")
        return f"{head}\n{self.extracted_text}"


def flatten_attachments(atts: List[Attachment]) -> "FlatAttachments":
    """在 tool 边界拍平：文本拼成证据串，图片收集 data URL 列表。"""
    texts = [a.as_evidence() for a in atts if a.extracted_text]
    images = [a.image_data_url for a in atts if a.is_image()]
    return FlatAttachments(text="\n\n".join(t for t in texts if t),
                           images=[i for i in images if i])


class FlatAttachments(BaseModel):
    """拍平后的附件：直接喂 tool 的两条通道。"""

    text: str = ""                         # → assembler retrieved 槽
    images: List[str] = Field(default_factory=list)  # data URL → vision content part


# --------------------------------------------------------------------------- 持久化
class ImageSummary(BaseModel):
    """图片的结构化摘要（vision 产出，落本地 KB 作可检索文本表示）。

    当前轮图片仍直接喂 vision 作答；本摘要是为**后续检索**保留的文本表示——
    截图/架构图/代码图/报错图等关键信息进 chunks，后续靠 retrieval 召回。
    """

    title: str = ""
    image_kind: str = "other"              # screenshot|architecture|code|error|diagram|other
    summary: str = ""                      # 图片主要内容（一段话）
    key_text: str = ""                     # 图中关键文字/标识（模型读图所见，非专用 OCR）
    topics: List[str] = Field(default_factory=list)

    def as_chunk_text(self, filename: str = "") -> str:
        head = f"【图片：{self.title or filename or 'image'}】"
        parts = [
            head,
            f"类型：{self.image_kind}",
            self.summary,
        ]
        if self.key_text:
            parts.append(f"关键信息：{self.key_text}")
        if self.topics:
            parts.append("主题：" + "、".join(self.topics))
        return "\n".join(p for p in parts if p)


class DocumentRef(BaseModel):
    """一个已入库附件的轻量引用（manifest 项；真实内容/切片在 chunks，不在此）。

    会话只保存这份引用（document_id/filename/summary），后续需要内容时经 retrieval
    从 SQLite 召回相关 chunks——不把全文塞进 session_summary。
    """

    document_id: str
    attachment_id: str = ""
    filename: str = ""
    kind: str = ""                         # pdf|markdown|text|image
    content_hash: str = ""
    chunk_count: int = 0
    topic_hint: Optional[str] = None
    summary: str = ""                      # 文本摘要 / 图片 summary（轻量，供 manifest/UI）
    image_kind: Optional[str] = None       # 仅图片：screenshot|architecture|code|error|diagram|other
    degraded: bool = False                 # 入库或 vision summary 降级
    reused: bool = False                   # 命中去重，未重复入库
    note: str = ""

    def as_artifact(self) -> Dict[str, str]:
        """落 dialogue_turns artifacts 的一行（document 引用，可经 document_id 回查 KB）。"""
        art = {
            "kind": "image" if self.kind == "image" else "document",
            "ref": self.document_id,
            "filename": self.filename,
        }
        if self.summary:
            art["summary"] = self.summary[:200]
        return art


class AttachmentManifest(BaseModel):
    """一批上传附件的入库清单：document 引用列表（轻量，供 artifact/session manifest）。"""

    documents: List[DocumentRef] = Field(default_factory=list)

    def artifacts(self) -> List[Dict[str, str]]:
        return [d.as_artifact() for d in self.documents]

    def manifest_line(self) -> str:
        """给 session/UI 的一句话 manifest（只列文件名+摘要，不含全文）。"""
        if not self.documents:
            return ""
        items = []
        for d in self.documents:
            tag = "图" if d.kind == "image" else "档"
            items.append(f"[{tag}]{d.filename or d.document_id}")
        return "已收录：" + "；".join(items)
