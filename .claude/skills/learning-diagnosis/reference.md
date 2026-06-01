# Learning Diagnosis — Reference

源: [learnforge/learnforge/agents/diagnosis.py](../../../learnforge/learnforge/agents/diagnosis.py)

## 常量与阈值

```python
_RECENCY_LAMBDA   = 0.05   # recency_weight = exp(-λ * age_days)
_WEAK_MASTERY_MAX = 0.6    # effective_mastery < 0.6 视为候选弱点
_TOP_N            = 5      # 弱点 atom / 话题簇 取 top 5
_MIN_EVENTS_FULL_CONF = 12 # 达到该事件量给满置信
```

## 弱点打分

```
recency_weight(age_days) = exp(-0.05 * age_days)       # 缺 created_at → 1.0
atom_score  = (1 - effective_mastery) * recency * error_freq
topic_raw   = recency * error_freq
severity    = clamp(topic_raw / max(topic_raw))         # 归一化到 [0,1]
```

`effective_mastery` 见 [learnforge/learnforge/mastery.py](../../../learnforge/learnforge/mastery.py):
`score * exp(-decay_rate * days_since_last_reviewed)`。

## 置信估计

```
vol  = min(1.0, n_events / 12)
base = 0.3 + 0.6 * vol
if not clusters: base = min(base, 0.45)
confidence = round(clamp(base), 3)
```

## 弱信号判定 `_is_weak_signal`

- `event_type == mock_weakness` → 恒为弱信号。
- `event_type == qa_signal` 且 signal 满足任一:`correct is False` / `verdict ∈ {unverified, uncertain}` / `weak is True`。

## DiagnosisInput / DiagnosisResult

- 输入: `time_window`(D7/D30/...)、`focus_topics`、`trigger`(USER/COMPOSITE/POST_MOCK)。
- 输出: `weak_atoms[]`(atom_id/topic/mastery/evidence_count)、`clusters[]`(topic/severity)、`recommendations[]`、`confidence`。

## 预留工具接口(占位 schema)

对齐 `ToolSpec`([learnforge/learnforge/mcp/base.py](../../../learnforge/learnforge/mcp/base.py))。只读工具,不得用于写。

```python
ToolSpec(
    name="events.list_window",
    description="读 interaction_events 指定时间窗(只读)",
    server="learnforge.local",
    # input:  {time_window: "D7"|"D30"|..., focus_topics?: [str]}
    # output: [{event_type, topic, atom_refs, signal, created_at}]
)
ToolSpec(
    name="atom.get",
    description="读单个 knowledge_atom 掌握度(只读)",
    server="learnforge.local",
    # input:  {atom_id}
    # output: {atom_id, topic, mastery_score, decay_rate, last_reviewed_at}
)
ToolSpec(
    name="retrieval.search",
    description="可选补证据,委托 knowledge-retrieval(只读)",
    server="learnforge.local",
    # input:  {query, scopes, method, top_k}
    # output: {chunks: [...]}
)
```

## 相关测试

- `tests/test_diagnosis.py::test_diagnosis_is_read_only` — 只读不变量。
