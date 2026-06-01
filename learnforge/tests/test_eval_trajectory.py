"""Trajectory 评测门禁（T-EV-10 / Design §9b）——拓扑 + 数据回流 + 写边界接入回归。

离线运行：Manager 走 _keyword_plan 与确定性兜底，系统级不变量可复现。
"""

from learnforge.eval.trajectory_eval import run_eval


def test_all_trajectory_invariants_hold():
    report = run_eval()
    failed = [c.name for c in report.checks if not c.passed]
    assert not failed, f"trajectory 不变量失败：{failed}\n" + "\n".join(
        f"  {c.name}: {c.detail}" for c in report.checks if not c.passed
    )


def test_topology_and_no_unauthorized_write_specifically():
    """单列 Design §9b 两条核心：DAG 拓扑正确、复合跳过分支不半写 path。"""
    by_name = {c.name: c for c in run_eval().checks}
    assert by_name["topology_dag"].passed
    assert by_name["diagnosis_read_only"].passed
    assert by_name["composite_no_half_write"].passed


def test_closed_loop_data_reflow_specifically():
    """单列 Design §9b 闭环回流：QA→event→诊断、路径写回、掌握度写回。"""
    by_name = {c.name: c for c in run_eval().checks}
    assert by_name["qa_event_reflow"].passed
    assert by_name["path_write_reflow"].passed
    assert by_name["mastery_write_reflow"].passed
