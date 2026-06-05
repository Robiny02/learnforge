"""多策略切片对比：fixed / structural / semantic / llamaindex / llm。

为"质量不一的真实面经"语料对比不同切块方法的产物，辅助选型。每个策略输出 `List[Piece]`，
Piece.text 是切块文本、Piece.meta 带策略与序号，便于落 `chunks` 表与人工评估。

策略：
- **fixed**：定长滑窗（复用 ingest.chunk_text，512 token / 128 overlap）——基线。
- **structural**：按 Markdown 标题 / 标签块（"一面问题：" 这类）切；裸 OCR 按空行+编号边界切。
- **semantic**：句/条向量化 → 相邻 cosine 距离断点（标准语义切块），用本项目 EMBEDDINGS。
- **llamaindex_sentence / llamaindex_semantic**：LlamaIndex 两种 NodeParser（可选依赖）。
- **llm**：LLM 面经感知切块（同主题问题归组、去口水话、加【公司·岗位·轮次】头）。

离线/缺依赖优雅降级：无 embedding key → semantic/llamaindex_semantic 跳过；无 LLM → llm 跳过；
未装 llama-index → llamaindex_* 跳过。CLI：`python -m learnforge.knowledge.chunking --file x.md`。
"""

from __future__ import annotations

import argparse
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .ingest import chunk_text as _fixed_window


@dataclass
class Piece:
    text: str
    method: str
    meta: Dict = field(default_factory=dict)


# ----------------------------------------------------------------- 分句/分段
_SENT_END = re.compile(r"(?<=[。！？!?；;])")


def _segments(text: str) -> List[str]:
    """原子语义片：每个非空行为一个单元；长散文行再按中文句末标点切。

    面经里 bullet/编号行本身就是一道题（天然语义单元），故按行优先，长行再细分。
    """
    segs: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(line) <= 60:
            segs.append(line)
            continue
        parts = [p.strip() for p in _SENT_END.split(line) if p.strip()]
        segs.extend(parts or [line])
    return segs


# ----------------------------------------------------------------- 1) fixed
def fixed_chunks(text: str, size: int = 512, overlap: int = 128) -> List[Piece]:
    return [Piece(t, "fixed", {"i": i}) for i, t in enumerate(_fixed_window(text, size, overlap))]


# ----------------------------------------------------------------- 2) structural
_LABEL = re.compile(r"^[#＃]{0,4}\s*([^\n：:]{1,24})[：:]\s*$")  # "一面问题：" / "## Note 1: ..."
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def structural_chunks(text: str, max_chars: int = 700) -> List[Piece]:
    """按文档结构切：Markdown 标题/标签块；裸文本按空行+编号边界聚段。"""
    has_md = bool(re.search(r"^#{1,6}\s|\n#{1,6}\s", text or ""))
    pieces = _structural_md(text, max_chars) if has_md else _structural_plain(text, max_chars)
    return [Piece(p["text"], "structural", {"i": i, "title": p.get("title", "")})
            for i, p in enumerate(pieces) if p["text"].strip()]


def _structural_md(text: str, max_chars: int) -> List[dict]:
    """以最近的 `##` 标题为上下文头，按 `###`/标签块切；块过长再在条目边界断。"""
    out: List[dict] = []
    note_title = ""
    buf: List[str] = []
    buf_title = ""

    def flush():
        if buf:
            body = "\n".join(buf).strip()
            if body:
                head = f"【{note_title}】" if note_title else ""
                out.append({"title": buf_title or note_title,
                            "text": (head + "\n" + body).strip() if head else body})
        buf.clear()

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        h = _HEADING.match(line)
        if h:
            level, title = len(h.group(1)), h.group(2).strip()
            flush()
            if level <= 2:
                note_title = title
                buf_title = title
            else:
                buf_title = title
            continue
        lbl = _LABEL.match(line)
        if lbl and buf:           # 新标签块 → 断
            flush()
            buf_title = lbl.group(1)
        buf.append(line)
        if sum(len(x) for x in buf) >= max_chars:  # 块过长 → 在当前条目边界断
            flush()
    flush()
    return out


