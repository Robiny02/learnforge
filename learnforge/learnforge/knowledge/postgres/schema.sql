-- LearnForge PostgreSQL 知识库 schema（共享库 SHARED）。
-- {{EMBEDDING_DIM}} 在 migrate 时被替换为 config.EMBEDDING_DIM（同 SQLite 注入维度的做法）。
-- 注意：pg_search(BM25) 扩展在 migrate.py 中机会性创建；这里只保证可靠的 FTS 路径。

CREATE EXTENSION IF NOT EXISTS vector;

-- 原始来源文档（一篇文章/课程/面经/文档）。
CREATE TABLE IF NOT EXISTS source_documents (
    id          TEXT PRIMARY KEY,
    source_url  TEXT,
    title       TEXT,
    source_type TEXT NOT NULL DEFAULT 'doc',
    topic       TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 文档切片：检索的最小单元。
CREATE TABLE IF NOT EXISTS document_chunks (
    id           TEXT PRIMARY KEY,
    document_id  TEXT REFERENCES source_documents(id) ON DELETE CASCADE,
    content      TEXT NOT NULL,
    source_url   TEXT,
    title        TEXT,
    heading_path TEXT,
    topic        TEXT,
    source_type  TEXT NOT NULL DEFAULT 'doc',
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding    vector({{EMBEDDING_DIM}}),
    -- PostgreSQL 原生 full-text 向量（关键词 fallback 路径用）。
    tsv          tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(heading_path, '') || ' ' || content)
    ) STORED,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 关键词 FTS：GIN(tsvector)。BM25 索引（若有 pg_search）在 migrate.py 额外创建。
CREATE INDEX IF NOT EXISTS ix_document_chunks_tsv ON document_chunks USING gin (tsv);
-- 向量检索：pgvector HNSW（cosine）。
CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
-- 常用 metadata filter 列。
CREATE INDEX IF NOT EXISTS ix_document_chunks_topic ON document_chunks (topic);
CREATE INDEX IF NOT EXISTS ix_document_chunks_source_type ON document_chunks (source_type);

-- 入库批次审计。
CREATE TABLE IF NOT EXISTS ingest_runs (
    id          TEXT PRIMARY KEY,
    source      TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    documents   INTEGER NOT NULL DEFAULT 0,
    chunks      INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0,
    keyword_backend TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

-- 入库错误（文档级），便于排查与重试。
CREATE TABLE IF NOT EXISTS source_errors (
    id         TEXT PRIMARY KEY,
    run_id     TEXT REFERENCES ingest_runs(id) ON DELETE CASCADE,
    source_url TEXT,
    error      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
