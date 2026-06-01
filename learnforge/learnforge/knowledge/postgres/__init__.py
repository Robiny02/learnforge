"""PostgreSQL 知识库存储层（可选后端，SQLite 仍为默认 fallback）。

设计目标（用户要求）：把共享知识库从 SQLite/FTS5/sqlite-vec 迁移到 PostgreSQL：
- BM25 关键词检索（若环境装有 pg_search 扩展），否则 **明确降级** 到 PostgreSQL 原生
  full-text（tsvector + ts_rank_cd）——绝不把 FTS 伪装成 BM25。
- pgvector + HNSW 向量检索。
- hybrid：keyword top_k + vector top_k → RRF 融合（复用现有 knowledge.sources.rrf_fuse）。

全部惰性导入 psycopg：未安装驱动或未配置 LF_POSTGRES_DSN 时整个后端"不可用"，
相关测试 skip，不影响现有 SQLite 链路与 pytest。
"""

from __future__ import annotations

from .db import PostgresUnavailable, get_pg_connection, pg_available

__all__ = ["PostgresUnavailable", "get_pg_connection", "pg_available"]
