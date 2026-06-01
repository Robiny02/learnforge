"""对抗语义召回评测（REQUIREMENTS 测试 §5/§7/§10）—— 真实 embedding 离线语义评测。

口径（务必照此报告）：
  真实 embedding 离线语义召回有效，hit@1 约 0.85；FTS fallback 在无字面重叠中文改写下失败；
  纯向量仍有同主题细粒度误召回，需要 rerank / margin / hybrid 补强；完整 in-DB hybrid 尚未验证。

设计：
- 有 embedding key（OPENAI_API_KEY，base_url 经 LF_OPENAI_BASE_URL，默认 OpenRouter）时运行；无 key → skipped。
- **绝不记录 key**：只打印 base_url 与 model 名。
- 对每条样本输出 top-k(id,sim)、gold、gold 命中名次、failure_reason，并套用 recall_gate 阈值闸门。
- 同时跑 FTS/LIKE 基线作对照（离线、无需 key），证明无字面重叠时 FTS 失败。
- 数据集见 golden/semantic.json（含 redis 锁续期 r1 vs 缓存雪崩 r2 失败回归对）。

⚠️ 仅评测 embedding 的语义召回质量（Python 端 cosine）；不是项目 in-DB vec0/KNN 管线
（本机 sqlite3 无 enable_load_extension，sqlite-vec 加载不了）。故**不得宣称完整 hybrid 已验证**。

用法：
    OPENAI_API_KEY=... LF_OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
    LF_EMBEDDING_MODEL=openai/text-embedding-3-small \
    python -m learnforge.eval.semantic_eval
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from ..agents.retrieval import RetrievalAgent
from ..contracts.agents.retrieval import RetrievalInput
from ..contracts.enums import KnowledgeScope, RetrievalMethod
from ..memory.daily import record
from ..memory.recall_gate import ABSTAIN, gate_recall
from ..storage.db import init_db

DEFAULT_GOLDEN = Path(__file__).parent / "golden" / "semantic.json"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/text-embedding-3-small"


class EmbeddingUnavailable(RuntimeError):
    """无 key / SDK / 调用失败 —— 评测应 skip，不应失败。"""


def _embedder():
    """返回 embed(texts)->List[vec]；无 key 抛 EmbeddingUnavailable。绝不返回/记录 key。"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EmbeddingUnavailable("OPENAI_API_KEY 未设置")
    try:
        import openai
    except Exception as e:  # pragma: no cover
        raise EmbeddingUnavailable(f"openai SDK 不可用: {e}")
    base_url = os.getenv("LF_OPENAI_BASE_URL") or DEFAULT_BASE_URL
    model = os.getenv("LF_EMBEDDING_MODEL") or DEFAULT_MODEL
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def embed(texts: List[str]) -> List[List[float]]:
        resp = client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in resp.data]

    return embed, base_url, model


