"""MockStateStore —— 跨 HTTP 调用持久化整场面试状态（替代旧 LangGraph checkpointer）。

把 `MockState` 序列化成 JSON 存进自愈表 `mock_session_state`（thread key = session_id），
每个请求 load→run_turn→save。DB 不可用 / 离线测试 → 进程内 dict 兜底（"链路永远通"）。

设计取舍：不再依赖 LangGraph 的 SqliteSaver + interrupt/resume replay；状态全量持久化，
"恢复"就是把上次的 MockState 读回来继续跑一轮。跨进程重启靠 SQLite，单进程多轮靠 dict 兜底。
"""

from __future__ import annotations

from typing import Dict, Optional

from ...contracts.agents.mock import MockState

_DDL = """
CREATE TABLE IF NOT EXISTS mock_session_state (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT
);
"""


class MockStateStore:
    """SQLite JSON 存储 + 进程内 dict 兜底。每个操作各自开/合连接，避免长连接锁竞争。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path
        self._mem: Dict[str, str] = {}     # 兜底（DB 不可用 / 同进程多轮）
        self._db_ok = self._ensure_table()

    def _ensure_table(self) -> bool:
        try:
            conn = self._connect()
            conn.executescript(_DDL)
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False  # DB 不可用 → 纯 dict 兜底

    def _connect(self):
        from ...storage.db import get_connection

        return get_connection(self._db_path, load_vec=False)

    # ------------------------------------------------------------------ load/save
    def load(self, session_id: str) -> Optional[MockState]:
        raw = self._mem.get(session_id)
        if raw is None and self._db_ok:
            raw = self._db_load(session_id)
        if not raw:
            return None
        try:
            return MockState.model_validate_json(raw)
        except Exception:
            return None

    def save(self, state: MockState) -> None:
        raw = state.model_dump_json()
        self._mem[state.session_id] = raw  # 进程内兜底始终更新
        if self._db_ok:
            self._db_save(state.session_id, raw)

    def _db_load(self, session_id: str) -> Optional[str]:
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT state_json FROM mock_session_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            if row is None:
                return None
            # get_connection 设了 row_factory=Row；按列名取更稳。
            try:
                return row["state_json"]
            except Exception:
                return row[0]
        except Exception:
            return None

    def _db_save(self, session_id: str, raw: str) -> None:
        from datetime import datetime, timezone

        try:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO mock_session_state (session_id, state_json, updated_at) "
                "VALUES (?, ?, ?)",
                (session_id, raw, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass  # 落库失败不阻断；dict 兜底已持有最新态
