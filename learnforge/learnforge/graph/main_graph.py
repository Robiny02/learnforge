"""兼容 shim → learnforge.orchestration.graph.main_graph。

注:顶层 graph/ 仅转发到 orchestration.graph，但 `python -m learnforge.graph.main_graph` 是 CLAUDE.md
记录的 smoke 入口——是合法别名而非 debris，故**保留**（评估见 docs/architecture/cleanup-manifest.md A 区）。
"""
from ..orchestration.graph.main_graph import (  # noqa: F401
    build_main_graph,
    compile_main_graph,
)

if __name__ == "__main__":
    # 让 `python -m learnforge.graph.main_graph`（历史 smoke 命令）跑实现层的 4 意图 demo。
    from ..orchestration.graph.main_graph import _demo

    _demo()