def _structural_plain(text: str, max_chars: int) -> List[dict]:
    """裸 OCR/正文：空行分段；编号行(1. 2.)视作新条目；小段聚合到 max_chars。"""
    blocks: List[str] = []
    cur: List[str] = []

    def _flush():
        if cur:
            blocks.append("\n".join(cur))
            cur.clear()

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            _flush()
            continue
        if re.match(r"^\d+[.、)]", line) and cur and sum(len(x) for x in cur) > 80:
            _flush()
        cur.append(line)
        if sum(len(x) for x in cur) >= max_chars:
            _flush()
    if cur:
        blocks.append("\n".join(cur))
    return [{"title": "", "text": b} for b in blocks]


# ----------------------------------------------------------------- 3) semantic
def _cos(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def semantic_chunks(text: str, pct: int = 88, max_chars: int = 700) -> List[Piece]:
    """语义切块：相邻片 cosine 距离 > 分布的 pct 分位 → 断点（标准 semantic chunking）。"""
    from ..llm.embeddings import EMBEDDINGS

    segs = _segments(text)
    if len(segs) < 2 or not getattr(EMBEDDINGS, "available", False):
        return []
    vecs = EMBEDDINGS.embed(segs)
    if len(vecs) != len(segs):
        return []
    dists = [1.0 - _cos(vecs[i], vecs[i + 1]) for i in range(len(segs) - 1)]
    ordered = sorted(dists)
    thr = ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]

    pieces: List[Piece] = []
    buf = [segs[0]]
    for i in range(1, len(segs)):
        cut = dists[i - 1] >= thr or sum(len(x) for x in buf) >= max_chars
        if cut:
            pieces.append(Piece("\n".join(buf), "semantic", {"i": len(pieces)}))
            buf = []
        buf.append(segs[i])
    if buf:
        pieces.append(Piece("\n".join(buf), "semantic", {"i": len(pieces)}))
    return pieces


# ----------------------------------------------------------------- 4) llamaindex
def llamaindex_chunks(text: str) -> Dict[str, List[Piece]]:
    """LlamaIndex 两种解析器：SentenceSplitter（定长句界）+ SemanticSplitter（向量断点）。"""
    out: Dict[str, List[Piece]] = {}
    try:
        from llama_index.core import Document
        from llama_index.core.node_parser import SentenceSplitter
    except Exception:
        return out
    doc = Document(text=text)
    try:
        ss = SentenceSplitter(chunk_size=256, chunk_overlap=32)
        out["llamaindex_sentence"] = [
            Piece(n.get_content(), "llamaindex_sentence", {"i": i})
            for i, n in enumerate(ss.get_nodes_from_documents([doc]))
        ]
    except Exception:
        pass
    try:
        from llama_index.core.node_parser import SemanticSplitterNodeParser
        from llama_index.embeddings.openai import OpenAIEmbedding

        emb = OpenAIEmbedding(
            model_name=os.getenv("LF_EMBEDDING_MODEL", "openai/text-embedding-3-small"),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            api_base=os.environ.get("LF_OPENAI_BASE_URL"),
            dimensions=int(os.getenv("LF_EMBEDDING_DIM", "1024")),
        )
        sem = SemanticSplitterNodeParser(
            buffer_size=1, breakpoint_percentile_threshold=88, embed_model=emb)
        out["llamaindex_semantic"] = [
            Piece(n.get_content(), "llamaindex_semantic", {"i": i})
            for i, n in enumerate(sem.get_nodes_from_documents([doc]))
        ]
    except Exception:
        pass
    return out


