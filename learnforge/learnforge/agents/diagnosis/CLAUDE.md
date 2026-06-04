# diagnosis/ — Diagnose 工具（只读弱点诊断）

聚合 `interaction_events` + `knowledge_atoms` 掌握度，找学习者薄弱点。ReAct 三段式（thought→action→observation）。

**三层**：契约 `../../contracts/agents/diagnosis.py` ｜ 运行时 skill（Sonnet）｜ 本文件。

## 职责边界

- **做**：只读地诊断"我哪里薄弱/掌握度怎么样"，产弱点簇 + 复习建议。
- **不做（严格只读）**：**绝不**改 `knowledge_atoms` 掌握度、**绝不**改 `learning_paths`。
  也不排课（那是 plan）、不答概念（那是 qa）、不排查代码/项目（那是 qa）。

## tool 契约

- 入口：`DiagnosisAgent.run(DiagnosisInput) -> DiagnosisResult`。
- `DiagnosisInput`：`time_window`(默认 30d) / `focus_topics` / `trigger`(user|post_mock|composite)。
- `DiagnosisResult`：`weak_atoms` + `clusters`(severity) + `recommendations` + `confidence`。

## 运行时 & 触发

- ReAct 3 步只读循环（`react/loop.py` + `_DIAG_TOOLS`）：拉事件 → 算有效掌握 → 聚簇。
- 触发场景：用户问"我哪里薄弱"；mock 结算后 Manager 自动触发 `post_mock` 诊断；复合"准备面试"先诊断。
- 有效掌握 = `mastery.effective_mastery`（时间衰减），< 0.6 视为候选弱点；事件量达 `_MIN_EVENTS_FULL_CONF`(12) 给满置信。

## 不变量 & 降级

- **read-only 是硬不变量**，由 `tests/test_diagnosis_is_read_only` 守护——改本工具时绝不能引入写。
- 无 key → 走确定性聚合（纯 `mastery` 函数 + 事件统计），仍产合法 DiagnosisResult。

## 已知限制（写文档/解释结果时注意）

- **读的是"已存掌握度"**，检测不到"最近一次答对/答错导致的回归"——掌握度由 Manager 异步落库后才反映。
- **clusters 倾向过报**（小样本下 severity 偏高）。
- **ghost atoms**（库里有 atom 但用户从没碰过）会造成**假阳性**弱点。

## 改这个工具

- 改诊断口径/建议风格 → skill；改阈值（`_WEAK_MASTERY_MAX` / `_TOP_N` / `_RECENCY_LAMBDA`）→ `diagnosis_agent.py` 顶部常量。
- 改输入输出 → contract。`_is_weak_signal` 被 eval 复用（经 `__init__` 再导出），改签名要同步。
- 测试：`tests/test_diagnosis.py`、`tests/test_agent_capabilities.py`；eval：`eval/diagnosis_eval.py`、`eval/trajectory_eval.py`。
