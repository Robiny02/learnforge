# Chunking Benchmark — RAGAS Retrieval Eval

_Judge: `openai/gpt-4o-mini`; k=5; contexts = top-5 retrieved (child + parent backfill). context_precision = relevant contexts ranked high; context_recall = reference answer covered by contexts._

| key | context_precision | context_recall |
|---|---|---|
| fixed/ALL | 0.901 | 1.000 |
| fixed/github_md | 0.954 | 1.000 |
| fixed/xiaohongshu | 0.907 | 1.000 |
| fixed/xiaolin_coding_style_blog | 0.853 | 1.000 |
| llamaindex/ALL | 0.838 | 0.929 |
| llamaindex/github_md | 0.867 | 0.750 |
| llamaindex/xiaohongshu | 0.875 | 1.000 |
| llamaindex/xiaolin_coding_style_blog | 0.778 | 1.000 |
| semantic/ALL | 0.806 | 0.929 |
| semantic/github_md | 0.842 | 1.000 |
| semantic/xiaohongshu | 0.818 | 0.800 |
| semantic/xiaolin_coding_style_blog | 0.767 | 1.000 |
| structural/ALL | 0.712 | 0.929 |
| structural/github_md | 0.789 | 1.000 |
| structural/xiaohongshu | 0.697 | 0.800 |
| structural/xiaolin_coding_style_blog | 0.667 | 1.000 |