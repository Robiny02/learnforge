"""简历诊断的记忆持久化与召回（详细保存 + 整条召回）。

设计目标（用户要求）：诊断结果**详细保存**且**召回时取回完整内容**，而非片段。

做法：把一次 `ResumeDiagnosis` 作为**单个 chunk** 写入本地用户库（`kb_scope='local'`，
`origin='resume_diagnosis'`）——
  - chunk **正文** = `diag.search_text()`：可读摘要 + 各问题片段，供 FTS/子串命中；
  - chunk **metadata.resume_diagnosis** = 完整 JSON：召回时按原样重建出整条 `ResumeDiagnosis`。

只写"内容/索引"（chunks），不碰 mastery/learning_paths，不破坏 Manager 唯一写者约束，
也不触发 DiagnosisAgent 只读不变量守护的状态表（knowledge_atoms/events/paths/diagnosis_reports）。
"""

from __future__ import annotations

import json
from typing import List, Optional

from ..contracts.agents.diagnosis import ResumeDiagnosis
from ..storage.repositories import ChunkRepository

ORIGIN = "resume_diagnosis"


def save_resume_diagnosis(
    diag: ResumeDiagnosis, db_path: Optional[str] = None,
    repo: Optional[ChunkRepository] = None,
) -> str:
    """整条保存一次简历诊断，返回 chunk_id。best-effort（失败抛给调用方决定降级）。"""
    repo = repo or ChunkRepository(db_path=db_path)
    chunk_id = f"resume-diag-{diag.diagnosis_id}"
    meta = {
        "origin": ORIGIN,
        "diagnosis_id": diag.diagnosis_id,
        "created_at": diag.created_at,
        "target_role": diag.target_role,
        "role_type": diag.role_type,
        "jd_fit": diag.jd_fit.value,
        "issue_count": len(diag.issues),
        # 完整诊断 JSON：召回时据此重建，保证「召回完整内容」。
        "resume_diagnosis": diag.model_dump_json(),
    }
    repo.upsert(
        chunk_id=chunk_id,
        text=diag.search_text(),       # 供 FTS/子串命中的可读正文
        source_type="doc",
        topic=diag.role_type or diag.target_role,
        kb_scope="local",
        metadata=meta,
    )
    return chunk_id


def _reconstruct(metadata_json: str) -> Optional[ResumeDiagnosis]:
    try:
        meta = json.loads(metadata_json or "{}")
        full = meta.get("resume_diagnosis")
        if not full:
            return None
        return ResumeDiagnosis.model_validate_json(full)
    except Exception:
        return None


def recall_resume_diagnoses(
    query: Optional[str] = None, db_path: Optional[str] = None,
    top_k: int = 5, repo: Optional[ChunkRepository] = None,
) -> List[ResumeDiagnosis]:
    """召回历史简历诊断，返回**完整** `ResumeDiagnosis` 列表（按新→旧）。

    query 为空 → 返回最近 top_k 条；有 query → 先 FTS，再子串(LIKE，CJK 友好)兜底。
    无论命中方式，结果都从 metadata 的完整 JSON 重建，召回方拿到整条诊断。
    """
    repo = repo or ChunkRepository(db_path=db_path)
    conn = repo.conn
    rows = []
    q = (query or "").strip()

    if q:
        # 1) FTS：对英文/有空白分词更准。失败/无命中再降级。
        try:
            rows = conn.execute(
                "SELECT c.metadata AS metadata, c.created_at AS created_at "
                "FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.chunk_id "
                "WHERE chunk_fts MATCH ? AND c.kb_scope='local' "
                "AND json_extract(c.metadata,'$.origin')=? LIMIT ?",
                (q, ORIGIN, top_k * 3),
            ).fetchall()
        except Exception:
            rows = []
        if not rows:
            # 2) 子串兜底（CJK FTS 不切词时仍可召回）。
            rows = conn.execute(
                "SELECT metadata, created_at FROM chunks "
                "WHERE kb_scope='local' AND json_extract(metadata,'$.origin')=? "
                "AND text LIKE ? ORDER BY created_at DESC LIMIT ?",
                (ORIGIN, f"%{q}%", top_k * 3),
            ).fetchall()
    else:
        rows = conn.execute(
            "SELECT metadata, created_at FROM chunks "
            "WHERE kb_scope='local' AND json_extract(metadata,'$.origin')=? "
            "ORDER BY created_at DESC LIMIT ?",
            (ORIGIN, top_k * 3),
        ).fetchall()

    out: List[ResumeDiagnosis] = []
    seen = set()
    for r in rows:
        diag = _reconstruct(r["metadata"] if isinstance(r, dict) or hasattr(r, "keys") else r[0])
        if diag is None or diag.diagnosis_id in seen:
            continue
        seen.add(diag.diagnosis_id)
        out.append(diag)
        if len(out) >= top_k:
            break
    # 按 created_at 新→旧稳定排序。
    out.sort(key=lambda d: d.created_at, reverse=True)
    return out


def latest_resume_diagnosis(
    db_path: Optional[str] = None, repo: Optional[ChunkRepository] = None
) -> Optional[ResumeDiagnosis]:
    """取最近一次简历诊断（完整）。无则 None。"""
    items = recall_resume_diagnoses(query=None, db_path=db_path, top_k=1, repo=repo)
    return items[0] if items else None
