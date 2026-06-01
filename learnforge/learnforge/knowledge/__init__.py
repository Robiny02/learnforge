"""knowledge —— 双层知识库与可扩展 RAG 检索层。

子模块：
- `ingest`：共享知识库灌库（切片 + embedding + 写 chunks/FTS/向量，atom 索引同步）。
- `sources`：可插拔 KnowledgeSource（本地用户库 / 共享知识库 / 远程共享库）。
- `rerank`：可插拔 Reranker（默认 NoOp，可选 LLMReranker）。

约束：本层只写"知识内容与索引"，不写 mastery / learning_paths（Manager 唯一写者）。
"""
