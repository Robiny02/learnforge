"""MemoryStore 抽象（预留）——长期记忆与作用域读取。

对应 Design §6b 的"全局静态 / 任务专属 / agent 内部累积"三层，以及 Proposal §5 决策 8 的
"作用域切片 + handoff_summary"。MemoryStore 提供：

  - read_scoped(): 按 agent 作用域返回最小切片（不广播全量）
  - write(): 长期数据写入（实际落 SQLite，经仓储层）
  - summarize(): 生成 handoff_summary（≤512 tok，Haiku，Q2）

Phase 1：stub，返回空切片 / 空摘要。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contracts.enums import AgentId


class MemoryStore:
    def read_scoped(
        self,
        agent_id: AgentId,
        keys: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """返回该 agent 作用域内的最小上下文切片（Phase 1：空）。"""
        return {}

    def write(self, namespace: str, key: str, value: Any) -> None:
        """写长期记忆（Phase 1：no-op；Phase 2 经仓储落库）。"""
        return None

    def summarize(self, context: Dict[str, Any], max_tokens: int = 512) -> str:
        """生成 handoff_summary（Phase 1：空字符串；Phase 2 用 Haiku，Q2）。"""
        return ""


# 全局单例（Phase 1 in-memory stub）。
MEMORY = MemoryStore()
