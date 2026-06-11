"""个人 local memory 评测脚手架（REQUIREMENTS 测试说明 §4–§14，第一阶段）。

不是"能不能搜到"的冒烟，而是端到端验证：该不该触发 → 召回对不对 → 排序对不对
（相关性/重要性/时间衰减）→ 注入/日志可解释 → 负样本不编造。

数据：内置固定 seed（覆盖 qa/weak/mock/progress/decision，带稳定 memory_id）+ MEMORY.md 偏好。
每次跑独立临时库（init_db → seed → 检索/排序 → 比对标注），全离线确定性。

环境探测：自动判断 vector 是否可用（EMBEDDINGS.available + chunk_vectors 表是否存在）。
离线无向量时只验证 FTS/LIKE 通路（第一阶段 §14）；完整 hybrid（§15）需向量环境，本脚手架
会在报告中明确标注「vector: 不可用 → 未验证 hybrid」，绝不冒称已验证。

用法：
    python -m learnforge.eval.memory_eval          # 跑全部场景并打印 §13 报告
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ..agents.manager import ManagerAgent
from ..agents.retrieval import RetrievalAgent
from ..contracts.agents.retrieval import RetrievalFilters, RetrievalInput
from ..contracts.enums import KnowledgeScope, RetrievalMethod
from ..memory.daily import record, reindex
from ..memory.files import append_root_memory, read_root_memory
from ..memory.log import MEMORY_LOG
from ..storage.db import get_connection, init_db
from ..storage.repositories import SessionStateRepository

_NOW = datetime.now(timezone.utc)


def _ago(days: float) -> str:
    return (_NOW - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# 固定 seed（§4：稳定 memory_id，覆盖全部 kind；含衰减/冲突/干扰项）
# ---------------------------------------------------------------------------
@dataclass
class Seed:
    memory_id: str
    kind: str
    topic: str
    content: str
    age_days: float


SEEDS: List[Seed] = [
    Seed("mem_redis_weak_001", "weak", "redis",
         "用户在 mock 面试中对 Redis 分布式锁回答不完整，尤其是 watchdog、锁续期和锁释放边界。", 5),
    Seed("mem_redis_qa_001", "qa", "redis",
         "Q: Redis 为什么快？\nA: 纯内存 + 单线程事件循环 + 高效数据结构。", 4),
    Seed("mem_payment_qa_001", "qa", "payment",
         "Q: payment callback failure 当时怎么处理？\nA: 幂等表 + 重试队列 + 对账补偿处理支付回调失败。", 3),
    Seed("mem_cas_qa_old_001", "qa", "concurrency",
         "Q: CAS 是什么？\nA: 比较并交换，乐观锁基础。", 30),
    Seed("mem_cas_weak_new_001", "weak", "concurrency",
         "薄弱点：CAS 的 ABA 问题与自旋开销，最近 mock 没讲清。", 0),
    Seed("mem_shared_decision_old_001", "decision", "memory_architecture",
         "项目决策：考虑做 shared memory 共享知识库。", 20),
    Seed("mem_shared_decision_new_001", "decision", "memory_architecture",
         "项目决策：当前阶段不做 shared memory，先做个人 local memory。", 1),
    Seed("mem_progress_jvm_001", "progress", "jvm",
         "学习进展：完成 JVM 内存模型与 GC，可手绘对象晋升过程。", 2),
    Seed("mem_mock_concurrency_001", "mock", "concurrency",
         "mock 会话 s1 暴露薄弱点（掌握信号<0.6）：并发线程池参数。", 6),
]
MEMORY_MD_PREFERENCE = "LeetCode 代码反馈只给最小修改，不重写整段。"


# ---------------------------------------------------------------------------
# 查询场景（§5）：每条带标注，便于算指标
# ---------------------------------------------------------------------------
@dataclass
class Case:
    name: str
    query: str
    should_recall: bool
    gold: List[str] = field(default_factory=list)        # 命中即算对（hit@k 用）
    expected_rank1: Optional[str] = None                  # 排序判别（衰减/冲突）
    expected_kind: Optional[str] = None
    expected_topic: Optional[str] = None
    kind_filter: Optional[str] = None
    topic_filter: Optional[str] = None


CASES: List[Case] = [
    Case("5.2 语义改写", "我上次 Redis 面试哪里卡住了？", True,
         gold=["mem_redis_weak_001"], expected_kind="weak", expected_topic="redis"),
    Case("5.3 关键词精确", "payment callback failure 当时怎么处理？", True,
         gold=["mem_payment_qa_001"], expected_topic="payment"),
    Case("5.4 kind 过滤", "最近 mock 暴露了哪些 weak point？", True,
         gold=["mem_cas_weak_new_001", "mem_redis_weak_001"],
         expected_kind="weak", kind_filter="weak"),
    Case("5.5 topic 过滤", "Redis 相关薄弱点有哪些？", True,
         gold=["mem_redis_weak_001"], expected_topic="redis", topic_filter="redis"),
    Case("5.6 时间衰减", "我最近 CAS 相关问题是什么？", True,
         gold=["mem_cas_weak_new_001", "mem_cas_qa_old_001"],
         expected_rank1="mem_cas_weak_new_001"),
    Case("5.7 冲突记忆", "LearnForge 现在要不要做 shared memory？", True,
         gold=["mem_shared_decision_new_001", "mem_shared_decision_old_001"],
         expected_rank1="mem_shared_decision_new_001"),
    Case("5.8 负样本", "我之前说过我喜欢 Go 语言吗？", False),
    Case("trigger-闲聊1", "你好", False),
    Case("trigger-闲聊2", "1+1 等于几", False),
    Case("trigger-闲聊3", "帮我翻译这句话", False),
]


# ---------------------------------------------------------------------------
# 环境探测 + seed
# ---------------------------------------------------------------------------
def detect_vector(db_path: str) -> Dict[str, bool]:
    from ..llm.embeddings import EMBEDDINGS

    emb = bool(getattr(EMBEDDINGS, "available", False))
    try:
        get_connection(db_path).execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()
        vec_table = True
    except Exception:
        vec_table = False
    return {"embedding_available": emb, "chunk_vectors_table": vec_table,
            "hybrid_ready": emb and vec_table}


def seed_all(db_path: str) -> Dict[str, str]:
    """写入全部 seed，返回 normalized(content) → memory_id 映射（供召回结果反查 id）。"""
    append_root_memory(MEMORY_MD_PREFERENCE)
    content_to_id: Dict[str, str] = {}
    for s in SEEDS:
        record(kind=s.kind, topic=s.topic, text=s.content, date=_ago(s.age_days), db_path=db_path)
        content_to_id[_norm(s.content)] = s.memory_id
    return content_to_id


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())


# ---------------------------------------------------------------------------
# 召回 + 指标
# ---------------------------------------------------------------------------
def _retrieve(db_path: str, case: Case):
    filters = None
    if case.kind_filter or case.topic_filter:
        filters = RetrievalFilters(kind=case.kind_filter, topic=case.topic_filter)
    out = RetrievalAgent(db_path=db_path).run(
        RetrievalInput(query=case.query, scopes=[KnowledgeScope.LOCAL],
                       method=RetrievalMethod.FULLTEXT, top_k=5, filters=filters)
    )
    return out


def _ids(out, c2i: Dict[str, str]) -> List[str]:
    return [c2i.get(_norm(c.text), f"?{c.text[:12]}") for c in out.chunks]


def run_eval(db_path: Optional[str] = None) -> dict:
    own = db_path is None
    db_path = db_path or tempfile.mktemp(suffix=".db")
    init_db(db_path)
    env = detect_vector(db_path)
    c2i = seed_all(db_path)

    rows: List[dict] = []
    for case in CASES:
        out = _retrieve(db_path, case)
        got = _ids(out, c2i)
        gold_hit_ranks = [i for i, mid in enumerate(got) if mid in case.gold]
        triggered = len(out.chunks) > 0
        rows.append({
            "case": case, "got": got, "scores": [round(c.score, 4) for c in out.chunks],
            "kinds": [(c.metadata or {}).get("kind") for c in out.chunks],
            "topics": [c.metadata or {} for c in out.chunks],
            "triggered": triggered, "gold_hit_ranks": gold_hit_ranks, "degraded": out.degraded,
            "top_kind": (out.chunks[0].metadata or {}).get("kind") if out.chunks else None,
            "top_topic": (out.chunks[0].metadata or {}).get("topic") if out.chunks else None,
        })

    metrics = _aggregate(rows)
    return {"env": env, "rows": rows, "metrics": metrics,
            "db_path": db_path if not own else None}


def _aggregate(rows: List[dict]) -> dict:
    # trigger
    tp = sum(1 for r in rows if r["case"].should_recall and r["triggered"])
    fp = sum(1 for r in rows if not r["case"].should_recall and r["triggered"])
    fn = sum(1 for r in rows if r["case"].should_recall and not r["triggered"])
    trig_prec = tp / (tp + fp) if (tp + fp) else 1.0
    trig_rec = tp / (tp + fn) if (tp + fn) else 1.0

    # retrieval（仅 should_recall 且有 gold 的 case）
    rel = [r for r in rows if r["case"].should_recall and r["case"].gold]
    def hit_at(k): return _mean([1.0 if any(i < k for i in r["gold_hit_ranks"]) else 0.0 for r in rel])
    mrr = _mean([1.0 / (min(r["gold_hit_ranks"]) + 1) if r["gold_hit_ranks"] else 0.0 for r in rel])

    # kind/topic 命中（top-1）
    kc = [r for r in rows if r["case"].expected_kind]
    tc = [r for r in rows if r["case"].expected_topic]
    kind_acc = _mean([1.0 if r["top_kind"] == r["case"].expected_kind else 0.0 for r in kc])
    topic_acc = _mean([1.0 if r["top_topic"] == r["case"].expected_topic else 0.0 for r in tc])

    # 排序判别（rank1）
    rk = [r for r in rows if r["case"].expected_rank1]
    rank1_acc = _mean([1.0 if r["got"] and r["got"][0] == r["case"].expected_rank1 else 0.0 for r in rk])

    # 负样本不编造（检索层代理：负样本应 0 命中 gold / 0 触发）
    negs = [r for r in rows if not r["case"].should_recall]
    neg_clean = _mean([1.0 if not r["triggered"] else 0.0 for r in negs])

    return {"trigger_precision": trig_prec, "trigger_recall": trig_rec,
            "hit@1": hit_at(1), "hit@3": hit_at(3), "hit@5": hit_at(5), "MRR@5": mrr,
            "kind_accuracy": kind_acc, "topic_accuracy": topic_acc,
            "rank1_accuracy": rank1_acc, "negative_no_recall_rate": neg_clean}


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 1.0


# ---------------------------------------------------------------------------
# 受控排序检查（§8.1 重要性 / §8.3 RRF 分数保真）——独立临时库，避免污染主数据
# ---------------------------------------------------------------------------
def ranking_checks() -> dict:
    db = tempfile.mktemp(suffix=".db")
    init_db(db)
    # 同 topic 同日期，仅 kind 不同：decision/weak(imp 0.9) 应排在 qa(imp 0.5) 前。
    record(kind="qa", topic="rk", text="限流 的普通问答记录甲。", date=_ago(0), db_path=db)
    record(kind="decision", topic="rk", text="项目决策：限流 用令牌桶乙。", date=_ago(0), db_path=db)
    out = RetrievalAgent(db_path=db).run(RetrievalInput(
        query="限流", scopes=[KnowledgeScope.LOCAL], method=RetrievalMethod.FULLTEXT, top_k=5))
    top_kind = (out.chunks[0].metadata or {}).get("kind") if out.chunks else None
    importance_ok = top_kind in ("decision", "weak")

    # RRF 分数保真：同 kind 同关键词，新 vs 旧（30d）；单源不应被外层 RRF 抹平。
    db2 = tempfile.mktemp(suffix=".db")
    init_db(db2)
    record(kind="note", topic="cas", text="CAS 新笔记今天。", date=_ago(0), db_path=db2)
    record(kind="note", topic="cas", text="CAS 旧笔记很久以前。", date=_ago(30), db_path=db2)
    out2 = RetrievalAgent(db_path=db2).run(RetrievalInput(
        query="CAS", scopes=[KnowledgeScope.LOCAL], method=RetrievalMethod.FULLTEXT, top_k=5))
    s = [c.score for c in out2.chunks]
    # 抹平的判据：两条分数几乎相等（差 < 20%）。保真则新明显高于旧。
    not_flattened = len(s) >= 2 and s[0] > 0 and (s[0] - s[1]) / s[0] > 0.2
    return {"importance_top_kind": top_kind, "importance_ok": importance_ok,
            "decay_scores": [round(x, 4) for x in s], "rrf_not_flattened": not_flattened}


# ---------------------------------------------------------------------------
# 功能冒烟（§6）：MEMORY.md / daily reindex / session 压缩 / 日志
# ---------------------------------------------------------------------------
def smoke_checks(db_path: str) -> dict:
    res: dict = {}
    # §6.1 MEMORY.md：初始化模板 + 偏好已写入 + 不进 daily 索引
    md = read_root_memory()
    res["memory_md_loads"] = bool(md) and MEMORY_MD_PREFERENCE in md
    conn = get_connection(db_path)
    pref_in_index = conn.execute(
        "SELECT COUNT(*) c FROM chunks WHERE text LIKE '%LeetCode 代码反馈%'").fetchone()["c"]
    res["memory_md_not_indexed"] = pref_in_index == 0

    # §6.3 daily reindex：清索引→从 markdown 重建→召回仍有
    before = len(_retrieve(db_path, Case("x", "Redis", True)).chunks)
    n = reindex(db_path=db_path)
    after = len(_retrieve(db_path, Case("x", "Redis", True)).chunks)
    res["reindex_count"] = n
    res["reindex_recall_stable"] = before > 0 and after > 0

    # §6.5 session 压缩（Claude Code 式：turn 结束按 token 阈值触发，不按轮数）。冒烟用一个**临时
    # 小阈值**验证机制（触发 + pin 保护 + 保留下限），与默认阈值（≈上限 80% ≈ 10 万 tok）解耦——
    # 默认下日常对话几乎不触发，不适合做冒烟。
    from .. import config

    mgr = ManagerAgent(db_path=db_path)
    sid = "evalsess"
    _saved = (config.SESSION_COMPACTION_THRESHOLD_TOKENS,
              config.SESSION_COMPACTION_TARGET_TOKENS,
              config.SESSION_MIN_RECENT_ROUNDS)
    config.SESSION_COMPACTION_THRESHOLD_TOKENS = 200
    config.SESSION_COMPACTION_TARGET_TOKENS = 100
    config.SESSION_MIN_RECENT_ROUNDS = 2
    try:
        mgr.record_turn(sid, "诊断我的弱点", "你的薄弱点是并发与锁机制", important=True)
        triggered = False
        for i in range(1, 9):
            mgr.record_turn(sid, f"问题{i}" + "啊" * 200, f"回答{i}" + "哦" * 200)
            if (SessionStateRepository(db_path=db_path).get(sid) or {}).get("summary"):
                triggered = True
        st = SessionStateRepository(db_path=db_path).get(sid) or {}
    finally:
        (config.SESSION_COMPACTION_THRESHOLD_TOKENS,
         config.SESSION_COMPACTION_TARGET_TOKENS,
         config.SESSION_MIN_RECENT_ROUNDS) = _saved
    final_sum = st.get("summary") or ""
    last_rounds = len(st.get("recent_messages") or [])
    pinned = (st.get("active_task") or {}).get("pinned") or []
    res["session_compress_triggered"] = triggered
    res["session_keeps_recent_n"] = 2 <= last_rounds < 9
    res["session_pin_protected"] = any("并发" in p.get("reply", "") for p in pinned)
    res["session_no_json_nesting"] = ('\\"' not in final_sum) and ("key_facts" not in final_sum)
    res["session_summary_len"] = len(final_sum)

    # §12 日志：一轮 begin_memory_turn + 检索后，面板含读/注入/写
    MEMORY_LOG.reset()
    mgr.begin_memory_turn("Redis 薄弱点", sid)
    _retrieve(db_path, Case("x", "Redis", True))
    acts = {e.action for e in MEMORY_LOG.events}
    res["log_has_load_stable"] = any("加载稳定记忆" in a for a in acts)
    res["log_has_search"] = any("搜索长期记忆" in a for a in acts)
    res["log_has_inject"] = any("注入" in a for a in acts)
    return res


# ---------------------------------------------------------------------------
# 报告（§13）+ 第一阶段通过判定（§14）
# ---------------------------------------------------------------------------
def phase1_criteria(report: dict) -> List[tuple]:
    m, rk, sm, env = report["metrics"], report["ranking"], report["smoke"], report["env"]
    return [
        ("§14-1 MEMORY.md 初始化并加载", sm["memory_md_loads"]),
        ("§14-2 daily 写入 + FTS 索引", sm["reindex_recall_stable"]),
        ("§14-3 session 压缩(token 触发/保留下限/pin 保护)",
         sm["session_compress_triggered"] and sm["session_keeps_recent_n"] and sm["session_pin_protected"]),
        ("§14-4 FTS fallback 基础召回", m["hit@5"] >= 0.90),
        ("§14-5 kind/topic 过滤", m["kind_accuracy"] >= 0.85 and m["topic_accuracy"] >= 0.85),
        ("§14-6 重要性 + 时间衰减影响排序", rk["importance_ok"] and m["rank1_accuracy"] >= 0.99),
        ("§14-7 无 session summary 递归套娃", sm["session_no_json_nesting"]),
        ("§14-8 单源不被外层 RRF 抹平衰减强度", rk["rrf_not_flattened"]),
        ("§14-9 负样本不召回(不编造代理)", m["negative_no_recall_rate"] >= 0.99),
        ("§14-10 日志展示加载/召回/注入/写入", sm["log_has_load_stable"] and sm["log_has_search"] and sm["log_has_inject"]),
    ]


def main(argv=None) -> int:
    db = tempfile.mktemp(suffix=".db")
    rep = run_eval(db)
    rep["ranking"] = ranking_checks()
    rep["smoke"] = smoke_checks(db)

    P = print
    P("=" * 74)
    P("# LearnForge 个人 local memory 评测报告（§13）")
    P("=" * 74)
    e = rep["env"]
    P(f"[环境] embedding_available={e['embedding_available']}  "
      f"chunk_vectors 表={e['chunk_vectors_table']}  hybrid_ready={e['hybrid_ready']}")
    if not e["hybrid_ready"]:
        P("       ⚠️ 无向量环境：仅验证 FTS/LIKE 通路；hybrid/vector 召回【未验证】(留待 §15 第二阶段)")

    P("\n[召回逐样本 trace]")
    for r in rep["rows"]:
        c = r["case"]
        tag = "✓" if (c.should_recall == r["triggered"]) else "✗"
        P(f"  {tag} {c.name:<12} trigger={r['triggered']!s:<5} "
          f"got={r['got'][:3]} scores={r['scores'][:3]}")
        if c.expected_rank1:
            ok = r["got"] and r["got"][0] == c.expected_rank1
            P(f"      rank1 期望={c.expected_rank1} 实得={r['got'][0] if r['got'] else '-'} {'✓' if ok else '✗'}")

    m = rep["metrics"]
    P("\n[指标]")
    for k in ("trigger_precision", "trigger_recall", "hit@1", "hit@3", "hit@5", "MRR@5",
              "kind_accuracy", "topic_accuracy", "rank1_accuracy", "negative_no_recall_rate"):
        P(f"  {k:<24} = {m[k]:.2f}")

    rk = rep["ranking"]
    P("\n[排序检查]")
    P(f"  重要性 top_kind={rk['importance_top_kind']}  importance_ok={rk['importance_ok']}")
    P(f"  衰减分数(新→旧)={rk['decay_scores']}  rrf_not_flattened={rk['rrf_not_flattened']}")

    sm = rep["smoke"]
    P("\n[功能冒烟]")
    for k in ("memory_md_loads", "memory_md_not_indexed", "reindex_count", "reindex_recall_stable",
              "session_compress_triggered", "session_keeps_recent_n", "session_pin_protected",
              "session_no_json_nesting",
              "session_summary_len", "log_has_load_stable", "log_has_search", "log_has_inject"):
        P(f"  {k:<32} = {sm[k]}")

    P("\n[第一阶段通过标准 §14]")
    crits = phase1_criteria(rep)
    allok = True
    for name, ok in crits:
        allok = allok and ok
        P(f"  {'PASS' if ok else 'FAIL'}  {name}")
    P("\n" + ("=" * 74))
    P(f"第一阶段结论：{'全部通过 ✅' if allok else '存在未通过项 ❌'}")
    P(f"第二阶段(§15 hybrid)：{'可在向量环境运行' if not e['hybrid_ready'] else '环境就绪，可执行'}（本次未跑 hybrid 指标）")
    return 0 if allok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
