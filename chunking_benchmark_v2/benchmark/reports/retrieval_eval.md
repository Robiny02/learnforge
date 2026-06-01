# Chunking Benchmark — Retrieval Eval (Phase 2)

| key | top_doc_correct | hit@5 | precision@5 | mrr | kw_recall@5 | kw_recall@5_parent | degraded |
|---|---|---|---|---|---|---|---|
| fixed/ALL | 0.929 | 1.000 | 0.400 | 0.907 | 0.799 | 0.799 | False |
| fixed/github_md | 1.000 | 1.000 | 0.550 | 0.800 | 0.609 | 0.609 | False |
| fixed/xiaohongshu | 0.800 | 1.000 | 0.200 | 0.900 | 0.870 | 0.870 | False |
| fixed/xiaolin_coding_style_blog | 1.000 | 1.000 | 0.480 | 1.000 | 0.880 | 0.880 | False |
| llamaindex/ALL | 0.857 | 1.000 | 0.443 | 0.857 | 0.827 | 0.827 | False |
| llamaindex/github_md | 0.750 | 1.000 | 0.650 | 0.750 | 0.650 | 0.650 | False |
| llamaindex/xiaohongshu | 1.000 | 1.000 | 0.320 | 1.000 | 0.837 | 0.837 | False |
| llamaindex/xiaolin_coding_style_blog | 0.800 | 1.000 | 0.400 | 0.800 | 0.960 | 0.960 | False |
| semantic/ALL | 0.714 | 1.000 | 0.329 | 0.729 | 0.693 | 0.693 | False |
| semantic/github_md | 1.000 | 1.000 | 0.450 | 0.675 | 0.525 | 0.525 | False |
| semantic/xiaohongshu | 0.800 | 1.000 | 0.240 | 0.800 | 0.680 | 0.680 | False |
| semantic/xiaolin_coding_style_blog | 0.400 | 1.000 | 0.320 | 0.700 | 0.840 | 0.840 | False |
| structural/ALL | 0.786 | 0.929 | 0.257 | 0.649 | 0.567 | 0.781 | False |
| structural/github_md | 1.000 | 1.000 | 0.250 | 0.625 | 0.425 | 0.658 | False |
| structural/xiaohongshu | 0.600 | 0.800 | 0.160 | 0.550 | 0.440 | 0.820 | False |
| structural/xiaolin_coding_style_blog | 0.800 | 1.000 | 0.360 | 0.767 | 0.807 | 0.840 | False |

_Retrieval mode: **hybrid (BM25 + vector RRF)** where `degraded=False`; BM25-only where `degraded=True`. Relevant = gold-doc chunk containing a gold keyword. kw_recall@5_parent backfills the matched child's parent chunk._
