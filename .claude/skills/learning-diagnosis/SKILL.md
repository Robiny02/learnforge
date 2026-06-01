---
name: learning-diagnosis
description: >-
  对学习者做只读弱点诊断(LearnForge DiagnosisAgent,ReAct 三段式聚合 interaction_events +
  knowledge_atoms 掌握度)。当用户问"我哪里薄弱/我的弱点/诊断一下我的学习/复习建议",或
  mock 结算后需 post_mock 诊断、复合"准备面试"流程需先诊断时使用。严格只读,绝不改路径或 atom。
allowed-tools:
  - events.list_window
  - atom.get
  - retrieval.search
---

# Learning Diagnosis(只读弱点诊断)

驱动 `DiagnosisAgent`([learnforge/learnforge/agents/diagnosis.py](../../../learnforge/learnforge/agents/diagnosis.py))。
**严格只读**:只 join 事件 + 掌握度产出弱点结论与建议,任何写入都经 Manager → Planning。

## 命令

```bash
cd learnforge

# 只读不变量测试(核心契约)
pytest tests/test_diagnosis.py::test_diagnosis_is_read_only

# 全部诊断测试
pytest tests/test_diagnosis.py
```

## 子 Agent 与工具职责

- 本 agent 无子 agent,内部是 ReAct 三段式(Thought→Action→Observation)。
- **预留工具**(接口 schema 见 [reference.md](reference.md)):
  - `events.list_window` — 读 `interaction_events` 时间窗(`EventRepository.list_window_dicts`)。
  - `atom.get` — 读 `knowledge_atoms` 掌握度(`AtomRepository.get`),计算 `effective_mastery`(时间遗忘)。
  - `retrieval.search` — 可选,补充弱点佐证片段(经 `knowledge-retrieval` skill)。

## 工作流

```
诊断进度:
- [ ] ① LOAD_EVENTS: 按 time_window/focus_topics 读事件
- [ ] ② JOIN_MASTERY: 对涉及 atom 读掌握度并计 effective
- [ ] ③ CLUSTER_RANK: 算弱点分、聚簇、排序、给建议
```

1. **① LOAD_EVENTS** — `EventRepository.list_window_dicts(time_window)`,按 `focus_topics` 过滤。无事件 → 直接返回 `confidence=0.0` + "数据不足"建议。
2. **② JOIN_MASTERY** — 仅统计弱信号事件(`mock_weakness` 恒是;`qa_signal` 看 `correct=False / verdict∈{unverified,uncertain} / weak=True`),按 topic/atom 累计 `error_freq`、`recency`;join 掌握度计 `effective_mastery`。
3. **③ CLUSTER_RANK** — `score = (1-effective) * recency * error_freq`;`effective < 0.6` 且 `score>0` 入选弱点;话题归一化得 `severity`;取 top 5;LLM 出 ≤3 条建议(回退模板)。

公式常量、阈值、置信估计见 [reference.md](reference.md)。

## 限制与不变量

- **严格只读**: 绝不写 `knowledge_atoms` / `learning_paths`(由 `test_diagnosis_is_read_only` 强制)。
- 数据不足不硬凑: `confidence < 0.5` 并显式声明(无簇时 `≤0.45`,无事件 `=0.0`)。
- 弱信号判定见 `_is_weak_signal`;掌握度缺失按 `effective=0`(即 `1-eff=1`)处理。
- ReAct 轨迹(`last_react_trace`)仅供 trace/调试,不外传给用户。

## 进一步参考

- 公式/阈值/置信/工具 schema: [reference.md](reference.md)
