"""SQLite 连接 + sqlite-vec 扩展加载 + 建表（Design §4c / §10 决策 10）。

Phase 1：提供建库入口（执行 schema.sql）。sqlite-vec 扩展按需加载；若环境未装扩展，
则跳过 vec0 虚拟表的创建错误（骨架阶段允许 FTS5-only 降级，Design §8c）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from ..config import DB_PATH, EMBEDDING_DIM

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """尝试加载 sqlite-vec 扩展，返回是否成功。"""
    try:
        import sqlite_vec  # type: ignore

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


def get_connection(db_path: Optional[str] = None, load_vec: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if load_vec:
        _try_load_sqlite_vec(conn)
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """执行 schema.sql 建全部表（Phase 1：只建表，不填数据）。

    若 sqlite-vec 未加载，vec0 虚拟表创建会失败——此时跳过这些语句，其余表照常建好。
    """
    conn = get_connection(db_path)
    has_vec = _try_load_sqlite_vec(conn)
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")

    # 向量维度按 config.EMBEDDING_DIM 注入 vec0 DDL（与 embedding provider 输出对齐）。
    if EMBEDDING_DIM != 1024:
        sql = sql.replace("FLOAT[1024]", f"FLOAT[{EMBEDDING_DIM}]")

    if not has_vec:
        # 过滤掉 vec0 虚拟表（骨架阶段降级，保证其余 DDL 可建）。
        statements = [s for s in sql.split(";") if "USING vec0" not in s]
        sql = ";".join(statements)

    conn.executescript(sql)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized schema at {DB_PATH}")
