# research/ — Research 工具（**未实现** · 规格底本）

> 状态：**尚未实现**。本目录目前只有这份规格 CLAUDE.md，无代码。落地前请先对齐本文件。

LearnForge 的**首个真 tool-calling ReAct 工具**：只读地上网/查资料/调外部工具回答时效性问题，
与 qa（库内检索增强）和 diagnosis（只读诊断学习）都不同。

## 计划职责边界

- **做（设想）**：面向"需要外部实时信息/多步检索"的问题，跑真 tool-calling ReAct（fetch / 搜索 / 必要时 gpt-image），
  产带来源的可核验结论。**只读**——不写掌握度/路径。
- **不做**：不替代 qa（库内八股/概念走 qa）；不替代 diagnosis（诊断学习走 diagnose）；不做编排（那是 Manager）。

## 落地时要补齐的三层

| 层 | 待建 |
|---|---|
| tool 契约 | `../../contracts/agents/research.py`（ResearchInput/Output：query → 结论 + 来源 + 轨迹） |
| 运行时人格 | SkillRegistry 加 `AgentId.RESEARCH`（system_prompt + 工具权限白名单 + model_tier） |
| 实现 | `research/research_agent.py`（复用 `../react/loop.py` 的 ReAct 循环）+ `__init__.py` 再导出 |

## 落地约束（必须遵守）

- **只读**：像 diagnosis 一样，绝不写 `knowledge_atoms` / `learning_paths`（Manager 才是唯一写者）。
- **工具权限**：经 `BaseAgent.require_tool()` 白名单门禁；外部调用走 `../../tools/mcp/`（fetch / gpt-image 已在 session 配过）。
- **链路永远通**：无 key / 无网络 → 确定性兜底（abstain 或退回 qa），不抛断链。
- 接入意图层：`../../intent/dispatch.py` 的 `_CAPS` 加 `research`，分类 prompt 区分"需外部实时信息"才走 research。

设计讨论见 memory `learnforge-research-react-track`。
