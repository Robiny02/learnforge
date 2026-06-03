"""daily 记忆写入 + 索引（真值 = markdown，派生 = chunks 索引）。

`record()`：①往 memory/YYYY-MM-DD.md 追加 note（真值，files.append_daily）
            ②把同一条 index 进 chunks(kb_scope='local')+fts+(向量)，metadata 带 {origin,kind,date}。
`reindex()`：清掉旧的 local-daily 索引，按 markdown 文件全量重建（文件可丢索引重建）。

只写"内容/索引"，不碰 mastery，不破坏 Manager 唯一写者约束。无 embedding key 时仅建 FTS。
"""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from ..knowledge.ingest import ingest_document
from ..storage.repositories import ChunkRepository
from .decay import importance_for
from .files import append_daily, iter_daily_notes

_DAILY_META_ORIGIN = "daily"


def _content_hash(kind: str, topic: Optional[str], text: str) -> str:
    """归一化（去首尾空白、折叠内部空白、小写）后对 kind|topic|text 取 SHA1，用于去重（R6.3）。"""
    norm = re.sub(r"\s+", " ", (text or "").strip()).lower()
    return hashlib.sha1(f"{kind}|{topic or ''}|{norm}".encode("utf-8")).hexdigest()


def _index_note(
    text: str,
    kind: str,
    topic: Optional[str],
    date_iso: str,
    importance: Optional[float] = None,
    content_hash: Optional[str] = None,
    repo: Optional[ChunkRepository] = None,
    db_path: Optional[str] = None,
    embed: bool = True,
) -> None:
    meta = {
        "origin": _DAILY_META_ORIGIN,
        "kind": kind,
        "topic": topic,
        "date": date_iso,
        "importance": importance if importance is not None else importance_for(kind),
    }
    if content_hash:
        meta["hash"] = content_hash
    ingest_document(
        text=text,
        source_type="doc",
        topic=topic,
        kb_scope="local",
        metadata=meta,
        db_path=db_path,
        repo=repo,
        embed=embed,
    )


def _is_duplicate(content_hash: str, db_path: Optional[str]) -> bool:
    """是否已存在同 hash 的 daily 记忆（R6.3 去重）。查询失败 → 视为不重复（best-effort）。"""
    try:
        repo = ChunkRepository(db_path=db_path)
        row = repo.conn.execute(
            "SELECT 1 FROM chunks WHERE kb_scope='local' "
            "AND json_extract(metadata,'$.origin')=? "
            "AND json_extract(metadata,'$.hash')=? LIMIT 1",
            (_DAILY_META_ORIGIN, content_hash),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def record(
    kind: str,
    text: str,
    topic: Optional[str] = None,
    date: Union[str, datetime, None] = None,
    db_path: Optional[str] = None,
    root: Optional[Union[str, Path]] = None,
) -> dict:
    """写一条 daily 记忆（markdown 真值 + chunks 索引）。返回 note dict。best-effort 索引。

    写前去重（R6.3）：同 kind+topic+归一化正文 已存在 → 跳过 markdown 与索引，
    返回带 `duplicate=True` 的 note dict（不重复沉淀无价值的重复内容）。
    """
    from .log import MEMORY_LOG, WRITE

    h = _content_hash(kind, topic, text)
    if _is_duplicate(h, db_path):
        MEMORY_LOG.record(WRITE, "跳过保存", "内容重复", reason="已存在相同记忆")
        return {"date": "", "kind": kind, "topic": topic,
                "text": (text or "").strip(), "duplicate": True}
    note = append_daily(kind=kind, text=text, topic=topic, date=date, root=root)
    try:
        _index_note(
            note["text"],
            kind,
            topic,
            note["date"],
            importance=note.get("importance"),
            content_hash=h,
            db_path=db_path,
            embed=False,
        )
        MEMORY_LOG.record(WRITE, "保存记忆", f"成功，类型为 {kind}")
    except Exception:
        MEMORY_LOG.record(WRITE, "记忆写入失败", "索引异常", reason="best-effort 跳过")
    return note


def reindex(
    db_path: Optional[str] = None, root: Optional[Union[str, Path]] = None
) -> int:
    """清掉旧 local-daily 索引并按 markdown 文件重建。返回重建的 note 数。"""
    repo = ChunkRepository(db_path=db_path)
    conn = repo.conn
    rows = conn.execute(
        "SELECT chunk_id FROM chunks "
        "WHERE kb_scope='local' AND json_extract(metadata,'$.origin')=?",
        (_DAILY_META_ORIGIN,),
    ).fetchall()
    for r in rows:
        cid = r["chunk_id"]
        conn.execute("DELETE FROM chunk_fts WHERE chunk_id = ?", (cid,))
        try:
            conn.execute("DELETE FROM chunk_vectors WHERE chunk_id = ?", (cid,))
        except Exception:
            pass
        conn.execute("DELETE FROM chunks WHERE chunk_id = ?", (cid,))
    conn.commit()

    n = 0
    for note in iter_daily_notes(root):
        if not note.get("text"):
            continue
        kind = note.get("kind", "note")
        topic = note.get("topic")
        h = _content_hash(kind, topic, note["text"])
        _index_note(
            note["text"],
            kind,
            topic,
            note.get("date", ""),
            importance=note.get("importance"),
            content_hash=h,
            repo=repo,
        )
        n += 1
    return n


def _main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="LearnForge daily 记忆索引重建")
    parser.add_argument("--reindex", action="store_true", help="从 markdown 重建 local-daily 索引")
    parser.add_argument("--db", help="目标 SQLite 路径（默认 config.DB_PATH）")
    parser.add_argument("--root", help="记忆根目录（默认 config.MEMORY_DIR）")
    args = parser.parse_args(argv)
    if args.reindex:
        n = reindex(db_path=args.db, root=args.root)
        print(f"[daily] 从 markdown 重建 {n} 条 daily 索引。")
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
