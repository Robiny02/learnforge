"""EvidenceResearchAgent input/output（统一只读证据 worker）。

设计动机：把"读 resume / repo / file / attachment 等不同 source、筛证据、总结"集中到一个
**临时隔离上下文**的只读 worker。它由 Manager 调用，只返回结构化 `EvidencePacket`
（summary + evidence_refs + source_refs + confidence + missing_info + warnings），
**不把完整中间上下文/原始文件内容塞回 Manager / Conversation State**。

只读不变量：本 worker 绝不写 mastery / learning_paths / events；底层只调 READ 工具
（file.read / repo.search / attachment.recall / resume.recall / retrieval.search）。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from ..enums import EvidenceSourceType


class EvidenceRef(BaseModel):
    """一条被采纳的证据（指针 + 精炼片段，不存整文件）。"""

    locator: str = ""                      # 文件路径 / chunk_id / 搜索命中位置
    snippet: str = ""                      # 精炼后的证据片段（截断，非全文）
    source_type: EvidenceSourceType = EvidenceSourceType.FILE
    score: float = 0.0                     # 相关性（0..1，弱信号）


class SourceRef(BaseModel):
    """一个被探查过的来源（读没读到、状态如何），供透明展示与下一步追读。"""

    source_type: EvidenceSourceType
    locator: str = ""                      # 路径 / query / document_id
    status: str = "ok"                     # ok | empty | error | denied | not_found
    detail: str = ""


class EvidenceRequest(BaseModel):
    """证据采集请求（Manager → EvidenceResearchAgent）。"""

    query: str = ""
    source_types: List[EvidenceSourceType] = Field(
        default_factory=lambda: [EvidenceSourceType.ATTACHMENT, EvidenceSourceType.RESUME]
    )
    targets: List[str] = Field(default_factory=list)   # 具体文件路径 / document_id / repo 关键词
    db_path: Optional[str] = None
    max_items: int = 6


class EvidencePacket(BaseModel):
    """证据采集结果（隔离上下文的唯一外传产物）。"""

    summary: str = ""                               # 对采到证据的客观总结（不带情绪/不臆测）
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    source_refs: List[SourceRef] = Field(default_factory=list)
    confidence: float = 0.0
    missing_info: List[str] = Field(default_factory=list)   # 还缺什么 / 该去哪读
    warnings: List[str] = Field(default_factory=list)       # 越权被拒 / 降级 / 来源不可达等

    def artifact_text(self) -> str:
        """渲染成可注入下游（如 diagnosis）的**只读 artifact 摘要**。

        只含 summary + 证据指针，不含完整原文 —— 保证 Conversation State 不被原始文件污染。
        """
        lines: List[str] = []
        if self.summary:
            lines.append(self.summary.strip())
        for ref in self.evidence_refs:
            tag = ref.locator or ref.source_type.value
            snip = (ref.snippet or "").strip().replace("\n", " ")
            if snip:
                lines.append(f"- [{tag}] {snip[:200]}")
        if self.missing_info:
            lines.append("缺口：" + "；".join(self.missing_info[:3]))
        return "\n".join(lines)
