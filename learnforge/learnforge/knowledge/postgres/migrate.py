"""PostgreSQL 知识库 migration。

用法：
    LF_POSTGRES_DSN=postgresql://user:pass@host:5432/db \
        python -m learnforge.knowledge.postgres.migrate

行为：
1. 注入 EMBEDDING_DIM 后执行 schema.sql（建扩展/表/FTS+HNSW 索引）。
2. 机会性尝试 BM25：CREATE EXTENSION pg_search + bm25 索引；失败则保留 FTS 并打印说明。
   —— 绝不把 FTS 当 BM25；keyword_backend 真实反映当前能力。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ... import config
from .db import get_pg_connection, pg_available


def _schema_sql() -> str:
    raw = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    return raw.replace("{{EMBEDDING_DIM}}", str(config.EMBEDDING_DIM))


def has_bm25(conn) -> bool:
    """当前库是否装有 pg_search（ParadeDB BM25）扩展。"""
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_search'")
        return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        cur.close()


def _try_enable_bm25(conn) -> bool:
    """尝试启用 pg_search 并建 BM25 索引。不可用返回 False（保留 FTS）。"""
    cur = conn.cursor()
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
        # ParadeDB bm25 索引：key_field 必填。
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_bm25 "
            "ON document_chunks USING bm25 (id, content, title, heading_path) "
            "WITH (key_field='id')"
        )
        return True
    except Exception:
        return False
    finally:
        cur.close()


def migrate(dsn: Optional[str] = None) -> str:
    """执行 migration，返回最终 keyword_backend（'bm25' 或 'postgres_fts'）。"""
    conn = get_pg_connection(dsn, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(_schema_sql())
        cur.close()
        backend = "bm25" if _try_enable_bm25(conn) else "postgres_fts"
        return backend
    finally:
        conn.close()


def main(argv: Optional[list] = None) -> None:
    if not pg_available():
        print("PostgreSQL 不可用：请设置 LF_POSTGRES_DSN 并安装 psycopg。")
        return
    backend = migrate()
    if backend == "bm25":
        print("migration 完成。关键词检索后端：BM25 (pg_search)。")
    else:
        print(
            "migration 完成。关键词检索后端：PostgreSQL full-text (tsvector + ts_rank_cd)。\n"
            "  说明：未检测到 pg_search 扩展，已降级为原生 FTS —— 这不是真正的 BM25。"
        )


if __name__ == "__main__":
    main()
