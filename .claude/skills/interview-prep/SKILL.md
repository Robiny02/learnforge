---
name: interview-prep
description: >-
  LearnForge "准备面试" 复合编排(Design §5.6):先只读诊断弱点,再据此修改学习路径,最后建议
  (但不自动开始)模拟面试。当用户说"快面试了帮我准备/帮我准备面试/面试冲刺"等跨能力一站式请求时使用。
  由 ManagerAgent 编排,组合 learning-diagnosis → learning-planning → mock-interview。
allowed-tools:
  - diagnosis.run
  - planning.modify
  - mock.suggest
---

# Interview Prep(准备面试·复合编排)

复合工作流,源 `ManagerAgent`([learnforge/learnforge/agents/manager.py](../../../learnforge/learnforge/agents/manager.py))
的 `_keyword_plan`(命中 "准备"+"面试")。本 skill **编排**三个子 skill,不直接做领域工作。

## 命令

```bash
cd learnforge

# 复合编排测试(含诊断为空跳过 modify)
pytest tests/test_manager_composite.py

# 烟测主图四类意图
python -m learnforge.graph.main_graph
```

## 编排的子 Skill / 工具职责

- **learning-diagnosis**(`diagnosis.run`): 只读诊断,产弱点簇。
- **learning-planning**(`planning.modify`): 据诊断改路径(modify 模式)。
- **mock-interview**(`mock.suggest`): 仅**建议**做 mock,**不自动开始**(控制权交回用户)。
- 由 Manager 生成依赖 DAG:`[{diagnosis, deps:[]}, {planning.modify, deps:[0]}]`。

## 工作流(条件分支)

```
准备面试进度:
- [ ] ① diagnosis(只读)
- [ ] ② 判断诊断是否为空
- [ ] ③a 空 → 跳过 modify,建议先 mock 采集数据
- [ ] ③b 非空 → planning.modify
- [ ] ④ 建议 mock(需用户确认,系统不自动开始)
```

1. **① 诊断** — `DiagnosisAgent`,`trigger=COMPOSITE`,结论注入后续 planning 的 `context["diagnosis"]`。
2. **② 判空** — `weak_atoms` 与 `clusters` 皆空 → 视为空(`_is_empty_diagnosis`)。
3. **③ 分支**:
   - **诊断为空**(§5.6 异常①)→ 跳过 `plan.modify`,`meta.skipped_modify=True`,改建议先做一场 mock 采集数据。
   - **诊断非空** → `PlanningAgent` modify;成功后 `meta.suggest_mock=True`。
4. **④ 聚合** — 输出 `reply_text` + `next_actions`;suggest_mock 时追加 "建议进行一场模拟面试(需你确认后开始,系统不会自动开始)"。

## 限制与不变量

- Manager 只调度不作答,**不替 worker 编造答案**、不广播完整上下文。
- 诊断只读;路径写入经 Manager 唯一写者。
- **改完路径建议 mock 但不自动启动**,控制权交回用户(§5.6 P5)。
- 诊断为空时不硬改路径,转为采集数据建议(§5.6 异常①)。
- replan: worker error / 非诚实低置信(`confidence<0.4`)触发重规划,`replan_count>2` 终止。

## 进一步参考

- DAG/replan/聚合 next_actions/工具 schema: [reference.md](reference.md)
