# Chunking Benchmark — Intrinsic Stats (Phase 1)

| key | n | avg_len | med | short | long | code_split | tbl_split | ocr_sep | qa_split |
|---|---|---|---|---|---|---|---|---|---|
| fixed/ALL | 1112 | 503.8 | 512 | 0.0 | 0.0 | 0.55 | 0.529 | 0.0 | 0.116 |
| fixed/github_md | 1084 | 506.1 | 512 | 0.0 | 0.0 | 0.556 | 0.529 | – | 0.127 |
| fixed/xiaohongshu | 12 | 384 | 406 | 0.0 | 0.0 | – | – | 0.0 | 0.0 |
| fixed/xiaolin_blog | 16 | 443 | 512 | 0.0 | 0.0 | 0.0 | – | 0.0 | 0.0 |
| llamaindex/ALL | 698 | 602.6 | 302 | 0.052 | 0.125 | 0.0 | 0.0 | 0.067 | 0.029 |
| llamaindex/github_md | 640 | 640.9 | 330 | 0.056 | 0.136 | 0.0 | 0.0 | – | 0.0 |
| llamaindex/xiaohongshu | 22 | 221 | 234 | 0.0 | 0.0 | – | – | 0.167 | 0.5 |
| llamaindex/xiaolin_blog | 36 | 153.2 | 147 | 0.0 | 0.0 | 0.0 | – | 0.0 | 0.0 |
| semantic/ALL | 1157 | 396.7 | 486 | 0.032 | 0.0 | 0.108 | 0.059 | 0.0 | 0.0 |
| semantic/github_md | 1089 | 413.2 | 494 | 0.02 | 0.0 | 0.109 | 0.059 | – | 0.0 |
| semantic/xiaohongshu | 43 | 91.7 | 68 | 0.326 | 0.0 | – | – | 0.0 | 0.0 |
| semantic/xiaolin_blog | 25 | 204.8 | 115 | 0.04 | 0.0 | 0.0 | – | 0.0 | 0.0 |
| structural/ALL | 3591 | 155.2 | 64 | 0.332 | 0.01 | 0.0 | 0.0 | 0.0 | 0.0 |
| structural/github_md | 3390 | 159.7 | 65 | 0.335 | 0.01 | 0.0 | 0.0 | – | 0.0 |
| structural/xiaohongshu | 87 | 67.4 | 53 | 0.31 | 0.0 | – | – | 0.0 | 0.0 |
| structural/xiaolin_blog | 114 | 64.3 | 62 | 0.231 | 0.0 | 0.0 | – | 0.0 | 0.0 |


_Note: noise_contamination / technical_retention require Phase 3 block labels._
