"""Retrieval mode A/B：workflow / workflow_v2 / RetrievalAgent 内部 ReAct。

离线可跑；设置 `LF_RETRIEVAL_REACT_LLM=1` + `OPENROUTER_API_KEY` 后会让 ReAct 使用
真实模型生成 query rewrites。目标不是证明某种模式一定更好，而是把多种架构放在同一组
更复杂的 query/corpus 上比较：命中率、负样本误触发、边界输入、trace 成本。

用法：
    python -m learnforge.eval.retrieval_mode_eval
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .. import config
from ..agents.retrieval import RetrievalAgent
from ..contracts.agents.retrieval import RetrievalInput
from ..contracts.enums import KnowledgeScope, RetrievalMethod, RetrievalMode
from ..memory.daily import record
from ..storage.db import init_db


@dataclass
class EvalDoc:
    doc_id: str
    kind: str
    topic: str
    text: str
    days_ago: int = 0


@dataclass
class EvalCase:
    name: str
    query: str
    should_recall: bool
    gold: Optional[str] = None


DOCS: List[EvalDoc] = [
    EvalDoc("jwt", "qa", "auth", "Q: JWT 怎么防篡改？\nA: 服务端用密钥对 header.payload 做签名校验。", 1),
    EvalDoc("optimistic", "qa", "concurrency", "Q: 乐观锁适合什么场景？\nA: 读多写少，用版本号做并发控制。", 2),
    EvalDoc("pessimistic", "qa", "concurrency", "Q: 悲观锁适合什么场景？\nA: 写冲突高、强一致要求高时先加锁再访问。", 5),
    EvalDoc("cas_aba", "weak", "concurrency", "薄弱点：CAS 的 ABA 问题与自旋开销，最近 mock 没讲清。", 0),
    EvalDoc("redis", "weak", "redis", "薄弱点：Redis 分布式锁 watchdog、锁续期和锁释放边界。", 3),
    EvalDoc("redis_cache", "qa", "redis", "Q: Redis 缓存雪崩怎么处理？\nA: TTL 加随机抖动、多级缓存、限流降级。", 4),
    EvalDoc("mysql_mvcc", "qa", "database", "Q: MySQL MVCC 怎么实现？\nA: undo log + read view 判断版本可见性。", 2),
    EvalDoc("btree", "weak", "database", "薄弱点：B+ 树索引失效场景，如最左前缀、函数列、隐式转换。", 8),
    EvalDoc("kafka_order", "mock", "mq", "mock 反馈：Kafka 分区与顺序消费回答不清晰。", 6),
    EvalDoc("mq_idempotency", "qa", "mq", "Q: MQ 消费如何保证幂等？\nA: 业务唯一键、去重表、状态机防重复执行。", 2),
    EvalDoc("payment", "qa", "payment", "Q: payment callback failure 怎么处理？\nA: 幂等表 + 重试队列 + 对账补偿。", 0),
    EvalDoc("rate_limit", "decision", "architecture", "项目决策：接口限流优先用令牌桶，突发流量可短暂借用容量。", 1),
    EvalDoc("jvm_gc", "progress", "jvm", "学习进展：完成 JVM GC Roots、对象晋升和 STW 排查路径复习。", 2),
    EvalDoc("threadpool", "weak", "java", "薄弱点：线程池参数 core/max/queue/rejection 的取舍解释不稳定。", 3),
    EvalDoc("distributed_tx", "qa", "distributed", "Q: 分布式事务有哪些方案？\nA: 2PC、TCC、Saga、本地消息表。", 7),
]

CASES: List[EvalCase] = [
    EvalCase("exact", "payment callback failure", True, "payment"),
    EvalCase("exact-cas", "CAS ABA 自旋开销", True, "cas_aba"),
    EvalCase("semantic-jwt", "怎么让登录态不被篡改", True, "jwt"),
    EvalCase("semantic-lock", "读多写少的并发方案", True, "optimistic"),
    EvalCase("semantic-redis-lock", "分布式锁自动续期边界我哪里弱", True, "redis"),
    EvalCase("semantic-cache", "缓存同时过期导致流量打爆怎么办", True, "redis_cache"),
    EvalCase("semantic-mvcc", "快照读怎么判断一行版本能不能看到", True, "mysql_mvcc"),
    EvalCase("semantic-index", "数据库索引为什么突然不走了", True, "btree"),
    EvalCase("semantic-kafka", "消息队列里怎么保证同一订单按顺序处理", True, "kafka_order"),
    EvalCase("semantic-mq-idem", "消费者重复收到消息怎么防止重复扣款", True, "mq_idempotency"),
    EvalCase("semantic-rate-limit", "突发请求限速但允许短时间 burst", True, "rate_limit"),
    EvalCase("semantic-jvm", "STW 和对象晋升我复习到哪了", True, "jvm_gc"),
    EvalCase("semantic-threadpool", "线程池队列满了参数怎么解释", True, "threadpool"),
    EvalCase("semantic-tx", "跨服务一致性除了强事务还能怎么做", True, "distributed_tx"),
    EvalCase("cross-en-aba", "compare and swap ABA spin overhead", True, "cas_aba"),
    # 近域负样本：库里有相近主题，但没有这些具体记忆，专门测过召回。
    EvalCase("neg-go", "我之前说过喜欢 Go 语言吗", False),
    EvalCase("neg-rust", "Rust ownership 和 borrow checker 我哪里薄弱", False),
    EvalCase("neg-redis-cluster", "Redis cluster hash slot 迁移怎么做", False),
    EvalCase("neg-payment-refund", "支付退款风控和拒付争议怎么处理", False),
    EvalCase("neg-spring-cloud", "Spring Cloud Gateway 限流过滤器怎么配", False),
    EvalCase("neg-epoll", "epoll 水平触发和边缘触发区别", False),
    EvalCase("neg-react", "React useEffect 依赖数组怎么写", False),
    EvalCase("neg-k8s", "Kubernetes HPA 根据 CPU 怎么扩容", False),
    EvalCase("edge-empty", "", False),
    EvalCase("edge-punct", "？？？", False),
    EvalCase("edge-math", "1+1 等于几", False),
    EvalCase("edge-injection", 'redis AND OR "(', False),
    EvalCase("edge-single-char", "锁", False),
    EvalCase("edge-long-unknown", "请问关于量子编译器里面 superconducting qubit calibration pipeline 的记忆是什么", False),
]

REALISTIC_QUERY_PATH = Path(__file__).parent / "golden" / "retrieval_realistic_queries.json"


def seed(db_path: str, memory_root: str) -> Dict[str, str]:
    """写入对比语料，返回 normalized text -> doc_id。"""
    config.MEMORY_DIR = memory_root
    now = datetime.now(timezone.utc)
    content_to_id: Dict[str, str] = {}
    for doc in DOCS:
        dt = now - timedelta(days=doc.days_ago)
        note = record(
            kind=doc.kind,
            topic=doc.topic,
            text=doc.text,
            date=dt.isoformat(),
            db_path=db_path,
        )
        content_to_id[_norm(note["text"])] = doc.doc_id
    return content_to_id


def run_eval(db_path: Optional[str] = None, memory_root: Optional[str] = None) -> dict:
    own = db_path is None
    root = memory_root or tempfile.mkdtemp(prefix="lf-retrieval-mode-mem-")
    db = db_path or tempfile.mktemp(suffix=".db")
    init_db(db)
    content_to_id = seed(db, root)

    rows = []
    for case in all_cases():
        row = {"case": case.name, "query": case.query, "gold": case.gold}
        for mode in (RetrievalMode.WORKFLOW, RetrievalMode.WORKFLOW_V2, RetrievalMode.REACT):
            out = RetrievalAgent(db_path=db).run(
                RetrievalInput(
                    query=case.query,
                    scopes=[KnowledgeScope.LOCAL],
                    method=RetrievalMethod.FULLTEXT,
                    mode=mode,
                    top_k=3,
                )
            )
            got = [content_to_id.get(_norm(c.text), "?") for c in out.chunks]
            row[mode.value] = {
                "got": got,
                "hit": case.gold in got if case.should_recall else not got,
                "triggered": bool(got),
                "trace_steps": len(out.trace),
            }
        rows.append(row)

    metrics = {
        "workflow": _metrics(rows, "workflow"),
        "workflow_v2": _metrics(rows, "workflow_v2"),
        "react": _metrics(rows, "react"),
    }
    return {"rows": rows, "metrics": metrics, "db_path": None if own else db}


def all_cases(include_realistic: bool = True) -> List[EvalCase]:
    cases = list(CASES)
    if include_realistic:
        cases.extend(load_query_cases())
    return cases


def load_query_cases(path: Optional[str] = None) -> List[EvalCase]:
    """Load additional realistic query cases from JSON."""
    source = Path(path) if path else REALISTIC_QUERY_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = raw.get("cases") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    out: List[EvalCase] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        query = str(item.get("query") or "")
        should_recall = bool(item.get("should_recall"))
        gold = item.get("gold")
        if not name:
            continue
        out.append(
            EvalCase(
                name=name,
                query=query,
                should_recall=should_recall,
                gold=str(gold) if gold else None,
            )
        )
    return out


def _metrics(rows: List[dict], mode: str) -> dict:
    positives = [r for r in rows if r["gold"]]
    negatives = [r for r in rows if not r["gold"]]
    hit_at_1 = _mean([1.0 if r[mode]["got"][:1] == [r["gold"]] else 0.0 for r in positives])
    hit_at_3 = _mean([1.0 if r[mode]["hit"] else 0.0 for r in positives])
    neg_clean = _mean([1.0 if r[mode]["hit"] else 0.0 for r in negatives])
    avg_trace_steps = _mean([float(r[mode]["trace_steps"]) for r in rows])
    false_positives = [r["case"] for r in negatives if r[mode]["triggered"]]
    misses = [r["case"] for r in positives if not r[mode]["hit"]]
    return {
        "hit@1": hit_at_1,
        "hit@3": hit_at_3,
        "negative_no_recall": neg_clean,
        "avg_trace_steps": avg_trace_steps,
        "false_positives": false_positives,
        "misses": misses,
    }


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 1.0


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def main() -> int:
    report = run_eval()
    print("# Retrieval mode A/B")
    print("\n[env]")
    import os

    print(f"- llm_rewrite={os.getenv('LF_RETRIEVAL_REACT_LLM') == '1'}")
    print(f"- model={os.getenv('LF_OPENROUTER_MODEL', 'openai/gpt-4o-mini')}")
    print("\n[rows]")
    for row in report["rows"]:
        print(
            f"- {row['case']:<22} gold={row['gold'] or '-':<14} "
            f"workflow={_compact(row['workflow']['got'])} "
            f"workflow_v2={_compact(row['workflow_v2']['got'])} "
            f"react={_compact(row['react']['got'])} steps={row['react']['trace_steps']}"
        )
    print("\n[metrics]")
    for mode, metrics in report["metrics"].items():
        print(
            f"- {mode}: hit@1={metrics['hit@1']:.2f}, hit@3={metrics['hit@3']:.2f}, "
            f"negative_no_recall={metrics['negative_no_recall']:.2f}, "
            f"avg_trace_steps={metrics['avg_trace_steps']:.2f}"
        )
        if metrics["misses"]:
            print(f"  misses={metrics['misses']}")
        if metrics["false_positives"]:
            print(f"  false_positives={metrics['false_positives']}")
    return 0


def _compact(ids: List[str]) -> str:
    if not ids:
        return "[]"
    return "[" + ",".join(ids[:3]) + "]"


if __name__ == "__main__":
    import sys

    sys.exit(main())
