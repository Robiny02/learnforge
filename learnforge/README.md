# LearnForge

面向程序员的学习伙伴 —— **orchestrator + tools** 多智能体系统。

> 在无 `OPENROUTER_API_KEY` 时优雅降级到确定性兜底（"链路永远通"），测试全程离线可跑。
> 完整架构与工程约定见仓库根目录 [`../README.md`](../README.md) 与 [`../CLAUDE.md`](../CLAUDE.md)。

## 架构总览

`ManagerAgent` 是**唯一调度者**，也是共享状态（mastery / learning_paths）的**唯一写者**；
每个领域能力是它调用的一个**工具**（agent-as-tool）：

- **qa** — 检索增强问答（Router → Synthesizer → Verifier），带引用、可核验。
- **diagnose** — 只读弱点诊断（ReAct 三段式），绝不改路径或 atom。
- **plan** — 生成 / 修改学习路径，只产增量 `PathDiff`。
- **mock** — 多轮模拟面试（Interviewer / Judge / Strategist / Coach，LangGraph 子图）。
- **retrieval** — 横切共享检索能力，不被 Manager 直接调度。

> Router / Synthesizer / Judge / Coach 等是**工具内部的子步骤**，不是独立工具。
> `research` 已设计但尚未实现。

## 双层知识库与统一检索

RetrievalAgent 是一层可扩展的 RAG 检索架构，支持双层知识体系：

- **本地用户库**（`KnowledgeScope.LOCAL`）：个人学习数据——mock 记录、历史问答等。
- **共享知识库**（`KnowledgeScope.SHARED`）：可复用公共内容——课程/博客/面经/文档切片、公共知识点。

可插拔组件：
- **KnowledgeSource**（`knowledge/sources/`）：`LocalUserSource` / `LocalSharedSource` /
  `RemoteSharedSource`（远程库，env `LF_REMOTE_KB_URL` 配置；未配置则离线不参与）。
- **检索方式**（`RetrievalMethod`）：`keyword` / `fulltext`(FTS5 BM25) / `vector`(sqlite-vec KNN) /
  `hybrid`(全文+向量 RRF 融合，默认)，并支持 `RetrievalFilters` 的 metadata 过滤。
- **EmbeddingProvider**（`llm/embeddings.py`）：env `LF_EMBEDDING_PROVIDER`∈{none,openai,voyage}；
  无 key 时离线降级（向量分路自动跳过，退化到 FTS）。
- **Reranker**（`knowledge/rerank.py`）：默认 `NoOpReranker`，可选 `LLMReranker`（扩展位）。

灌库（仅写知识内容与索引，不触碰 mastery，保持 Manager 唯一写者约束）：

```bash
python -m learnforge.knowledge.ingest --file docs.json   # 切片+embedding+写 chunks/FTS/向量
python -m learnforge.knowledge.ingest --sync-atoms       # 同步 atom FTS/向量索引
```

## 交互式 CLI（像素标题 + 多 agent 实时编排可视化）

零依赖纯 ANSI 终端界面：顶部像素块标题，下方与 ManagerAgent 对话，每次请求可见
PLAN 的 DAG、各 worker 结果、聚合回复与 trace_id。离线可跑（无 key 走确定性兜底）。

```bash
python -m learnforge.seed_demo           # （首次）灌入演示种子库：八股 atom/chunk + 弱点信号
python -m learnforge.cli                 # 默认库 learnforge.db（缺表自动建）
python -m learnforge.cli --db demo.db    # 指定 SQLite 库
```

种子库让 QA 检索有真实引用、诊断/规划有候选与信号。CJK 检索靠关键词/子串兜底（FTS5 不切中文），
故用「乐观锁」「线程池」「缓存穿透」「TCP」这类关键词查询命中最佳。

交互：直接输入问题/需求（Manager 自动编排）；`/mock <topic>` 多轮模拟面试
（`/pause` `/switch <t>` `/end` `/back`）；`/diagnose` 只读诊断；`/plan <goal>` 生成计划；
`/help` `/clear` `/quit`。

## 评测脚手架（T-EV，离线可复现指标）

```bash
python -m learnforge.eval.diagnosis_eval    # 诊断 Recall@5 + 时间衰减/掌握度判别性检查
python -m learnforge.eval.trajectory_eval   # 端到端拓扑 + 数据回流 + 写边界不变量
```

## 目录结构

```
learnforge/
├── contracts/        # ★ 所有数据契约（先于实现）：消息信封 / Atom / 状态 / 各 agent in&out
├── agents/           # 各工具实现：qa / diagnose / plan / mock + 共享 retrieval
│   ├── qa/           #   QAAgent + Router/Synthesizer/Verifier
│   └── mock/         #   MockInterviewAgent + Interviewer/Judge/Strategist/Coach
├── graph/            # LangGraph 主图骨架（compile + START→END）+ mock 子图占位
├── storage/          # SQLite DDL（atoms/chunks/qa_history/agent_traces + FTS5 + sqlite-vec）+ 仓储
├── knowledge/        # ★ 双层知识库：ingest（灌库）+ sources（可插拔知识源）+ rerank（重排）
├── llm/              # Haiku/Sonnet client + embeddings（可插拔 EmbeddingProvider）+ 成本统计
├── skills/           # ★ 预留：agent 专属能力（system_prompt + 模型 + 工具 + workflow），由 SkillRegistry 驱动
├── mcp/              # ★ 预留：外部工具 / MCP 接入（MVP Non-Goal，仅留接口前向兼容）
├── memory/           # ★ 预留：长期记忆 + 上下文分层 + prompt 拼装（design §6c）
├── config.py         # 模型分级 + 各 agent Bounds 常量
└── app.py            # FastAPI 入口（/qa /plan /mock 占位）
```

## 本地验证（骨架可 compile + START→END）

```bash
python -m venv .venv && source .venv/bin/activate
pip install pydantic langgraph
python -m learnforge.graph.main_graph    # 编译主图并跑一次 START→END（mock 节点）
```

## Phase 路线

1. **Phase 1（已完成）**：契约 + 骨架 + DDL + 可编译主图。
2. **Phase 2（已完成）**：自底向上实装 Retrieval → Manager → QA 子系统 → Planning。
3. **Phase 3（已完成）**：
   - **掌握度算法**（`mastery.py`）：指数更新(mock0.5/qa0.3/self0.15) + 时间遗忘 + 5 档映射。
   - **DiagnosisAgent（ReAct 三段式）**：`events → mastery(effective) → cluster/rank → 建议`，
     弱点 score=`(1-mastery)*recency*error_freq`，**严格只读**（有读前后 DB 不变断言）。
   - **Mock 子图（interrupt/resume）**：`graph/mock_graph.py` 的 S1-S7 状态机 + checkpoint;
     S3 `interrupt()` 等用户作答、`Command(resume=...)` 续跑;换 topic 不重置累积分;
     pause 可跨调用 resume;end/超 max_turns → 复盘结算;改计划/诊断 → escalate 回 Manager。
   - **复合 workflow 5.6**：诊断→改路径→建议 mock(控制权交回,不自动启 mock);诊断为空跳过
     modify 改建议先 mock;Manager 唯一写者 `commit_mastery`;mock 结算后 post_mock 自动诊断;
     `make_handoff_summary`(≤512tok) 供跨子系统 handoff。

### 验证

```bash
python -m learnforge.graph.main_graph   # 主图 plan→execute→aggregate（含复合 5.6）
# mock 多轮 interrupt/resume：见 agents/mock/mock_agent.py 的 start()/answer()
```
