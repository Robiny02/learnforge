# Learning Planning — Reference

源: [learnforge/learnforge/agents/planning.py](../../../learnforge/learnforge/agents/planning.py)

## 两模式输入

| 模式 | 必填 | 关键输入 |
|---|---|---|
| `generate` | `goal` | `goal`、`deadline?`、`current_level?` |
| `modify` | `existing_path_ref` | `diagnosis`(DiagnosisResult)、`user_feedback?` |

缺必填 → `PlanningOutput(status=NEEDS_INPUT)`。

## 排序与分桶

```
priority = weakness * goal_relevance / mastery
```
- `weakness` 来自诊断簇 severity / (1-mastery)。
- 按 `priority` 降序,再按天分桶到 `deadline`,不超期硬塞。
- 候选 Atom 上限 50 条进 prompt(`atoms[:50]`)。

## PathDiff 输出

```
PathDiff:
  add:     [PathItem]   # 新增条目
  remove:  [atom_id]    # 移除
  reorder: [...]        # 重排
  rationale: str        # 必填,冲突时标注"以用户为准"
```
空 diff(无变更)是合法 `ok` 终态,语义为"无需调整"。

## 冲突处理

用户反馈与诊断冲突 → 以用户为准,并在 `rationale` 显式标注理由。

## 写边界

```python
# 唯一写者:Manager.commit_path
ManagerAgent(db_path).commit_path(path_id, items, user_id="default")
# → LearningPathRepository.commit(LearningPath(path_id, items), user_id)
```

## 预留工具接口(占位 schema)

```python
ToolSpec(
    name="atom.list_by_topic",
    description="按主题读候选 knowledge_atoms(只读)",
    server="learnforge.local",
    # input:  {topic}
    # output: [{atom_id, topic, title, mastery_score}]
)
ToolSpec(
    name="path.commit",
    description="提交 learning_paths(唯一写者,仅 Manager)",
    server="learnforge.local",
    # input:  {path_id, items: [PathItem], user_id}
    # output: {ok: bool}
)
```

## 相关测试

- `tests/test_manager_composite.py` — 诊断→改路径复合编排(含诊断为空跳过 modify)。
