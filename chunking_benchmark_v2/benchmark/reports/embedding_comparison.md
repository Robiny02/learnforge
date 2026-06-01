# Embedding Model Comparison

_k=5; hybrid = BM25 + vector RRF. Each column is a different embedding model/dim; `bm25` is the no-vector baseline._

### mrr

| chunker | bm25 | 3-small@1024 | 3-large@1024 | 3-large@3072 | ada-002@1536 |
|---|---|---|---|---|---|
| fixed | 0.762 | 0.907 | 0.845 | 0.848 | 0.762 |
| llamaindex | 0.720 | 0.857 | 0.893 | 0.893 | 0.720 |
| semantic | 0.567 | 0.729 | 0.760 | 0.729 | 0.567 |
| structural | 0.395 | 0.649 | 0.618 | 0.654 | 0.395 |

### kw_recall@5_parent

| chunker | bm25 | 3-small@1024 | 3-large@1024 | 3-large@3072 | ada-002@1536 |
|---|---|---|---|---|---|
| fixed | 0.784 | 0.799 | 0.744 | 0.768 | 0.784 |
| llamaindex | 0.770 | 0.827 | 0.825 | 0.825 | 0.770 |
| semantic | 0.683 | 0.693 | 0.714 | 0.726 | 0.683 |
| structural | 0.624 | 0.781 | 0.781 | 0.769 | 0.624 |