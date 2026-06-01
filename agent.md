# LearnForge Agent 架构说明

## 任务背景

LearnForge 是一个面向程序员的多智能体学习伴侣，目标不是简单聊天，而是围绕“学习、诊断、规划、模拟面试、知识检索”形成可持续反馈闭环。系统把用户的问答、模拟面试表现、学习路径和长期记忆沉淀到本地数据库与 markdown 记忆文件中，再通过诊断和规划不断调整后续学习安排。

项目当前是一个 Python 包，核心目录在 `learnforge/learnforge/`。它使用 LangGraph 编排多 agent 工作流，FastAPI 暴露 HTTP 接口，SQLite 作为默认本地状态与知识库，FTS5 和 sqlite-vec 支持全文与向量检索。没有 `ANTHROPIC_API_KEY` 时，LLM 调用会降级到确定性 fallback，保证“链路永远通”。

## 核心目标

1. 提供程序员学习场景的一站式智能体系统：问答、规划、诊断、模拟面试和复盘。
2. 将个人学习信号沉淀为可检索、可诊断、可调整的长期状态。
3. 用严格的层级式多 agent 架构控制副作用，避免多个 agent 同时写共享状态。
4. 支持离线运行，保证测试和基本链路不依赖外部模型服务。
5. 为未来远程知识库、PostgreSQL/pgvector、MCP 工具和更强评测体系预留扩展点。

## 总体架构

LearnForge 采用 hierarchical multi-agent 架构。`ManagerAgent` 是唯一总调度者，也是共享学习状态的唯一写者。四类领域 worker 由 Manager 调度，`RetrievalAgent` 作为横切检索能力被 QA 等 agent 调用，但不直接进入 Manager 的任务调度列表。

```text
用户请求
  |
  v
LangGraph 主图
  START -> manager_plan -> manager_execute -> manager_aggregate -> END
                  |
                  v
             ManagerAgent
                  |
        +---------+----------+------------+----------------+
        |                    |            |                |
      QAAgent          PlanningAgent  DiagnosisAgent  MockInterviewAgent
        |                                               |
        v                                               v
 Router -> Retrieval -> Synthesizer -> Verifier    Mock LangGraph 子图
        |
        v
 RetrievalAgent
        |
 LocalUserSource / LocalSharedSource / RemoteSharedSource / PostgreSQL 扩展
```

系统设计上共有 13 个 agent：

| 层级 | Agent | 职责 |
| --- | --- | --- |
| Orchestrator | `ManagerAgent` | 规划、执行、聚合、重规划、唯一写共享状态 |
| QA | `QAAgent` | 问答子系统外壳 |
| QA subagents | `RouterAgent`, `SynthesizerAgent`, `VerifierAgent` | 问题路由、答案合成、事实校验 |
| Planning | `PlanningAgent` | 生成或修改学习路径，输出增量 `PathDiff` |
| Mock | `MockInterviewAgent` | 模拟面试外壳 |
| Mock subagents | `InterviewerAgent`, `JudgeAgent`, `StrategistAgent`, `CoachAgent` | 出题、评分、策略调整、复盘 |
| Diagnosis | `DiagnosisAgent` | 只读弱点诊断 |
| Retrieval | `RetrievalAgent` | 统一知识检索入口，不负责写状态或合成答案 |

## 主工作流

主图定义在 `learnforge/learnforge/graph/main_graph.py` 和 `learnforge/learnforge/graph/nodes.py`：

1. `manager_plan`：读取稳定记忆和会话短期记忆，然后由 Manager 将用户输入拆成任务 DAG。
2. `manager_execute`：按依赖顺序执行 worker，必要时触发 replan，最多重规划 `MAX_REPLAN` 次。
3. `manager_aggregate`：把 worker 结果聚合成最终回复、引用和下一步动作，并写入会话短期记忆。

典型单意图会被路由到一个 worker，例如问答走 QA，诊断走 Diagnosis，计划走 Planning。复合意图会产生多步 DAG，例如“快面试了帮我准备一下”会走：

```text
DiagnosisAgent -> PlanningAgent.modify -> 建议用户启动 mock
```

如果诊断数据不足，Manager 会跳过修改计划，建议先做模拟面试来采集信号。系统不会自动启动 mock，控制权交还给用户。

## Manager 的关键职责

