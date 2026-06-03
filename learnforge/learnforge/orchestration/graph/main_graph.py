"""LangGraph 主图（Design §2b / §5）——Manager ReAct 编排 substrate。

    START → manager_plan(记忆读取) → manager_execute(ReAct 循环：看结果逐步决策) → manager_aggregate → END

单意图与复合任务（诊断→改路径→建议 mock，Design §5.6）统一走 ReAct 循环；mock 多轮 interrupt/resume
在 mock 子图内（graph/mock_graph.py）。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ...contracts.state import MainState
from . import nodes as N


def build_main_graph() -> StateGraph:
    g = StateGraph(MainState)

    g.add_node(N.N_PLAN, N.manager_plan)
    g.add_node(N.N_EXECUTE, N.manager_execute)
    g.add_node(N.N_AGGREGATE, N.manager_aggregate)

    g.add_edge(START, N.N_PLAN)
    g.add_edge(N.N_PLAN, N.N_EXECUTE)
    g.add_edge(N.N_EXECUTE, N.N_AGGREGATE)
    g.add_edge(N.N_AGGREGATE, END)

    return g


def compile_main_graph():
    """编译主图，返回可执行 app。"""
    return build_main_graph().compile()


def _demo() -> None:
    """4 个意图 smoke（无需 API key）。供 `python -m learnforge.graph.main_graph` 与本模块直接运行复用。"""
    app = compile_main_graph()
    cases = [
        "乐观锁还是悲观锁?",
        "帮我生成学习计划",
        "诊断我的弱点",
        "快面试了帮我准备一下",  # 复合 §5.6（ReAct 续跑：diagnosis→planning）
    ]
    for intent_input in cases:
        result = app.invoke(
            {
                "user_input": intent_input,
                "session_id": "s-demo",
                "trace_id": "t-demo",
                "replan_count": 0,
            }
        )
        print(f"input={intent_input!r}")
        print(f"  plan={[t['agent'] for t in result.get('plan') or []]}")
        print(f"  status={result.get('status')} replan={result.get('replan_count')}")
        print(f"  reply={result.get('reply_text')}")
        print(f"  next_actions={result.get('next_actions')}")
    print("OK: main graph compiled and ran START->END.")


if __name__ == "__main__":
    _demo()
