# Mock Interview — Reference

详细规格,仅在 `SKILL.md` 不足以完成任务时按需加载。

## 状态机 S1-S7

源: [learnforge/learnforge/graph/mock_graph.py](../../../learnforge/learnforge/graph/mock_graph.py)

```
S1 INIT → S2 INTERVIEWER_TURN(出题) → S3 AWAIT_USER(interrupt) → S4 JUDGE(评分,累积不重置)
  → S5 STRATEGIST(决策) →
      continue/raise/lower/switch_topic → 回 S2
      pause                              → S_PAUSE(interrupt,可跨会话 resume)
      end / turn_index ≥ max_turns       → S6 COACH_REVIEW → S7 SETTLE(emit events) → END
      escalate                           → S_EXIT(返回 Manager) → END
```

- `interrupt()` 只在 S3(`kind="await_user"`)与 S_PAUSE(`kind="paused"`)。
- checkpointer 缺省 `MemorySaver`(进程内);生产可换 `SqliteSaver` 实现跨进程持久化。
- 节点 resume 时从头重放,`interrupt()` 返回 `Command(resume=...)` 的值。

## resume 载荷格式

```python
# answer():
Command(resume={"user_answer": <str|None>, "user_interrupt": <str|None>})
```

- 仅 `user_answer` → 正常评分推进。
- 仅 `user_interrupt`(纯中断)→ S4 跳过评分,S5 依据中断语决策(可能 pause/switch/escalate)。

## 输出 MockOutput.status 语义

| status | 触发 | 关键字段 |
|---|---|---|
| `active` | S3 interrupt | `question`、`turn_index` |
| `paused` | S_PAUSE interrupt | `turn_index` |
| `review` | S7 settled | `review`(CoachReport)、`events` |
| `escalate` | S_EXIT | `escalate_action` |

## mastery 信号聚合(S7 → Manager)

`_collect_mastery_updates`:
```
signal = mean(turn_scores[].overall) / 5.0     # 归一化到 [0,1]
updates = [{"atom_id": aid, "signal": signal, "source": "mock"} for aid in answered_atom_refs]
```
`source="mock"` → `update_mastery` 用 α=0.5(见 [learnforge/learnforge/mastery.py](../../../learnforge/learnforge/mastery.py))。

## 预留工具接口(占位 schema)

对齐 `ToolSpec`([learnforge/learnforge/mcp/base.py](../../../learnforge/learnforge/mcp/base.py))。MVP 注册表为空,调用即抛 `NotImplementedError`;未来接入时 agent/skill 接口不变。

```python
ToolSpec(
    name="mock.session.upsert",
    description="创建/更新 mock_sessions 记录",
    server="learnforge.local",
    # input:  {session_id, topic, difficulty, max_turns}
    # output: {ok: bool}
)
ToolSpec(
    name="mock.turn.add",
    description="追加一轮 mock_turns(题目+期望点+回答+评分)",
    server="learnforge.local",
    # input:  {turn_id, session_id, turn_index, question, expected_points, user_answer, score}
    # output: {ok: bool}
)
ToolSpec(
    name="event.append",
    description="向 interaction_events 追加事件(append-only)",
    server="learnforge.local",
    # input:  {event_type, topic, atom_refs, signal}
    # output: {event_id}
)
ToolSpec(
    name="mastery.commit",
    description="提交掌握度更新(唯一写者通道,仅 Manager)",
    server="learnforge.local",
    # input:  [{atom_id, signal, source}]
    # output: {committed: int}
)
```

注册示例(未来):
```python
from learnforge.mcp.registry import MCP_REGISTRY
from learnforge.mcp.base import ToolSpec
MCP_REGISTRY.register(ToolSpec(name="mock.turn.add", server="learnforge.local"))
```

## 相关测试

- `tests/test_mock_subgraph.py` — 状态机/interrupt/resume/结算结构契约。
