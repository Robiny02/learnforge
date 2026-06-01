# LearnForge：Research / Diagnosis / Report 与首个真 tool-calling ReAct 设计

> 本轮只做设计与少量基础设施搭建（会话内 MCP），不大规模改代码。判断均基于已读源码并标注 `文件:行`。

## 架构图（gpt-image 经 OpenRouter 生成，风格库 `runtime/skills/gpt-image-2-style-library`）

**源项目 Reactor-agent：策略模式选 ReAct/Plan-Solve/Flow + Skill 三级渐进 + 统一 MCP 注册表**

![Reactor-agent architecture](assets/reactor-agent-architecture.png)

**LearnForge 目标：ResearchAgent 的 tool-calling ReAct → Report 链路（只读工具 + Manager 唯一写者边界）**

![LearnForge research-report chain](assets/learnforge-research-report-chain.png)

---

> 不变量全程守住：**Manager 唯一写者 / 无 key 链路永远通 / Py3.9 typing / 每条 LLM 路径有 stub / 所有工具进 MCP_REGISTRY 且过 require_tool**。

## 1. 现在哪个模块最适合先做 tool-calling ReAct？
**结论：都不要改存量，新增一个只读的 `ResearchAgent`（AgentId.RESEARCH）作为首发。** 理由（源码层面）：
- **Diagnosis 不合适**：`agents/diagnosis.py` 的“ReAct 三段式”是**假 ReAct**——`_ReActStep`（`:36-47`）只是轨迹日志，三步 LOAD_EVENTS→JOIN_MASTERY→CLUSTER_RANK 全是写死 Python（`run:59-88`），LLM 仅在末尾产建议（`:209`）。它**严格只读内部 `interaction_events`+`knowledge_atoms`，设计上就不需要外部工具**；改成 tool-calling 既无价值又会动它的只读契约（`test_diagnosis_is_read_only`）。
- **QA 不合适（且你已要求不动）**：`agents/qa/qa_agent.py:66-121` 是固定链 + 重测试（`tests/test_agent_capabilities.py`），改造回归风险高。
- **ResearchAgent 最合适**：**绿地**（无存量链路/测试要回归）、**天然多工具**（GitHub/Context7/Fetch/检索）、**天然只读**（不威胁唯一写者），且 LearnForge 已有底座可复用：`knowledge/github_markdown_crawler.py`、`knowledge/sources/remote_shared.py`、`RetrievalAgent`，连 `RetrievalMode.REACT`（`enums.py:161-171`）都已为 ReAct 回路预留语义。

> 诚实提醒（不盲从设想）：这条“研究→报告”链路是 LearnForge **新增的产品面**，略在现有“个人掌握度闭环”（QA/诊断/规划/mock）之外。建议把它定位成**只读 + 喂养共享知识库**的能力，**别与掌握度写路径耦合**——这恰恰也是它适合当“首个真 ReAct showcase”的原因：风险被隔离在绿地模块里。

## 2. 为什么不是 QA？
- QA 已是被 skill 规则选定的**固定链**，LLM 无自主工具选择权；改它要重排已稳定的链路 + 一大批断言（chitchat 跳检索、缺权限抛错等），回归面大。
- QA 的核心诉求是“带引用的可核验问答”，不是“多步外部研究 + 报告产出”；把 ReAct 强加给 QA 是错配。
- 本轮已明确“QA 先不要动”。✅

## 3. 推荐目标架构（代码层单 Agent + 工具；产品层 multi-agent）
- **代码层**：一个 `ResearchAgent` 通过 **tool call** 编排多个专家能力，**不是多 Agent 互相聊天**。
- **产品层**：对外叫 multi-agent —— Deep Research / GitHub Research / Report Generator / Summary 各司其职（实为 ResearchAgent 调用的不同工具/能力）。
- **与 Manager 关系**：ResearchAgent 是 Manager 可派发的**只读 worker**（新增 `agent.research`）。Manager 只负责派发与（可选）把研究产物经既有 `knowledge/ingest.py` 落共享库——**写动作仍只在 Manager 侧，ResearchAgent 一行都不写 state**。Manager 的 plan/replan/写边界**不动**。

## 4. 主 Agent + 专家工具/远端能力 调用链
```
Manager.dispatch(agent.research)
  └─ ResearchAgent（ReAct 主循环：think → tool_use → observe → …）
       ├─ github.repo_read   （GitHub MCP/REST：README、目录树、关键源码、issues、PR、commits）
       ├─ context7.docs      （查最新框架/库文档，治“过时”）
       ├─ fetch.url          （读官方文档/技术博客/用户给的网页）
       ├─ retrieval.search   （查本地/共享知识库已有资料，去重、补背景）
       ├─ repository.read.*  （只读用户自己项目源码，做项目分析）
       ├─ skill.load / skill.read_file / skill.grep（按需拉 SKILL.md 与 references）
       └─ report.generate    （收集完资料后，生成结构化 Markdown 报告）
  → Summary 收口（最终 ResearchOutput：结构化报告 + 引用 + “LearnForge 可借鉴点”）
```
主 Agent **不直接写完整报告**：先用 research 工具收集 → 再调 `report.generate` → 最后 summary 收口。

