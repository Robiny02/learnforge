# Knowledge Retrieval — Reference

源: [learnforge/learnforge/agents/retrieval.py](../../../learnforge/learnforge/agents/retrieval.py)

## RetrievalMethod 语义

| method | 说明 | 依赖 |
|---|---|---|
| `keyword` | 关键词匹配 | — |
| `fulltext` | FTS5 BM25 全文 | sqlite FTS5 |
| `vector` | sqlite-vec KNN 向量 | embedding + sqlite-vec |
| `hybrid`(默认) | FTS + vector,RRF 融合 | 同上,缺则降级 |

## KnowledgeScope ↔ 知识源

| scope | 源 | 内容 |
|---|---|---|
| `LOCAL`(本地用户库) | `LocalUserSource` | `mock_turns`、`qa_history` |
| `SHARED`(共享知识库) | `LocalSharedSource` / `RemoteSharedSource` | `chunks`、`knowledge_atoms` |

## 流程与降级矩阵

```
1. _maybe_rewrite: len(query) > 120 且 LLM 可用 → Haiku 改写一次,否则原样
2. method ∈ {vector,hybrid} → embed_query(); embedding is None →
       degraded=True; method==vector 时降级为 fulltext
3. 各 source.search(query, method, filters, top_k, embedding); 异常 → 该源 [] 且 degraded=True
4. rrf_fuse(ranked_lists, k=60); rerank 时先取 top_k*2
5. rerank? LLMReranker(若 available) else NoOpReranker → top_k; 否则直接截 top_k
6. 返回 RetrievalOutput(chunks, method_used, degraded)
```

`RetrievalFilters`: topic / source_type / kb_scope / difficulty / source_name。

## RRF 融合

```
RRF(k=60): score(doc) = Σ 1 / (k + rank_in_list)
```
跨多源 ranked_lists 融合后排序。

## 扩展点

```python
agent = RetrievalAgent(db_path)
agent.register_source(MyCustomSource())           # 运行时加知识源
RetrievalAgent(db_path, reranker=MyReranker())    # 自定义重排
```

## 预留工具接口(占位 schema)

```python
ToolSpec(
    name="kb.search",
    description="远程共享知识库检索(MCP,对齐 LF_REMOTE_KB_URL);离线不参与",
    server="learnforge.remote",
    # input:  {query, method, filters, top_k, embedding?}
    # output: {chunks: [{chunk_id, text, source_type, metadata}]}
)
ToolSpec(
    name="embed.query",
    description="查询向量化(EmbeddingProvider);无 key 返回 None 触发降级",
    server="learnforge.local",
    # input:  {query}
    # output: {embedding: [float] | null}
)
ToolSpec(
    name="rerank",
    description="对候选片段重排",
    server="learnforge.local",
    # input:  {query, chunks, top_k}
    # output: {chunks}
)
```

## 相关测试

- `tests/test_retrieval.py` — 检索/融合/降级结构契约。
