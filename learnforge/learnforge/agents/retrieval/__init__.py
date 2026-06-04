"""Retrieval 共享能力：统一检索入口（RetrievalAgent，双层知识库 + 可插拔 RAG）。

非 Manager 直接调度的子能力，被 QA/Mock/Diagnosis 等复用。
公开入口保持兼容：`from learnforge.agents.retrieval import RetrievalAgent`。
工具契约见 ../../contracts/agents/retrieval.py；运行时人格见 skills 注册表；本包文档见 CLAUDE.md。
"""

from .retrieval_agent import RetrievalAgent, rrf_fuse  # rrf_fuse 向后兼容再导出

__all__ = ["RetrievalAgent", "rrf_fuse"]
