---
name: knowledge-retrieval
description: >-
  LearnForge 统一检索入口(RetrievalAgent,双层知识库 + 可插拔 RAG:keyword/fulltext/vector/hybrid
  + RRF 融合 + 可选 rerank)。当需要从本地用户库/共享知识库召回片段、调试检索质量、配置
  embedding/rerank/远程 KB,或其他 skill 需要 retrieval.search 时使用。只召回排序,不合成、不写状态。
allowed-tools:
  - kb.search
  - embed.query
  - rerank
---

# Knowledge Retrieval(统一检索入口)

驱动 `RetrievalAgent`([learnforge/learnforge/agents/retrieval.py](../../../learnforge/learnforge/agents/retrieval.py))。
两层知识库经 `KnowledgeScope` 选择;**只做召回 + 排序**,不合成、不写任何学习状态。

## 命令

```bash
cd learnforge

# 检索测试
pytest tests/test_retrieval.py

# 向库内灌数据(chunk→embed→写 chunks/chunk_fts/chunk_vectors)
python -m learnforge.knowledge.ingest --file docs.json
python -m learnforge.knowledge.ingest --sync-atoms
```

环境变量: `LF_EMBEDDING_PROVIDER ∈ {none,openai,voyage}`、`LF_EMBEDDING_DIM`(默认 1024)、`LF_REMOTE_KB_URL`(远程共享库)。

## 子能力与工具职责

- **KnowledgeSource**(可插拔知识源): `LocalUserSource`(本地用户库:mock_turns/qa_history)、`LocalSharedSource`(共享库:chunks/atoms)、`RemoteSharedSource`(远程,离线则不参与)。`register_source()` 可运行时扩展。
- **EmbeddingProvider**: `vector/hybrid` 需向量;无 key → 向量降级 FTS。
- **Reranker**: 默认 `NoOpReranker`;`LLMReranker` 可用时优先。
- **预留工具**(schema 见 [reference.md](reference.md)):
  - `kb.search` — 远程共享库检索(MCP 占位,对齐 `LF_REMOTE_KB_URL`)。
  - `embed.query` — 查询向量化(`embed_query`)。
  - `rerank` — 候选重排。

## 工作流

```
检索进度:
- [ ] 条件改写(query >120 或模糊 → Haiku 改写一次)
- [ ] embed(vector/hybrid;不可用降级 FTS)
- [ ] 按 scopes 选源 → 各源 search
- [ ] 跨源 RRF(k=60) 融合
- [ ] (rerank) 重排
- [ ] 截断 top_k,标注 method_used/degraded
```

详细 method 语义、RRF、降级矩阵、扩展示例见 [reference.md](reference.md)。

## 限制与不变量

- **只召回排序,不合成、不写学习状态**(Proposal §5 决策 6)。
- 未配置的远程源属正常离线,不参与即可,**不算降级**;真正的源异常/向量不可用才置 `degraded=True`。
- 向后兼容: 保留 `rrf_fuse` 名称与 `RetrievalInput.backend` 字段(映射到 scopes)。

## 进一步参考

- method/RRF/降级/扩展点/工具 schema: [reference.md](reference.md)
