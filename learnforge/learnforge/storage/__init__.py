"""storage —— SQLite + sqlite-vec + FTS5 DDL 与仓储接口（Design §4c）。"""

from .db import get_connection, init_db

__all__ = ["get_connection", "init_db"]
