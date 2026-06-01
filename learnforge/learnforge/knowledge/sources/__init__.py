"""可插拔知识源（双层知识体系）。

- `LocalUserSource`：本地用户库（mock 记录 + 历史问答），scope=LOCAL。
- `LocalSharedSource`：共享知识库（chunks + atoms，FTS5 + 向量），scope=SHARED。
- `RemoteSharedSource`：远程共享知识库（HTTP，可插拔；未配置/离线降级），scope=SHARED。
"""

from typing import List, Optional

from .base import KnowledgeSource, rrf_fuse
from .local_shared import LocalSharedSource
from .local_user import LocalUserSource
from .remote_shared import RemoteSharedSource

__all__ = [
    "KnowledgeSource",
    "rrf_fuse",
    "LocalUserSource",
    "LocalSharedSource",
    "RemoteSharedSource",
    "build_default_sources",
]


def build_default_sources(db_path: Optional[str] = None) -> List[KnowledgeSource]:
    """默认知识源列表。

    无 LF_POSTGRES_DSN（或驱动缺失）→ 与历史行为逐字一致（SQLite 三源）。
    配置了 PG → 追加 PostgresSharedSource（SHARED）；SQLite 共享源仍保留作 fallback。
    """
    sources: List[KnowledgeSource] = [
        LocalUserSource(db_path=db_path),
        LocalSharedSource(db_path=db_path),
        RemoteSharedSource(),
    ]
    try:
        from ..postgres.db import pg_available
        from ..postgres.source import PostgresSharedSource

        if pg_available():
            sources.append(PostgresSharedSource())
    except Exception:
        pass
    return sources
