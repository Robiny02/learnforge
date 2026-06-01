# LearnForge Tasks —— MVP 工程任务拆解

> 阶段:Task(回答"按什么顺序做、谁做、怎么验收")
> 上游依据:`LearnForge-Design.md`(简称 Design)、`LearnForge-Proposal.md`(决策上下文)
> 团队假设:**单人全职**,容量 ~5 人天/周,2 周 Sprint 按 80% 计 **~8 人天/Sprint**。无硬 deadline,质量优先。
> golden set 与 Atom 知识库**从零构建**;团队对 LangGraph 不熟,M1 前置 spike。
> 不引入 Design 之外的模块/字段;Proposal Non-Goals 仍不做。

---

## 1. Design Recap(≤300 字)

13 个 agent:ManagerAgent(plan-and-execute + replan≤2,唯一写者)统辖 QAAgent(RouterAgent/SynthesizerAgent/VerifierAgent)、PlanningAgent(generate+modify 合并)、MockInterview 子图(InterviewerAgent/JudgeAgent/StrategistAgent/CoachAgent)、DiagnosisAgent(只读);RetrievalAgent 为共享可插拔后端(本地 sqlite-vec + FTS5,RRF 融合,不 rerank)。数据模型:`AgentMessage` 信封 + `ContextEnvelope`(handoff_summary ≤512tok,Haiku 生成)、Knowledge Atom schema + 掌握度算法(连续分指数衰减,mock0.5/qa0.3/self0.15 + 时间遗忘 + 5 档映射)、SQLite 表 + agent_traces。五能力各有状态机 workflow(问答/生成/修改/mock/诊断)+ 复合 workflow 5.6。LangGraph runtime(看中 interrupt/resume),Haiku/Sonnet 分级。Q5 每场 mock 结束自动触发诊断。

---

## 2. Milestone Plan(里程碑规划)

按**可独立验证的能力**切分,基础设施折叠进首个用到它的能力里(M1)。

### M1 — 问答能力端到端跑通(含基础设施)
- **目标**:用户提问能得到带引用、经校验的答案;沿途打通数据层、检索、消息协议、最小 Manager。
- **入口条件**:LangGraph spike 完成、Python 3.11 环境就绪。
- **出口条件(DoD)**:`/qa` 端点对内置 20 题 happy path 全过;Router 分类、RRF 检索、Synth+Verifier 链路在 trace 中可见;检索超时降级可复现(Design §2c-C)。
- **预估**:18-26 人天(乐观-悲观)。
- **关键风险**:LangGraph 不熟 + RetrievalAgent 融合调参,首里程碑工期不确定性最高。

### M2 — 学习路径生成与修改
- **目标**:用户给目标/现状产出分天路径,且能基于诊断/反馈 diff 重排。
- **入口条件**:M1 完成(消息协议、Atom 仓储、Manager 单派可复用)。
- **出口条件(DoD)**:`/plan` 端点 generate 与 modify 两分支均产出合法 PathDiff 并经 Manager 单点落库;掌握度算法单测通过;needs_input 追问回路可复现。
- **预估**:9-13 人天。
- **关键风险**:掌握度算法与优先级排序质量,需 golden set 校验。

### M3 — 模拟面试基础闭环
- **目标**:多轮 mock 出题/追问/评分/复盘,支持换 topic 与 pause/resume。
- **入口条件**:M1 完成(检索、消息协议);LangGraph 子图与 checkpoint 能力已在 spike 验证。
- **出口条件(DoD)**:`/mock` 多轮跑通,中途换 topic 不丢评分(Design §4d),pause 可跨会话 resume,终场出复盘;Judge 评分一致性脚本可运行。
- **预估**:13-19 人天。
- **关键风险**:interrupt/resume 状态机 + 多 sub-agent 单轮延迟 ≤5s。

### M4 — 弱点诊断 + 复合任务联调
- **目标**:诊断只读聚合产出弱点;Manager 完整 DAG 编排打通复合任务"快面试了帮我准备"。
- **入口条件**:M1/M2/M3 完成(诊断依赖 events、复合依赖 Planning 与 Diagnosis)。
- **出口条件(DoD)**:每场 mock 后自动触发诊断(Q5);复合 workflow 5.6 端到端跑通含 replan≤2;handoff_summary 生成并在跨子系统 escalate 时携带;部分成功能正确呈现(Design §8b)。
- **预估**:11-16 人天。
- **关键风险**:Manager plan 质量(R3),复合链路延迟累积(R1)。

### M5 — 评测与质量加固
- **目标**:全量评测脚手架、性能/成本达标、错误韧性、文档与 demo 齐备。
- **入口条件**:M1-M4 功能闭环。
- **出口条件(DoD)**:Trajectory Eval 通过;五能力 p95/成本达 Proposal §7 门槛;LLM/向量库降级路径验证;README/API/架构/runbook 齐全;复合任务现场 demo 可跑。
- **预估**:12-18 人天。
- **关键风险**:压测后指标不达标需返工调参。

**全程总估**:约 63-92 人天 ≈ 13-19 周(单人,含 buffer)。

---

## 3. Task Breakdown(核心)

字段:Task ID / Title / Description / Depends On / Blocks / Acceptance Criteria(AC)/ Estimate / Owner Skills / Risk。测试与评测任务统一列在 §6(独立追踪,防被砍)。

### M1 任务

**T-M1-01 完成 LangGraph spike 热身**
- Description:搭一个含 2 节点 + interrupt/resume + checkpoint 的最小图,验证 mock 所需的中断恢复能力(Design §5.4 前置)。
- Depends On:无 ｜ Blocks:T-M1-09, T-M3-01
- AC:① 图可 interrupt 在等待节点并 resume;② checkpoint 持久化后进程重启可续跑;③ 产出 1 页内部上手笔记。
- Estimate:2-3d ｜ Owner Skills:LangGraph ｜ Risk:High(不熟,缓解:限定 spike 范围、超 3d 即止并记录已知问题)

