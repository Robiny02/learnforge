"""EvidenceResearchAgent 的只读 source 工具 handler。

三个工具，全部**只读**（effect=READ），供隔离上下文的证据 worker 调用：

  - repo.search       : 在工作区 `config.WORKSPACE_READ_ROOT` 内做关键词搜索（受控、只读）。
  - attachment.recall : 召回上传附件材料（local chunks，origin=attachment）。
  - resume.recall     : 召回已存简历诊断 / 简历正文。

它们都不写任何 state；DB 路径经 args["db_path"] 传入（与 tools/diagnosis.py 同构）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config
from .base import ToolResult

# 只搜文本类源码/文档；跳过噪声目录与大文件。
_TEXT_SUFFIXES = {".py", ".md", ".txt", ".rst", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml"}
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "data", "dist", "build", ".pytest_cache"}
_MAX_FILES_SCANNED = 4000
_MAX_MATCHES = 20
_MAX_FILE_BYTES = 400_000


def register_evidence_source_tools(registry: Any) -> None:
    registry.register("repo.search", repo_search)
    registry.register("attachment.recall", attachment_recall)
    registry.register("resume.recall", resume_recall)


def _db_path(args: Dict[str, Any]) -> Optional[str]:
    value = args.get("db_path")
    return str(value) if value else None


# ------------------------------------------------------------------ repo.search
def repo_search(args: Dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    top_k = int(args.get("top_k") or _MAX_MATCHES)
    if not query:
        return ToolResult(name="repo.search", ok=False, observation="missing 'query'",
                          error="missing_query")
    root = Path(config.WORKSPACE_READ_ROOT).resolve()
    needle = query.lower()
    matches: List[Dict[str, Any]] = []
    scanned = 0
    for path in _iter_text_files(root):
        scanned += 1
        if scanned > _MAX_FILES_SCANNED:
            break
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                rel = path.relative_to(root)
                matches.append({"path": str(rel), "line": lineno, "snippet": line.strip()[:200]})
                if len(matches) >= min(top_k, _MAX_MATCHES):
                    break
        if len(matches) >= min(top_k, _MAX_MATCHES):
            break
    return ToolResult(
        name="repo.search", ok=True,
        observation=f"{len(matches)} matches for '{query}' across {scanned} files",
        data={"matches": matches},
    )


def _iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


# ------------------------------------------------------------ attachment.recall
def attachment_recall(args: Dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 4)
    document_id = args.get("document_id")
    from ..agents.retrieval import RetrievalAgent
    from ..contracts.agents.retrieval import RetrievalFilters, RetrievalInput
    from ..contracts.enums import KnowledgeScope, RetrievalMethod

    if not query and not document_id:
        return ToolResult(name="attachment.recall", ok=True, observation="no query/document_id",
                          data={"chunks": []})
    try:
        out = RetrievalAgent(db_path=_db_path(args)).run(
            RetrievalInput(
                query=query or str(document_id or ""),
                scopes=[KnowledgeScope.LOCAL],
                method=RetrievalMethod.FULLTEXT,
                filters=RetrievalFilters(origin="attachment", document_id=document_id),
                top_k=top_k,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 召回失败不抛断，返回空证据
        return ToolResult(name="attachment.recall", ok=False, observation=str(exc),
                          error="recall_error", data={"chunks": []})
    chunks = [{"chunk_id": getattr(c, "chunk_id", ""),
               "text": (getattr(c, "text", "") or "")[:400]} for c in out.chunks]
    return ToolResult(
        name="attachment.recall", ok=True,
        observation=f"recalled {len(chunks)} attachment chunks",
        data={"chunks": chunks, "degraded": out.degraded},
    )


# ----------------------------------------------------------------- resume.recall
def resume_recall(args: Dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 3)
    from ..memory.resume import recall_resume_diagnoses

    try:
        diags = recall_resume_diagnoses(query=query or None, db_path=_db_path(args), top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(name="resume.recall", ok=False, observation=str(exc),
                          error="recall_error", data={"diagnoses": []})
    items = [{"diagnosis_id": d.diagnosis_id, "target_role": d.target_role,
              "summary": (d.summary or "")[:400]} for d in diags]
    return ToolResult(
        name="resume.recall", ok=True,
        observation=f"recalled {len(items)} resume diagnoses",
        data={"diagnoses": items},
    )
