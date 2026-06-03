"""兼容 shim → learnforge.orchestration.graph.main_graph。"""
from ..orchestration.graph.main_graph import (  # noqa: F401
    build_main_graph,
    compile_main_graph,
)

if __name__ == "__main__":
    # 让 `python -m learnforge.graph.main_graph`（历史 smoke 命令）跑实现层的 4 意图 demo。
    from ..orchestration.graph.main_graph import _demo

    _demo()
