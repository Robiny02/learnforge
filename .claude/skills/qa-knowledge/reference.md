# QA Knowledge — Reference

源: [learnforge/learnforge/agents/qa/qa_agent.py](../../../learnforge/learnforge/agents/qa/qa_agent.py)

## 问题类型 → 链路开关(Router)

| q_type | need_retrieval | need_verifier | 说明 |
|---|---|---|---|
| `temporal`(时效) | True | True | 强制,不臆测时效性 |
| `concept`(八股概念) | True | True | 默认 |
| `project`(项目相关) | True | (默认) | 查个人 + 公共 |
| `chitchat`(闲聊) | False | False | 轻路径 |

## 知识源/方式选择 `_pick_scopes`

| q_type | scopes | method |
|---|---|---|
| `concept` / `temporal` | `[SHARED]` | `HYBRID` |
| `project`(及其他) | `[LOCAL, SHARED]` | `HYBRID` |

## verdict → confidence

```python
{ VERIFIED: 0.85, UNCERTAIN: 0.5, UNVERIFIED: 0.4 }
# 未核验(need_verifier=False)→ verdict=UNCERTAIN
```

## 输出 QAOutput

`answer`、`citations[]`(chunk_id/source_type/snippet≤120)、`verdict`、`confidence`、`topic`(优先取检索片段 topic,回退 q_type)。

## 信号回写(经 Manager)

QA 返回后由 `ManagerAgent._emit_qa_signal` 写:
```
EventPayload(event_type=QA_SIGNAL, topic, atom_refs=citations[:5],
             signal={verdict, confidence, weak: verdict∈{unverified,uncertain}})
```
该事件供 `learning-diagnosis` 作为弱信号。

## 预留工具接口(占位 schema)

```python
ToolSpec(
    name="retrieval.search",
    description="召回片段,委托 knowledge-retrieval(只读)",
    server="learnforge.local",
    # input:  {query, scopes, method, top_k}
    # output: {chunks, method_used, degraded}
)
ToolSpec(
    name="qa_history.append",
    description="写 qa_history 个人本地库(append-only)",
    server="learnforge.local",
    # input:  {question, answer, verdict, topic, citations}
    # output: {qa_id}
)
```