**T-M1-02 实现 Atom schema 与 SQLite 仓储接口**
- Description:按 Design §4d 建 `knowledge_atoms` 表与 CRUD 仓储,字段/默认值/约束齐全。
- Depends On:无 ｜ Blocks:T-M1-03, T-M1-14, T-M2-02
- AC:① 字段与 Design §4d 完全一致;② 仓储层屏蔽 SQL(为未来迁移 PG 预留);③ 迁移脚本可建表。
- Estimate:2d ｜ Owner Skills:Python/SQLite/Pydantic ｜ Risk:Low

**T-M1-03 集成 sqlite-vec 向量表与 FTS5 全文表**
- Description:建 `atom_vectors`(vec0,dim 1024)与 `atom_fts`(FTS5),按 Design §4c/§7a 配 HNSW 参数。
- Depends On:T-M1-02 ｜ Blocks:T-M1-05
- AC:① 向量 KNN 与 FTS5 match 各自可查;② HNSW 参数 M=16/ef=64 可配;③ Atom 写入同步建索引。
- Estimate:2d ｜ Owner Skills:sqlite-vec/FTS5 ｜ Risk:Medium(扩展加载与维度对齐,缓解:首日先验证 sqlite-vec 加载)

**T-M1-04 实现 AgentMessage 与 ContextEnvelope 协议**
- Description:按 Design §4a 实现消息信封、payload 三型、ContextEnvelope,带 Pydantic 校验与 schema_version。
- Depends On:无 ｜ Blocks:T-M1-09, T-M1-10, T-M2-03, T-M3-01, T-M4-04
- AC:① 三类 payload 校验通过/拒绝非法;② trace_id/message_id 自动生成;③ scoped_slice 与 handoff_summary 字段就位。
- Estimate:2d ｜ Owner Skills:Pydantic ｜ Risk:Low

**T-M1-05 实现 RetrievalAgent LocalBackend**
- Description:按 Design §3.6/§7b 实现向量+FTS5 召回、RRF(k=60)融合、条件查询改写;后端经 `backend` 参数抽象(云端留接口)。
- Depends On:T-M1-03 ｜ Blocks:T-M1-07, T-M1-08, T-M3-02, T-M4-02
- AC:① RRF 融合输出按分降序;② 长/模糊 query 触发一次 Haiku 改写,短 query 不触发;③ 向量或 FTS5 单边失败可降级;④ p95 ≤1.5s(小库)。
- Estimate:3d ｜ Owner Skills:检索/RRF/Haiku ｜ Risk:High(融合质量与延迟,缓解:先 RRF 默认参数跑通再调)

**T-M1-06 实现 RouterAgent 意图分类逻辑**
- Description:按 Design §3.3 用 Haiku 输出 q_type 与 retrieve/verify/rewrite 开关,固定枚举 JSON。
- Depends On:T-M1-04 ｜ Blocks:T-M1-09
- AC:① 四类问题正确开关;② temporal 强制 retrieve+verify;③ 解析失败回退保守全开;④ 输出 ≤64tok、≤1.5s。
- Estimate:1d ｜ Owner Skills:Haiku/Prompt ｜ Risk:Low

**T-M1-07 实现 SynthesizerAgent RAG 合成**
- Description:按 Design §3.4 用 Sonnet 基于检索证据合成答案并输出 claims,无证据时显式声明。
- Depends On:T-M1-05 ｜ Blocks:T-M1-08, T-M1-09
- AC:① 事实陈述绑定 source_chunk_id;② 无证据 temporal 产出 uncertain 模板;③ 输出 ≤1.5k tok、≤4s。
- Estimate:2d ｜ Owner Skills:Sonnet/RAG ｜ Risk:Medium

**T-M1-08 实现 VerifierAgent 事实把关**
- Description:按 Design §3.5 用 Haiku 逐 claim 校验,可补检索佐证,产出 verdict 与 flagged。
- Depends On:T-M1-05, T-M1-07 ｜ Blocks:T-M1-09
- AC:① 注入错误 claim 能被 flag;② 无支撑 claim 降级为不确定;③ 超时透传 draft 标 unverified;④ ≤3s。
- Estimate:2d ｜ Owner Skills:Haiku/Prompt ｜ Risk:Medium

**T-M1-09 实现 QAAgent 编排外壳与轻路径**
- Description:按 Design §3.2 串联 Router→Retrieval→Synth→Verifier,chitchat 走轻路径跳过检索/校验。
- Depends On:T-M1-01, T-M1-06, T-M1-07, T-M1-08 ｜ Blocks:T-M1-15
- AC:① 按 Router 开关动态跳过节点;② 轻路径 ≤3s;③ 输出 verdict+confidence;④ emit qa interaction_event。
- Estimate:2d ｜ Owner Skills:LangGraph ｜ Risk:Medium

**T-M1-10 实现最小 ManagerAgent 单意图直派**
- Description:实现 Design §3.1 的子集——识别单意图并直派对应 worker,暂不含 DAG/replan(留 T-M4-04)。
- Depends On:T-M1-04 ｜ Blocks:T-M1-15
- AC:① 单问答请求正确路由到 QAAgent;② 透传 trace_id;③ 返回结构化用户响应。
- Estimate:1d ｜ Owner Skills:Python ｜ Risk:Low

