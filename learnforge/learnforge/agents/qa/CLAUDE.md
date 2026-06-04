# qa/ — QA 工具（检索增强问答）

面向程序员的可核验问答。Manager 把它当一个 tool 调用；也有 UI fast-lane（概念卡）绕过 Manager 直调。

**三层**：契约 `../../contracts/agents/qa.py` ｜ 运行时 skill（见 SkillRegistry）｜ 本文件。

## 职责边界

- **做**：回答概念/八股/项目相关技术问题，带**引用**（citations），无证据时降断言强度。
- **不做**：不写任何状态（掌握度/路径）；不评判学习弱点（那是 diagnose）；不编造引用。

## tool 契约

- 入口：`QAAgent.run(QAInput) -> QAOutput`。
- `QAInput`：`question` (+ 可选 project_context / recent_topics)。
- `QAOutput`：`answer` + `citations: List[Citation]`（chunk_id/source_type/snippet）。

## 运行时链路（动态，由 Router 决定）

```
Router(Haiku) → [Retrieval 共享能力] → Synthesizer(Sonnet) → [Verifier(Haiku)]
```

- **Router**：判 q_type / need_retrieval / need_verifier / rewrite_needed——决定是否走检索、是否核验。
- **Retrieval**：调 `../retrieval`（共享能力），不在本包内。
- **Synthesizer**：据检索片段 + scoped_atoms produce 带 claims 的 draft。
- **Verifier**：核验 claims，标记无据/夸大（FlaggedClaim），据此降断言。
- `concept_cards.py`：fast-lane 概念卡（`is_fast_concept_request` 命中时 UI 直调，跳过 Manager LLM 路由）。

## 不变量 & 降级

- 只读：不写 mastery/path。值得记住的 QA 由 **Manager** 决定写入 daily 记忆（不是 QA 自己）。
- 无 key → Router/Synth/Verifier 全走确定性 stub，仍返回结构合法的 QAOutput（可能无引用）。
- 无检索证据 → 不编引用，answer 降低断言强度。

## 改这个工具

- 改"怎么答/什么风格" → 改 skill（SkillRegistry 里 QA/Router/Synthesizer/Verifier 的 system_prompt）。
- 改输入输出形状 → 改 `contracts/agents/qa.py`。
- 检索质量问题 → 多半在 `../retrieval`，不在这里。
- 测试：`tests/test_qa_*.py`、`tests/test_qa_latency_policy.py`（fast-lane）。
