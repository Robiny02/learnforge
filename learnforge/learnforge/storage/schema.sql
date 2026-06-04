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
    kb_scope        TEXT NOT NULL DEFAULT 'shared'   -- 双层：local/shared（共享知识库默认 shared）
        CHECK (kb_scope IN ('local','shared')),
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

CREATE INDEX IF NOT EXISTS idx_chunks_scope
    ON chunks (kb_scope, topic);

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
-- 7b) 历史问答（本地用户库，可检索；供 LocalUserSource 召回个人问答历史）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS qa_history (
    qa_id      TEXT PRIMARY KEY,
    question   TEXT NOT NULL,
    answer     TEXT,
    topic      TEXT,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS qa_history_fts USING fts5 (
    qa_id UNINDEXED,
    question,
    answer,
    tokenize = 'porter unicode61'
);

-- ===========================================================================
-- 7c) 短期会话记忆（跨轮连续性）：每 session 的滚动摘要 + 当前任务态。
--     长期记忆走 chunks/向量库,这里只放轻量、易变的会话态(单写者 Manager 收口)。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS session_state (
    session_id      TEXT PRIMARY KEY,
    summary         TEXT NOT NULL DEFAULT '',         -- 更旧消息压缩成的会话摘要
    recent_messages TEXT NOT NULL DEFAULT '[]',       -- JSON array，最近 N 轮 {user, reply} 原文
    active_task     TEXT NOT NULL DEFAULT '{}',        -- JSON object（active_mock 等）
    updated_at      TEXT NOT NULL
);

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

-- ===========================================================================
-- 10) 会话对话账本（替代进程内 _UI_CTX：持久、可查、可 replay）
--     一行 = 一个对话轮 + 它的路由结果与产物。供意图分类(取投影)与主 agent(取全文)加载。
--     参考 Reactor 的 DialogueRun/ToolInvocation/ToolOutput：role/content/tool_calls/status。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS dialogue_turns (
    turn_id     TEXT PRIMARY KEY,                 -- str(uuid4)
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,                 -- 会话内自增,定序/取最近 N 轮
    role        TEXT NOT NULL,                    -- user | assistant | system
    text        TEXT NOT NULL DEFAULT '',         -- 用户原话 / 助手回应摘要

    capability  TEXT,                             -- qa|planning|diagnosis|mock|note|composite
    route_mode  TEXT,                             -- single | multi
    topic       TEXT,                             -- 抽到的主题（意图分类的关键信号）

    tool_calls  TEXT NOT NULL DEFAULT '[]',       -- JSON array [{agent,task,status}]（≈ ToolInvocation）
    artifacts   TEXT NOT NULL DEFAULT '[]',       -- JSON array [{kind,ref}]（path_id/mock_sid… ≈ ToolArtifactRegistry）

    status      TEXT NOT NULL DEFAULT 'completed',-- completed|failed|needs_input
    trace_id    TEXT,                             -- 关联 agent_traces，便于下钻
    created_at  TEXT NOT NULL                     -- ISO8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_dialogue_session_seq
    ON dialogue_turns (session_id, seq);