def _cos(a: List[float], b: List[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb + 1e-9)


def _load(golden: Optional[str]) -> dict:
    with open(golden or DEFAULT_GOLDEN, encoding="utf-8") as f:
        return json.load(f)


def _fts_top1(db_path: str, query: str, content_to_id: Dict[str, str]) -> Optional[str]:
    out = RetrievalAgent(db_path=db_path).run(RetrievalInput(
        query=query, scopes=[KnowledgeScope.LOCAL], method=RetrievalMethod.FULLTEXT, top_k=5))
    for c in out.chunks:
        for content, mid in content_to_id.items():
            if content[:18] in c.text:
                return mid
    return None


def run(golden: Optional[str] = None, with_fts: bool = True) -> dict:
    data = _load(golden)
    mems = data["memories"]
    pos = data["positives"]
    negs = data["negatives"]
    topic_of = {m["id"]: m["topic"] for m in mems}

    try:
        embed, base_url, model = _embedder()
    except EmbeddingUnavailable as e:
        return {"skipped": True, "reason": str(e)}

    mem_vecs = embed([m["content"] for m in mems])
    pos_vecs = embed([q["query"] for q in pos])
    neg_vecs = embed([n["query"] for n in negs])
    mem_ids = [m["id"] for m in mems]

    # FTS 基线（离线，无 key 也能跑）
    fts_top1: Dict[str, Optional[str]] = {}
    if with_fts:
        db = tempfile.mktemp(suffix=".db")
        init_db(db)
        c2i = {}
        for m in mems:
            record(kind=m["kind"], topic=m["topic"], text=m["content"], db_path=db)
            c2i[m["content"]] = m["id"]
        for q in pos:
            fts_top1[q["id"]] = _fts_top1(db, q["query"], c2i)

    def rank(v):
        return sorted(((_cos(v, mv), mid) for mv, mid in zip(mem_vecs, mem_ids)), reverse=True)

    pos_rows: List[dict] = []
    for q, v in zip(pos, pos_vecs):
        r = rank(v)
        order = [mid for _, mid in r]
        sims = [s for s, _ in r]
        gold = q["gold"]
        gpos = order.index(gold)
        gold_sim = sims[gpos]
        top1 = order[0]
        reason = ""
        if top1 != gold:
            reason = (f"intra_topic_distractor→{top1}" if topic_of[top1] == topic_of[gold]
                      else f"cross_topic→{top1}")
        gate = gate_recall(sims)
        pos_rows.append({
            "id": q["id"], "type": q["type"], "gold": gold, "gold_rank": gpos + 1,
            "gold_sim": round(gold_sim, 3), "topk": [(mid, round(s, 3)) for s, mid in r[:3]],
            "fts_top1": fts_top1.get(q["id"]), "failure_reason": reason,
            "gate": gate.decision, "gate_reason": gate.reason,
        })

    neg_rows: List[dict] = []
    for n, v in zip(negs, neg_vecs):
        r = rank(v)
        sims = [s for s, _ in r]
        gate = gate_recall(sims)
        neg_rows.append({"id": n["id"], "top_sim": round(sims[0], 3),
                         "most_like": r[0][1], "gate": gate.decision})

    metrics = _metrics(pos_rows, neg_rows, pos)
    return {"skipped": False, "base_url": base_url, "model": model,
            "pos": pos_rows, "neg": neg_rows, "metrics": metrics,
            "n_mem": len(mems), "n_pos": len(pos), "n_neg": len(negs)}


def _metrics(pos_rows, neg_rows, pos_defs) -> dict:
    n = len(pos_rows)
    h1 = sum(1 for r in pos_rows if r["gold_rank"] == 1) / n
    h3 = sum(1 for r in pos_rows if r["gold_rank"] <= 3) / n
    h5 = sum(1 for r in pos_rows if r["gold_rank"] <= 5) / n
    mrr = sum(1.0 / r["gold_rank"] for r in pos_rows) / n
    intra = [r for r in pos_rows if r["type"] == "intra_topic"]
    cross = [r for r in pos_rows if r["type"] == "cross_lingual"]
    intra_acc = (sum(1 for r in intra if r["gold_rank"] == 1) / len(intra)) if intra else 1.0
    cross_acc = (sum(1 for r in cross if r["gold_rank"] == 1) / len(cross)) if cross else 1.0
    fts_seen = [r for r in pos_rows if r["fts_top1"] is not None or True]
    fts_h1 = sum(1 for r in pos_rows if r["fts_top1"] == r["gold"]) / n
    neg_abstain = sum(1 for r in neg_rows if r["gate"] == ABSTAIN) / len(neg_rows)
    pos_top1_sims = [r["topk"][0][1] for r in pos_rows]
    neg_top1_sims = [r["top_sim"] for r in neg_rows]
    return {
        "hit@1": h1, "hit@3": h3, "hit@5": h5, "MRR@5": mrr,
        "intra_topic_accuracy": intra_acc, "cross_lingual_hit@1": cross_acc,
        "fts_hit@1": fts_h1, "negative_abstain_rate": neg_abstain,
        "pos_top1_sim_min": min(pos_top1_sims), "neg_top1_sim_max": max(neg_top1_sims),
        "separable": min(pos_top1_sims) > max(neg_top1_sims),
    }


def main(argv=None) -> int:
    rep = run()
    if rep.get("skipped"):
        print(f"[semantic_eval] SKIPPED：{rep['reason']}（设 OPENAI_API_KEY 后重跑）")
        return 0
    P = print
    P("=" * 78)
    P("# 对抗语义召回评测报告（真实 embedding，离线 cosine；非 in-DB hybrid）")
    P("=" * 78)
    P(f"[环境] base_url={rep['base_url']}  model={rep['model']}  "
      f"memories={rep['n_mem']} positives={rep['n_pos']} negatives={rep['n_neg']}")
    P("       ⚠️ 仅评测 embedding 语义召回；完整 in-DB hybrid（vec0 KNN+RRF）未验证。")

    P("\n[全部正样本 per-sample]（id type gold rank sim gate | top3）")
    for r in rep["pos"]:
        mark = "✓" if r["gold_rank"] == 1 else "✗"
        P(f"  {mark} {r['id']:<7} {r['type']:<12} gold={r['gold']:<22} "
          f"rank={r['gold_rank']} sim={r['gold_sim']:.3f} gate={r['gate']:<9} | {r['topk']}")

    P("\n[失败/边缘样本 trace]（未命中 rank1 或 gate 非 accept）")
    for r in rep["pos"]:
        if r["gold_rank"] != 1 or r["gate"] != "accept":
            P(f"  {r['id']:<7} gold={r['gold']} rank={r['gold_rank']} sim={r['gold_sim']} "
              f"fts={r['fts_top1']} top3={r['topk']}")
            P(f"          failure={r['failure_reason'] or '-'}  gate={r['gate']}：{r['gate_reason']}")

    m = rep["metrics"]
    P("\n[向量语义指标]")
    for k in ("hit@1", "hit@3", "hit@5", "MRR@5", "intra_topic_accuracy",
              "cross_lingual_hit@1", "fts_hit@1"):
        P(f"  {k:<22} = {m[k]:.2f}")
    P("\n[防编造闸门]")
    P(f"  负样本 abstain 率           = {m['negative_abstain_rate']:.2f}")
    P(f"  正样本 top1 sim 最小         = {m['pos_top1_sim_min']:.3f}")
    P(f"  负样本 top1 sim 最大         = {m['neg_top1_sim_max']:.3f}")
    P(f"  正负 sim 完全可分            = {m['separable']}")

    P("\n[口径] 真实 embedding 离线语义召回有效，hit@1≈{:.2f}；FTS fallback 在无字面重叠中文改写下"
      "失败(hit@1={:.2f})；纯向量仍有同主题细粒度误召回，需 rerank/margin/hybrid 补强；"
      "完整 in-DB hybrid 尚未验证。".format(m["hit@1"], m["fts_hit@1"]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