**T-M1-11 接入模型分级 client 与成本统计**
- Description:封装 Haiku/Sonnet 调用 client,按 agent 配置模型,统计 prompt/completion token 与 cost_usd。
- Depends On:无 ｜ Blocks:T-M1-12, T-M5-02
- AC:① 每次调用返回 token 与成本;② 模型按 agent 可配(Design §3 各 Bounds);③ 超时/429 抛可捕获异常。
- Estimate:1d ｜ Owner Skills:Anthropic SDK ｜ Risk:Low

**T-M1-12 实现 agent_traces 埋点基础**
- Description:按 Design §9a 写 `agent_traces`(message_id/trace_id/status/confidence/cost/latency)。
- Depends On:T-M1-11 ｜ Blocks:T-EV-10, T-M5-02
- AC:① 每次 agent 调用落一条 trace;② 可按 trace_id 拉全链路;③ 写 trace 失败不阻断主流程。
- Estimate:1d ｜ Owner Skills:Python/SQLite ｜ Risk:Low

**T-M1-13 实现 QA 检索超时降级路径**
- Description:实现 Design §2c-C/§8——检索超时改走无证据合成 + 强制 Verifier,返回 partial。
- Depends On:T-M1-09 ｜ Blocks:无
- AC:① 注入检索超时可触发降级;② 答案附"未检索到本地证据"声明;③ status=partial、confidence≤0.5。
- Estimate:1d ｜ Owner Skills:Python ｜ Risk:Low

**T-M1-14 构建 Atom 种子知识库导入管线**
- Description:按 Design §7c 实现切片(语义切/父子 chunk)+ embedding + 导入,灌入初始八股语料(从零)。
- Depends On:T-M1-02, T-M1-03 ｜ Blocks:T-EV-02
- AC:① 三类来源按 §7c 切片并带 metadata;② 至少导入 200 条可检索 Atom;③ 重复导入幂等。
- Estimate:3d ｜ Owner Skills:数据处理/embedding ｜ Risk:Medium(语料获取与质量,缓解:先小批量验证管线)

**T-M1-15 集成 /qa 端点与端到端联调**
- Description:用 FastAPI 暴露 `/qa`,串通 Manager→QAAgent 全链路并联调。
- Depends On:T-M1-09, T-M1-10 ｜ Blocks:T-EV-09
- AC:① `/qa` 返回答案+引用+verdict;② 20 题 happy path 全过;③ trace 可见完整链路。
- Estimate:2d ｜ Owner Skills:FastAPI ｜ Risk:Low

### M2 任务

**T-M2-01 实现 UserProfile 与路径表仓储**
- Description:按 Design §4b/§4c 建 `user_profile`、`learning_paths`、`path_items` 表与仓储。
- Depends On:T-M1-02 ｜ Blocks:T-M2-03, T-M2-05
- AC:① 表结构符合 Design;② path_items 含 order_idx/day_index/status;③ 读写仓储单测可调。
- Estimate:2d ｜ Owner Skills:SQLite/Pydantic ｜ Risk:Low

**T-M2-02 实现掌握度算法模块**
- Description:按 Design §4d 实现更新(加权 mock0.5/qa0.3/self0.15)、时间遗忘衰减、5 档映射。
- Depends On:T-M1-02 ｜ Blocks:T-M2-03, T-M3-07
- AC:① 更新/衰减/分档与 Design 公式一致;② 边界值 clamp 到 [0,1];③ 纯函数可单测。
- Estimate:2d ｜ Owner Skills:Python ｜ Risk:Low

**T-M2-03 实现 PlanningAgent generate 分支**
- Description:按 Design §3.7 从目标/现状选 Atom、排序、按天分桶,产出 PathDiff。
- Depends On:T-M1-04, T-M2-01, T-M2-02 ｜ Blocks:T-M2-07
- AC:① 输出合法 PathDiff(add/reorder);② 目标过大触发裁剪+提示;③ ≤20s、≤$0.10。
- Estimate:3d ｜ Owner Skills:Sonnet/Prompt ｜ Risk:Medium

**T-M2-04 实现 PlanningAgent modify 分支**
- Description:按 Design §3.7/§5.3 基于诊断+现有路径做 diff 重排,反馈与诊断冲突时以用户为准并标注。
- Depends On:T-M2-03 ｜ Blocks:T-M2-07, T-M4-06
- AC:① 仅产出 diff 不全量重写;② 冲突标注正确;③ 空 diff 返回"无需调整"。
- Estimate:2d ｜ Owner Skills:Sonnet/Prompt ｜ Risk:Medium

**T-M2-05 实现 Manager commit_path 单点写与冲突重试**
- Description:实现 Design §3.1/§8b——PlanningAgent 产 diff、Manager 唯一落库,冲突重试 1 次,失败整体不落库。
- Depends On:T-M2-01 ｜ Blocks:T-M2-07
- AC:① 仅 Manager 写 path(越权写被拒);② diff 校验非法则拒绝;③ commit 冲突重试且不半写。
- Estimate:2d ｜ Owner Skills:Python/事务 ｜ Risk:Medium

**T-M2-06 实现 needs_input 追问回路**
- Description:按 Design §5.2——现状信息不足时 Planning 返回 needs_input,Manager 向用户追问后再派。
- Depends On:T-M2-03 ｜ Blocks:无
- AC:① 现状缺失触发 needs_input;② Manager 生成追问问题;③ 补全后可续跑。
- Estimate:1d ｜ Owner Skills:Python ｜ Risk:Low

**T-M2-07 集成 /plan 端点与端到端联调**
- Description:暴露 `/plan`,联调 generate 与 modify 两路径。
- Depends On:T-M2-04, T-M2-05 ｜ Blocks:T-EV-09
- AC:① 两分支均端到端跑通;② 落库后可读回路径;③ trace 完整。
- Estimate:2d ｜ Owner Skills:FastAPI ｜ Risk:Low

