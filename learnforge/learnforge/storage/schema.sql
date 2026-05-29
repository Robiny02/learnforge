-- LearnForge SQLite DDL（Design §4c / §4d / §7a / §9a）
-- Phase 1：建表 DDL 全写完，不填数据。
-- 依赖：sqlite-vec 扩展（vec0 虚拟表）、FTS5（SQLite 内建）。
-- 约定：所有 *_id 为 str(uuid4)；时间戳存 ISO8601 文本（UTC）。

PRAGMA foreign_keys = ON;

-- ===========================================================================
-- 1) Knowledge Atom 主表（Design §4d）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS knowledge_atoms (
    atom_id            TEXT PRIMARY KEY,
    topic              TEXT NOT NULL,
    subtopic           TEXT,
    title              TEXT NOT NULL,
    canonical_question TEXT NOT NULL,
    canonical_answer   TEXT,
    tags               TEXT NOT NULL DEFAULT '[]',   -- JSON array
    difficulty         INTEGER NOT NULL DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5),
    mastery_score      REAL NOT NULL DEFAULT 0.0 CHECK (mastery_score BETWEEN 0.0 AND 1.0),
    mastery_tier       TEXT NOT NULL DEFAULT 'unknown'
        CHECK (mastery_tier IN ('unknown','learning','familiar','proficient','mastered')),
    last_reviewed_at   TEXT,
    review_count       INTEGER NOT NULL DEFAULT 0,
    correct_count      INTEGER NOT NULL DEFAULT 0,
    decay_rate         REAL NOT NULL DEFAULT 0.05,
    source_refs        TEXT NOT NULL DEFAULT '[]',   -- JSON array
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

-- 复合索引（Design §4c 索引策略）
CREATE INDEX IF NOT EXISTS idx_atoms_topic_tier
    ON knowledge_atoms (topic, mastery_tier);

-- ===========================================================================
-- 2) 检索语料 chunks（Design §7c 切片：interview_post / blog / doc / atom）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    source_type     TEXT NOT NULL DEFAULT 'atom'
        CHECK (source_type IN ('interview_post','blog','doc','atom')),
    topic           TEXT,
    parent_chunk_id TEXT,                            -- 父子 chunk（面经）
    atom_refs       TEXT NOT NULL DEFAULT '[]',      -- JSON array
    token_count     INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT NOT NULL DEFAULT '{}',      -- JSON object
    created_at      TEXT NOT NULL,
    FOREIGN KEY (parent_chunk_id) REFERENCES chunks (chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_source_topic
    ON chunks (source_type, topic);

-- ===========================================================================
-- 3) 向量索引（sqlite-vec vec0，Design §4c / §7a，dim=1024）
--    注意：dim 待 retrieval spike 锁定（DG1），此处先按 Design 默认 1024。
-- ===========================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS atom_vectors USING vec0 (
    atom_id   TEXT PRIMARY KEY,
    embedding FLOAT[1024]
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0 (
    chunk_id  TEXT PRIMARY KEY,
    embedding FLOAT[1024]
);

-- ===========================================================================
-- 4) 全文索引（FTS5，BM25 内建，Design §4c / §7b）
--    字段权重 canonical_question > canonical_answer > tags 在查询期用 bm25() 配置。
-- ===========================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS atom_fts USING fts5 (
    atom_id UNINDEXED,
    canonical_question,
    canonical_answer,
    tags,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5 (
    chunk_id UNINDEXED,
    text,
    tokenize = 'porter unicode61'
);

-- ===========================================================================
-- 5) 共享状态（Design §4b/§4c）：UserProfile / LearningPath / path_items
-- ===========================================================================
CREATE TABLE IF NOT EXISTS user_profile (
    user_id       TEXT PRIMARY KEY,
    goals         TEXT NOT NULL DEFAULT '[]',        -- JSON array
    current_level TEXT,
    preferences   TEXT NOT NULL DEFAULT '{}',        -- JSON object
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_paths (
    path_id    TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES user_profile (user_id)
);

CREATE TABLE IF NOT EXISTS path_items (
    item_id   TEXT PRIMARY KEY,
    path_id   TEXT NOT NULL,
    atom_id   TEXT NOT NULL,
    order_idx INTEGER NOT NULL,
    day_index INTEGER NOT NULL,
    status    TEXT NOT NULL DEFAULT 'pending',
    FOREIGN KEY (path_id) REFERENCES learning_paths (path_id),
    FOREIGN KEY (atom_id) REFERENCES knowledge_atoms (atom_id)
);

CREATE INDEX IF NOT EXISTS idx_path_items_path_order
    ON path_items (path_id, order_idx);

-- ===========================================================================
-- 6) 事件日志（Design §4c，Proposal §4c 事件解耦给诊断）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS interaction_events (
    event_id   TEXT PRIMARY KEY,
    trace_id   TEXT,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('qa_signal','mock_weakness','path_changed')),
    topic      TEXT NOT NULL,
    atom_refs  TEXT NOT NULL DEFAULT '[]',           -- JSON array
    signal     TEXT NOT NULL DEFAULT '{}',           -- JSON object
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created_type
    ON interaction_events (created_at, event_type);

-- ===========================================================================
-- 7) Mock 会话与逐轮（Design §4c）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS mock_sessions (
    session_id        TEXT PRIMARY KEY,
    topic             TEXT NOT NULL,
    difficulty        INTEGER NOT NULL DEFAULT 3,
    max_turns         INTEGER NOT NULL DEFAULT 10,
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mock_turns (
    turn_id         TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    turn_index      INTEGER NOT NULL,
    question        TEXT,
    expected_points TEXT NOT NULL DEFAULT '[]',      -- JSON array
    user_answer     TEXT,
    score           TEXT,                            -- JSON object (Score)
    created_at      TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES mock_sessions (session_id)
);

CREATE INDEX IF NOT EXISTS idx_mock_turns_session
    ON mock_turns (session_id, turn_index);

-- ===========================================================================
-- 8) 诊断快照（只读 agent 产出，由 Manager 落，Design §4c）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS diagnosis_reports (
    report_id   TEXT PRIMARY KEY,
    trace_id    TEXT,
    time_window TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    result      TEXT NOT NULL DEFAULT '{}',          -- JSON object (DiagnosisResult)
    confidence  REAL NOT NULL DEFAULT 0.0,
    created_at  TEXT NOT NULL
);

-- ===========================================================================
-- 9) 全链路追踪（Design §9a，Proposal §7 责任归属）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS agent_traces (
    message_id TEXT PRIMARY KEY,
    trace_id   TEXT NOT NULL,
    sender     TEXT NOT NULL,
    recipient  TEXT NOT NULL,
    status     TEXT,
    confidence REAL,
    cost_usd   REAL NOT NULL DEFAULT 0.0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_trace_id
    ON agent_traces (trace_id);
