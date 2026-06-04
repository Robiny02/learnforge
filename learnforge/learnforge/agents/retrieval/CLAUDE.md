# retrieval/ — Retrieval 共享能力（统一检索入口）

**不是对外工具，是共享子能力**：不由 Manager 直接调度，被 qa/mock/diagnosis 等复用。双层知识库 + 可插拔 RAG。

**三层**：契约 `../../contracts/agents/retrieval.py` ｜ 运行时 skill（Haiku）｜ 本文件。

## 职责边界

- **做**：从本地用户库 / 共享知识库召回片段并排序，输出带 `method_used` + `degraded`。
- **不做**：不合成答案（那是 Synthesizer）、不写任何状态、不评判。**只召回 + 排序**。

## tool 契约

- 入口：`RetrievalAgent.run(RetrievalInput) -> RetrievalOutput`。
- `RetrievalInput`：`query` / `filters`(RetrievalFilters) / `top_k`(≤20) / `method` / `mode` / `scopes` / `rerank`。
  `backend` 字段为**向后兼容**，经 `effective_scopes()` 映射到 scopes。
- `RetrievalOutput`：`chunks`(按 RRF 融合分降序) + `method_used` + `degraded`。

## 架构（详见 `../../knowledge/`）

- 两层 scope：**LOCAL**（用户私有：mock_turns/qa_history）、**SHARED**（公共：chunks/knowledge_atoms）。
- 方法：`keyword` / `fulltext`(FTS5 BM25) / `vector`(sqlite-vec KNN) / `hybrid`(FTS+vector RRF，默认)。
- 流程：rewrite → embed(若 vector/hybrid) → 查各 source → 跨源 RRF(`rrf_fuse`) → 可选 rerank → top_k。
- 记忆召回经 `recall_gate`（cosine 相似度闸门）防编造。

## 不变量 & 降级

- 无 embedding key → vector **降级为 FTS**（`degraded=True` 标注），不报错。
- 无 sqlite-vec 扩展 → 只走 FTS5。
- `rrf_fuse` 经 `__init__` 再导出（向后兼容 `from ...agents.retrieval import rrf_fuse`），改签名要同步。

## 改这个能力

- 改召回策略/重写 → `retrieval_agent.py`；改融合/source/rerank → `../../knowledge/`；改 embedding provider → `../../llm/embeddings.py`。
- 测试：`tests/test_retrieval.py`、`tests/test_retrieval_modes.py`、`tests/test_fts_overrecall.py`、记忆召回系列；
  eval：`eval/retrieval_*_eval.py`、`eval/semantic_eval.py`。