### M3 任务

**T-M3-01 实现 MockState 与 LangGraph 子图骨架**
- Description:按 Design §5.4 搭 mock 子图(S1-S7 节点框架)+ checkpoint + interrupt 等待用户输入。
- Depends On:T-M1-01, T-M1-04 ｜ Blocks:T-M3-02..08
- AC:① 子图节点与状态机一致;② AWAIT_USER 可 interrupt/resume;③ MockState 持久化。
- Estimate:3d ｜ Owner Skills:LangGraph ｜ Risk:High(状态机复杂,缓解:复用 T-M1-01 spike 成果)

**T-M3-02 实现 InterviewerAgent 出题与追问**
- Description:按 Design §3.9 用 Sonnet 按主题/难度出题+追问,带 expected_points 与兜底题。
- Depends On:T-M3-01, T-M1-05 ｜ Blocks:T-M3-08
- AC:① 出题不重复、不超难度;② 输出考点与 atom_refs;③ 超时取预置题兜底;④ ≤5s。
- Estimate:2d ｜ Owner Skills:Sonnet ｜ Risk:Medium

**T-M3-03 实现 JudgeAgent 评分 rubric**
- Description:按 Design §3.10 用 Haiku 独立评分(0-5 档 + 三维 + 失分点),与出题分离。
- Depends On:T-M3-01 ｜ Blocks:T-M3-08, T-EV-06
- AC:① 输出固定 rubric JSON;② 不被回答自信措辞带偏;③ 解析失败给 null 不计一致性;④ ≤3s。
- Estimate:2d ｜ Owner Skills:Haiku/Prompt ｜ Risk:Medium

**T-M3-04 实现 StrategistAgent 难度策略与中断分类**
- Description:按 Design §3.11/Q4 决策 action(continue/raise/lower/switch_topic/pause/end/escalate),连续2轮规则调档。
- Depends On:T-M3-03 ｜ Blocks:T-M3-06
- AC:① 升降档规则正确;② 局部意图本地处理、跨能力 escalate;③ 歧义默认 continue 并澄清。
- Estimate:2d ｜ Owner Skills:Haiku/Prompt ｜ Risk:Medium

**T-M3-05 实现 CoachAgent 终场复盘与弱点事件**
- Description:按 Design §3.12 聚合逐轮评分产出复盘,每条 weakness 带 evidence,并 emit mock_weakness 事件。
- Depends On:T-M3-03 ｜ Blocks:T-M3-07, T-M4-01
- AC:① 复盘结构化且 weakness 带证据轮次;② 样本<2轮给"样本不足";③ emit 事件。
- Estimate:2d ｜ Owner Skills:Sonnet ｜ Risk:Low

**T-M3-06 实现 mock 中断与恢复**
- Description:按 Design §5.4——换topic/pause/skip 局部 resume(Judge 累积分不重置),end/改计划 escalate 给 Manager。
- Depends On:T-M3-04 ｜ Blocks:T-M3-08
- AC:① 换 topic 不丢评分;② pause 跨会话 resume;③ escalate 正确返回 Manager。
- Estimate:2d ｜ Owner Skills:LangGraph ｜ Risk:High(中断分支多,缓解:逐 action 写集成用例)

**T-M3-07 实现 mock 结算 S7**
- Description:按 Design §5.4——结束时 emit events 并由 Manager 更新 Atom 掌握度(唯一写)。
- Depends On:T-M3-05, T-M2-02 ｜ Blocks:T-M4-03
- AC:① 结算触发 mastery 更新经 Manager;② events 落 interaction_events;③ 中途超时也能部分结算。
- Estimate:1d ｜ Owner Skills:Python ｜ Risk:Low

**T-M3-08 集成 /mock 端点(多轮 SSE)与端到端联调**
- Description:暴露 `/mock` 支持多轮交互(SSE/轮询),联调完整一场 mock。
- Depends On:T-M3-02, T-M3-03, T-M3-06 ｜ Blocks:T-EV-09
- AC:① 多轮出题→评分→复盘跑通;② 单轮首字 ≤5s;③ 单场 ≤15min/≤10轮/≤$0.30。
- Estimate:2d ｜ Owner Skills:FastAPI/SSE ｜ Risk:Medium

### M4 任务

**T-M4-01 实现 interaction_events 事件日志写入**
- Description:按 Design §4c/§6a 在 QA 与 mock 路径写 append-only 事件,带 event_id 幂等。
- Depends On:T-M1-09, T-M3-05 ｜ Blocks:T-M4-02
- AC:① QA/mock 均正确 emit;② event_id 幂等去重;③ schema 符合 Design。
- Estimate:1d ｜ Owner Skills:Python/SQLite ｜ Risk:Low

**T-M4-02 实现 DiagnosisAgent 只读聚合**
- Description:按 Design §3.13/§5.5 按时间窗聚合 events+掌握度,产出弱点簇与建议,严格只读。
- Depends On:T-M4-01, T-M1-05 ｜ Blocks:T-M4-03, T-M4-06, T-EV-08
- AC:① 只读(无任何写);② 弱点 score 公式符合 Design;③ 数据不足给低置信;④ ≤10s。
- Estimate:3d ｜ Owner Skills:Python/聚合 ｜ Risk:Medium

**T-M4-03 实现 post_mock 自动诊断触发**
- Description:按 Q5——每场 mock 结算后自动触发一次诊断(trigger=post_mock),成本计入监控。
- Depends On:T-M3-07, T-M4-02 ｜ Blocks:无
- AC:① mock 结束自动跑诊断;② 诊断成本单独记账(Design §10a G3);③ 失败不影响 mock 结算。
- Estimate:1d ｜ Owner Skills:Python ｜ Risk:Low

