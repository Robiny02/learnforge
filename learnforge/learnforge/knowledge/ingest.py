"""共享知识库灌库管线（Design §7c）。

流程：文档 → 语义切片（约 512 token / 128 overlap，面经父子 chunk）→ 写 `chunks`
→ 同步 `chunk_fts` →（embedding 可用时）写 `chunk_vectors`；并提供把 `knowledge_atoms`
同步到 `atom_fts` / `atom_vectors` 的能力。

约束：本管线只写知识内容与索引，绝不写 mastery，保持 ManagerAgent 唯一写者约束。
无 embedding provider 时，仅建 FTS 索引（"链路永远通"，向量为可选增强）。

CLI：
    python -m learnforge.knowledge.ingest --file docs.json [--db path.db]
    python -m learnforge.knowledge.ingest --sync-atoms [--db path.db]

docs.json 形如：
    [{"text": "...", "source_type": "doc", "topic": "concurrency",
      "kb_scope": "shared", "metadata": {"title": "..."}}]
"""

from __future__ import annotations

import argparse
import json
import uuid
from typing import Dict, List, Optional

from ..llm.embeddings import EMBEDDINGS
from ..storage.repositories import AtomRepository, ChunkRepository

# 切片参数（Design §7c：512 token / 128 overlap，按空白近似 token）。
CHUNK_SIZE = 512
CHUNK_OVERLAP = 128


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """把长文本切成带重叠的片段。

    以空白分词近似 token；若几乎无空白（如中文），退化为字符窗口，保证 CJK 也可切片。
    """
    text = text.strip()
    if not text:
        return []
    tokens = text.split()
    # CJK / 无空白文本：token 数远小于字符数时，按字符窗口切。
    if len(tokens) < max(1, len(text) // 50):
        step = max(1, size - overlap)
        return [text[i:i + size] for i in range(0, len(text), step)] or [text]
    if len(tokens) <= size:
        return [" ".join(tokens)]
    step = max(1, size - overlap)
    pieces: List[str] = []
    for i in range(0, len(tokens), step):
        window = tokens[i:i + size]
        if window:
            pieces.append(" ".join(window))
        if i + size >= len(tokens):
            break
    return pieces


def _embed_batch(texts: List[str]) -> List[Optional[List[float]]]:
    """批量编码；不可用/失败时返回与 texts 等长的 None 列表。"""
    if not getattr(EMBEDDINGS, "available", False) or not texts:
        return [None] * len(texts)
    vecs = EMBEDDINGS.embed(texts)
    if len(vecs) != len(texts):
        return [None] * len(texts)
    return list(vecs)


def ingest_document(
    text: str,
    source_type: str = "doc",
    topic: Optional[str] = None,
    kb_scope: str = "shared",
    metadata: Optional[Dict] = None,
    parent_chunk_id: Optional[str] = None,
    db_path: Optional[str] = None,
    repo: Optional[ChunkRepository] = None,
) -> List[str]:
    """切片并灌入单篇文档，返回新建 chunk_id 列表。"""
    repo = repo or ChunkRepository(db_path=db_path)
    pieces = chunk_text(text)
    embeddings = _embed_batch(pieces)
    chunk_ids: List[str] = []
    for idx, (piece, emb) in enumerate(zip(pieces, embeddings)):
        chunk_id = str(uuid.uuid4())
        meta = dict(metadata or {})
        meta.setdefault("chunk_index", idx)
        repo.upsert(
            chunk_id=chunk_id,
            text=piece,
            source_type=source_type,
            topic=topic,
            kb_scope=kb_scope,
            metadata=meta,
            parent_chunk_id=parent_chunk_id,
            token_count=len(piece.split()),
            embedding=emb,
        )
        chunk_ids.append(chunk_id)
    return chunk_ids


def ingest_documents(docs: List[Dict], db_path: Optional[str] = None) -> int:
    """批量灌库；返回写入的 chunk 总数。docs 每项见模块 docstring。"""
    repo = ChunkRepository(db_path=db_path)
    total = 0
    for doc in docs:
        ids = ingest_document(
            text=doc["text"],
            source_type=doc.get("source_type", "doc"),
            topic=doc.get("topic"),
            kb_scope=doc.get("kb_scope", "shared"),
            metadata=doc.get("metadata"),
            parent_chunk_id=doc.get("parent_chunk_id"),
            repo=repo,
        )
        total += len(ids)
    return total


def sync_atom_index(db_path: Optional[str] = None, topic: Optional[str] = None) -> int:
    """把 knowledge_atoms 同步到 atom_fts（+ embedding 时 atom_vectors）。返回同步数量。

    仅写检索索引，不触碰 mastery。无 topic 时遍历全部 topic。
    """
    ar = AtomRepository(db_path=db_path)
    if topic:
        atoms = ar.list_by_topic(topic, limit=100000)
    else:
        rows = ar.conn.execute("SELECT DISTINCT topic FROM knowledge_atoms").fetchall()
        atoms = []
        for r in rows:
            atoms.extend(ar.list_by_topic(r["topic"], limit=100000))
    texts = [
        " ".join(filter(None, [a.canonical_question, a.canonical_answer])) for a in atoms
    ]
    embeddings = _embed_batch(texts)
    for atom, emb in zip(atoms, embeddings):
        ar.sync_index(atom, embedding=emb)
    return len(atoms)


def _main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="LearnForge 共享知识库灌库")
    parser.add_argument("--file", help="待灌库的文档 JSON 文件路径")
    parser.add_argument("--db", help="目标 SQLite 路径（默认 config.DB_PATH）")
    parser.add_argument("--sync-atoms", action="store_true", help="同步 atom FTS/向量索引")
    parser.add_argument("--topic", help="仅同步指定 topic 的 atom（配合 --sync-atoms）")
    args = parser.parse_args(argv)

    if not getattr(EMBEDDINGS, "available", False):
        print("[ingest] embedding provider 不可用，仅建 FTS 索引（向量分路跳过）。")

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            docs = json.load(f)
        n = ingest_documents(docs, db_path=args.db)
        print(f"[ingest] 灌入 {n} 个 chunk。")

    if args.sync_atoms:
        n = sync_atom_index(db_path=args.db, topic=args.topic)
        print(f"[ingest] 同步 {n} 个 atom 索引。")

    if not args.file and not args.sync_atoms:
        parser.print_help()


if __name__ == "__main__":
    _main()