## 5. GitHub Tech Research 功能设计
- **输入**：GitHub repo URL（`ResearchInput{repo_url, goal?}`）。
- **工具**：`github.repo_read`（优先 GitHub MCP；无 MCP 时退到 GitHub REST API——注意本机 **`gh` CLI 不可用**，用 raw/API + 现有 `knowledge/github_markdown_crawler.py` 作 markdown 抓取底座）、`context7.docs`、`fetch.url`、`retrieval.search`。
- **流程**：读 README+目录树 → 选关键源码/配置抓取 → 必要时 context7 查框架版本特性、fetch 读官方文档 → 汇总。
- **输出**：结构化三段——**最新技术点 / 架构设计 / LearnForge 可借鉴点**；每条带来源引用。可选：把要点经 Manager→`ingest.py` 沉淀进 SHARED KB（只读 agent 不自己写）。

## 6. Report Generation 功能设计
- `report.generate` 是 **COMPUTE 工具**（LLM 背后，**不写任何 state**），入参=已收集的结构化素材，出参=结构化 Markdown（分节：概览/技术点/架构/可借鉴点/参考来源）。
- **MVP 只做 Markdown**；HTML/PPT 列为后续（同一工具加 `format` 参数，渲染层后接）。
- 主 Agent 调用顺序固化在 **SKILL.md**（见 §7），而非散落在 prompt：research 工具收集 → report.generate → summary。

## 7. Skill 渐进加载设计（文件式三级）
- **目录**：`learnforge/runtime/skills/tech-research/{SKILL.md, references/*.md}`；`SKILL.md` frontmatter（`name`/`description`）+ 正文写 **“何时用哪个工具、调用顺序、输出格式、约束”**（不是普通 prompt）。
- **L1 常驻**：把 file-skills 的 `name+description` 注入系统提示——在 `memory/context_assembler.py` 的 `cacheable_prefix` 加 `skills_index` 稳定段。
- **L2 按需**：`skill.load(skill_name)` → 返回 SKILL.md 正文。
- **L3 按需**：`skill.read_file/grep/glob` 读 references，经新增 `SkillPathGuard`（限定 skill 根目录 + 字符/条数上限）。越界→error tool_result。
- **装配**：新增 `skills/file_skills.py`（`FileSkillRegistry`）与现有 `SKILL_REGISTRY`（SkillSpec，管权限/模型档/契约）**并存**。`skill.*` 工具登记进 MCP_REGISTRY（namespace `skill`，READ）→ 能被 §4 的 ReAct 循环**自主调用**，渐进加载与自主加载就此闭环。

## 8. MCP / Tool Registry 设计
扩展现有 `MCP_REGISTRY`（已有 name/description/effect/owner/audit_required/input|output_schema/server，`mcp/base.py:31`），不另起炉灶：
- **运行时 handler 表**（新 `mcp/tools.py` 的 `TOOL_RUNTIME`）：`name → Callable[[dict],dict]`；ToolSpec 管元数据，TOOL_RUNTIME 管真执行。
- **新 namespace**：`skill`（READ）；外部 MCP 工具动态注册为 `ToolSpec(server=..., effect=EXTERNAL, audit_required=True, owner_agents=["research"])`。
- **暴露给 LLM = 交集**：`skill.allows(name)` ∧ `name in TOOL_RUNTIME`；`to_anthropic_tools(specs)` 把 `input_schema`（pydantic）转 Anthropic tool schema。
- **权限门 + 不崩溃**：每次调用先 `require_tool`（`agents/base.py:74`），越权→捕获 `SkillPermissionError`→以 `tool_result{is_error:true}` 回灌 LLM（不中断）；handler 异常同样转 error tool_result；`permission_audit` 记录每次调用。
- **真 MCPClient**：用官方 `mcp` Python SDK stdio client 实现 `mcp/base.py` 的 `call/list_tools/connect`，配置 `LF_MCP_SERVERS`；未配置/连不上→不注册、不暴露（离线降级）。
- **第一阶段只读**：GitHub（读 repo/file/issues/PR）、Context7（读文档）、Fetch（读网页）、本地 repository.read（读用户项目）；**禁止任何写/改代码/提 PR，禁止触碰 Manager 写路径**。

