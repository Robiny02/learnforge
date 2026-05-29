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
from typing import List, Optional

from ..contracts.agents.retrieval import Chunk
from ..contracts.atom import KnowledgeAtom
from ..contracts.enums import SourceType
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


class ChunkRepository(_Base):
    """本地 RAG：FTS5 检索 chunks（用户澄清：本地用 FTS5）。"""

    def fts_match(self, query: str, top_k: int = 8) -> List[Chunk]:
        try:
            rows = self.conn.execute(
                """SELECT c.chunk_id, c.text, c.source_type, c.topic, c.metadata,
                          bm25(chunk_fts) AS rank
                   FROM chunk_fts
                   JOIN chunks c ON c.chunk_id = chunk_fts.chunk_id
                   WHERE chunk_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (_fts_sanitize(query), top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            # FTS5 unicode61 不切 CJK 词；空召回时退化为 LIKE 子串（Design §8c 降级）。
            rows = self._like_fallback(query, top_k)
        return [_row_to_chunk(r) for r in rows]

    def _like_fallback(self, query: str, top_k: int) -> List[sqlite3.Row]:
        terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in query).split() if t]
        if not terms:
            return []
        where = " OR ".join("c.text LIKE ?" for _ in terms)
        params = [f"%{t}%" for t in terms] + [top_k]
        try:
            return self.conn.execute(
                f"""SELECT c.chunk_id, c.text, c.source_type, c.topic, c.metadata, 0.0 AS rank
                    FROM chunks c WHERE {where} LIMIT ?""",
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    def vector_knn(self, embedding: List[float], top_k: int = 8) -> List[Chunk]:
        # sqlite-vec 可用时实现；Phase 2 本地以 FTS5 为主（用户澄清），向量为可选增强。
        return []


class MockTurnRepository(_Base):
    """本地 RAG：检索历史 mock 记录（用户澄清：可搜 mock 记录）。"""

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
                      source_type=SourceType.INTERVIEW_POST, metadata={"origin": "mock_turn"})
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
    rank = row["rank"] if "rank" in row.keys() else 0.0
    return Chunk(
        chunk_id=row["chunk_id"], text=row["text"],
        score=-float(rank),  # bm25 越小越相关 → 取负使越大越好
        source_type=SourceType(row["source_type"]),
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )
