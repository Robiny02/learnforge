"""仓储层（Phase 2 实装）——屏蔽 SQL，为未来迁移 PG 预留（Design §10 决策 10）。

写权限边界（Design §2a）：仅 Manager 经 LearningPathRepository.commit /
AtomRepository.update_mastery 落库；其余 agent 只读。

本地检索（用户澄清）：LocalBackend 用 FTS5 检索 SQLite 内容——chunks（atoms/doc 等）
与 mock_turns（历史 mock 记录）。云端语料（面经+技术博客）走 CloudBackend，不在此。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:  # 仅类型注解用，避免运行期循环导入（实体在方法内延迟导入）。
    from ..contracts.dialogue import DialogueTurn

from ..contracts.agents.retrieval import Chunk, RetrievalFilters
from ..contracts.atom import KnowledgeAtom
from ..contracts.enums import KnowledgeScope, SourceType
from ..contracts.message import EventPayload
from ..contracts.state import LearningPath, UserProfile
from .db import get_connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fts_sanitize(query: str) -> str:
    """把自然语言 query 转为安全的 FTS5 MATCH 串（OR 连接的带引号词项）。"""
    tokens = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if t]
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"' for t in tokens)


def _serialize_embedding(embedding: List[float]) -> str:
    """sqlite-vec 接受 JSON 浮点数组作为向量字面量（插入与 KNN query 通用）。"""
    return json.dumps([float(x) for x in embedding])


def _chunk_filter_sql(filters: Optional[RetrievalFilters]) -> Tuple[List[str], list]:
    """把 RetrievalFilters 转为对 chunks 表（别名 c）的额外 WHERE 子句与参数。"""
    clauses: List[str] = []
    params: list = []
    if filters is None:
        return clauses, params
    if filters.topic:
        clauses.append("c.topic = ?")
        params.append(filters.topic)
    if filters.source_type:
        clauses.append("c.source_type = ?")
        params.append(filters.source_type.value)
    if filters.kb_scope:
        clauses.append("c.kb_scope = ?")
        params.append(filters.kb_scope.value)
    if filters.kind:
        # daily memory 的类型存于 metadata.kind（REQUIREMENTS R4.5 按类型召回）。
        clauses.append("json_extract(c.metadata, '$.kind') = ?")
        params.append(filters.kind)
    if filters.origin:
        # 上传附件/图片切片的来源标记（metadata.origin = attachment）。
        clauses.append("json_extract(c.metadata, '$.origin') = ?")
        params.append(filters.origin)
    if filters.document_id:
        # 定位单个已入库文档/图片的全部切片（「刚才那张图」按 artifact document_id 回查）。
        clauses.append("json_extract(c.metadata, '$.document_id') = ?")
        params.append(filters.document_id)
    return clauses, params


class _Base:
    def __init__(self, conn: Optional[sqlite3.Connection] = None, db_path: Optional[str] = None):
        self._own = conn is None
        self.conn = conn or get_connection(db_path)


class AtomRepository(_Base):
    def get(self, atom_id: str) -> Optional[KnowledgeAtom]:
        row = self.conn.execute(
            "SELECT * FROM knowledge_atoms WHERE atom_id = ?", (atom_id,)
        ).fetchone()
        return _row_to_atom(row) if row else None

    def list_by_topic(self, topic: str, limit: int = 200) -> List[KnowledgeAtom]:
        rows = self.conn.execute(
            "SELECT * FROM knowledge_atoms WHERE topic = ? LIMIT ?", (topic, limit)
        ).fetchall()
        return [_row_to_atom(r) for r in rows]

    def upsert(self, atom: KnowledgeAtom) -> None:
        self.conn.execute(
            """INSERT INTO knowledge_atoms
               (atom_id, topic, subtopic, title, canonical_question, canonical_answer,
                tags, difficulty, mastery_score, mastery_tier, last_reviewed_at,
                review_count, correct_count, decay_rate, source_refs, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(atom_id) DO UPDATE SET
                 topic=excluded.topic, title=excluded.title,
                 mastery_score=excluded.mastery_score, mastery_tier=excluded.mastery_tier,
                 updated_at=excluded.updated_at""",
            (
                atom.atom_id, atom.topic, atom.subtopic, atom.title,
                atom.canonical_question, atom.canonical_answer,
                json.dumps(atom.tags), atom.difficulty, atom.mastery_score,
                atom.mastery_tier.value, atom.last_reviewed_at.isoformat() if atom.last_reviewed_at else None,
                atom.review_count, atom.correct_count, atom.decay_rate,
                json.dumps(atom.source_refs), atom.created_at.isoformat(), atom.updated_at.isoformat(),
            ),
        )
        self.conn.commit()

    def update_mastery(self, atom_id: str, new_score: float, new_tier: str,
                       correct: bool = False) -> bool:
        """更新单个 Atom 掌握度（写边界：仅 Manager 经此落库，Design §2a/§4b）。

        同步 last_reviewed_at / review_count / correct_count（供时间遗忘计算）。
        返回是否命中（atom 不存在则 False，不报错——掌握度可滞后于 atom 灌库）。
        """
        cur = self.conn.execute(
            """UPDATE knowledge_atoms
               SET mastery_score=?, mastery_tier=?, last_reviewed_at=?,
                   review_count=review_count+1,
                   correct_count=correct_count+?, updated_at=?
               WHERE atom_id=?""",
            (new_score, new_tier, _now_iso(), 1 if correct else 0, _now_iso(), atom_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- 共享知识库：把 atom 作为公共知识点/题库检索源（FTS5 + 向量） ---
    def sync_index(self, atom: KnowledgeAtom, embedding: Optional[List[float]] = None) -> None:
        """同步单个 atom 到 atom_fts（+ embedding 时 atom_vectors）。仅索引，不写 mastery。"""
        self.conn.execute("DELETE FROM atom_fts WHERE atom_id = ?", (atom.atom_id,))
        self.conn.execute(
            """INSERT INTO atom_fts (atom_id, canonical_question, canonical_answer, tags)
               VALUES (?,?,?,?)""",
            (atom.atom_id, atom.canonical_question, atom.canonical_answer or "",
             " ".join(atom.tags)),
        )
        if embedding:
            try:
                self.conn.execute("DELETE FROM atom_vectors WHERE atom_id = ?", (atom.atom_id,))
                self.conn.execute(
                    "INSERT INTO atom_vectors (atom_id, embedding) VALUES (?,?)",
                    (atom.atom_id, _serialize_embedding(embedding)),
                )
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def fts_match(self, query: str, top_k: int = 8,
                  filters: Optional[RetrievalFilters] = None) -> List[Chunk]:
        topic_clause = ""
        params: list = [_fts_sanitize(query)]
        if filters and filters.topic:
            topic_clause = " AND a.topic = ?"
            params.append(filters.topic)
        params.append(top_k)
        try:
            rows = self.conn.execute(
                f"""SELECT a.atom_id, a.canonical_question, a.canonical_answer, a.topic,
                           bm25(atom_fts) AS rank
                    FROM atom_fts
                    JOIN knowledge_atoms a ON a.atom_id = atom_fts.atom_id
                    WHERE atom_fts MATCH ?{topic_clause}
                    ORDER BY rank LIMIT ?""",
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [_atom_row_to_chunk(r) for r in rows]

    def vector_knn(self, embedding: List[float], top_k: int = 8,
                   filters: Optional[RetrievalFilters] = None) -> List[Chunk]:
        if not embedding:
            return []
        topic_clause = ""
        params: list = [_serialize_embedding(embedding), top_k * 4]
        if filters and filters.topic:
            topic_clause = " WHERE a.topic = ?"
            params.append(filters.topic)
        params.append(top_k)
        try:
            rows = self.conn.execute(
                f"""WITH knn AS (
                        SELECT atom_id, distance FROM atom_vectors
                        WHERE embedding MATCH ? ORDER BY distance LIMIT ?
                    )
                    SELECT a.atom_id, a.canonical_question, a.canonical_answer, a.topic,
                           knn.distance AS distance
                    FROM knn JOIN knowledge_atoms a ON a.atom_id = knn.atom_id
                    {topic_clause}
                    ORDER BY knn.distance LIMIT ?""",
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [_atom_row_to_chunk(r) for r in rows]


class ChunkRepository(_Base):
    """共享知识库语料检索 + 灌库（FTS5 + sqlite-vec 向量，支持 metadata 过滤）。"""

    _COLS = "c.chunk_id, c.text, c.source_type, c.kb_scope, c.topic, c.metadata"

    def fts_match(
        self, query: str, top_k: int = 8, filters: Optional[RetrievalFilters] = None
    ) -> List[Chunk]:
        extra, fparams = _chunk_filter_sql(filters)
        where = " AND ".join(["chunk_fts MATCH ?"] + extra)
        try:
            rows = self.conn.execute(
                f"""SELECT {self._COLS}, bm25(chunk_fts) AS rank
                    FROM chunk_fts
                    JOIN chunks c ON c.chunk_id = chunk_fts.chunk_id
                    WHERE {where}
                    ORDER BY rank LIMIT ?""",
                [_fts_sanitize(query), *fparams, top_k],
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            # FTS5 unicode61 不切 CJK 词；空召回时退化为 LIKE 子串（Design §8c 降级）。
            rows = self._like_fallback(query, top_k, filters)
        return [_row_to_chunk(r) for r in rows]

    def _like_fallback(
        self, query: str, top_k: int, filters: Optional[RetrievalFilters] = None
    ) -> List[sqlite3.Row]:
        # 丢弃单字符词（ASCII 如 "1"/"a"，CJK 如 "加"/"锁"）：单字 LIKE 子串过宽，会把
        # 含 "s1"/"增加" 的记忆误召回（过召回，REQUIREMENTS 测试 §7.1）。需 ≥2 字符才参与 LIKE。
        terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in query).split()
                 if len(t) >= 2]
        if not terms:
            return []
        like = " OR ".join("c.text LIKE ?" for _ in terms)
        extra, fparams = _chunk_filter_sql(filters)
        where = " AND ".join([f"({like})"] + extra)
        params = [f"%{t}%" for t in terms] + fparams + [top_k]
        try:
            return self.conn.execute(
                f"""SELECT {self._COLS}, 0.0 AS rank
                    FROM chunks c WHERE {where} LIMIT ?""",
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    def vector_knn(
        self, embedding: List[float], top_k: int = 8, filters: Optional[RetrievalFilters] = None
    ) -> List[Chunk]:
        """sqlite-vec 向量 KNN；扩展不可用或无向量数据时返回空（由上层降级到 FTS）。"""
        if not embedding:
            return []
        # 过采样后在 JOIN 上做 metadata 过滤（vec0 虚拟表只存向量，过滤须 JOIN 后做）。
        fetch = top_k * 4
        extra, fparams = _chunk_filter_sql(filters)
        where = (" WHERE " + " AND ".join(extra)) if extra else ""
        try:
            rows = self.conn.execute(
                f"""WITH knn AS (
                        SELECT chunk_id, distance FROM chunk_vectors
                        WHERE embedding MATCH ? ORDER BY distance LIMIT ?
                    )
                    SELECT {self._COLS}, knn.distance AS distance
                    FROM knn JOIN chunks c ON c.chunk_id = knn.chunk_id
                    {where}
                    ORDER BY knn.distance LIMIT ?""",
                [_serialize_embedding(embedding), fetch, *fparams, top_k],
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [_row_to_chunk(r) for r in rows]

    def find_document_by_hash(self, content_hash: str) -> Optional[str]:
        """命中去重：返回已入库且 content_hash 相同的 document_id（无则 None）。

        重复上传同一文件时避免重复切片入库（按内容哈希判等，而非文件名）。
        """
        if not content_hash:
            return None
        try:
            row = self.conn.execute(
                "SELECT json_extract(metadata, '$.document_id') AS did FROM chunks "
                "WHERE json_extract(metadata, '$.content_hash') = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return row["did"] if row and row["did"] else None

    def count_by_document(self, document_id: str) -> int:
        """某 document_id 名下的切片数（供 manifest 回报 chunk_count）。"""
        if not document_id:
            return 0
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM chunks "
                "WHERE json_extract(metadata, '$.document_id') = ?",
                (document_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row["n"]) if row else 0

    def upsert(
        self,
        chunk_id: str,
        text: str,
        source_type: str = "doc",
        topic: Optional[str] = None,
        kb_scope: str = "shared",
        metadata: Optional[dict] = None,
        atom_refs: Optional[List[str]] = None,
        parent_chunk_id: Optional[str] = None,
        token_count: int = 0,
        embedding: Optional[List[float]] = None,
    ) -> None:
        """灌库写入：chunks + 同步 chunk_fts（+ embedding 时同步 chunk_vectors）。

        仅写"知识内容与索引"，不触碰 mastery，不破坏 Manager 唯一写者约束。
        """
        self.conn.execute(
            """INSERT INTO chunks
               (chunk_id, text, source_type, kb_scope, topic, parent_chunk_id,
                atom_refs, token_count, metadata, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(chunk_id) DO UPDATE SET
                 text=excluded.text, source_type=excluded.source_type,
                 kb_scope=excluded.kb_scope, topic=excluded.topic,
                 metadata=excluded.metadata""",
            (chunk_id, text, source_type, kb_scope, topic, parent_chunk_id,
             json.dumps(atom_refs or []), token_count,
             json.dumps(metadata or {}), _now_iso()),
        )
        self.conn.execute("DELETE FROM chunk_fts WHERE chunk_id = ?", (chunk_id,))
        self.conn.execute(
            "INSERT INTO chunk_fts (chunk_id, text) VALUES (?,?)", (chunk_id, text)
        )
        if embedding:
            try:
                self.conn.execute(
                    "DELETE FROM chunk_vectors WHERE chunk_id = ?", (chunk_id,)
                )
                self.conn.execute(
                    "INSERT INTO chunk_vectors (chunk_id, embedding) VALUES (?,?)",
                    (chunk_id, _serialize_embedding(embedding)),
                )
            except sqlite3.OperationalError:
                pass  # 无 sqlite-vec 扩展：跳过向量索引，FTS 仍可用。
        self.conn.commit()


class MockTurnRepository(_Base):
    """本地用户库：检索历史 mock 记录（个人学习数据，scope=LOCAL）。"""

    def fts_match(self, query: str, top_k: int = 8) -> List[Chunk]:
        try:
            rows = self.conn.execute(
                """SELECT turn_id, question, user_answer FROM mock_turns
                   WHERE question LIKE ? OR user_answer LIKE ? LIMIT ?""",
                (f"%{query}%", f"%{query}%", top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        chunks: List[Chunk] = []
        for r in rows:
            text = " ".join(filter(None, [r["question"], r["user_answer"]]))
            chunks.append(
                Chunk(chunk_id=f"mock:{r['turn_id']}", text=text, score=0.0,
                      source_type=SourceType.INTERVIEW_POST, scope=KnowledgeScope.LOCAL,
                      metadata={"origin": "mock_turn"})
            )
        return chunks


class QAHistoryRepository(_Base):
    """本地用户库：历史问答存取（append + FTS5 检索），scope=LOCAL。"""

    def append(self, qa_id: str, question: str, answer: Optional[str] = None,
               topic: Optional[str] = None) -> None:
        """写一条历史问答并同步 qa_history_fts。仅个人交互记录，不写学习状态。"""
        try:
            self.conn.execute(
                """INSERT OR REPLACE INTO qa_history (qa_id, question, answer, topic, created_at)
                   VALUES (?,?,?,?,?)""",
                (qa_id, question, answer, topic, _now_iso()),
            )
            self.conn.execute("DELETE FROM qa_history_fts WHERE qa_id = ?", (qa_id,))
            self.conn.execute(
                "INSERT INTO qa_history_fts (qa_id, question, answer) VALUES (?,?,?)",
                (qa_id, question, answer or ""),
            )
            self.conn.commit()
        except sqlite3.OperationalError:
            self.conn.rollback()

    def fts_match(self, query: str, top_k: int = 8) -> List[Chunk]:
        try:
            rows = self.conn.execute(
                """SELECT h.qa_id, h.question, h.answer, h.topic, bm25(qa_history_fts) AS rank
                   FROM qa_history_fts
                   JOIN qa_history h ON h.qa_id = qa_history_fts.qa_id
                   WHERE qa_history_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (_fts_sanitize(query), top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            try:
                rows = self.conn.execute(
                    """SELECT qa_id, question, answer, topic, 0.0 AS rank FROM qa_history
                       WHERE question LIKE ? OR answer LIKE ? LIMIT ?""",
                    (f"%{query}%", f"%{query}%", top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        chunks: List[Chunk] = []
        for r in rows:
            rank = r["rank"] if "rank" in r.keys() and r["rank"] is not None else 0.0
            text = " ".join(filter(None, [r["question"], r["answer"]]))
            chunks.append(
                Chunk(chunk_id=f"qa:{r['qa_id']}", text=text, score=-float(rank),
                      source_type=SourceType.DOC, scope=KnowledgeScope.LOCAL,
                      metadata={"origin": "qa_history", "topic": r["topic"]})
            )
        return chunks


class UserProfileRepository(_Base):
    def get(self, user_id: str) -> Optional[UserProfile]:
        row = self.conn.execute(
            "SELECT * FROM user_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        return UserProfile(
            user_id=row["user_id"],
            goals=json.loads(row["goals"]),
            current_level=row["current_level"],
            preferences=json.loads(row["preferences"]),
        )

    def upsert(self, profile: UserProfile) -> None:
        self.conn.execute(
            """INSERT INTO user_profile (user_id, goals, current_level, preferences, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 goals=excluded.goals, current_level=excluded.current_level,
                 preferences=excluded.preferences, updated_at=excluded.updated_at""",
            (profile.user_id, json.dumps(profile.goals), profile.current_level,
             json.dumps(profile.preferences), _now_iso()),
        )
        self.conn.commit()


class SessionStateRepository(_Base):
    """短期会话记忆：每 session 的滚动摘要 + 当前任务态（跨轮连续性）。

    只存轻量易变的会话态，不是长期记忆（长期走 chunks/向量库）。
    """

    def get(self, session_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT summary, recent_messages, active_task FROM session_state "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "summary": row["summary"],
            "recent_messages": json.loads(row["recent_messages"] or "[]"),
            "active_task": json.loads(row["active_task"] or "{}"),
        }

    def upsert(self, session_id: str, summary: str,
               active_task: Optional[dict] = None,
               recent_messages: Optional[list] = None) -> None:
        self.conn.execute(
            """INSERT INTO session_state
                 (session_id, summary, recent_messages, active_task, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 summary=excluded.summary, recent_messages=excluded.recent_messages,
                 active_task=excluded.active_task, updated_at=excluded.updated_at""",
            (session_id, summary, json.dumps(recent_messages or []),
             json.dumps(active_task or {}), _now_iso()),
        )
        self.conn.commit()


class LearningPathRepository(_Base):
    def get(self, path_id: str) -> Optional[LearningPath]:
        prow = self.conn.execute(
            "SELECT * FROM learning_paths WHERE path_id = ?", (path_id,)
        ).fetchone()
        if not prow:
            return None
        items = self.conn.execute(
            "SELECT * FROM path_items WHERE path_id = ? ORDER BY order_idx", (path_id,)
        ).fetchall()
        from ..contracts.agents.planning import PathItem

        return LearningPath(
            path_id=path_id,
            items=[
                PathItem(item_id=i["item_id"], atom_id=i["atom_id"], order_idx=i["order_idx"],
                         day_index=i["day_index"], status=i["status"])
                for i in items
            ],
            updated_at=datetime.fromisoformat(prow["updated_at"]) if prow["updated_at"] else None,
        )

    def commit(self, path: LearningPath, user_id: str = "default") -> None:
        """仅 Manager 调用（写边界，Design §2a）。整体事务，失败回滚不半写。"""
        try:
            self.conn.execute(
                """INSERT INTO learning_paths (path_id, user_id, updated_at) VALUES (?,?,?)
                   ON CONFLICT(path_id) DO UPDATE SET updated_at=excluded.updated_at""",
                (path.path_id, user_id, _now_iso()),
            )
            self.conn.execute("DELETE FROM path_items WHERE path_id = ?", (path.path_id,))
            for it in path.items:
                self.conn.execute(
                    """INSERT INTO path_items
                       (item_id, path_id, atom_id, order_idx, day_index, status)
                       VALUES (?,?,?,?,?,?)""",
                    (it.item_id, path.path_id, it.atom_id, it.order_idx, it.day_index, it.status),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


class EventRepository(_Base):
    def append(self, event: EventPayload, event_id: str, trace_id: Optional[str] = None) -> None:
        """append-only；event_id 主键保证 at-least-once 下幂等（Design §6a）。"""
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO interaction_events
                   (event_id, trace_id, event_type, topic, atom_refs, signal, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (event_id, trace_id, event.event_type.value, event.topic,
                 json.dumps(event.atom_refs), json.dumps(event.signal), _now_iso()),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def list_window(self, window: str = "30d") -> List[EventPayload]:
        """按时间窗读事件（Design §3.13/§5.5）。window ∈ {7d, 30d, all}。"""
        from ..contracts.enums import EventType

        sql = "SELECT * FROM interaction_events"
        params: list = []
        cutoff = _window_cutoff_iso(window)
        if cutoff is not None:
            sql += " WHERE created_at >= ?"
            params.append(cutoff)
        sql += " ORDER BY created_at DESC"
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = []
        return [
            EventPayload(event_type=EventType(r["event_type"]), topic=r["topic"],
                         atom_refs=json.loads(r["atom_refs"]), signal=json.loads(r["signal"]))
            for r in rows
        ]

    def list_window_dicts(self, window: str = "30d") -> List[dict]:
        """带 created_at 的事件读取（供 Diagnosis 计算 recency_weight）。"""
        sql = "SELECT * FROM interaction_events"
        params: list = []
        cutoff = _window_cutoff_iso(window)
        if cutoff is not None:
            sql += " WHERE created_at >= ?"
            params.append(cutoff)
        sql += " ORDER BY created_at DESC"
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {
                "event_type": r["event_type"], "topic": r["topic"],
                "atom_refs": json.loads(r["atom_refs"]), "signal": json.loads(r["signal"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]


class DiagnosisReportRepository(_Base):
    """诊断快照落库（只读 agent 产出，由 Manager 单点落，Design §4c）。

    DiagnosisAgent 严格只读；其结论作为快照由 Manager 在 dispatch / settle_mock 后写入，
    供复盘审计与趋势分析。append-only：report_id 主键保证幂等。
    """

    def write(self, report_id: str, time_window: str, trigger: str, result: dict,
              confidence: float, trace_id: Optional[str] = None) -> None:
        """写一条诊断快照；best-effort，失败不抛（不阻断主链路）。"""
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO diagnosis_reports
                   (report_id, trace_id, time_window, trigger, result, confidence, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (report_id, trace_id, time_window, trigger,
                 json.dumps(result, ensure_ascii=False), confidence, _now_iso()),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def get(self, report_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM diagnosis_reports WHERE report_id = ?", (report_id,)
        ).fetchone()
        if not row:
            return None
        return _row_to_diagnosis_report(row)

    def latest(self, limit: int = 1) -> List[dict]:
        """按时间倒序取最近 N 条诊断快照（趋势/复盘用）。"""
        try:
            rows = self.conn.execute(
                "SELECT * FROM diagnosis_reports ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [_row_to_diagnosis_report(r) for r in rows]


class MockSessionRepository(_Base):
    """mock 会话 + 逐轮落库（Design §4c）。供 S7 结算与本地 RAG（mock_turns）检索。"""

    def upsert_session(self, session_id: str, topic: str, difficulty: int,
                       max_turns: int, status: str = "active") -> None:
        self.conn.execute(
            """INSERT INTO mock_sessions
               (session_id, topic, difficulty, max_turns, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 topic=excluded.topic, difficulty=excluded.difficulty,
                 status=excluded.status, updated_at=excluded.updated_at""",
            (session_id, topic, difficulty, max_turns, status, _now_iso(), _now_iso()),
        )
        self.conn.commit()

    def add_turn(self, turn_id: str, session_id: str, turn_index: int, question: Optional[str],
                 expected_points: List[str], user_answer: Optional[str],
                 score: Optional[dict]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO mock_turns
               (turn_id, session_id, turn_index, question, expected_points,
                user_answer, score, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (turn_id, session_id, turn_index, question, json.dumps(expected_points),
             user_answer, json.dumps(score) if score is not None else None, _now_iso()),
        )
        self.conn.commit()


def _window_cutoff_iso(window: str) -> Optional[str]:
    """时间窗 → 起始 ISO 时间戳；'all' 或未知返回 None（不过滤）。"""
    days = {"7d": 7, "30d": 30}.get(window)
    if days is None:
        return None
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class TraceRepository(_Base):
    def write(self, message_id: str, trace_id: str, sender: str, recipient: str,
              status: Optional[str] = None, confidence: Optional[float] = None,
              cost_usd: float = 0.0, latency_ms: int = 0) -> None:
        """写 agent_traces；best-effort，失败不抛（Design §6a/§9a）。"""
        try:
            self.conn.execute(
                """INSERT OR REPLACE INTO agent_traces
                   (message_id, trace_id, sender, recipient, status, confidence,
                    cost_usd, latency_ms, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (message_id, trace_id, sender, recipient, status, confidence,
                 cost_usd, latency_ms, _now_iso()),
            )
            self.conn.commit()
        except Exception:
            pass

    def list_by_trace(self, trace_id: str) -> List[dict]:
        """按 trace_id 拉全链路（Design §9a AC②）。"""
        try:
            rows = self.conn.execute(
                "SELECT * FROM agent_traces WHERE trace_id = ? ORDER BY created_at",
                (trace_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]


# 主线意图能力（开了一条"线程"）；qa/note 是自包含旁支（aside），不接管主线。
_THREAD_CAPS = ("planning", "diagnosis", "mock")
# 连续多少个 aside 后认为主线已放弃（避免 last_capability 长期粘住误带后续承接句）。
_THREAD_DECAY = 3


# dialogue_turns 自愈 DDL：旧库（init_db 早于本表加入）首次访问时即建表，透明迁移。
_DIALOGUE_DDL = """
CREATE TABLE IF NOT EXISTS dialogue_turns (
    turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
    capability TEXT, route_mode TEXT, topic TEXT,
    tool_calls TEXT NOT NULL DEFAULT '[]', artifacts TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'completed', trace_id TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dialogue_session_seq ON dialogue_turns (session_id, seq);
"""


class DialogueTurnRepository(_Base):
    """会话对话账本：每轮一行（dialogue_turns）。替代进程内 _UI_CTX 的薄 dict。

    三类读：`recent`（主 agent 取完整轮含产物）、`recent_projection`（意图分类取紧凑投影）、
    `derive_context`（派生主线状态/承接上下文，含 aside 衰减）。写经 `add_turn`（best-effort）。
    """

    def __init__(self, conn: Optional[sqlite3.Connection] = None, db_path: Optional[str] = None):
        super().__init__(conn=conn, db_path=db_path)
        try:  # 自愈建表（IF NOT EXISTS，幂等；旧库无此表时透明迁移）。
            self.conn.executescript(_DIALOGUE_DDL)
            self.conn.commit()
        except Exception:
            pass

    def next_seq(self, session_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM dialogue_turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["m"]) + 1

    def add_turn(self, turn: "DialogueTurn") -> None:
        """落一行对话轮；best-effort，失败不抛（与 TraceRepository 一致）。"""
        from ..contracts.dialogue import DialogueTurn  # noqa: F401 - 仅类型/校验

        try:
            self.conn.execute(
                """INSERT OR REPLACE INTO dialogue_turns
                   (turn_id, session_id, seq, role, text, capability, route_mode, topic,
                    tool_calls, artifacts, status, trace_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (turn.turn_id, turn.session_id, turn.seq, turn.role, turn.text,
                 turn.capability, turn.route_mode, turn.topic,
                 json.dumps([tc.model_dump() for tc in turn.tool_calls]),
                 json.dumps(turn.artifacts),
                 turn.status, turn.trace_id, turn.created_at or _now_iso()),
            )
            self.conn.commit()
        except Exception:
            pass

    def recent(self, session_id: str, limit: int = 8) -> List["DialogueTurn"]:
        """取最近 N 轮完整记录（升序，含 tool_calls/artifacts）——主 agent 加载用。"""
        from ..contracts.dialogue import DialogueTurn, ToolCall

        try:
            rows = self.conn.execute(
                "SELECT * FROM dialogue_turns WHERE session_id = ? "
                "ORDER BY seq DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        turns: List[DialogueTurn] = []
        for r in reversed(rows):
            turns.append(DialogueTurn(
                turn_id=r["turn_id"], session_id=r["session_id"], seq=r["seq"],
                role=r["role"], text=r["text"], capability=r["capability"],
                route_mode=r["route_mode"], topic=r["topic"],
                tool_calls=[ToolCall(**tc) for tc in json.loads(r["tool_calls"] or "[]")],
                artifacts=json.loads(r["artifacts"] or "[]"),
                status=r["status"], trace_id=r["trace_id"], created_at=r["created_at"],
            ))
        return turns

    def recent_projection(self, session_id: str, limit: int = 4) -> List[dict]:
        """意图分类的紧凑窗口：每轮只取 {text(截断), capability, topic, tools(agent 名)}。

        刻意不取全文/产物——分类器只需要"上文做了什么"的轻量信号，便宜且可回归。
        """
        try:
            rows = self.conn.execute(
                "SELECT text, capability, topic, tool_calls FROM dialogue_turns "
                "WHERE session_id = ? AND role = 'user' ORDER BY seq DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        out: List[dict] = []
        for r in reversed(rows):
            tools = [tc.get("agent") for tc in json.loads(r["tool_calls"] or "[]")]
            out.append({"text": (r["text"] or "")[:80], "capability": r["capability"],
                        "topic": r["topic"], "tools": tools})
        return out

    def derive_context(self, session_id: str, recent_k: int = 4, scan: int = 20) -> dict:
        """派生意图层上下文：最近 K 轮 + 关键节点(anchors) + 会话概要(summary) + 主线状态。

        替代旧的"纯最近 N 轮"窗口（会丢掉早于窗口的关键转折）：
        - **anchors(①)**：扫描更长窗口，钉住显著轮——当前主线开始的那一轮(转折)、带 live 产物
          的轮、待澄清轮——不论多旧；主线被 aside 衰减后这些锚点自然不再入选(过期)。
        - **summary(④)**：优先取已有 session_state 摘要(LLM 折叠)，无则从更旧轮**确定性** digest
          (主题/能力)，始终有值，且不在意图热路径加 LLM 调用。
        承接/aside 衰减仍按最近轮算(与旧行为一致)。
        """
        rows = self._scan_rows(session_id, scan)
        if not rows:
            return {"history": [], "anchors": [], "active_mock": self._active_mock(session_id)}
        proj = [self._project_row(r) for r in rows]   # 紧凑投影(chrono)
        last_capability: Optional[str] = None
        aside_streak = 0
        for h in reversed(proj):                        # 主线 + aside 衰减(按最近轮)
            cap = h.get("capability")
            if cap in _THREAD_CAPS:
                if aside_streak < _THREAD_DECAY:
                    last_capability = cap
                break
            if cap:
                aside_streak += 1
        last_topic = next((h["topic"] for h in reversed(proj) if h.get("topic")), None)

        recent = proj[-recent_k:]
        older = rows[:-recent_k] if len(rows) > recent_k else []
        anchors = self._select_anchors(older, last_capability)
        summary = self._session_summary(session_id) or _digest_rows(older)

        ctx: dict = {"history": recent, "anchors": anchors,
                     "active_mock": self._active_mock(session_id)}
        if summary:
            ctx["summary"] = summary
        if last_capability:
            ctx["last_capability"] = last_capability
        if last_topic:
            ctx["last_topic"] = last_topic
        return ctx

    def _scan_rows(self, session_id: str, limit: int) -> List[sqlite3.Row]:
        """取最近 `limit` 个 user 轮的富字段(chrono)，供 anchors/summary 计算。"""
        try:
            rows = self.conn.execute(
                "SELECT seq, text, capability, topic, tool_calls, artifacts, status "
                "FROM dialogue_turns WHERE session_id = ? AND role = 'user' "
                "ORDER BY seq DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return list(reversed(rows))

    @staticmethod
    def _project_row(r: sqlite3.Row) -> dict:
        tools = [tc.get("agent") for tc in json.loads(r["tool_calls"] or "[]")]
        return {"text": (r["text"] or "")[:80], "capability": r["capability"],
                "topic": r["topic"], "tools": tools}

    def _select_anchors(self, older_rows: List[sqlite3.Row],
                        last_capability: Optional[str]) -> List[dict]:
        """从更旧的轮里钉住显著节点：主线转折 / live 产物 / 待澄清。chrono、去重、≤3。"""
        anchors: List[dict] = []
        seen = set()

        def add(r: sqlite3.Row, kind: str) -> None:
            anchors.append({**self._project_row(r), "kind": kind, "seq": r["seq"]})
            seen.add(r["seq"])

        if last_capability:                    # 当前主线开始的那一轮(转折)
            for r in older_rows:
                if r["capability"] == last_capability:
                    add(r, "thread_start")
                    break
        for r in reversed(older_rows):         # 最近一条带 live 产物(mock/path)
            if r["seq"] in seen:
                continue
            arts = json.loads(r["artifacts"] or "[]")
            if any(a.get("kind") in ("mock_active", "path") for a in arts):
                add(r, "artifact")
                break
        for r in reversed(older_rows):         # 最近一条待澄清
            if r["seq"] in seen:
                continue
            if r["status"] == "needs_input":
                add(r, "clarify")
                break
        anchors.sort(key=lambda a: a["seq"])
        return anchors[:3]

    def _session_summary(self, session_id: str) -> str:
        """已有会话摘要(session_state.summary，LLM 折叠)；无则空。best-effort。"""
        try:
            row = self.conn.execute(
                "SELECT summary FROM session_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return ""
        return row["summary"] if row and row["summary"] else ""

    def _active_mock(self, session_id: str) -> bool:
        """最近一轮是否仍处于进行中的 mock（artifacts 里带未结算的 mock 引用）。"""
        try:
            row = self.conn.execute(
                "SELECT artifacts, status FROM dialogue_turns WHERE session_id = ? "
                "ORDER BY seq DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        if not row:
            return False
        for a in json.loads(row["artifacts"] or "[]"):
            if a.get("kind") == "mock_active":
                return True
        return False


def _digest_rows(rows: List[sqlite3.Row]) -> str:
    """更旧轮的确定性摘要(无 LLM)：聊过的主题 + 做过的实质能力。作 session_state 摘要的兜底。"""
    topics: List[str] = []
    caps: List[str] = []
    for r in rows:
        t = r["topic"]
        if t and t not in topics:
            topics.append(t)
        c = r["capability"]
        if c in _THREAD_CAPS and c not in caps:
            caps.append(c)
    parts = []
    if topics:
        parts.append("聊过：" + "、".join(topics[:5]))
    if caps:
        parts.append("做过：" + "、".join(caps))
    return "；".join(parts)


# --- row mappers ---
def _row_to_atom(row: sqlite3.Row) -> KnowledgeAtom:
    from ..contracts.enums import MasteryTier

    return KnowledgeAtom(
        atom_id=row["atom_id"], topic=row["topic"], subtopic=row["subtopic"], title=row["title"],
        canonical_question=row["canonical_question"], canonical_answer=row["canonical_answer"],
        tags=json.loads(row["tags"]), difficulty=row["difficulty"],
        mastery_score=row["mastery_score"], mastery_tier=MasteryTier(row["mastery_tier"]),
        last_reviewed_at=datetime.fromisoformat(row["last_reviewed_at"]) if row["last_reviewed_at"] else None,
        review_count=row["review_count"], correct_count=row["correct_count"],
        decay_rate=row["decay_rate"], source_refs=json.loads(row["source_refs"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    keys = row.keys()
    # bm25 rank 与 vec0 distance 均越小越相关 → 取负使越大越好。
    if "distance" in keys and row["distance"] is not None:
        score = -float(row["distance"])
    elif "rank" in keys and row["rank"] is not None:
        score = -float(row["rank"])
    else:
        score = 0.0
    scope = KnowledgeScope.SHARED
    if "kb_scope" in keys and row["kb_scope"]:
        scope = KnowledgeScope(row["kb_scope"])
    return Chunk(
        chunk_id=row["chunk_id"], text=row["text"], score=score,
        source_type=SourceType(row["source_type"]), scope=scope,
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


def _row_to_diagnosis_report(row: sqlite3.Row) -> dict:
    return {
        "report_id": row["report_id"],
        "trace_id": row["trace_id"],
        "time_window": row["time_window"],
        "trigger": row["trigger"],
        "result": json.loads(row["result"]) if row["result"] else {},
        "confidence": row["confidence"],
        "created_at": row["created_at"],
    }


def _atom_row_to_chunk(row: sqlite3.Row) -> Chunk:
    keys = row.keys()
    if "distance" in keys and row["distance"] is not None:
        score = -float(row["distance"])
    elif "rank" in keys and row["rank"] is not None:
        score = -float(row["rank"])
    else:
        score = 0.0
    text = " ".join(filter(None, [row["canonical_question"], row["canonical_answer"]]))
    return Chunk(
        chunk_id=f"atom:{row['atom_id']}", text=text, score=score,
        source_type=SourceType.ATOM, scope=KnowledgeScope.SHARED,
        metadata={"origin": "atom", "atom_id": row["atom_id"], "topic": row["topic"]},
    )
