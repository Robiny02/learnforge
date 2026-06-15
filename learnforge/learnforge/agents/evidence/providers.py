"""证据来源 Provider（轻量 DDD 重构 Phase 4：收敛分散的读取路径）。

把原先散在 `EvidenceResearchAgent._collect_files/_collect_repo/_collect_attachment/_collect_resume`
里的逐源读取，抽成一组实现 `EvidenceProviderHandler` 协议的 provider（file/repo/attachment/resume）。
EvidenceResearchAgent 按 source_type 编排它们——新增证据源 = 加一个 provider，不改 worker 主循环。

每个 provider 持有 owning agent（EvidenceResearchAgent），经 `agent.call_tool(...)` 走**权限门**
（require_tool）调用底层只读工具（file.read / repo.search / attachment.recall / resume.recall）。
严格只读:绝不写 state、不执行命令;只回指针 + 截断片段（非全文）。

行为保持:逐源逻辑与原 `_collect_*` 一一对应（同样的工具、同样的 SourceRef 状态、同样的 score）。
设计文档见 docs/architecture/refactor-plan.md。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ...contracts.agents.evidence import (
    EvidenceRef,
    ProviderResult,
    SourceRef,
)
from ...contracts.enums import EvidenceSourceType

_MAX_SNIPPET = 240


class _BaseProvider:
    """共享基类:持有 owning agent（拿 call_tool + db_path）。"""

    source_type: EvidenceSourceType

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    @property
    def _db_path(self):
        return getattr(self.agent, "_db_path", None)


class FileEvidenceProvider(_BaseProvider):
    source_type = EvidenceSourceType.FILE

    def fetch(self, query: str, targets: List[str], context: Dict[str, Any]) -> ProviderResult:
        evidence_refs: List[EvidenceRef] = []
        source_refs: List[SourceRef] = []
        for path in targets:
            res = self.agent.call_tool("file.read", {"path": path})
            if res.ok and isinstance(res.data, dict):
                content = (res.data.get("content") or "").strip()
                source_refs.append(SourceRef(source_type=EvidenceSourceType.FILE,
                                             locator=path, status="ok" if content else "empty"))
                if content:
                    evidence_refs.append(EvidenceRef(
                        locator=path, snippet=content[:_MAX_SNIPPET],
                        source_type=EvidenceSourceType.FILE, score=0.6))
            else:
                source_refs.append(SourceRef(source_type=EvidenceSourceType.FILE, locator=path,
                                             status="not_found", detail=res.error or ""))
        return ProviderResult(evidence_refs=evidence_refs, source_refs=source_refs)


class RepoEvidenceProvider(_BaseProvider):
    source_type = EvidenceSourceType.REPO

    def fetch(self, query: str, targets: List[str], context: Dict[str, Any]) -> ProviderResult:
        res = self.agent.call_tool("repo.search", {"query": query, "db_path": self._db_path})
        matches = (res.data or {}).get("matches", []) if isinstance(res.data, dict) else []
        source_refs = [SourceRef(source_type=EvidenceSourceType.REPO, locator=query,
                                 status="ok" if matches else "empty")]
        evidence_refs = [
            EvidenceRef(locator=f"{m.get('path')}:{m.get('line')}",
                        snippet=str(m.get("snippet", ""))[:_MAX_SNIPPET],
                        source_type=EvidenceSourceType.REPO, score=0.5)
            for m in matches
        ]
        return ProviderResult(evidence_refs=evidence_refs, source_refs=source_refs)


class AttachmentEvidenceProvider(_BaseProvider):
    source_type = EvidenceSourceType.ATTACHMENT

    def fetch(self, query: str, targets: List[str], context: Dict[str, Any]) -> ProviderResult:
        res = self.agent.call_tool("attachment.recall", {"query": query, "db_path": self._db_path})
        chunks = (res.data or {}).get("chunks", []) if isinstance(res.data, dict) else []
        source_refs = [SourceRef(source_type=EvidenceSourceType.ATTACHMENT, locator=query,
                                 status="ok" if chunks else "empty")]
        evidence_refs = [
            EvidenceRef(locator=str(c.get("chunk_id", "")),
                        snippet=str(c.get("text", ""))[:_MAX_SNIPPET],
                        source_type=EvidenceSourceType.ATTACHMENT, score=0.5)
            for c in chunks
        ]
        return ProviderResult(evidence_refs=evidence_refs, source_refs=source_refs)


class ResumeEvidenceProvider(_BaseProvider):
    source_type = EvidenceSourceType.RESUME

    def fetch(self, query: str, targets: List[str], context: Dict[str, Any]) -> ProviderResult:
        res = self.agent.call_tool("resume.recall", {"query": query, "db_path": self._db_path})
        diags = (res.data or {}).get("diagnoses", []) if isinstance(res.data, dict) else []
        source_refs = [SourceRef(source_type=EvidenceSourceType.RESUME, locator=query,
                                 status="ok" if diags else "empty")]
        evidence_refs = [
            EvidenceRef(locator=str(d.get("diagnosis_id", "")),
                        snippet=str(d.get("summary", ""))[:_MAX_SNIPPET],
                        source_type=EvidenceSourceType.RESUME, score=0.5)
            for d in diags
        ]
        return ProviderResult(evidence_refs=evidence_refs, source_refs=source_refs)


def build_default_providers(agent: Any) -> Dict[EvidenceSourceType, _BaseProvider]:
    """构造四个 provider，返回 source_type → provider 的映射（agent 私有）。"""
    providers = [
        FileEvidenceProvider(agent),
        RepoEvidenceProvider(agent),
        AttachmentEvidenceProvider(agent),
        ResumeEvidenceProvider(agent),
    ]
    return {p.source_type: p for p in providers}
