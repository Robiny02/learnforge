"""PostgreSQL 连接与可用性探测（惰性驱动，离线安全）。

驱动优先级：psycopg(v3) → psycopg2。两者都没装，或未配置 LF_POSTGRES_DSN，
则 `pg_available()` 为 False，调用 `get_pg_connection()` 抛 `PostgresUnavailable`。
SQL 统一用 %s 占位（psycopg2/3 通用），向量以 '[..]'::vector 字符串传参，
因此不依赖 pgvector 的 Python 适配器。
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from ... import config


class PostgresUnavailable(RuntimeError):
    """未配置 DSN 或未安装 psycopg 驱动时抛出。"""


def _driver() -> Tuple[Optional[str], Any]:
    """返回 (driver_name, module)；都不可用返回 (None, None)。"""
    try:
        import psycopg  # type: ignore

        return "psycopg", psycopg
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore

        return "psycopg2", psycopg2
    except ImportError:
        return None, None


def driver_name() -> Optional[str]:
    return _driver()[0]


def pg_available(dsn: Optional[str] = None) -> bool:
    """是否可以连接 PG：有 DSN 且驱动已安装。不真正建立连接。"""
    dsn = dsn or config.POSTGRES_DSN
    return bool(dsn) and _driver()[0] is not None


def get_pg_connection(dsn: Optional[str] = None, autocommit: bool = False):
    """建立 PG 连接。缺 DSN / 缺驱动 → PostgresUnavailable。"""
    dsn = dsn or config.POSTGRES_DSN
    name, module = _driver()
    if not dsn:
        raise PostgresUnavailable("LF_POSTGRES_DSN 未配置。")
    if module is None:
        raise PostgresUnavailable("psycopg / psycopg2 未安装（pip install 'learnforge[postgres]'）。")
    conn = module.connect(dsn)
    conn.autocommit = autocommit
    return conn