**T-M4-04 实现 ManagerAgent 完整 plan-and-execute 与 replan**
- Description:按 Design §2b/§3.1 实现 LLM_plan 产 DAG、拓扑派发、聚合、失败 replan(≤2)。
- Depends On:T-M1-10, T-M1-04 ｜ Blocks:T-M4-06
- AC:① 复合请求拆出合法 DAG;② 无依赖子任务可并发派;③ 失败/低置信触发 replan,>2 终止报错。
- Estimate:3d ｜ Owner Skills:LangGraph/编排 ｜ Risk:High(plan 质量,缓解:用固定场景集回归 plan 正确性)

**T-M4-05 实现 handoff_summary 生成**
- Description:按 Q2/Design §6b 用 Haiku 生成 ≤512tok 摘要({intent,key_facts,constraints,open_items}),跨子系统 handoff 携带。
- Depends On:T-M1-11 ｜ Blocks:T-M4-06
- AC:① 输出 ≤512tok;② 结构化四字段;③ escalate/handoff 时注入 ContextEnvelope。
- Estimate:2d ｜ Owner Skills:Haiku/Prompt ｜ Risk:Medium

**T-M4-06 实现复合 workflow 5.6 端到端**
- Description:按 Design §5.6 打通"诊断→改路径→建议 mock",控制权不自动进 mock。
- Depends On:T-M2-04, T-M4-02, T-M4-04, T-M4-05 ｜ Blocks:T-EV-10
- AC:① "快面试了帮我准备"端到端跑通;② 诊断空时跳过 modify 改建议先 mock;③ 端到端 ≤40s。
- Estimate:2d ｜ Owner Skills:编排 ｜ Risk:Medium

**T-M4-07 实现 workflow 级失败回退**
- Description:按 Design §8b——复合任务中途失败保留已成功结果、未提交 diff 丢弃,返回 partial 并告知。
- Depends On:T-M4-04 ｜ Blocks:无
- AC:① 中途失败不半写 path;② 返回 status=partial+已完成项+trace_id;③ 可复现测试。
- Estimate:1d ｜ Owner Skills:Python ｜ Risk:Low

### M5 任务

**T-M5-01 实现 LLM/向量库降级与重试**
- Description:按 Design §8c——LLM 429/超时指数退避重试2次+降级,向量库异常仅 FTS5。
- Depends On:T-M1-11 ｜ Blocks:无
- AC:① 429 触发退避重试;② 持续失败降级标 error;③ 向量库故障注入仅 FTS5 可用。
- Estimate:2d ｜ Owner Skills:Python ｜ Risk:Medium

**T-M5-02 实现成本预算监控与超限告警**
- Description:按 Design §9a/§10a 每能力累计 token/成本,超 Proposal §7 预算(含 mock+诊断 $0.35)告警。
- Depends On:T-M1-12 ｜ Blocks:无
- AC:① 每能力成本可看;② 超限触发告警/标记;③ mock 含自动诊断成本归并正确。
- Estimate:1d ｜ Owner Skills:Python ｜ Risk:Low

**T-M5-03 压测 p95 延迟并调参**
- Description:按 Design §7a/§9——压测五能力 p95,调 ef_search/RRF/k 使达 Proposal §7 门槛。
- Depends On:T-EV-09 ｜ Blocks:T-M5-06
- AC:① 五能力 p95 达标(问答≤8s/mock单轮≤5s/计划≤20s);② 调参结果记录;③ 检索 ≤1.5s。
- Estimate:2-4d ｜ Owner Skills:性能/调参 ｜ Risk:High(不达标需返工,缓解:M5 早启,留返工 buffer)

**T-M5-04 实现 prompt assembly KV-cache prefix 优化**
- Description:按 Design §6c——按稳定→易变排序拼装,前4段做可缓存 prefix。
- Depends On:T-M1-09 ｜ Blocks:无
- AC:① 拼装顺序符合 §6c;② 安全/格式段不被 user input 覆盖;③ prefix 命中可观测。
- Estimate:1d ｜ Owner Skills:Prompt ｜ Risk:Low

**T-M5-05 编写 README/API/架构/运维 runbook**
- Description:产出上手 README、API 文档、内部架构说明、运维 runbook(启动/备份/故障处置)。
- Depends On:T-M4-06 ｜ Blocks:无
- AC:① 新人照 README 可本地起服务;② API 文档覆盖三端点;③ runbook 含降级与备份步骤。
- Estimate:2d ｜ Owner Skills:文档 ｜ Risk:Low

**T-M5-06 实现端到端 demo 脚本**
- Description:脚本化复合任务"快面试了帮我准备"全流程,供现场演示。
- Depends On:T-M4-06, T-M5-03 ｜ Blocks:无
- AC:① 一键跑完复合任务;② 输出诊断+重排路径+mock 建议;③ trace 可回放。
- Estimate:1d ｜ Owner Skills:Python ｜ Risk:Low

---

## 4. Dependency Graph(依赖关系)

**关键路径(决定最短工期)**:
> T-M1-01(spike)→ T-M1-02(Atom 仓储)→ T-M1-03(向量/FTS5)→ T-M1-05(RetrievalAgent)→ T-M1-07(Synth)→ T-M1-09(QAAgent)→ T-M1-15(QA 联调)→ T-M3-01(mock 子图)→ T-M4-02(诊断)→ T-M4-04(Manager DAG)→ T-M4-06(复合)→ T-M5-03(压测)→ T-M5-06(demo)

