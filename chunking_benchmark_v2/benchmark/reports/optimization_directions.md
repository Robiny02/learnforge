# 下一步优化方向

按"投入产出比 + 现有证据"排序。每条都标了**为什么**（来自本 benchmark 观测）和**怎么验证**。

## P0 — 直接影响入库质量

### 1. structural 子块过碎 → 合并 + 调大目标
- **为什么**：RAGAS `context_precision` structural 仅 0.71（最低），子块均长 64 字符；LLM 评委判"信息不足"。Phase 1 `too_short_ratio=0.33`。
- **做法**：在 `chunkers/structural.py` 给子块加"最小尺寸合并"（相邻同 heading 的小 block 合并到 ~256–384 字符），保留 code/table/image_ocr 原子性。
- **验证**：重跑 `eval_ragas`，structural precision 应从 0.71 升向 0.80+，`stats.md` short 比例下降，code/ocr split 仍为 0。

### 2. BlockClassifier 负向/泛词误判 → LLM 复核常态化
- **为什么**：规则版在 off-topic 文档上会被"项目"等泛词、"完全没有问 MySQL"等**否定提及**带偏（已加 strong-gate 缓解，但 comment/边界仍有残留）。
- **做法**：默认开启 `use_llm_recheck`（有 key 时），对 `ambiguous` block 用 Haiku/gpt-4o-mini 复核；扩大 ambiguous 触发到"含否定词的技术提及"。保留"绝不降级强技术块"护栏。
- **验证**：对 10 篇 xhs 人工标注 block_type 金标，算分类 precision/recall；目标 off_topic_job 文档 0 technical 泄漏到 main。

### 3. 检索召回真值化 → 建 gold-chunk 标注，跑 NonLLM context recall
- **为什么**：当前 relevance=「正确文档+含 gold 关键词」是弱代理；14 条 query、单标注，绝对值噪声大（计划 §8）。
- **做法**：为每条 query 标注 gold chunk id（reference_contexts），用 RAGAS `NonLLMContextRecall/Precision`（无需 LLM、确定性）做交叉验证；query 扩到每源 15–20 条。
- **验证**：三套指标（关键词/RAGAS-LLM/RAGAS-NonLLM）排序一致性即可信。

## P1 — 链路鲁棒性与可观测

### 4. 检索加 rerank 层（对齐 learnforge Reranker 协议）
- **为什么**：downweighted 噪声偶尔仍排进 top-5（如"个人感受"段）。
- **做法**：在 `RetrievalAgent` 后接可选 cross-encoder / LLM rerank（无 key 用 NoOp），top_k*2 → rerank → top_k。
- **验证**：noise_contamination@5 下降而 kw_recall 不降。

### 5. QA 加 Verifier（引用核验，对齐 learnforge QA 链）
- **为什么**：现在 QA 只 Router→Synth，未核验引用是否支持结论，可能过度自信。
- **做法**：加一个 Verifier 子步骤——逐条结论检查是否被引用片段支撑，不支撑则降断言强度或标"证据不足"。
- **验证**：RAGAS `faithfulness`（需生成答案）作为该步指标。

### 6. 嵌入成本/质量调参
- **为什么**：3-large 未显著优于 3-small@1024（embedding_comparison.md）。
- **做法**：固定 3-small@1024 为默认；仅对 github_md 长技术文试 3-large@1024 看 mrr 是否值回成本。
- **验证**：分源对比表，单源增益 < 2pp 则不升级。

## P2 — 工程化 / 接真实数据（Plan Phase 4）

### 7. 增量入库 + 真实爬虫数据
- **做法**：IngestionAgent 支持按 `doc_id` 幂等增量；真实 xhs/OCR/博客落 `data/raw/<source>/`，图片 OCR 预留 `ocr_provider`（默认 noop）。
- **验证**：二次 ingest 仅重切变更文档，store 角色分布稳定。

### 8. 持久化 KnowledgeStore（接 learnforge SQLite/PG）
- **为什么**：当前 store 在内存，每次 ingest 重建。
- **做法**：把 IndexedChunk 写入 learnforge `chunks`(+`chunk_fts`/`chunk_vectors`)，RetrievalAgent 直接复用主栈 FTS5+vec0；benchmark 与生产同栈。
- **验证**：`python -m learnforge.knowledge.ingest` 灌库后，pipeline 检索结果与内存版一致。

## 已知不可靠点（解读结果时注意）
- 纯检索指标系统性偏爱大块（fixed 最高），但 fixed 撕碎 55% 代码 → **必须结构+检索合看**。
- structural 低 precision 主要是粒度问题（P0-1 可解），非策略本身差。
- off-topic/career 文档是**负样本**，用于测降权能力，不能当失败案例删除。
