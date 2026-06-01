"""Semantic chunker (Plan §4.3).

Groups adjacent blocks by topic: merge consecutive blocks sharing a topic bucket
until the char budget is reached; break on topic change. Atomic blocks
(code/table/image_ocr) are never split.

Two modes (reported via metadata `semantic_mode`):
- `embedding`: when an embedding provider is configured (Phase 2/3) — cosine
  break between adjacent blocks. (Hook present; falls back today.)
- `lexical`: offline default — topic buckets via a tech keyword dictionary +
  word-overlap, fully deterministic.
"""

from __future__ import annotations

from typing import List, Optional

from ..lib.block_split import split_document
from ..lib.schema import Block, Chunk, Document
from .base import MAX_CHARS, TARGET_CHARS, make_chunk, window_chars

NAME = "semantic"

# Topic buckets (Plan §4.3). Order matters only for tie reporting.
TOPIC_KEYWORDS = {
    "redis": ["redis", "缓存", "击穿", "穿透", "雪崩", "热 key", "热key", "分布式锁", "redisson", "看门狗", "io 多路复用", "持久化"],
    "mysql": ["mysql", "索引", "事务", "隔离级别", "mvcc", "undo", "readview", "间隙锁", "explain", "最左前缀", "幻读", "binlog"],
    "jvm": ["jvm", "gc", "垃圾回收", "gc roots", "堆", "栈", "类加载", "可达性", "synchronized", "内存模型"],
    "juc": ["juc", "concurrenthashmap", "volatile", "reentrantlock", "线程安全", "原子性", "cas", "aqs", "线程池"],
    "network": ["tcp", "https", "tls", "握手", "对称加密", "非对称", "会话密钥", "http", "三次握手", "四次挥手"],
    "mq": ["mq", "rocketmq", "kafka", "消息队列", "幂等", "削峰", "消息不丢", "回调", "状态机", "库存"],
    "agent_rag": ["agent", "rag", "mcp", "sse", "websocket", "向量", "嵌入", "检索", "分割", "流式", "skill"],
    "algorithm": ["手撕", "leetcode", "滑动窗口", "单调队列", "最长子串", "复杂度", "双指针", "动态规划", "算法"],
    "project": ["项目", "追问", "架构", "登录", "jwt", "token", "秒杀", "扣减"],
}


def _bucket(block: Block) -> str:
    if block.structure_type == "comment":
        return "comment"
    if block.structure_type in ("ad", "tags"):
        return "meta"
    low = block.text.lower()
    best, best_score = "other", 0
    for topic, kws in TOPIC_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in low)
        if score > best_score:
            best, best_score = topic, score
    return best


def _flush(doc, buf_blocks, buf_topic, out, mode):
    if not buf_blocks:
        return
    text = "\n\n".join(b.text for b in buf_blocks).strip()
    if not text:
        return
    heading = buf_blocks[0].heading_path
    for piece in ([text] if len(text) <= MAX_CHARS else window_chars(text)):
        out.append(
            make_chunk(
                doc.doc_id,
                doc.source_type,
                piece,
                NAME,
                structure_type="semantic_topic",
                heading_path=heading,
                metadata={"topic": buf_topic, "semantic_mode": mode, "n_blocks": len(buf_blocks)},
            )
        )


def chunk(doc: Document, embedder: Optional[object] = None) -> List[Chunk]:
    blocks = doc.blocks or split_document(doc)
    if not blocks:
        return []
    mode = "embedding" if getattr(embedder, "available", False) else "lexical"

    out: List[Chunk] = []
    buf: List[Block] = []
    buf_topic = None
    buf_len = 0
    for b in blocks:
        if b.structure_type == "heading" and b.char_len < 40:
            # headings anchor topic but aren't standalone chunks
            continue
        topic = _bucket(b)
        same = (buf_topic is None) or (topic == buf_topic) or (topic == "other")
        if buf and (not same or buf_len + b.char_len > TARGET_CHARS):
            _flush(doc, buf, buf_topic, out, mode)
            buf, buf_len = [], 0
            buf_topic = None
        buf.append(b)
        buf_len += b.char_len
        if buf_topic is None and topic != "other":
            buf_topic = topic
    _flush(doc, buf, buf_topic or "other", out, mode)
    return out
