---
name: mock-interview
description: >-
  驱动 LearnForge 多轮模拟面试子系统(Interviewer/Judge/Strategist/Coach 四子 agent +
  LangGraph interrupt/resume 状态机)。当用户要求开始模拟面试、mock interview、出题练习、
  追问、面试评分/复盘,或需要 start/answer/settle 一场 mock 时使用。覆盖 MockInterviewAgent
  与 graph/mock_graph.py 的 S1-S7 状态机。
allowed-tools:
  - mock.session.upsert
  - mock.turn.add
  - event.append
  - mastery.commit
---

# Mock Interview(模拟面试)

驱动 `MockInterviewAgent`([learnforge/learnforge/agents/mock/mock_agent.py](../../../learnforge/learnforge/agents/mock/mock_agent.py))
所持有的 LangGraph 子图([learnforge/learnforge/graph/mock_graph.py](../../../learnforge/learnforge/graph/mock_graph.py))。
子图是带 checkpoint 的 `interrupt/resume` 状态机,`thread_id = session_id`。

## 命令

```bash
cd learnforge

# 跑 mock 子图测试
pytest tests/test_mock_subgraph.py

# 单条用例
pytest tests/test_mock_subgraph.py -k pause

# 起服务后通过 API 驱动 start/answer
uvicorn learnforge.app:api --reload
```

## 子 Agent 与工具职责

- **Interviewer**(Sonnet): 按 topic/difficulty 出题与追问,输出 `expected_points` + `atom_refs`。不透露评分、不给标准答案、不重复已问题、不超难度。
- **Judge**(Haiku): 仅依据 `expected_points` 与考生回答打分,输出 rubric(`overall` 0-5 + dims + `missed_points` + `confidence`)。不被自信措辞带偏、不向考生补正确答案。
- **Strategist**(Haiku): 只决策不作答,输出 `action ∈ {continue,raise,lower,switch_topic,pause,end,escalate}`。跨能力请求(改计划/诊断)必须 `escalate`。
- **Coach**(Sonnet): 终场复盘,输出 `summary/strengths/weaknesses/next_steps`,每条 weakness 必带 evidence(引哪一轮)。
- **预留工具**(对齐 [learnforge/mcp/registry.py](../../../learnforge/learnforge/mcp/registry.py) 的 `MCP_REGISTRY`,接口详见 [reference.md](reference.md)):
  - `mock.session.upsert` / `mock.turn.add` — 落 `mock_sessions` / `mock_turns`。
  - `event.append` — 结算时写 `interaction_events`(append-only)。
  - `mastery.commit` — 唯一写者通道,**仅 Manager 可调**。

## 工作流

```
Mock 进度:
- [ ] start: 出首题,停在 S3(interrupt)
- [ ] answer 循环: resume → S4 评分 → S5 决策 → 回 S2 / S_PAUSE / S6 / S_EXIT
- [ ] settle: status=review 时由 Manager 提交 mastery + 触发 post_mock 只读诊断
```

1. **start** — `MockInput(topic, target_difficulty, max_turns)` 且无 `user_answer/user_interrupt`。子图 S1→S2 出题,S3 `interrupt()` 暂停;输出 `status="active"` + `question`。
2. **answer 循环** — `MockInput(session_id, user_answer / user_interrupt)` → `Command(resume=...)`。决策路由(`route_after_strategist`):
   - `continue/raise/lower/switch_topic` → 回 S2 出下一题(`status="active"`)。
   - `pause` → S_PAUSE `interrupt()`,可跨会话 resume(`status="paused"`)。
   - `end` 或 `turn_index ≥ max_turns` → S6 复盘 → S7 结算(`status="review"`)。
   - `escalate` → S_EXIT 交回 Manager(`status="escalate"`)。
3. **settle** — 见 `Manager.settle_mock(session_id)`:读 `mock.mastery_updates()` → `commit_mastery`(唯一写者)→ 自动 `POST_MOCK` 只读诊断(失败不影响结算)。

详细状态机、resume 载荷格式、mastery 信号聚合公式见 [reference.md](reference.md)。

## 限制与不变量

- **唯一写者**: mastery 只能由 `ManagerAgent.commit_mastery` 落库;子图 S7 只产出 `mastery_updates` 交回 Manager,自身不写 `knowledge_atoms`。
- **合法中断点仅 S3 / S_PAUSE**;其余节点不得 `interrupt()`。
- 纯中断(只给 `user_interrupt` 不作答)不评分、不升降档、不推进 `turn_index`。
- 难度规则: 连续 2 轮 `overall ≥ 4` 升档、`≤ 2` 降档。
- `switch_topic` 不重置累积分(`turn_scores`/`answered_atom_refs` 全场累积)。
- 复盘样本 < 2 轮时如实说明、不下结论。
- 所有落库副作用 best-effort:DB 不可用时静默跳过,链路仍通(无 `ANTHROPIC_API_KEY` 走确定性 stub)。

## 进一步参考

- 状态机/路由/resume/工具 schema 全规格: [reference.md](reference.md)
