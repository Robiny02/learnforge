# planning/ — Plan 工具（生成/修改学习路径）

排程，不评判对错。只产**增量** `PathDiff`，由 Manager 落库（唯一写者）。

**三层**：契约 `../../contracts/agents/planning.py` ｜ 运行时 skill（Sonnet）｜ 本文件。

## 职责边界

- **做**：据目标/deadline 生成学习路径（generate），或据诊断/反馈调整路径（modify）。
- **不做**：不直接写 `learning_paths`（只产 diff，Manager 落库）；不判薄弱点（那是 diagnose 的输入）；不答概念。

## tool 契约

- 入口：`PlanningAgent.run(PlanningInput) -> PlanningOutput`。
- `PlanningInput`：`mode`(generate|modify)；generate 需 `goal`；modify 需 `diagnosis` + `existing_path_ref`；
  可选 `deadline` / `current_level` / `user_feedback`。
- `PlanningOutput`：`diff: PathDiff`(add/remove/reorder + rationale) + `status`。

## 写权限（关键）

- PlanningAgent **只产 PathDiff**。落库经 `Manager._apply_path_diff`（唯一写者，emit PATH_CHANGED）。
- 复合"准备面试"：diagnosis → planning.modify；**若诊断为空则跳过 modify**，转而建议先做 mock（§5.6 异常）。

## 不变量 & 降级

- 增量语义：diff 是相对 `existing_path_ref` 的最小改动，不是全量重排。
- 无 key → 确定性排程 stub（按 atom 难度/掌握度排序），仍产合法 PathDiff。

## 已知限制

- `goal` 缺失判定靠 slots 剥词（`intent/slots.build_plan_slots`）；deadline 正则只认**阿拉伯数字**，
  "两周"漏判、"2周"才命中（见 `intent/slots.DEADLINE_RE`）。

## 改这个工具

- 改排程策略/口径 → skill + `planning_agent.py`；改 diff 结构 → contract。
- 集成（notion/report/gpt_image 导出）在 `planning_agent.py` 内延迟导入，按需触发。
- 测试：`tests/test_planning*.py`、复合流程 `tests/test_*composite*` / interview-prep skill。