# ----------------------------------------------------------------- 5) llm
_LLM_SYS = (
    "你是面经结构化助手。把输入切成若干**自包含、可独立检索**的知识块，专为面试题检索优化。"
)
_LLM_PROMPT = (
    "把下面的面经切块，规则：\n"
    "1) 同一轮面试里**同主题**的问题归到一块（如『缓存击穿/穿透/雪崩+布隆过滤器』一块、"
    "『GC/G1/ThreadLocal/ConcurrentHashMap』一块）；项目追问链单独成块。\n"
    "2) **去掉口水话/情绪/营销词**，只保留事实与问题原文。\n"
    "3) 每块开头加一行上下文头【公司·岗位·轮次】（识别不到就省略）。\n"
    "4) offer 截图/封面/纯照片之类无信息内容不要产出成块。\n"
    "输出 JSON：{\"chunks\":[{\"title\":\"...\",\"text\":\"...\"}]}。只输出 JSON。\n\n面经：\n"
)


def llm_chunks(text: str) -> List[Piece]:
    from ..contracts.enums import ModelTier
    from ..llm.client import LLM

    if not getattr(LLM, "available", False):
        return []
    try:
        res = LLM.complete(
            prompt=_LLM_PROMPT + text[:12000],
            model_tier=ModelTier.SONNET, system=_LLM_SYS,
            max_tokens=4000, response_format={"type": "json_object"},
        )
        import json

        data = json.loads(_extract_json(res.text))
        chunks = data.get("chunks") or []
    except Exception:
        return []
    pieces: List[Piece] = []
    for i, c in enumerate(chunks):
        body = str(c.get("text", "")).strip()
        if not body:
            continue
        title = str(c.get("title", "")).strip()
        head = f"【{title}】\n" if title and title not in body else ""
        pieces.append(Piece(head + body, "llm", {"i": i, "title": title}))
    return pieces


def _extract_json(s: str) -> str:
    s = (s or "").strip()
    a, b = s.find("{"), s.rfind("}")
    return s[a:b + 1] if a >= 0 and b > a else s


# ----------------------------------------------------------------- runner
def run_all(text: str) -> Dict[str, List[Piece]]:
    out: Dict[str, List[Piece]] = {
        "fixed": fixed_chunks(text),
        "structural": structural_chunks(text),
        "semantic": semantic_chunks(text),
        "llm": llm_chunks(text),
    }
    out.update(llamaindex_chunks(text))
    return {k: v for k, v in out.items() if v}


def _stats(pieces: List[Piece]) -> Dict[str, float]:
    lens = [len(p.text) for p in pieces] or [0]
    return {"n": len(pieces), "min": min(lens), "max": max(lens),
            "avg": round(sum(lens) / len(lens), 1)}


def _main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="面经多策略切片对比")
    ap.add_argument("--file", required=True, help="输入语料（md / 纯文本 / OCR）")
    ap.add_argument("--out", default=None, help="各策略切块写出目录（每策略一个 .md）")
    args = ap.parse_args(argv)

    with open(args.file, "r", encoding="utf-8") as f:
        text = f.read()
    results = run_all(text)

    print(f"\n语料：{args.file}（{len(text)} 字）\n")
    print(f"{'策略':<22}{'块数':>6}{'最小':>8}{'平均':>8}{'最大':>8}")
    print("-" * 52)
    order = ["fixed", "structural", "semantic", "llamaindex_sentence",
             "llamaindex_semantic", "llm"]
    for name in [m for m in order if m in results] + [m for m in results if m not in order]:
        s = _stats(results[name])
        print(f"{name:<22}{s['n']:>6}{s['min']:>8}{s['avg']:>8.0f}{s['max']:>8}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        base = os.path.splitext(os.path.basename(args.file))[0]
        for name, pieces in results.items():
            path = os.path.join(args.out, f"{base}__{name}.md")
            with open(path, "w", encoding="utf-8") as f:
                for i, p in enumerate(pieces):
                    f.write(f"### chunk {i} ({len(p.text)}字)"
                            f"{' · ' + p.meta.get('title','') if p.meta.get('title') else ''}\n")
                    f.write(p.text.strip() + "\n\n")
        print(f"\n已写出各策略切块到 {args.out}/")


if __name__ == "__main__":
    _main()
