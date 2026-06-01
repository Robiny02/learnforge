"""memory —— OpenClaw 风格记忆系统（Design §6b/§6c）。

- 稳定规则：MEMORY.md（base.MemoryStore.memory_prefix + files.read_root_memory）。
- 长期记忆：memory/YYYY-MM-DD.md（files）+ chunks(local) 索引（daily.record/reindex）。
- 摘要/压缩 + prompt 拼装：base.MemoryStore.summarize + context_assembler.assemble_prompt。
"""

from .base import MEMORY, MemoryStore
from .context_assembler import AssembledPrompt, assemble_prompt
from .daily import record, reindex
from .files import append_daily, append_root_memory, iter_daily_notes, read_root_memory
from .log import (
    MEMORY_LOG,
    MemoryEvent,
    MemoryLog,
    memory_files_overview,
    memory_index_overview,
)

__all__ = [
    "MEMORY",
    "MemoryStore",
    "AssembledPrompt",
    "assemble_prompt",
    "record",
    "reindex",
    "read_root_memory",
    "append_root_memory",
    "append_daily",
    "iter_daily_notes",
    "MEMORY_LOG",
    "MemoryEvent",
    "MemoryLog",
    "memory_files_overview",
    "memory_index_overview",
]
