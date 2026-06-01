"""PgChunkRepository —— PostgreSQL 上的文档/分块读写与检索。

检索能力：
- keyword_search：优先 BM25（pg_search 的 @@@ 算子）；不可用则降级 PostgreSQL 原生 FTS
  （tsvector @@ plainto_tsquery + ts_rank_cd）。`keyword_backend` 真实反映用了哪种，
  并写入每个 Chunk.metadata，绝不把 FTS 当 BM25。
- vector_search：pgvector HNSW，cosine 距离（<=>）。
- hybrid_search：keyword + vector 各取 top_k 后 RRF 融合（复用 knowledge.sources.rrf_fuse）。

所有 SQL 用 %s 占位（psycopg2/3 通用）；向量以 '[..]'::vector 字符串传参，不依赖 pgvector 适配器。
"""

from __future__ import annotations

import json
from typing import List, Optional, Tuple

from ...contracts.agents.retrieval import Chunk, RetrievalFilters
from ...contracts.enums import SourceType
from ..sources.base import rrf_fuse
from .db import get_pg_connection
from .migrate import has_bm25

_SELECT_COLS = "id, content, source_url, title, heading_path, topic, source_type, metadata"


def _vec_literal(embedding: List[float]) -> str:
    """list[float] → pgvector 字面量 '[0.1,0.2,...]'。"""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