**并行机会(单人下作为可重排的独立块,多人时可真并行)**:
> - 基础设施期:T-M1-04(消息协议)、T-M1-11(模型 client)与 T-M1-02/03 无依赖,可穿插。
> - QA sub-agent:T-M1-06(Router)在 T-M1-05 未完成时即可先做(仅依赖 T-M1-04)。
> - M2 内:T-M2-01 与 T-M2-02 相互独立可并行。
> - mock sub-agent:T-M3-02/03/05 在 T-M3-01 完成后相互独立。
> - 评测块:T-EV-01..03 在对应 agent 完成后即可启动,与下一里程碑功能开发并行。

**瓶颈任务(延期拖累整体)**:
> - T-M1-01 LangGraph spike:阻塞所有图编排(QA 壳、mock 子图、Manager)。
> - T-M1-05 RetrievalAgent:被 QA/mock/诊断三处依赖。
> - T-M3-01 mock 子图骨架:阻塞整个 M3。
> - T-M4-04 Manager DAG:阻塞复合任务。

---

## 5. Priority & Sequencing(优先级与排序)

Sprint = 2 周 ≈ 8 人天可用(单人,80% 容量)。P0=不做跑不起来;P1=不做体验差;P2=可推迟。

| Task ID | Title | Sprint | Priority | 关键路径 |
|---|---|---|---|---|
| T-M1-01 | LangGraph spike 热身 | S1 | P0 | 是 |
| T-M1-02 | Atom schema 与仓储 | S1 | P0 | 是 |
| T-M1-04 | 消息协议 | S1 | P0 | 否 |
| T-M1-03 | 向量/FTS5 索引 | S1-S2 | P0 | 是 |
| T-M1-11 | 模型分级 client | S2 | P0 | 否 |
| T-M1-05 | RetrievalAgent Local | S2 | P0 | 是 |
| T-M1-06 | RouterAgent | S2 | P0 | 否 |
| T-M1-14 | Atom 种子知识库导入 | S2-S3 | P0 | 否 |
| T-M1-07 | SynthesizerAgent | S3 | P0 | 是 |
| T-M1-08 | VerifierAgent | S3 | P1 | 否 |
| T-M1-09 | QAAgent 外壳/轻路径 | S3 | P0 | 是 |
| T-M1-10 | 最小 Manager 直派 | S3 | P0 | 否 |
| T-M1-12 | agent_traces 埋点 | S3 | P1 | 否 |
| T-M1-13 | QA 检索超时降级 | S4 | P1 | 否 |
| T-M1-15 | /qa 端点联调 | S4 | P0 | 是 |
| T-EV-01..04 | QA 类评测/单测 | S4 | P1 | 否 |
| T-M2-01 | 路径表仓储 | S5 | P0 | 否 |
| T-M2-02 | 掌握度算法 | S5 | P0 | 否 |
| T-M2-03 | Planning generate | S5 | P0 | 否 |
| T-M2-04 | Planning modify | S5-S6 | P0 | 是 |
| T-M2-05 | Manager 单点写 | S6 | P0 | 否 |
| T-M2-06 | needs_input 追问 | S6 | P1 | 否 |
| T-M2-07 | /plan 端点联调 | S6 | P0 | 否 |
| T-EV-05 | Planning 评测/单测 | S6 | P1 | 否 |
| T-M3-01 | mock 子图骨架 | S7 | P0 | 是 |
| T-M3-02 | InterviewerAgent | S7 | P0 | 否 |
| T-M3-03 | JudgeAgent | S7 | P0 | 否 |
| T-M3-04 | StrategistAgent | S8 | P0 | 否 |
| T-M3-05 | CoachAgent | S8 | P0 | 否 |
| T-M3-06 | mock 中断恢复 | S8 | P0 | 否 |
| T-M3-07 | mock 结算 S7 | S8 | P0 | 否 |
| T-M3-08 | /mock 端点联调 | S9 | P0 | 否 |
| T-EV-06..07 | Judge 一致性/mock 测试 | S9 | P1 | 否 |
| T-M4-01 | 事件日志写入 | S9 | P0 | 否 |
| T-M4-02 | DiagnosisAgent | S10 | P0 | 是 |
| T-M4-03 | post_mock 自动诊断 | S10 | P1 | 否 |
| T-M4-05 | handoff_summary | S10 | P0 | 否 |
| T-M4-04 | Manager 完整 DAG/replan | S10-S11 | P0 | 是 |
| T-M4-06 | 复合 workflow 联调 | S11 | P0 | 是 |
| T-M4-07 | workflow 级回退 | S11 | P1 | 否 |
| T-EV-08 | 诊断评测/单测 | S11 | P1 | 否 |
| T-M5-01 | LLM/向量库降级 | S12 | P1 | 否 |
| T-M5-02 | 成本监控告警 | S12 | P1 | 否 |
| T-M5-04 | KV-cache prefix | S12 | P2 | 否 |
| T-EV-09 | 端到端集成测试 | S12 | P0 | 否 |
| T-EV-10 | Trajectory Eval | S12-S13 | P0 | 否 |
| T-EV-11 | CI 回归集成 | S13 | P1 | 否 |
| T-M5-03 | p95 压测调参 | S13 | P0 | 是 |
| T-M5-05 | 文档/runbook | S13 | P1 | 否 |
| T-M5-06 | 端到端 demo | S13 | P0 | 是 |

---

## 6. Test & Evaluation Tasks(独立追踪)

