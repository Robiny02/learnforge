"""RAGAS-like answer quality eval for retrieval modes.

This script compares WORKFLOW_V2 vs REACT after retrieval: retrieved chunks are used
to generate an answer, then a model judge scores faithfulness, relevance, correctness,
and abstention behavior. It uses OpenRouter only when OPENROUTER_API_KEY is set.

用法：
    OPENROUTER_API_KEY=... LF_RETRIEVAL_REACT_LLM=1 \
      python -m learnforge.eval.retrieval_answer_quality_eval
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from ..agents.retrieval import RetrievalAgent
from ..contracts.agents.retrieval import Chunk, RetrievalInput
from ..contracts.enums import KnowledgeScope, RetrievalMethod, RetrievalMode
from ..storage.db import init_db
from .retrieval_mode_eval import DOCS, EvalCase, _norm, all_cases, seed


ANSWER_CASE_NAMES = {
    "semantic-jwt",
    "semantic-lock",
    "semantic-redis-lock",
    "semantic-cache",
    "semantic-mvcc",
    "semantic-index",
    "semantic-kafka",
    "semantic-mq-idem",
    "semantic-threadpool",
    "semantic-tx",
    "neg-go",
    "neg-rust",
    "neg-redis-cluster",
    "edge-injection",
    "edge-math",
    "real-jwt-memory",
    "real-redis-lock",
    "real-index-noise",
    "real-threadpool",
    "real-yesterday-mock-weakness",
    "real-last-mock-bad-answer",
    "real-recent-weakness-summary",
    "real-what-to-review",
    "real-negative-rust",
    "real-negative-redis-cluster",
    "real-negative-frontend",
}


def run_eval(db_path: Optional[str] = None, memory_root: Optional[str] = None) -> dict:
    root = memory_root or tempfile.mkdtemp(prefix="lf-answer-quality-mem-")
    db = db_path or tempfile.mktemp(suffix=".db")
    init_db(db)
    content_to_id = seed(db, root)
    id_to_text = {d.doc_id: d.text for d in DOCS}

    rows = []
    cases = [c for c in all_cases() if c.name in ANSWER_CASE_NAMES]
    for case in cases:
        row = {"case": case.name, "query": case.query, "gold": case.gold}
        for mode in (RetrievalMode.WORKFLOW_V2, RetrievalMode.REACT):
            chunks = _retrieve(db, case, mode)
            context_ids = [content_to_id.get(_norm(c.text), "?") for c in chunks]
            answer = _generate_answer(case.query, chunks)
            judge = _judge_answer(
                case=case,
                answer=answer,
                context_ids=context_ids,
                chunks=chunks,
                gold_text=id_to_text.get(case.gold or ""),
            )
            row[mode.value] = {
                "context_ids": context_ids,
                "answer": answer,
                "judge": judge,
            }
        rows.append(row)
    return {"rows": rows, "metrics": _aggregate(rows)}


def _retrieve(db_path: str, case: EvalCase, mode: RetrievalMode) -> List[Chunk]:
    out = RetrievalAgent(db_path=db_path).run(
        RetrievalInput(
            query=case.query,
            scopes=[KnowledgeScope.LOCAL],
            method=RetrievalMethod.FULLTEXT,
            mode=mode,
            top_k=3,
        )
    )
    return out.chunks


def _generate_answer(query: str, chunks: List[Chunk]) -> str:
    context = _format_context(chunks)
    prompt = (
        "你是 LearnForge 的学习记忆助手。只基于给定检索上下文回答用户。"
        "如果上下文为空，或明显和用户问题无关，请回答“没有找到明确记忆”。"
        "如果上下文里有相关学习记录、薄弱点、mock 反馈或项目决策，即使措辞不同，"
        "也要基于上下文总结，不要过度拒答；不要用常识补全。\n\n"
        f"用户问题：{query}\n\n检索上下文：\n{context}\n\n请用中文简洁回答。"
    )
    return _chat(prompt, max_tokens=220)


def _judge_answer(
    case: EvalCase,
    answer: str,
    context_ids: List[str],
    chunks: List[Chunk],
    gold_text: Optional[str],
) -> dict:
    context = _format_context(chunks)
    gold = gold_text or "NEGATIVE_CASE: 应该承认没有找到明确记忆。"
    prompt = (
        "You are a strict RAG answer-quality judge. Return JSON only with fields: "
        "faithfulness, relevance, correctness (floats 0..1), abstained (boolean), notes.\n"
        "Faithfulness: answer is supported by retrieved context. Relevance: answer addresses query. "
        "Correctness: for positive cases, answer matches gold memory; for negative cases, answer abstains.\n\n"
        f"case={case.name}\n"
        f"should_recall={case.should_recall}\n"
        f"query={case.query}\n"
        f"retrieved_ids={context_ids}\n"
        f"retrieved_context=\n{context}\n\n"
        f"gold_memory=\n{gold}\n\n"
        f"answer=\n{answer}\n"
    )
    raw = _chat(prompt, max_tokens=180)
    try:
        data = json.loads(_extract_json_object(raw))
    except Exception:
        data = {"faithfulness": 0.0, "relevance": 0.0, "correctness": 0.0,
                "abstained": False, "notes": "judge_parse_failed"}
    abstained = bool(data.get("abstained")) or _answer_abstained(answer)
    return {
        "faithfulness": _clamp_float(data.get("faithfulness")),
        "relevance": _clamp_float(data.get("relevance")),
        "correctness": _clamp_float(data.get("correctness")),
        "abstained": abstained,
        "notes": str(data.get("notes", ""))[:160],
    }


def _aggregate(rows: List[dict]) -> dict:
    out: Dict[str, dict] = {}
    for mode in (RetrievalMode.WORKFLOW_V2.value, RetrievalMode.REACT.value):
        judges = [r[mode]["judge"] for r in rows]
        positives = [r for r in rows if r["gold"]]
        negatives = [r for r in rows if not r["gold"]]
        out[mode] = {
            "faithfulness": _mean([j["faithfulness"] for j in judges]),
            "relevance": _mean([j["relevance"] for j in judges]),
            "correctness": _mean([j["correctness"] for j in judges]),
            "positive_correctness": _mean([r[mode]["judge"]["correctness"] for r in positives]),
            "negative_abstain_rate": _mean([
                1.0 if r[mode]["judge"]["abstained"] else 0.0 for r in negatives
            ]),
        }
    return out


def _format_context(chunks: List[Chunk]) -> str:
    if not chunks:
        return "(empty)"
    lines = []
    for i, c in enumerate(chunks, 1):
        lines.append(f"[{i}] {c.text[:500]}")
    return "\n".join(lines)


def _answer_abstained(answer: str) -> bool:
    text = (answer or "").strip()
    markers = ["没有找到明确记忆", "没有相关记忆", "未找到明确记忆", "未找到相关记忆"]
    return any(m in text for m in markers)


def _chat(prompt: str, max_tokens: int) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for answer quality eval")
    model = os.getenv("LF_OPENROUTER_MODEL", "openai/gpt-4o-mini")
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://learnforge.local",
            "X-Title": "LearnForge Answer Quality Eval",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - explicit API endpoint
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        raise RuntimeError(f"OpenRouter request failed: {e}") from e
    return str(payload["choices"][0]["message"]["content"])


def _extract_json_object(text: str) -> str:
    raw = (text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start:end + 1]
    return raw


def _clamp_float(value) -> float:
    try:
        x = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, x))


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 1.0


def main() -> int:
    report = run_eval()
    print("# Retrieval Answer Quality Eval")
    print("\n[rows]")
    for row in report["rows"]:
        parts = []
        for mode in (RetrievalMode.WORKFLOW_V2.value, RetrievalMode.REACT.value):
            j = row[mode]["judge"]
            parts.append(
                f"{mode}: ids={row[mode]['context_ids']} "
                f"faith={j['faithfulness']:.2f} rel={j['relevance']:.2f} "
                f"corr={j['correctness']:.2f} abstain={j['abstained']}"
            )
        print(f"- {row['case']} gold={row['gold'] or '-'} | " + " | ".join(parts))
    print("\n[metrics]")
    for mode, metrics in report["metrics"].items():
        print(
            f"- {mode}: faithfulness={metrics['faithfulness']:.2f}, "
            f"relevance={metrics['relevance']:.2f}, correctness={metrics['correctness']:.2f}, "
            f"positive_correctness={metrics['positive_correctness']:.2f}, "
            f"negative_abstain_rate={metrics['negative_abstain_rate']:.2f}"
        )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