class PgChunkRepository:
    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn = dsn
        self._bm25: Optional[bool] = None  # 懒探测并缓存

    # ----------------------------------------------------------- 连接 / 能力
    def _conn(self, autocommit: bool = False):
        return get_pg_connection(self._dsn, autocommit=autocommit)

    def keyword_backend(self) -> str:
        """'bm25'（pg_search 可用）或 'postgres_fts'（原生 FTS 降级）。"""
        if self._bm25 is None:
            conn = self._conn()
            try:
                self._bm25 = has_bm25(conn)
            finally:
                conn.close()
        return "bm25" if self._bm25 else "postgres_fts"

    # ----------------------------------------------------------------- 写入
    def upsert_document(
        self,
        doc_id: str,
        source_url: str = "",
        title: str = "",
        source_type: str = "doc",
        topic: str = "",
        metadata: Optional[dict] = None,
        conn=None,
    ) -> None:
        owns = conn is None
        conn = conn or self._conn(autocommit=True)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO source_documents (id, source_url, title, source_type, topic, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET source_url=EXCLUDED.source_url, title=EXCLUDED.title, "
                "source_type=EXCLUDED.source_type, topic=EXCLUDED.topic, metadata=EXCLUDED.metadata",
                (doc_id, source_url, title, source_type, topic, json.dumps(metadata or {})),
            )
            cur.close()
        finally:
            if owns:
                conn.close()

    def upsert_chunk(
        self,
        chunk_id: str,
        content: str,
        document_id: Optional[str] = None,
        source_url: str = "",
        title: str = "",
        heading_path: str = "",
        topic: str = "",
        source_type: str = "doc",
        metadata: Optional[dict] = None,
        embedding: Optional[List[float]] = None,
        conn=None,
    ) -> None:
        owns = conn is None
        conn = conn or self._conn(autocommit=True)
        try:
            cur = conn.cursor()
            emb_sql = "%s::vector" if embedding is not None else "NULL"
            params: list = [
                chunk_id, document_id, content, source_url, title, heading_path,
                topic, source_type, json.dumps(metadata or {}),
            ]
            if embedding is not None:
                params.append(_vec_literal(embedding))
            cur.execute(
                f"INSERT INTO document_chunks "
                f"(id, document_id, content, source_url, title, heading_path, topic, source_type, metadata, embedding) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, {emb_sql}) "
                f"ON CONFLICT (id) DO UPDATE SET content=EXCLUDED.content, source_url=EXCLUDED.source_url, "
                f"title=EXCLUDED.title, heading_path=EXCLUDED.heading_path, topic=EXCLUDED.topic, "
                f"source_type=EXCLUDED.source_type, metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding",
                params,
            )
            cur.close()
        finally:
            if owns:
                conn.close()

    def count_chunks(self) -> int:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM document_chunks")
            n = cur.fetchone()[0]
            cur.close()
            return int(n)
        finally:
            conn.close()

    # ------------------------------------------------------------- filter SQL
    @staticmethod
    def _where(filters: Optional[RetrievalFilters], extra: str = "") -> Tuple[str, list]:
        clauses: List[str] = []
        params: list = []
        if extra:
            clauses.append(extra)
        if filters is not None:
            if filters.topic:
                clauses.append("topic = %s")
                params.append(filters.topic)
            if filters.source_type is not None:
                clauses.append("source_type = %s")
                params.append(filters.source_type.value)
            if filters.source_name:
                clauses.append("title ILIKE %s")
                params.append(f"%{filters.source_name}%")
            if filters.difficulty:
                clauses.append("metadata->>'difficulty' = %s")
                params.append(filters.difficulty)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def _rows_to_chunks(self, rows: list, backend: str) -> List[Chunk]:
        chunks: List[Chunk] = []
        for r in rows:
            cid, content, source_url, title, heading_path, topic, source_type, meta, score = r
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            meta = dict(meta or {})
            meta.update({
                "title": title, "source_url": source_url, "heading_path": heading_path,
                "topic": topic, "keyword_backend": backend,
            })
            try:
                st = SourceType(source_type)
            except ValueError:
                st = SourceType.DOC
            chunks.append(Chunk(chunk_id=cid, text=content, score=float(score or 0.0),
                                source_type=st, metadata=meta))
        return chunks

    # --------------------------------------------------------------- 检索：关键词
    def keyword_search(
        self, query: str, filters: Optional[RetrievalFilters] = None, top_k: int = 10
    ) -> List[Chunk]:
        backend = self.keyword_backend()
        conn = self._conn()
        try:
            cur = conn.cursor()
            if backend == "bm25":
                # BM25（pg_search）：@@@ 文本算子 + paradedb.score。
                where, params = self._where(filters, extra="content @@@ %s")
                params = [query] + params + [top_k]
                cur.execute(
                    f"SELECT {_SELECT_COLS}, paradedb.score(id) AS score "
                    f"FROM document_chunks{where} ORDER BY score DESC LIMIT %s",
                    params,
                )
            else:
                # 原生 FTS 降级（非真正 BM25）。
                where, params = self._where(filters, extra="tsv @@ plainto_tsquery('simple', %s)")
                params = [query, query] + params + [top_k]
                cur.execute(
                    f"SELECT {_SELECT_COLS}, ts_rank_cd(tsv, plainto_tsquery('simple', %s)) AS score "
                    f"FROM document_chunks{where} ORDER BY score DESC LIMIT %s",
                    params,
                )
            rows = cur.fetchall()
            cur.close()
            return self._rows_to_chunks(rows, backend)
        finally:
            conn.close()

    # --------------------------------------------------------------- 检索：向量
    def vector_search(
        self, embedding: Optional[List[float]], filters: Optional[RetrievalFilters] = None,
        top_k: int = 10,
    ) -> List[Chunk]:
        if not embedding:
            return []
        backend = self.keyword_backend()
        conn = self._conn()
        try:
            cur = conn.cursor()
            where, params = self._where(filters, extra="embedding IS NOT NULL")
            vec = _vec_literal(embedding)
            # score = cosine 相似度（1 - 距离）；HNSW 走 <=> 排序。
            params = [vec] + params + [vec, top_k]
            cur.execute(
                f"SELECT {_SELECT_COLS}, 1 - (embedding <=> %s::vector) AS score "
                f"FROM document_chunks{where} ORDER BY embedding <=> %s::vector ASC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
            cur.close()
            return self._rows_to_chunks(rows, backend)
        finally:
            conn.close()

    # --------------------------------------------------------------- 检索：hybrid
    def hybrid_search(
        self, query: str, embedding: Optional[List[float]] = None,
        filters: Optional[RetrievalFilters] = None, top_k: int = 10,
    ) -> List[Chunk]:
        kw = self.keyword_search(query, filters, top_k)
        vec = self.vector_search(embedding, filters, top_k) if embedding else []
        return rrf_fuse([kw, vec], top_k=top_k)

    # --------------------------------------------------------------- 审计写入
    def start_run(self, run_id: str, source: str) -> None:
        conn = self._conn(autocommit=True)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO ingest_runs (id, source, status, keyword_backend) "
                "VALUES (%s, %s, 'running', %s)",
                (run_id, source, self.keyword_backend()),
            )
            cur.close()
        finally:
            conn.close()

    def finish_run(self, run_id: str, documents: int, chunks: int, errors: int,
                   status: str = "ok") -> None:
        conn = self._conn(autocommit=True)
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ingest_runs SET status=%s, documents=%s, chunks=%s, errors=%s, "
                "finished_at=now() WHERE id=%s",
                (status, documents, chunks, errors, run_id),
            )
            cur.close()
        finally:
            conn.close()

    def record_error(self, error_id: str, run_id: str, source_url: str, error: str) -> None:
        conn = self._conn(autocommit=True)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO source_errors (id, run_id, source_url, error) VALUES (%s, %s, %s, %s)",
                (error_id, run_id, source_url, error[:2000]),
            )
            cur.close()
        finally:
            conn.close()