**T-EV-01 构建 RouterAgent golden set 与 accuracy 评测**
- Description:按 Design §9c 标注 ≥150 条四类问题,实现分类准确率评测脚本。
- Depends On:T-M1-06 ｜ Blocks:T-EV-11
- AC:① ≥150 条均衡标注;② 脚本输出 accuracy;③ 可纳入回归。
- Estimate:1-2d ｜ Owner Skills:数据标注/评测 ｜ Risk:Medium(从零标注,缓解:先 80 条跑通)

**T-EV-02 构建 RetrievalAgent golden set 与 Recall/MRR 评测**
- Description:按 §9c 标注 ≥100 query + 相关 chunk,评测 Recall@8/MRR。
- Depends On:T-M1-14 ｜ Blocks:T-EV-11
- AC:① ≥100 query 标注;② 输出 Recall@8/MRR;③ 可比较调参前后。
- Estimate:2d ｜ Owner Skills:检索评测 ｜ Risk:Medium

**T-EV-03 构建 Synth/Verifier golden set 与准确率/拦截率评测**
- Description:按 §9c 准备 ≥80 条(含20注入错误/时效陷阱),评测合成准确率与 Verifier 拦截率。
- Depends On:T-M1-07, T-M1-08 ｜ Blocks:T-EV-11
- AC:① ≥80 条含20注入;② 输出准确率与拦截率;③ 0 编造校验项。
- Estimate:2d ｜ Owner Skills:评测 ｜ Risk:Medium

**T-EV-04 编写 QA 链路各 agent 单测**
- Description:为 Router/Retrieval/Synth/Verifier/QAAgent 编写单测(含降级路径)。
- Depends On:T-M1-13 ｜ Blocks:T-EV-11
- AC:① 各 agent 正常+异常分支覆盖;② 降级路径有用例;③ 可本地一键跑。
- Estimate:2d ｜ Owner Skills:pytest ｜ Risk:Low

**T-EV-05 构建 Planning golden set 与单测**
- Description:按 §9c 准备 ≥30 目标场景,评测路径覆盖度/优先级合理性,并编写单测。
- Depends On:T-M2-04 ｜ Blocks:T-EV-11
- AC:① ≥30 场景;② 覆盖/优先级指标输出;③ generate/modify 单测覆盖。
- Estimate:2d ｜ Owner Skills:评测/pytest ｜ Risk:Medium

**T-EV-06 构建 Judge 评分一致性评测**
- Description:按 §9c 准备 ≥50 题×2-3 份不同质量回答,评测同答案两评分档差 ≤1 的一致性。
- Depends On:T-M3-03 ｜ Blocks:T-EV-11
- AC:① 数据集就绪;② 输出一致性比例;③ 目标 ≥80%。
- Estimate:2d ｜ Owner Skills:评测 ｜ Risk:Medium

**T-EV-07 编写 mock 子 agent 单测与子图集成测试**
- Description:为 Interviewer/Judge/Strategist/Coach 单测,并对 mock 子图(含中断)做集成测试。
- Depends On:T-M3-08 ｜ Blocks:T-EV-11
- AC:① 四子 agent 单测;② 换topic/pause/escalate 各有集成用例;③ 结算路径覆盖。
- Estimate:2-3d ｜ Owner Skills:pytest/LangGraph ｜ Risk:Medium

**T-EV-08 构建 Diagnosis golden set 与单测**
- Description:按 §9c 准备 ≥30 用户档案 + 人工标注弱点,评测 Recall@5,编写只读断言单测。
- Depends On:T-M4-02 ｜ Blocks:T-EV-11
- AC:① ≥30 档案标注;② Recall@5 输出(目标 ≥70%);③ 只读性单测(无写)。
- Estimate:2d ｜ Owner Skills:评测 ｜ Risk:Medium

**T-EV-09 编写五能力端到端集成测试**
- Description:为五能力各写 happy path + 2 edge case 集成测试(Proposal §7 验收项)。
- Depends On:T-M1-15, T-M2-07, T-M3-08 ｜ Blocks:T-M5-03, T-EV-10
- AC:① 五能力 happy 全过;② 各 2 edge 覆盖;③ 可在 CI 运行。
- Estimate:3d ｜ Owner Skills:pytest ｜ Risk:Medium

**T-EV-10 实现 Trajectory Eval**
- Description:按 Design §9b——对场景集回放断言消息链拓扑、闭环数据回流、无越权写。
- Depends On:T-M4-06, T-M1-12 ｜ Blocks:无
- AC:① 拓扑断言通过;② mastery/path 回流可验证;③ 越权写被检出。
- Estimate:2-3d ｜ Owner Skills:评测/编排 ｜ Risk:High(多 agent 评测复杂,缓解:先覆盖复合场景一条主链)

**T-EV-11 集成 CI 回归门禁**
- Description:把单测 + 关键评测(Router/Judge/Diagnosis 指标)接入 CI,设回归门禁。
- Depends On:T-EV-01..09 ｜ Blocks:无
- AC:① CI 跑全单测;② 关键指标低于门槛则 fail;③ trace/成本报告产出。
- Estimate:2d ｜ Owner Skills:CI/pytest ｜ Risk:Low

---

## 7. Risks & Mitigation Plan(项目级风险)

1. **LangGraph 学习曲线拖慢早期(对应 R:团队不熟新框架)**
   - 触发信号:T-M1-01 spike 超 3 天仍无法 interrupt/resume。
   - 影响:M1/M3/M4 全部编排任务。
   - 缓解:限定 spike 范围、官方示例先行;预留学习 buffer。
   - 应急:M3 mock 若 checkpoint 受阻,降级为"无 pause、仅 in-session 中断"。

2. **LLM 成本超预算(R5)**
   - 触发信号:T-M5-02 监控显示 mock 含自动诊断 >$0.35 或问答 >$0.03。
   - 影响:M3/M4 成本门槛、Proposal §7。
   - 缓解:严格模型分级、上下文切片、检索缓存;事件级成本记账。
   - 应急:降低 mock max_turns、post_mock 诊断改采样触发(非每场)。

