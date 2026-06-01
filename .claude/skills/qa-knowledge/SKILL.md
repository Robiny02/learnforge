---
name: qa-knowledge
description: >-
  面向程序员的检索增强问答(LearnForge QAAgent,Router→Retrieval→Synthesizer→Verifier 动态链路)。
  当用户提出概念/八股/项目相关技术问题、需要带引用的可核验答案,或问及时效性内容时使用。
  无证据时降断言强度、禁编造引用。
allowed-tools:
  - retrieval.search
  - qa_history.append
---

# QA Knowledge(检索增强问答)

驱动 `QAAgent`([learnforge/learnforge/agents/qa/qa_agent.py](../../../learnforge/learnforge/agents/qa/qa_agent.py))。
动态链路:`Router → (Retrieval) → Synthesizer → (Verifier)`;chitchat 走轻路径跳过检索/校验。

## 命令

```bash
cd learnforge

# 烟测主图(含 qa 意图)
python -m learnforge.graph.main_graph

# 起服务
uvicorn learnforge.app:api --reload
```

## 子 Agent 与工具职责

- **Router**(Haiku): 判定问题类型(`concept/temporal/project/chitchat`)并设开关。`temporal` 强制 `need_retrieval=need_verifier=True`;`chitchat` 全 False 走轻路径。
- **Synthesizer**(Sonnet): 只基于证据 + 常识合成,每条事实尽量绑定 `source_chunk_id`,输出 `draft` + 可验证 `claims`。无证据须显式声明并降断言强度。
- **Verifier**(Haiku): 逐 claim 比对证据,无支撑降级为不确定;全无支撑 → `verdict=unverified`。
- **知识源选择**(`_pick_scopes`): `concept/temporal` → SHARED 混合检索;`project` → LOCAL + SHARED。
- **预留工具**(schema 见 [reference.md](reference.md)):
  - `retrieval.search` — 委托 `knowledge-retrieval` skill 召回片段。
  - `qa_history.append` — 写 `qa_history`(个人本地库,append-only)。

## 工作流

```
QA 进度:
- [ ] Router 分类 + 设开关
- [ ] (need_retrieval) 选 scope/method → 检索
- [ ] Synthesizer 合成 draft + claims
- [ ] (need_verifier) 逐 claim 核验 → verified_answer + verdict
- [ ] 装配 citations + 信号 topic
```

1. **Router** → `q_type` + `need_retrieval/need_verifier`。
2. **Retrieval**(条件) → `_pick_scopes(q_type)` 得 scopes/method → `RetrievalAgent.run`。
3. **Synthesizer** → `draft` + `claims`(绑 `source_chunk_id`)。
4. **Verifier**(条件) → `verified_answer` + `verdict`;否则 `verdict=UNCERTAIN`。
5. **装配** → `citations`(chunk_id/source_type/snippet)、`topic`、`confidence`(按 verdict 映射)。

verdict→confidence 映射、scope 选择表见 [reference.md](reference.md)。

## 限制与不变量

- 无检索证据时**显式声明并降断言强度**,禁止无依据断言时效性、禁止编造引用。
- `temporal` 类**强制**检索 + 核验。
- QA 外壳本身不调 LLM,成本累计自各子 agent(`last_cost_usd`)。
- 链路永远通:无 `ANTHROPIC_API_KEY` 时各子 agent 走确定性 stub。
- QA 信号经 Manager 写 `interaction_events(qa_signal)`(供诊断),QA 自身不改掌握度。

## 进一步参考

- verdict 映射/scope 表/工具 schema: [reference.md](reference.md)