`ManagerAgent` 位于 `learnforge/learnforge/agents/manager.py`，是系统最重要的边界：

- 规划任务 DAG：LLM 可用时结构化规划，不可用时关键词 fallback。
- 调度 worker：统一封装 `dispatch()`，写 trace，聚合成本与置信度。
- 唯一写者：学习路径、mastery 分数、诊断快照、关键事件都由 Manager 收口写入。
- 复合 workflow：处理诊断到改计划再到建议 mock 的跨 agent 编排。
- mock 结算：模拟面试完成后提交 mastery 更新，并自动触发一次只读 post-mock 诊断。
- 记忆入口：每轮开始读取稳定记忆与会话记忆，每轮结束更新会话摘要。

这个设计的核心不变量是：领域 agent 可以读数据、产出建议或 diff，但不能直接改共享学习状态。真正改变用户学习状态的动作必须回到 Manager。

## QA 子系统

QA 入口是 `learnforge/learnforge/agents/qa/qa_agent.py`。它由四段组成：

```text
RouterAgent -> RetrievalAgent -> SynthesizerAgent -> VerifierAgent
```

Router 判断问题类型和是否需要检索。概念类、时效类问题默认查共享知识库；项目类问题会同时查本地用户库和共享知识库。Synthesizer 基于召回内容生成答案，Verifier 对需要校验的答案做验证。QA 输出会带 citations、verdict、confidence 和 topic，Manager 会把有价值的 QA 结果沉淀到 daily memory，并把弱信号写入事件流。

## Retrieval 与知识库

`RetrievalAgent` 是统一检索入口，位于 `learnforge/learnforge/agents/retrieval.py`。它只负责召回和排序，不合成答案，不写学习状态。

支持的知识范围：

- `KnowledgeScope.LOCAL`：本地用户库，例如历史问答、mock turns、daily memory 索引。
- `KnowledgeScope.SHARED`：共享知识库，例如课程、博客、文档切片、面经和公共知识点。

支持的检索方式：

- `keyword`：关键词或子串兜底。
- `fulltext`：SQLite FTS5 BM25。
- `vector`：sqlite-vec KNN。
- `hybrid`：全文和向量检索融合，默认使用 RRF。

可插拔组件包括：

- `KnowledgeSource`：`LocalUserSource`、`LocalSharedSource`、`RemoteSharedSource`，以及 PostgreSQL source 扩展。
- `EmbeddingProvider`：OpenAI、Voyage 或 Null provider。无 key 时向量检索会降级。
- `Reranker`：默认 `NoOpReranker`，可选 LLM rerank。

当前代码还包含 `workflow_v2` 和 `react` 检索模式。它们在固定 workflow 基础上加入查询改写、结果采纳闸门和有限重试，用于提高个人记忆或弱召回场景的稳健性。

## Diagnosis 诊断

`DiagnosisAgent` 位于 `learnforge/learnforge/agents/diagnosis.py`，严格只读。它读取 `interaction_events` 和 `knowledge_atoms`，用三段式 ReAct 聚合弱点：

1. `LOAD_EVENTS`：读取时间窗口内的 QA/mock 弱点事件。
2. `JOIN_MASTERY`：连接 atom 掌握度，并计算时间衰减后的 effective mastery。
3. `CLUSTER_RANK`：按 `(1 - mastery) * recency * error_freq` 排序弱点，生成簇和建议。

数据不足时，Diagnosis 不硬凑结论，而是返回低置信度和采集更多信号的建议。诊断报告本身由 Manager 写入 `diagnosis_reports`，保持只读 agent 的边界。

## Planning 学习路径

`PlanningAgent` 位于 `learnforge/learnforge/agents/planning.py`。它支持两种模式：

- `GENERATE`：根据目标生成学习路径。
- `MODIFY`：根据诊断结果和现有路径生成增量调整。

Planning 只输出 `PathDiff`，不直接写 `learning_paths` 或 `path_items`。Manager 会解析 diff 并提交路径变化，同时写入 `path_changed` 事件和 daily decision memory。

## Mock 模拟面试

Mock 子图定义在 `learnforge/learnforge/graph/mock_graph.py`，是一个带 interrupt/resume 的 LangGraph 状态机：

