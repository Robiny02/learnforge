"""LearnForge-style agentic RAG pipeline for the chunking benchmark.

A hierarchical, single-responsibility agent chain (mirrors learnforge's
ManagerAgent → workers design): the Manager orchestrates and the workers call
each other explicitly. Every agent degrades gracefully (no API key → BM25
retrieval + extractive QA) so the chain is always runnable.
"""
