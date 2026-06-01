"""种子库灌库脚本（T-M1-14 演示版）——让离线 CLI 的 QA/检索/诊断/规划面板有真实内容。

只写"知识内容 + 索引 + 少量交互信号"，不破坏 Manager 唯一写者约束：
- knowledge_atoms（公共知识点/题库）+ atom_fts 索引（经 AtomRepository.sync_index）。
- chunks（可检索语料：每个知识点的问答正文）+ chunk_fts（经 ChunkRepository.upsert）。
- qa_history（本地用户库历史问答）。
- interaction_events（少量弱点信号，使 /diagnose 首跑即非空）。

幂等：atom/chunk 用 upsert（ON CONFLICT），event/qa 用固定 id + INSERT OR IGNORE/REPLACE，
可重复运行不重复堆数据（T-M1-14 AC③）。无 embedding key 时仅建 FTS 索引（向量分路跳过）。

运行：
    python -m learnforge.seed_demo                 # 灌入默认库 config.DB_PATH
    python -m learnforge.seed_demo --db demo.db    # 灌入指定库
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .contracts.atom import KnowledgeAtom
from .contracts.enums import EventType, SourceType
from .contracts.message import EventPayload
from .mastery import to_tier
from .storage.db import init_db
from .storage.repositories import (
    AtomRepository,
    ChunkRepository,
    EventRepository,
    QAHistoryRepository,
)

# (topic, title, question, answer, difficulty, mastery, source_type)
# mastery：刻意混合强弱——低分(<0.4) 供诊断/规划当弱点，高分供对照。
_SEED: List[Tuple[str, str, str, str, int, float, SourceType]] = [
    # —— 并发 ——
    ("concurrency", "乐观锁 vs 悲观锁",
     "乐观锁和悲观锁的区别与适用场景？",
     "悲观锁假设冲突常发生，先加锁再操作（如 synchronized、SELECT FOR UPDATE），适合写多；"
     "乐观锁假设冲突少，通过版本号或 CAS 在提交时校验（如 AtomicInteger、版本字段），适合读多写少。",
     3, 0.2, SourceType.INTERVIEW_POST),
    ("concurrency", "线程池核心参数",
     "ThreadPoolExecutor 的核心参数有哪些，任务提交后的执行流程？",
     "核心参数：corePoolSize、maximumPoolSize、keepAliveTime、workQueue、拒绝策略。"
     "提交流程：核心线程未满则新建核心线程；满了入队列；队列满且未达最大线程则新建非核心线程；再满触发拒绝策略。",
     3, 0.35, SourceType.BLOG),
    ("concurrency", "volatile 关键字",
     "volatile 的作用与原理？",
     "volatile 保证可见性（写立即刷主存、读从主存）与有序性（禁止指令重排，插入内存屏障），"
     "但不保证原子性。常用于状态标志位与双重检查锁的单例。",
     2, 0.7, SourceType.DOC),
    ("concurrency", "synchronized 与 ReentrantLock",
     "synchronized 和 ReentrantLock 有何区别？",
     "synchronized 是 JVM 关键字、自动释放、非公平；ReentrantLock 是 API、需手动 unlock、"
     "支持公平/非公平、可中断、可绑定多个 Condition、可尝试获取锁。",
     4, 0.55, SourceType.INTERVIEW_POST),

    # —— 数据库 ——
    ("database", "B+ 树索引",
     "为什么数据库索引用 B+ 树而不是二叉树或 B 树？",
     "B+ 树矮胖、磁盘 IO 少；非叶子节点只存键不存数据，单页能放更多键、扇出更大；"
     "叶子节点用双向链表相连，天然支持范围查询与排序。",
     3, 0.25, SourceType.INTERVIEW_POST),
    ("database", "事务隔离级别",
     "事务的四种隔离级别分别解决什么问题？",
     "读未提交→脏读；读已提交→不可重复读；可重复读（MySQL 默认，靠 MVCC+间隙锁基本解决幻读）；"
     "串行化→完全隔离但并发最低。隔离级别越高一致性越强、并发越低。",
     4, 0.3, SourceType.BLOG),
    ("database", "MVCC 多版本并发控制",
     "MVCC 是如何实现的？",
     "通过隐藏列（事务 id、回滚指针）+ undo log 版本链 + ReadView 实现：读时按可见性规则选择版本，"
     "使读不加锁、读写不冲突，提升并发。",
     4, 0.4, SourceType.DOC),
    ("database", "索引失效场景",
     "哪些情况会导致索引失效？",
     "对索引列做函数/运算、隐式类型转换、前导模糊匹配 LIKE '%x'、违反最左前缀、"
     "OR 连接非索引列、优化器判定全表更快等。",
     3, 0.6, SourceType.INTERVIEW_POST),

    # —— JVM ——
    ("jvm", "JVM 运行时内存区域",
     "JVM 的运行时数据区有哪些？",
     "线程私有：程序计数器、虚拟机栈、本地方法栈；线程共享：堆（对象与数组）、方法区/元空间（类信息、常量池）。",
     3, 0.5, SourceType.DOC),
    ("jvm", "垃圾回收算法与收集器",
     "常见 GC 算法和垃圾收集器有哪些？",
     "算法：标记-清除、复制、标记-整理、分代收集。收集器：Serial、ParNew、CMS（低停顿但有碎片）、"
     "G1（分 Region、可预测停顿）、ZGC（着色指针、亚毫秒停顿）。",
     4, 0.3, SourceType.BLOG),
    ("jvm", "类加载机制与双亲委派",
     "类加载过程与双亲委派模型？",
     "加载→验证→准备→解析→初始化。双亲委派：加载请求先委派父加载器，父加载不了再自己加载，"
     "保证核心类唯一与安全。",
     4, 0.65, SourceType.INTERVIEW_POST),

    # —— 网络 ——
    ("network", "TCP 三次握手与四次挥手",
     "TCP 为什么三次握手、四次挥手？",
     "三次握手确认双方收发能力、防止历史连接；四次挥手因 TCP 全双工，被动方需先 ACK 再处理剩余数据后才 FIN，"
     "故拆成四次，并有 TIME_WAIT 等待 2MSL。",
     3, 0.45, SourceType.INTERVIEW_POST),
    ("network", "HTTP 与 HTTPS 区别",
     "HTTP 和 HTTPS 的区别？",
     "HTTPS = HTTP + TLS：通过非对称加密协商对称密钥、证书验证身份，保证机密性、完整性与身份认证；"
     "默认端口 443，有握手与加密开销。",
     2, 0.7, SourceType.DOC),
    ("network", "TCP 拥塞控制",
     "TCP 拥塞控制的四个阶段？",
     "慢启动（指数增长）、拥塞避免（线性增长）、快重传（收到 3 个重复 ACK 立即重传）、"
     "快恢复（不回到慢启动，减半后线性增长）。",
     4, 0.3, SourceType.BLOG),

    # —— 算法 ——
    ("algorithm", "快速排序",
     "快速排序的原理、复杂度与优化？",
     "选基准做分区，递归排序左右。平均 O(n log n)、最坏 O(n²)（有序+固定基准）。"
     "优化：随机/三数取中选基准、小数组转插入排序、三路切分处理重复元素。",
     3, 0.6, SourceType.DOC),
    ("algorithm", "动态规划",
     "动态规划的核心思想与解题步骤？",
     "把问题拆成重叠子问题，用最优子结构自底向上递推并缓存结果避免重复计算。"
     "步骤：定义状态、写状态转移方程、确定边界与遍历顺序。",
     4, 0.25, SourceType.INTERVIEW_POST),
    ("algorithm", "二分查找边界",
     "二分查找如何避免死循环与边界错误？",
     "明确区间定义（左闭右闭 or 左闭右开）并全程一致；mid=l+(r-l)/2 防溢出；"
     "查找左/右边界时收缩条件与返回值要对应区间定义。",
     3, 0.5, SourceType.BLOG),

    # —— 缓存 ——
    ("cache", "Redis 持久化 RDB 与 AOF",
     "Redis RDB 和 AOF 持久化的区别？",
     "RDB 是快照，文件小、恢复快，但可能丢最后一次快照后的数据；AOF 记录写命令，"
     "可秒级持久、数据更安全但文件大、恢复慢。生产常 RDB+AOF 混合。",
     3, 0.4, SourceType.BLOG),
    ("cache", "缓存穿透、击穿、雪崩",
     "缓存穿透、击穿、雪崩分别是什么，如何解决？",
     "穿透：查不存在的 key→布隆过滤器/缓存空值；击穿：热点 key 过期→互斥锁/逻辑过期/不过期；"
     "雪崩：大量 key 同时过期→过期时间加随机、多级缓存、限流降级。",
     4, 0.3, SourceType.INTERVIEW_POST),
    ("cache", "缓存与数据库一致性",
     "如何保证缓存与数据库的一致性？",
     "常用旁路缓存 + 先更新库再删缓存（Cache Aside）；配合延迟双删、订阅 binlog 异步删缓存"
     "降低不一致窗口；强一致代价高，多数场景接受最终一致。",
     4, 0.45, SourceType.DOC),

    # —— 操作系统 ——
    ("os", "进程与线程",
     "进程和线程的区别？",
     "进程是资源分配的基本单位、有独立地址空间、开销大、隔离好；线程是 CPU 调度的基本单位、"
     "共享进程内存、切换开销小、需同步。",
     2, 0.6, SourceType.DOC),
    ("os", "虚拟内存与分页",
     "虚拟内存和分页机制的作用？",
     "虚拟内存让每个进程拥有独立连续的地址空间，通过页表映射到物理内存，按需调页、提升内存利用率与隔离；"
     "缺页时触发缺页中断从磁盘换入。",
     4, 0.35, SourceType.BLOG),

    # —— 分布式 ——
    ("distributed", "CAP 理论",
     "CAP 理论是什么，为什么不能同时满足？",
     "一致性 C、可用性 A、分区容错 P。网络分区不可避免，故 P 必选；分区时要么拒绝服务保一致（CP），"
     "要么继续服务容忍不一致（AP），不能同时满足 C 和 A。",
     3, 0.4, SourceType.INTERVIEW_POST),
    ("distributed", "Raft 一致性算法",
     "Raft 如何保证分布式一致性？",
     "通过 Leader 选举（任期+多数票）、日志复制（Leader 接收写并复制到多数节点才提交）、"
     "安全性约束保证状态机一致。比 Paxos 更易理解。",
     5, 0.25, SourceType.BLOG),
]

# 少量历史弱点信号（让 /diagnose 首跑即非空）。固定 event_id 保证幂等。
_EVENTS: List[Tuple[EventType, str, List[str], dict]] = [
    (EventType.MOCK_WEAKNESS, "concurrency", ["concurrency-1"], {"weak": True}),
    (EventType.MOCK_WEAKNESS, "concurrency", ["concurrency-1"], {"weak": True}),
    (EventType.MOCK_WEAKNESS, "concurrency", ["concurrency-2"], {"weak": True}),
    (EventType.MOCK_WEAKNESS, "algorithm", ["algorithm-2"], {"weak": True}),
    (EventType.MOCK_WEAKNESS, "algorithm", ["algorithm-2"], {"weak": True}),
    (EventType.QA_SIGNAL, "database", ["database-1"], {"correct": False, "weak": True}),
    (EventType.QA_SIGNAL, "distributed", ["distributed-2"], {"verdict": "uncertain", "weak": True}),
]

# 历史问答（本地用户库）。
_QA_HISTORY: List[Tuple[str, str, str, str]] = [
    ("seed-qa-1", "什么是乐观锁？", "基于版本号或 CAS 的并发控制策略，适合读多写少场景。", "concurrency"),
    ("seed-qa-2", "B+ 树为什么适合做索引？", "矮胖、扇出大、叶子链表支持范围查询。", "database"),
]


def seed(db_path: Optional[str] = None) -> dict:
    """灌入种子库；返回各类计数。幂等可重复运行。"""
    init_db(db_path)  # 缺表自动建（schema 用 IF NOT EXISTS）
    now = datetime.now(timezone.utc)

    ar = AtomRepository(db_path=db_path)
    cr = ChunkRepository(conn=ar.conn)  # 复用同一连接（同库）
    n_atoms = n_chunks = 0
    topic_counter: dict = {}
    for topic, title, q, a, diff, mastery, stype in _SEED:
        topic_counter[topic] = topic_counter.get(topic, 0) + 1
        atom_id = f"{topic}-{topic_counter[topic]}"
        atom = KnowledgeAtom(
            atom_id=atom_id, topic=topic, title=title, canonical_question=q,
            canonical_answer=a, difficulty=diff, mastery_score=mastery,
            mastery_tier=to_tier(mastery), last_reviewed_at=now,
            tags=[topic], created_at=now, updated_at=now,
        )
        ar.upsert(atom)
        ar.sync_index(atom)  # atom_fts（无 embedding → 跳过向量）
        n_atoms += 1

        # 标题并入正文：FTS5 unicode61 不切 CJK，CJK 召回主要靠 chunks.text 的 LIKE 子串兜底，
        # 把标题关键词（如"线程池"）并入正文可显著提升关键词检索命中（Design §8c 降级）。
        cr.upsert(
            chunk_id=f"chunk-{atom_id}", text=f"{title}。{q} {a}", source_type=stype.value,
            topic=topic, kb_scope="shared", atom_refs=[atom_id],
            metadata={"topic": topic, "title": title, "source_type": stype.value},
        )
        n_chunks += 1

    er = EventRepository(conn=ar.conn)
    for i, (etype, topic, refs, signal) in enumerate(_EVENTS):
        er.append(EventPayload(event_type=etype, topic=topic, atom_refs=refs, signal=signal),
                  event_id=f"seed-ev-{i}", trace_id="seed")

    qr = QAHistoryRepository(conn=ar.conn)
    for qa_id, q, a, topic in _QA_HISTORY:
        qr.append(qa_id=qa_id, question=q, answer=a, topic=topic)

    return {"atoms": n_atoms, "chunks": n_chunks,
            "events": len(_EVENTS), "qa_history": len(_QA_HISTORY)}


def _main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="LearnForge 种子库灌库（演示用）")
    parser.add_argument("--db", default=None, help="目标 SQLite 路径（默认 config.DB_PATH）")
    args = parser.parse_args(argv)

    from .config import DB_PATH

    db = args.db or DB_PATH
    counts = seed(db)
    print(f"[seed] 已灌入 → {db}")
    print(f"[seed] atoms={counts['atoms']}  chunks={counts['chunks']}  "
          f"events={counts['events']}  qa_history={counts['qa_history']}")
    print("[seed] 现在可运行：python -m learnforge.cli"
          + ("" if args.db is None else f" --db {db}"))


if __name__ == "__main__":
    _main()
