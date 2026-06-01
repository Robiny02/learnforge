"""记忆召回演示 / 测试夹具：多类型记忆文件 + 不同质量 query + 边界情况。

离线（无 embedding key）走 BM25/FTS + 时间衰减；语义-only 的 query 会暴露 FTS 的局限
（需向量召回才好命中），这正是要观察的对比。

人工查看：.venv/bin/python tests/memory_recall_demo.py
被 tests/test_memory_recall_quality.py 复用 build_corpus / recall。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

from learnforge import config
from learnforge.contracts.agents.retrieval import RetrievalInput, RetrievalOutput
from learnforge.contracts.enums import KnowledgeScope, RetrievalMethod
from learnforge.storage.db import init_db

# (kind, text, topic, days_ago) —— 覆盖 qa / note / weak / mock 四类，跨多个日期供衰减观察。
CORPUS = [
    ("qa", "Q: Redis 怎么做缓存过期？\nA: 用 TTL/EXPIRE，惰性删除 + 定期删除。", "redis", 0),
    ("qa", "Q: JWT 怎么防篡改？\nA: 服务端用密钥对 header.payload 做签名校验。", "auth", 2),
    ("qa", "Q: CAS 是什么？\nA: 比较并交换(compare-and-swap)，乐观锁的底层原子操作。", "concurrency", 3),
    ("qa", "Q: 乐观锁适合什么场景？\nA: 读多写少，用版本号做并发控制。", "concurrency", 20),
    ("note", "B+ 树索引适合范围查询与排序优化。", "database", 8),
    ("weak", "薄弱点：分布式事务(2PC/TCC/Saga)理解不深。", "distributed", 25),
    ("weak", "薄弱点：索引失效场景(最左前缀、函数列)。", "database", 25),
    ("mock", "mock 反馈：Kafka 分区与顺序消费回答不清晰。", "mq", 5),
    ("qa", "Q: Redis 和 Memcached 区别？\nA: Redis 数据结构丰富、可持久化。", "redis", 40),
]


def build_corpus(db_path: str, root) -> None:
    """种入 MEMORY.md（稳定规则，不进索引）+ 多类型 daily 记忆（进 chunks/local 索引）。"""
    config.MEMORY_DIR = str(root)
    from learnforge.memory.daily import record
    from learnforge.memory.files import read_root_memory, root_memory_path

    read_root_memory()  # 建模板
    root_memory_path().write_text(
        "# MEMORY\n\n## 偏好\n- 回答用中文，给可核验要点。\n- 长期关注 Redis、JVM、并发。\n",
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    for kind, text, topic, days in CORPUS:
        record(kind=kind, text=text, topic=topic,
               date=(now - timedelta(days=days)).isoformat(), db_path=db_path)


def recall(
    db_path: str, query: str,
    method: RetrievalMethod = RetrievalMethod.FULLTEXT, top_k: int = 3,
) -> RetrievalOutput:
    from learnforge.agents.retrieval import RetrievalAgent

    return RetrievalAgent(db_path=db_path).run(
        RetrievalInput(query=query, scopes=[KnowledgeScope.LOCAL], method=method, top_k=top_k)
    )


def _show(query: str, out: RetrievalOutput, note: str = "") -> None:
    from learnforge.memory.decay import decay_weight

    tag = f"  «{note}»" if note else ""
    print(f"\nQ: {query!r}{tag}")
    if not out.chunks:
        print("   └─ (无召回)")
        return
    for c in out.chunks:
        meta = c.metadata or {}
        # 衰减权重（已作用于源内排序；rank_score 是跨源 RRF 的位次分）。
        w = decay_weight(meta.get("date"), meta.get("kind"))
        snippet = c.text.replace("\n", " ")[:44]
        print(f"   └─ [{str(meta.get('kind','?')):<4} {str(meta.get('date',''))[:10]}] "
              f"decay={w:.2f} rank_score={c.score:.4f}  {snippet}")


def main() -> None:
    from learnforge.llm.embeddings import EMBEDDINGS

    tmp = tempfile.mkdtemp(prefix="lf_recall_")
    db_path = os.path.join(tmp, "lf.db")
    init_db(db_path)
    build_corpus(db_path, os.path.join(tmp, "mem"))

    print("=" * 78)
    print(f"语料：{len(CORPUS)} 条 daily 记忆（qa/note/weak/mock，跨 0~40 天）+ 1 个 MEMORY.md")
    print(f"embedding 可用：{EMBEDDINGS.available}  → 离线走 BM25/FTS + 时间衰减")
    print("=" * 78)

    print("\n### 1) 高质量 query（精确关键词命中）")
    for q in ["Redis", "JWT", "CAS", "Kafka"]:
        _show(q, recall(db_path, q))

    print("\n### 2) 中等质量 query（中文词 / 相关概念）")
    _show("缓存过期", recall(db_path, "缓存过期"))
    _show("并发控制", recall(db_path, "并发控制"))
    _show("索引", recall(db_path, "索引"))

    print("\n### 3) 多关键词 query")
    _show("Redis 缓存 过期", recall(db_path, "Redis 缓存 过期"))
    _show("分布式 事务", recall(db_path, "分布式 事务"))

    print("\n### 4) 仅语义 query（FTS 难命中，需向量召回才好）")
    _show("怎么让登录态不被篡改", recall(db_path, "怎么让登录态不被篡改"), "期望→JWT")
    _show("读多写少的并发方案", recall(db_path, "读多写少的并发方案"), "期望→乐观锁")

    print("\n### 5) 时间衰减（同关键词不同年龄 / 黏性）")
    _show("Redis", recall(db_path, "Redis"), "0天 vs 40天 → 新者应在前")
    _show("索引", recall(db_path, "索引"), "note(8天,快衰) vs weak(25天,慢衰)")

    print("\n### 6) 边界情况（鲁棒性：不得崩，召回可空）")
    edge = [
        ("", "空 query"),
        ("     ", "纯空白"),
        ("？？？", "纯标点"),
        ("GraphQL 量子纠缠", "不存在的词"),
        ('redis AND OR "(', "FTS 特殊符号/注入"),
        ("锁", "单个 CJK 字"),
        ("REDIS", "大小写（FTS porter 不区分）"),
        ("请问一下关于这个系统里面那个用来做缓存的中间件它的过期策略到底是怎么设计的能不能详细说说惰性删除和定期删除", "超长 query(>120)"),
    ]
    for q, note in edge:
        _show(q, recall(db_path, q), note)

    print("\n### 7) MEMORY.md 不参与召回（查其内容词应召不回）")
    _show("回答用中文", recall(db_path, "回答用中文"), "MEMORY.md 内容，不在索引")
    print("\n" + "=" * 78)
    print(f"演示库：{tmp}")


if __name__ == "__main__":
    main()
