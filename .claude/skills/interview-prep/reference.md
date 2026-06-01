# Interview Prep — Reference

源: [learnforge/learnforge/agents/manager.py](../../../learnforge/learnforge/agents/manager.py)

## plan-and-execute 流程

```
make_plan(user_input)
  → LLM 产依赖 DAG;无 key 回退 _keyword_plan
  → "准备"+"面试" 命中 → [
       {agent:"diagnosis",  task_type:"diagnosis",   deps:[]},
       {agent:"planning",   task_type:"plan.modify", deps:[0]},
    ]
execute(plan)
  → 串行按依赖序;诊断结论注入 context["diagnosis"]
  → 诊断为空 → 跳过 planning.modify(meta.skipped_modify/suggest_mock)
execute_with_replan
  → _needs_replan 为真且 replan_count<2 → 重规划
aggregate(responses, plan, meta)
  → reply_text + next_actions + status
```

## _is_empty_diagnosis

```python
not (diag.get("weak_atoms") or diag.get("clusters"))
```

## replan 触发 `_needs_replan`

- `status == ERROR` → 必触发。
- `status ∈ {ESCALATE, NEEDS_INPUT}` → 跳过(合法终态)。
- `confidence < 0.4` 且非"诚实低置信"(结果含 `weak_atoms` 或 `skipped`)→ 触发。
- 上限 `MAX_REPLAN = 2`。

## aggregate 输出

```
{ reply_text, citations: [], next_actions: [str], status }
```
- `meta.suggest_mock` → next_actions 追加 "建议进行一场模拟面试...(需你确认后开始,系统不会自动开始)"。
- `meta.skipped_modify` → 模板回复 "诊断信号不足,已跳过改计划;建议先做一场模拟面试采集数据..."。

## 编排的子 skill 工具(占位 schema)

```python
ToolSpec(name="diagnosis.run",   description="只读诊断,trigger=COMPOSITE",  server="learnforge.local")
ToolSpec(name="planning.modify", description="据诊断改路径(modify 模式)",   server="learnforge.local")
ToolSpec(name="mock.suggest",    description="建议 mock,不自动开始",        server="learnforge.local")
```
完整 schema 见各子 skill:
- [mock-interview/reference.md](../mock-interview/reference.md)
- [learning-diagnosis/reference.md](../learning-diagnosis/reference.md)
- [learning-planning/reference.md](../learning-planning/reference.md)

## 相关测试

- `tests/test_manager_composite.py` — 复合编排(含诊断为空跳过 modify、suggest_mock)。
