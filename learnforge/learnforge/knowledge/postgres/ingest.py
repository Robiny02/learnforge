"""PG 知识库本地入库管线：JSONL / Markdown fixture → chunk → embed → PostgreSQL。

先用本地 fixture 跑通，不接爬虫（用户要求）。embedding 经 EMBEDDINGS（无 key → Null →
embedding 为空，向量列写 NULL，向量检索自然降级）。每批写 ingest_runs，文档级异常写 source_errors。

CLI：
  python -m learnforge.knowledge.postgres.ingest --jsonl path/to/docs.jsonl
  python -m learnforge.knowledge.postgres.ingest --markdown path/to/doc.md --topic concurrency
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from ...llm.embeddings import embed_query
from ..ingest import chunk_text
from .db import pg_available
from .repository import PgChunkRepository


def load_jsonl(path: str) -> List[dict]:
    """每行一个文档 dict：{title, text, source_type, topic, source_url, heading_path}。"""
    docs: List[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            docs.append(json.loads(line))
    return docs


def load_markdown(path: str, topic: str = "", source_type: str = "doc") -> List[dict]:
    """把一个 .md 文件按一级/二级标题切成多个"文档"，heading_path 记录标题层级。"""
    text = Path(path).read_text(encoding="utf-8")
    docs: List[dict] = []
    title = Path(path).stem
    heading = title
    buf: List[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            docs.append({
                "title": title, "heading_path": heading, "text": body,
                "topic": topic, "source_type": source_type, "source_url": str(path),
            })

    for raw in text.splitlines():
        if raw.startswith("#"):
            flush()
            buf = []
            heading = raw.lstrip("#").strip() or title
        else:
            buf.append(raw)
    flush()
    return docs


def ingest_documents_pg(
    docs: List[dict], dsn: Optional[str] = None, source: str = "fixture",
    repo: Optional[PgChunkRepository] = None,
) -> Dict[str, int]:
    """文档列表 → 切片 → 向量化 → 入库。返回 {documents, chunks, errors}。"""
    repo = repo or PgChunkRepository(dsn=dsn)
    run_id = str(uuid.uuid4())
    repo.start_run(run_id, source)
    n_docs = n_chunks = n_errors = 0
    for doc in docs:
        text = doc.get("text", "")
        if not text:
            continue
        try:
            doc_id = str(uuid.uuid4())
            repo.upsert_document(
                doc_id=doc_id,
                source_url=doc.get("source_url", ""),
                title=doc.get("title", ""),
                source_type=doc.get("source_type", "doc"),
                topic=doc.get("topic", ""),
                metadata=doc.get("metadata") or {},
            )
            for i, piece in enumerate(chunk_text(text)):
                emb = embed_query(piece)
                repo.upsert_chunk(
                    chunk_id=str(uuid.uuid4()),
                    content=piece,
                    document_id=doc_id,
                    source_url=doc.get("source_url", ""),
                    title=doc.get("title", ""),
                    heading_path=doc.get("heading_path", ""),
                    topic=doc.get("topic", ""),
                    source_type=doc.get("source_type", "doc"),
                    metadata={"chunk_index": i, **(doc.get("metadata") or {})},
                    embedding=emb,
                )
                n_chunks += 1
            n_docs += 1
        except Exception as exc:  # 文档级隔离：单篇失败不阻断整批。
            n_errors += 1
            repo.record_error(str(uuid.uuid4()), run_id, doc.get("source_url", ""), str(exc))
    repo.finish_run(run_id, n_docs, n_chunks, n_errors,
                    status="ok" if n_errors == 0 else "partial")
    return {"documents": n_docs, "chunks": n_chunks, "errors": n_errors}


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="LearnForge PostgreSQL knowledge ingestion")
    parser.add_argument("--jsonl", help="JSONL file: one document per line")
    parser.add_argument("--markdown", help="Markdown file")
    parser.add_argument("--topic", default="", help="topic for markdown ingestion")
    args = parser.parse_args(argv)

    if not pg_available():
        print("PostgreSQL 不可用：请设置 LF_POSTGRES_DSN 并安装 psycopg。")
        return
    repo = PgChunkRepository()
    docs: List[dict] = []
    if args.jsonl:
        docs += load_jsonl(args.jsonl)
    if args.markdown:
        docs += load_markdown(args.markdown, topic=args.topic)
    if not docs:
        print("无输入文档。用 --jsonl / --markdown 指定 fixture。")
        return
    stats = ingest_documents_pg(docs, repo=repo)
    print(f"入库完成：{stats}；关键词后端={repo.keyword_backend()}")


if __name__ == "__main__":
    main()
