---
name: learning-planning
description: >-
  生成或修改学习路径(LearnForge PlanningAgent,generate/modify 两模式,只产增量 PathDiff)。
  当用户要求"制定/调整学习计划、学习路径、规划复习、安排到某 deadline",或诊断后需 plan.modify
  时使用。只排程不评判对错,写路径经 Manager 唯一写者。
allowed-tools:
  - atom.list_by_topic
  - path.commit
---

# Learning Planning(学习路径规划)

驱动 `PlanningAgent`([learnforge/learnforge/agents/planning.py](../../../learnforge/learnforge/agents/planning.py))。
两分支统一推理,差异只在输入;输出**增量** `PathDiff`(add/remove/reorder + rationale),不全量重写。

## 命令

```bash
cd learnforge

# 复合编排(诊断→改路径)相关测试
pytest tests/test_manager_composite.py

# 烟测主图(含 planning 意图)
python -m learnforge.graph.main_graph
```

## 子 Agent 与工具职责

- 无子 agent;单 Sonnet 调用产结构化 `PathDiff`。
- **预留工具**(schema 见 [reference.md](reference.md)):
  - `atom.list_by_topic` — 按主题读候选 `knowledge_atoms`(`AtomRepository.list_by_topic`)。
  - `path.commit` — 落 `learning_paths`,**仅 Manager 可调**(`ManagerAgent.commit_path`,唯一写者)。

## 工作流

```
规划进度:
- [ ] 校验输入(缺现状/路径引用 → needs_input)
- [ ] 读候选 Atom
- [ ] LLM 排序分桶 → PathDiff
- [ ] (Manager) commit_path
```

1. **判定模式**:
   - **generate**(目标/现状)→ 必填 `goal`,缺 → `needs_input`。
   - **modify**(诊断 + 现有路径)→ 必填 `existing_path_ref`,缺 → `needs_input`。
2. **读候选 Atom** — modify 用诊断 `clusters[].topic` 过滤;generate 按目标主题。DB 空 → `[]`(用占位条目)。
3. **排序分桶** — `priority = weakness * goal_relevance / mastery`,按天分桶到 deadline,产 `PathDiff`。LLM 不可用回退空 diff(`ok` "无需调整")。
4. **提交** — 由 Manager `commit_path(path_id, items)` 落库。

排序公式与冲突处理见 [reference.md](reference.md)。

## 限制与不变量

- **只排程不评判对错**;用户反馈与诊断冲突时以用户为准并在 `rationale` 标注。
- 只产增量 diff,不全量重写、不超 deadline 硬塞。
- **写路径经 Manager**(唯一写者),PlanningAgent 自身不写 `learning_paths`。
- 缺必填输入返回 `needs_input` 而非编造。

## 进一步参考

- 排序/分桶/冲突/工具 schema: [reference.md](reference.md)