## 9. 如何保证 Manager 唯一写者？
- ResearchAgent ∈ 只读 agent；其 skill **不声明任何 `repository.write.*`**（注册期 + `test_agent_capabilities.py:86-91` 范式强制），工具集经 `skill.allows` 过滤 ⇒ **物理上无法 emit 写工具**。
- 外部 MCP 工具一律 EXTERNAL/READ，owner 限 `research`，`may_declare` 拒绝 Manager 写路径声明。
- `report.generate` 是 COMPUTE、返回文本，不落库；研究产物若要进 SHARED KB，走 **Manager → 既有 `knowledge/ingest.py`**，不在 ResearchAgent 内写。
- 守护测试：`ResearchAgent` 工具集 ∩ `MCP_REGISTRY.write_tools()` == ∅。

## 10. 没有 API key / MCP 不可用时如何降级？
- 无 `ANTHROPIC_API_KEY`：`complete_with_tools` 抛 `LLMUnavailable` → ResearchAgent 返回**确定性 stub**（基于 retrieval/crawler 的纯模板摘要，不调 LLM），链路永远通。
- MCP 未配/连不上：外部工具不注册；ReAct 仍用本地工具（retrieval.search / repository.read / skill.* / github_markdown_crawler 直读）。
- skill 目录缺失：`skill.*` 返回温和 "no skills" tool_result。
- handler 异常/越权/超步数预算（`AGENT_BOUNDS`）：转 error tool_result 或安全收尾，绝不崩溃。

## 11. 最小 MVP 实现顺序
| 步 | 内容 | 关键文件 | 判据 |
|---|---|---|---|
| 1 | `complete_with_tools` 单回合 tool-use 传输 | `llm/client.py` | 单测（mock anthropic）：`tools` 透传、tool_use/end_turn 解析 |
| 2 | `TOOL_RUNTIME` + `to_anthropic_tools` + 注册 `retrieval.search`/`fetch.url`/`report.generate`/`skill.*` | `mcp/tools.py`、`mcp/registry.py` | handler 可调、schema 正确、`is_known` 通过 |
| 3 | `ReactRunner`（循环+预算+权限门） + `ResearchAgent`（AgentId.RESEARCH） + `ResearchInput/Output` + research skill；Manager 加 `agent.research` 派发 | `agents/react/loop.py`、`agents/research.py`、`contracts/agents/research.py`、`enums.py`、`config.py`、`skills/definitions` | **spy 测试**：脚本化假 LLM emit `fetch.url`/`retrieval.search` tool_use → 断言**真 handler 被调用**、tool_result 回灌、最终报告含抓取内容 |
| 4 | 文件式 Skill：`FileSkillRegistry` + `SkillPathGuard` + `skill.*` + L1 注入 + `runtime/skills/tech-research/SKILL.md` | `skills/file_skills.py`、`skills/path_guard.py`、`memory/context_assembler.py` | L1 只暴露 name+desc；`skill.load` 返正文；越界被拒 |
| 5 | 真接一个只读外部 MCP（**推荐 Fetch 先，最简单**；GitHub 次之） + `LF_MCP_SERVERS` + 降级 | `mcp/base.py`、`mcp/registry.py` | 配了：trace 出现外部 tool_use；没配：跳过且链路通 |
| 6 | `report.generate`（Markdown）+ summary 收口 + GitHub repo_read | `mcp/tools.py`、`agents/research.py`、复用 `knowledge/github_markdown_crawler.py` | 给一个 repo URL → 产出三段结构化报告 + 引用 |

## 12. 如何验证“真的 tool call”而非 prompt 假装？
1. **Trace 可观测**：每次 tool_use 的 name/args/result 落 `permission_audit` + `agent_traces`，运行后能看到完整工具调用序列。
2. **Spy 测试**：脚本化假 LLM 第一回合 emit `fetch.url`（或 `github.repo_read`）tool_use、第二回合 end_turn；断言被 monkeypatch 的真 handler **确实被调用**，其返回作为 tool_result 进入第二回合 messages，且最终报告引用了该内容。
3. **权限测试**：假 LLM emit 写工具 tool_use → runner 返回 error tool_result、**DB 无写入、无崩溃**。
4. **真 key 手验**：给一个 GitHub repo URL，trace 出现 ≥1 次外部 tool_use（github/fetch/context7），报告含“最新技术点/架构/可借鉴点”三段。

---

## 附：会话内 MCP 搭建记录（本轮基础设施）
- **配置位置**：当前 Claude Code 会话（`claude mcp add`）。
- **Fetch**（免 key）：读网页/文档/博客。
- **GitHub**（只读 PAT）：读 repo/目录/源码/issues/PR/commits。
- **GPT 图像生成**（`OPENAI_API_KEY`）：渲染架构图，配合 `runtime/skills/gpt-image-2-style-library` 风格库。
- 详细搭建步骤见仓库 `docs/assets/` 旁的架构图产物与本设计的实现阶段。