3. **Agent 协作延迟累积(R1)**
   - 触发信号:T-M5-03 压测复合任务 p95 >40s 或问答 >8s。
   - 影响:M4 复合、M5 性能门槛。
   - 缓解:无依赖子任务并发、问答轻路径、Haiku 承担高频小推理。
   - 应急:复合任务对用户先返回诊断结果、路径异步补。

4. **golden set 准备不及时(R:评测最易被砍)**
   - 触发信号:进入 M5 时 T-EV-01/06/08 仍未就绪。
   - 影响:质量门槛无法验证、DoD 受阻。
   - 缓解:评测任务列为独立 P1、与功能开发并行启动(§5 已穿插)。
   - 应急:先用缩小版数据集(各 50%)验证趋势,MVP 后补全。

5. **关键 Agent 输出质量不达标(R:质量)**
   - 触发信号:T-EV-03 准确率 <85% 或 T-EV-08 Recall <70%。
   - 影响:对应能力可用性。
   - 缓解:prompt 迭代 + 检索质量提升 + Verifier 兜底。
   - 应急:该能力降级标注"beta"、收紧适用范围,不阻断其他能力交付。

6. **第三方依赖不稳定(R:LLM API/向量库)**
   - 触发信号:429/超时频发或 sqlite-vec 加载失败。
   - 影响:全局可用性。
   - 缓解:T-M5-01 重试退避 + 降级;向量库故障仅 FTS5。
   - 应急:本地缓存最近答案;sqlite-vec 不可用时纯 FTS5 运行。

7. **Scope creep(R:范围蔓延)**
   - 触发信号:出现 Proposal Non-Goals(云端 KB、多用户、SRS、笔记整理)相关任务。
   - 影响:工期与关键路径。
   - 缓解:任何新需求对照 Non-Goals 审查;云端 KB 仅留接口(Design §10a G1)。
   - 应急:新需求一律记入 MVP 后 backlog。

8. **单人单点风险(R:容量)**
   - 触发信号:关键路径任务(spike/Retrieval/mock 子图/Manager DAG)集中延期。
   - 影响:整体工期。
   - 缓解:关键路径任务优先排、留 20% buffer;高风险任务给乐观-悲观区间。
   - 应急:砍 P2(T-M5-04 等)、edge case 延后到 MVP 后。

---

## 8. Definition of Done(MVP 整体完成定义)

- **功能完成度**:五能力 happy path 全过(T-EV-09)+ 各 2 个 edge case 验证(Proposal §7 / Design §5)。
- **质量门槛**:Router 分类 accuracy 达标;问答事实准确率 ≥85% 且可验证答案 0 编造;Mock 评分一致性 ≥80%;诊断 Recall@5 ≥70%(T-EV-03/06/08)。
- **性能门槛**:问答 p95 ≤8s、mock 单轮首字 ≤5s、计划生成 ≤20s、检索 ≤1.5s(T-M5-03)。
- **成本门槛**:问答 <$0.03、mock 单场(含自动诊断)<$0.35、计划 <$0.10、诊断 <$0.05(T-M5-02)。
- **可观测**:agent_traces 覆盖全链路、可按 trace_id 回溯;每能力成本/延迟可看(T-M1-12/T-M5-02)。
- **文档**:README/API/架构/运维 runbook 齐全(T-M5-05)。
- **演示**:复合任务"快面试了帮我准备"现场一键跑通(T-M5-06),输出诊断+重排路径+mock 建议且控制权交回用户。

---

## 9. Open Issues(未决问题)

### 9a) Design Gaps

- **DG1 embedding 模型/维度未锁定**:Design §7a 给了 1024 或 1536 候选但留待 spike。**处置**:在 T-M1-03 之前的 retrieval spike 内锁定,锁定前 `atom_vectors` 维度参数化,避免返工。
- **DG2 Atom 种子语料来源未定**:Design §7c 定义了切片但未指定从零构建的语料获取方式。**处置**:T-M1-14 先用公开八股/官方文档小批量验证管线;语料合规性需用户确认来源。
- **DG3 post_mock 自动诊断成本归属(承接 Design §10a G3)**:mock 预算是否上调为 $0.35 仍待 Proposal 确认。**处置**:T-M5-02 暂按 $0.35 监控,最终值待裁决。
- **DG4 `/mock` 多轮传输机制未定**:Design §3.8 提"SSE/轮询"未择一。**处置**:T-M3-08 默认 SSE(单用户本地够用),如不适配回退轮询。
- **DG5 用户身份与会话边界**:单用户本地,Design 未明确 session 与 user 的存储边界。**处置**:T-M2-01 按单用户单档建模,user_id 固定常量,不引入账号体系(守 Non-Goal)。

### 9b) Task 阶段遗留

- **TL1 Sprint 容量为单人估算**:若后续加人,§5 的可并行块可立即并行,Sprint 数相应压缩——建议加人时重排而非改任务粒度。
- **TL2 压测调参 buffer**:T-M5-03 给了 2-4d 区间,若 p95 多能力不达标可能需回到对应 agent 优化——建议 M5 提前在 M4 末启动一次预压测探底。
- **TL3 golden set 规模为下限**:§9c 规模为达标下限,质量优先下可能需扩充——建议在 Implementation 各能力收尾时按评测稳定性决定是否扩样。

---

*本 Tasks 文档基于 Design 拆解,不引入新模块/字段,Non-Goals 仍不做。完整代码、schema 细节、部署脚本留待 Implementation。*
