"""记忆日志面板演示 —— 往记忆库塞入数据并模拟一轮记忆检索/读写，打印面板。

它演示 REQUIREMENTS 面板需求的完整闭环：
  ① 先 seed：MEMORY.md 稳定规则 + 若干 daily memory（qa/weak/progress/decision）+ 一个会话；
  ② 再模拟一轮对话：加载稳定记忆 → 加载会话记忆 → 搜索长期记忆 → 注入 → 提取候选 → 保存/跳过；
  ③ 打印「记忆来源 / 长期记忆分类 / 本轮最近操作 / 第一版最小摘要」。

运行（全离线，无需 API key）：
    python -m learnforge.memory.demo_panel              # 默认库 data/memory_demo.db
    python -m learnforge.memory.demo_panel --db x.db
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from typing import List, Optional

from .. import config
from ..contracts.agents.retrieval import RetrievalInput
from ..contracts.enums import KnowledgeScope, RetrievalMethod
from ..storage.db import init_db
from ..storage.repositories import SessionStateRepository
from .daily import record
from .files import append_root_memory
from .log import (
    MEMORY_LOG,
    memory_files_overview,
    memory_index_overview,
)

_SESSION_ID = "demo-session"


def _hr(title: str = "") -> None:
    print(f"\n{'─' * 4} {title} {'─' * max(0, 56 - len(title))}" if title else "─" * 62)


def seed(db_path: str) -> None:
    """往记忆库塞入演示数据（稳定规则 + 多类 daily memory + 一个会话）。"""
    # 稳定规则层（MEMORY.md）
    append_root_memory("回答用中文，优先给可核验要点，无证据时降低断言强度。")
    append_root_memory("主攻方向：后端面试，重点并发、Redis、JVM。")

    # 长期个人记忆（daily，多类 kind；带 topic）
    record(kind="qa", topic="concurrency", db_path=db_path,
           text="Q: 乐观锁和悲观锁怎么选？\nA: 读多写少用乐观锁(CAS/版本号)，写冲突频繁用悲观锁。")
    record(kind="qa", topic="redis", db_path=db_path,
           text="Q: Redis 为什么快？\nA: 纯内存 + 单线程事件循环 + 高效数据结构。")
    record(kind="weak", topic="concurrency", db_path=db_path,
           text="薄弱点：AQS 独占/共享模式与 Condition 等待队列理解不牢。")
    record(kind="progress", topic="jvm", db_path=db_path,
           text="学习进展：完成 JVM 内存模型与 GC 基础，可手绘对象晋升过程。")
    record(kind="decision", topic="plan", db_path=db_path,
           text="项目决策：面试冲刺期优先并发与 Redis，JVM 放第二梯队。")

    # 短期会话记忆（最近原文 + 早期摘要）
    SessionStateRepository(db_path=db_path).upsert(
        _SESSION_ID,
        "早期：用户在准备后端面试，已聊过 Redis 基础。",
        {"active_mock": None},
        [{"user": "Redis 持久化有哪些方式？", "reply": "RDB 快照与 AOF 日志，可混合。"}],
    )


def simulate_turn(db_path: str, query: str) -> None:
    """模拟一轮对话的记忆读写（用真实 RetrievalAgent 检索 seeded 的长期记忆）。"""
    from ..orchestration.manager import ManagerAgent
    from ..agents.retrieval import RetrievalAgent

    mgr = ManagerAgent(db_path=db_path)

    # 入口：加载稳定记忆 + 会话记忆（清空并开始记录本轮）。
    mgr.begin_memory_turn(query, _SESSION_ID)

    # 搜索长期记忆（LOCAL）：真实 FTS 检索 seeded daily，面板记录命中/采用。
    RetrievalAgent(db_path=db_path).run(
        RetrievalInput(query=query, scopes=[KnowledgeScope.LOCAL],
                       method=RetrievalMethod.FULLTEXT, top_k=3)
    )

    # 写入演示：① 有价值 QA → 提取候选并保存；② 同内容重复 → 跳过；③ 闲聊 → 价值不足跳过。
    good = SimpleNamespace(
        verdict="verified", topic="concurrency",
        answer="乐观锁基于版本号/CAS，适合读多写少；悲观锁靠加锁串行化，适合写冲突频繁。",
    )
    mgr.remember_qa(query, good)          # 保存成功
    mgr.remember_qa(query, good)          # 重复 → 跳过保存
    chitchat = SimpleNamespace(verdict="chitchat", topic=None, answer="嗯嗯好的")
    mgr.remember_qa("随便聊聊", chitchat)  # 价值不足 → 跳过保存


def render_text_panel() -> None:
    """纯文本打印面板（演示用，不依赖 CLI 配色）。"""
    _hr("最近操作（本轮记忆日志）")
    for line in MEMORY_LOG.render_lines():
        print("  " + line)
    _hr("第一版最小摘要")
    for line in MEMORY_LOG.summary_lines():
        print("  " + line)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="记忆日志面板演示")
    parser.add_argument("--db", default="data/memory_demo.db", help="演示 SQLite 库路径")
    parser.add_argument("--query", default="乐观锁和悲观锁怎么选？", help="模拟提问")
    args = parser.parse_args(argv)

    init_db(args.db)
    seed(args.db)

    print(f"记忆根目录：{config.MEMORY_DIR}    演示库：{args.db}")
    _hr("记忆来源（§2.1）")
    for line in memory_files_overview(args.db):
        print("  ▸ " + line)
    _hr("长期记忆分类（§2.2）")
    for line in memory_index_overview(args.db):
        print("  ▸ " + line)

    simulate_turn(args.db, args.query)
    print(f"\n本轮提问：{args.query}")
    render_text_panel()


if __name__ == "__main__":
    main()