```text
S1_INIT
  -> S2_INTERVIEWER_TURN
  -> S3_AWAIT_USER interrupt
  -> S4_JUDGE
  -> S5_STRATEGIST
       -> continue / raise / lower / switch_topic 回 S2
       -> pause 进入 S_PAUSE interrupt
       -> end 或达到 max_turns 进入 S6_COACH_REVIEW
       -> escalate 交回 Manager
  -> S7_SETTLE
  -> END
```

Mock 的本地状态包括当前题目、轮次、难度、已覆盖 topic、逐轮分数和待结算 mastery updates。子图可以写 append-only 的 mock turns 和 interaction events，但 mastery 的最终提交仍由 Manager 完成。

## 记忆系统

当前代码中有三层记忆：

1. 稳定规则层：`data/memory/MEMORY.md`，只在用户明确说“记住/请记住/以后...”时写入。
2. 会话短期记忆：`session_state` 表保存滚动摘要和最近 N 轮原文，用于跨轮连续性。
3. 长期内容记忆：daily markdown 和 `chunks` 索引共同构成可检索长期记忆。

`MemoryStore` 本身不是长期存储系统，而是负责渲染稳定 prefix 和生成短摘要。长期记忆统一通过仓储、chunks、FTS/向量索引和 RetrievalAgent 查询。

## 存储层

SQLite DDL 在 `learnforge/learnforge/storage/schema.sql`。主要表包括：

- `knowledge_atoms`：知识点和 mastery 状态。
- `chunks`、`chunk_fts`、`chunk_vectors`：共享/本地语料切片和索引。
- `qa_history`、`qa_history_fts`：历史问答。
- `interaction_events`：QA、mock、path change 等 append-only 事件。
- `mock_sessions`、`mock_turns`：模拟面试状态和逐轮记录。
- `learning_paths`、`path_items`：学习路径。
- `diagnosis_reports`：诊断快照。
- `agent_traces`：跨 agent 调用链路追踪。
- `session_state`：短期会话记忆。

仓储实现位于 `learnforge/learnforge/storage/repositories.py`，数据库初始化位于 `learnforge/learnforge/storage/db.py`。如果 sqlite-vec 不可用，系统会退化为 FTS5-only 模式。

## API、CLI 与评测

FastAPI 入口在 `learnforge/learnforge/app.py`，主要接口：

- `POST /qa`
- `POST /plan`
- `POST /diagnose`
- `POST /mock`

CLI 入口在 `learnforge/learnforge/cli.py`，用于本地交互式体验。评测脚手架在 `learnforge/learnforge/eval/`，覆盖诊断、语义、轨迹、检索模式和答案质量等维度。测试位于 `learnforge/tests/`，重点验证结构契约、离线可跑、只读边界和复合工作流。

常用命令需要在 Python 包目录执行：

```bash
cd learnforge
pip install -e ".[dev]"
pytest
python -m learnforge.graph.main_graph
uvicorn learnforge.app:api --reload
ruff check learnforge tests
```

## 关键设计不变量

- Manager 是唯一 orchestrator，也是唯一共享状态写者。
- Diagnosis 只能读，不能写 mastery、learning path 或 profile。
- Planning 只产出 diff，不直接落库。
- Retrieval 只召回排序，不合成答案，不写学习状态。
- Mock 的 mastery updates 必须由 Manager settle 后提交。
- 所有 agent 都有离线 fallback，测试不依赖外部 API key。
- 所有 `*_id` 使用 `str(uuid4)`，SQLite 时间戳使用 UTC ISO8601 文本。
- Python 类型注解保持兼容风格，源码倾向使用 `Optional/List/Union`，避免强依赖新语法。

## 扩展方向

1. 增强远程共享知识库：通过 `RemoteSharedSource` 或 PostgreSQL/pgvector source 扩展。
2. 强化评测闭环：继续扩展 `eval/` 下的 golden case 和端到端轨迹评估。
3. 改进检索质量：调优 query rewrite、intent gate、RRF、rerank 和中文检索策略。
4. 持久化 mock checkpoint：当前子图默认可用 MemorySaver，生产环境可替换为持久 checkpointer。
5. 接入 MCP 工具：`mcp/` 已保留注册和协议抽象，可用于外部工具调用。
6. 丰富 SkillRegistry：让各 agent 的 prompt、模型档位、工具权限和策略规则更配置化。

