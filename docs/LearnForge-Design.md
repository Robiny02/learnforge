# LearnForge Design —— Hierarchical Multi-Agent 实施设计

> 阶段:Design(回答"怎么做",让新工程师看完即可写代码)
> 上游依据:`LearnForge-Proposal.md`(下文简称 Proposal,引用其章节号以保可追溯)
> 本文不重新论证 Proposal 已拍板的决策,只细化实现。Non-Goals 在 Design 阶段仍不做。

---

## 1. Proposal Recap(≤300 字)

系统为严格 hierarchical multi-agent。**ManagerAgent**(plan-and-execute + 有限失败循环,唯一调度者)统辖四个域 worker:**QAAgent**(下挂 RouterAgent / SynthesizerAgent / VerifierAgent)、**PlanningAgent**(合并"生成+修改计划",单体)、**MockInterviewAgent**(下挂 InterviewerAgent / JudgeAgent / StrategistAgent / CoachAgent)、**DiagnosisAgent**(只读单体)。**RetrievalAgent** 为横切共享子能力,被 QA/Mock/Diagnosis 调用,不被 Manager 直接调度。通信三选:message passing(强类型 `task/context/response` 信封,主力)、shared state(UserProfile/LearningPath/Atom 掌握度,落 SQLite)、event log(弱点信号解耦给诊断)。覆盖五能力:问答、生成计划、修改计划、模拟面试、诊断弱点。技术栈:Python 3.11 + FastAPI + LangGraph(看中 interrupt/resume)+ Pydantic + Claude Haiku/Sonnet 分级 + SQLite + sqlite-vec + FTS5。

---

## 2. System Architecture

### 2a) Agent 清单与职责矩阵

| Agent | 角色 | 输入 | 输出 | 工具/数据依赖 | sub-agent |
|---|---|---|---|---|---|
| ManagerAgent | 编排者,唯一对外入口 | 用户原始请求 + 会话 state | 子任务 plan、聚合后的用户响应、写 LearningPath/Atom 的提交 | 全部 worker(message passing);UserProfile/LearningPath 读写 | 无 |
| QAAgent | 问答子系统外壳 | QA task message | 答案 + 引用 + 置信度 | RouterAgent/Synth/Verifier/RetrievalAgent | Router, Synthesizer, Verifier |
| RouterAgent | 问题分类与路由 | 用户问题文本 + 轻量上下文 | 问题类型 + 检索/verifier 开关 | LLM(Haiku) | 无 |
| SynthesizerAgent | 检索增强合成 | 问题 + 检索片段 + 用户上下文 | 答案草稿 + claims 列表 | LLM(Sonnet) | 无 |
| VerifierAgent | 事实/来源把关 | 答案草稿 + claims + 检索片段 | 校验后答案 + verdict | LLM(Haiku)、RetrievalAgent(只读) | 无 |
| RetrievalAgent | 共享混合检索 | query + filters + top_k | 排序后片段 list | sqlite-vec(只读)、FTS5(只读)、可选 Haiku 改写 | 无 |
| PlanningAgent | 学习路径规划(生成+修改) | 目标/现状 或 诊断结论+现有路径 | 结构化 LearningPath(增量 diff) | UserProfile(读)、Atom 掌握度(读)、LearningPath(写经 Manager) | 无 |
| MockInterviewAgent | 模拟面试子系统外壳 | mock 启动 task + 用户每轮输入 | 题目/追问/复盘 + 逐轮评分 | Interviewer/Judge/Strategist/Coach/Retrieval | Interviewer, Judge, Strategist, Coach |
| InterviewerAgent | 出题与追问 | 当前主题 + 难度 + 对话历史(本场) | 题目/追问文本 | LLM(Sonnet)、RetrievalAgent | 无 |
| JudgeAgent | 每轮独立评分 | 题目 + 用户回答 | 评分(0-5 档)+ 维度细分 + 失分点 | LLM(Haiku) | 无 |
| StrategistAgent | 难度/选题策略 + 中断处理 | 逐轮评分序列 + 用户中断意图 | 下一步主题/难度/动作 | LLM(Haiku) | 无 |
| CoachAgent | 终场复盘 | 全场题目+评分序列 | 复盘报告 + 弱点事件 | LLM(Sonnet) | 无 |
| DiagnosisAgent | 只读弱点诊断 | 触发请求 + 时间窗 | 弱点结论 + 行动建议(只读输出) | Atom 掌握度(读)、interaction_events(读)、RetrievalAgent(读) | 无 |

**写权限边界(Proposal §4c)**:仅 ManagerAgent 可提交对 `learning_paths` / `knowledge_atoms.mastery_*` 的写;PlanningAgent 产出 diff 但由 Manager 落库;DiagnosisAgent / RetrievalAgent / 所有 QA sub-agent 一律只读。

### 2b) Agent 层级与调度

- **常驻**:ManagerAgent、RetrievalAgent(无状态服务,常驻复用连接池)。
- **按需启动**:QAAgent、PlanningAgent、DiagnosisAgent 在被派发时实例化、完成即释放。MockInterviewAgent 子系统在一场 mock 期间作为带 checkpoint 的 LangGraph 子图常驻,直到 mock 结束。
- **Manager 调度策略(Proposal §5 决策 1,此处只说怎么做)**:
  - 步骤:`PLAN`(一次 LLM 调用产出有依赖关系的 DAG 子任务列表)→ `EXECUTE`(按拓扑序派发,无依赖者并行)→ `AGGREGATE`(收集 ResponsePayload)→ 终止。
  - 失败循环:任一 worker 返回 `status=error` 或 `confidence < 0.4`,Manager 进入 `REPLAN`,重规划计数 +1;`replan_count > 2` 则终止并向用户报错(Proposal R3)。
  - Manager 不做领域推理(Proposal §4a),plan 只产出"派给谁 + task_type + 依赖",不替 worker 思考答案。

### 2c) 核心数据流(3 个场景,含 1 个 interrupt + 1 个 failure)

**场景 A(happy,复合)——"快面试了帮我准备"**
1. Manager.PLAN 产出 DAG:`T1 Diagnosis(只读)` → `T2 Planning(依赖 T1)`。
2. Manager 派 T1 给 DiagnosisAgent,收到弱点结论(`status=ok, confidence=0.8`)。
3. Manager 把诊断结论注入 T2 的 `scoped_slice`,派给 PlanningAgent,收到 LearningPath diff。
4. Manager 校验 diff 合法后落库(唯一写点),AGGREGATE 输出"已按最弱三块重排计划,建议先做并发 mock"。控制权交回用户,不自动启 mock。

**场景 B(interrupt)——mock 中途"换个 topic"(Proposal §4d 场景 2,Q4 已定边界)**
1. 控制权已 handoff 给 MockInterview 子图;LangGraph 在 `await_user_turn` 节点 interrupt 等待输入。
2. 用户输入经子图入口分类:StrategistAgent 判定为"换主题"(局部意图)。
3. StrategistAgent 更新本场 `session_state`(topic、difficulty_baseline),resume 子图到 `interviewer_turn`;JudgeAgent 已累积评分不重置。
4. 若 Strategist 判定意图为"结束面试"或"帮我改计划"(跨能力),则子图以 `status=needs_input/escalate` 返回 Manager,由 Manager 重新编排。

**场景 C(failure)——问答时 RetrievalAgent 超时**
1. QAAgent 调 RetrievalAgent,1.5s timeout(见 §3 Bounds)未返回。
2. RetrievalAgent 抛 `RetrievalTimeout`,QAAgent 捕获,降级:SynthesizerAgent 以"无检索证据"模式回答,且强制 VerifierAgent 启用,verdict 标 `unverified`。
3. QAAgent 返回 `status=partial, confidence≤0.5`,答案附"未检索到本地证据,以下为通用回答"。
4. Manager 不重派(检索超时非 plan 错误),原样透传给用户。

---

## 3. Agent Specification(核心章节)

字段类型记法:`name: type [required|optional] = default`。所有 `*_id` 为 `str(uuid4)`。

### 3.1 ManagerAgent

- **Role & Scope**:编排与聚合。**不做**领域推理、不直接写检索、不替 worker 生成内容。
- **Inputs**:
  - `user_input: str [required]`
  - `session_id: str [required]`
  - `conversation_summary: str [optional]`(上一轮 handoff_summary,≤512 tok)
  - `active_mock_session_id: str [optional]`(若存在,优先路由回 mock 子图)
- **Outputs**:
  - 对 worker:`AgentMessage(msg_type=task)`(见 §4a)
  - 对用户:`{reply_text: str, citations: list[Citation], next_actions: list[str]}`
  - 写 state:`learning_paths`、`knowledge_atoms.mastery_*`(唯一写者)
- **Tools**:`dispatch(agent, task)`(write to message bus)、`read_user_profile()`、`commit_path(diff)`、`commit_mastery(updates)`。
- **Internal Logic**:
  ```
  plan = LLM_plan(user_input, profile, active_mock)   # 一次 Sonnet
  if plan.is_single_task: dispatch and await
  else:
    for task in topo_order(plan.dag):
      if task.deps_ready: dispatch (parallel if independent)
    collect responses
    if any(r.status==error or r.confidence<0.4) and replan_count<=2:
      replan_count+=1; goto plan
  aggregated = LLM_aggregate(responses)   # 一次 Sonnet
  commit writes; return aggregated
  ```
- **System Prompt 大纲**:身份=调度器非专家;能力边界=只拆解/派发/聚合,禁止自行作答领域问题;输出格式=结构化 plan DAG(JSON);反模式=不要替 worker 编造答案、不要广播完整上下文。
- **Bounds**:`max_replan=2`、`plan_timeout=10s`、`total_workflow_timeout=60s`、`max_cost_per_request=$0.40`。
- **Failure Modes**:plan 解析失败 → 重试 1 次后退化为"单意图直派最匹配 worker";worker 全部失败 → 向用户返回结构化错误 + trace_id;聚合超 token → 截断各 worker result 至各 1k tok 再聚合。

### 3.2 QAAgent(子系统外壳)

- **Role & Scope**:编排 QA 内部三 agent + 检索,产出可信答案。**不做**计划/诊断。
- **Inputs**:`question: str [required]`、`project_context_ref: str [optional]`、`scoped_atoms: list[AtomBrief] [optional]`。
- **Outputs**:`{answer: str, citations: list[Citation], verdict: enum[verified|unverified|uncertain], confidence: float}`。
- **Tools**:子 agent 调用 + RetrievalAgent(只读)。
- **Internal Logic**:
  ```
  route = RouterAgent(question)            # 类型 + need_retrieval + need_verifier
  ctx = RetrievalAgent(question, filters)  if route.need_retrieval else []
  draft = SynthesizerAgent(question, ctx, scoped_atoms)
  if route.need_verifier: answer = VerifierAgent(draft, ctx)
  else: answer = draft
  emit interaction_event(qa, topic, signal)   # 供诊断(Q5 自动触发链路)
  ```
- **System Prompt 大纲**:外壳无独立 prompt(纯编排),人格统一在 Synthesizer。
- **Bounds**:`qa_total_timeout=8s`(对齐 Proposal §7 p95)、`max_cost=$0.03`。
- **Failure Modes**:见 §2c 场景 C(检索超时降级);Synthesizer 失败 → 重试 1 次;Verifier 失败 → 答案标 `unverified` 透传,不阻断。

### 3.3 RouterAgent

- **Role & Scope**:判问题类型与下游开关。**不**作答。
- **Inputs**:`question: str [required]`、`recent_topics: list[str] [optional]`。
- **Outputs**:`{q_type: enum[concept|project|temporal|chitchat], need_retrieval: bool, need_verifier: bool, rewrite_needed: bool}`。
- **Tools**:LLM(Haiku)。
- **Internal Logic**:`temporal` 强制 `need_retrieval=need_verifier=True`;`concept` 默认 `need_retrieval=True, need_verifier=True`;`chitchat` 全 False(走轻路径,Proposal R1)。
- **System Prompt 大纲**:身份=分类器;输出=固定枚举 JSON;反模式=不解释、不作答、不臆测时效性。
- **Bounds**:`timeout=1.5s`、`max_cost=$0.002`、单次输出 ≤64 tok。
- **Failure Modes**:解析失败 → 默认 `concept + 全开`(保守,宁可多检索多校验)。

### 3.4 SynthesizerAgent

- **Role & Scope**:合成答案并显式列出可验证 claims。**不**自评对错。
- **Inputs**:`question: str`、`retrieved: list[Chunk]`、`scoped_atoms: list[AtomBrief] [optional]`、`project_context: str [optional]`。
- **Outputs**:`{draft: str, claims: list[{text:str, source_chunk_id:str|None}]}`。
- **Tools**:LLM(Sonnet)。
- **Internal Logic**:RAG 模式;无检索证据时必须显式声明并降低断言强度;每条事实性陈述尽量绑定 `source_chunk_id`。
- **System Prompt 大纲**:身份=程序员问答专家;能力边界=只基于给定证据+常识,无证据要声明;输出格式=答案正文 + claims JSON;反模式=禁止无依据断言时效性、禁止编造引用。
- **Bounds**:`timeout=4s`、`max_cost=$0.02`、输出 ≤1.5k tok。
- **Failure Modes**:超时 → 返回部分草稿 + `partial`;无证据且为 temporal → 直接产出 `uncertain` 模板答案。

### 3.5 VerifierAgent

- **Role & Scope**:对 claims 做事实/来源把关。**不**重写整段答案风格,只标注与修正硬错误。
- **Inputs**:`draft: str`、`claims: list[Claim]`、`retrieved: list[Chunk]`。
- **Outputs**:`{verified_answer: str, verdict: enum[verified|unverified|uncertain], flagged: list[{claim_id, reason}]}`。
- **Tools**:LLM(Haiku)、RetrievalAgent(只读,补检索佐证)。
- **Internal Logic**:逐 claim 比对证据;无证据支撑且无法补检索 → 降级该 claim 为"不确定"并在答案中弱化;全部 claim 无支撑 → `verdict=unverified`。
- **System Prompt 大纲**:身份=事实审查;边界=只判可验证 claim,主观表述放行;输出=verdict + flagged JSON;反模式=不放过无依据的时效性断言。
- **Bounds**:`timeout=3s`、`max_cost=$0.008`。
- **Failure Modes**:超时 → 透传 draft 标 `unverified`(不阻断用户)。

### 3.6 RetrievalAgent(共享,可插拔后端)

- **Role & Scope**:混合召回 + 融合排序。**不**合成答案。后端可插拔(MVP=LocalBackend;CloudBackend 留接口,见 §10 Gap)。
- **Inputs**:`query: str [required]`、`filters: {topic?, tags?, source_type?} [optional]`、`top_k: int = 8`、`backend: enum[local|cloud] = local`。
- **Outputs**:`list[Chunk{chunk_id, text, score, source_type, metadata}]`(按融合分降序)。
- **Tools**:sqlite-vec(只读)、FTS5(只读)、LLM(Haiku,仅条件改写)。
- **Internal Logic(Q3 已定)**:
  ```
  q = HaikuRewrite(query) if (len(query)>120 chars or is_fuzzy) else query   # 条件改写
  vec_hits = sqlite_vec.knn(embed(q), k=top_k*4)
  kw_hits  = fts5.match(q, k=top_k*4)
  fused = RRF(vec_hits, kw_hits, k_rrf=60)     # 本地:RRF,不 rerank
  return fused[:top_k]
  # CloudBackend(未来):hybrid_sort + cross_encoder_rerank(top_k*4 -> top_k)
  ```
- **System Prompt 大纲**:仅改写子调用有 prompt——身份=查询改写器;输出=改写后单 query;反模式=不扩展成多 query、不臆造术语。
- **Bounds**:`timeout=1.5s`(对齐 Proposal §7)、`max_cost=$0.003`、`top_k≤20`。
- **Failure Modes**:向量库异常 → 仅用 FTS5 结果;FTS5 异常 → 仅用向量;两者皆失败 → 抛 `RetrievalTimeout` 由调用方降级(§2c-C)。

### 3.7 PlanningAgent(生成+修改合并,Proposal §5 决策 2)

- **Role & Scope**:产出/重排学习路径 diff。**不**评判对错(诊断的事)、**不**直接落库。
- **Inputs**:
  - `mode: enum[generate|modify] [required]`
  - `goal: str [required if generate]`、`deadline: date [optional]`、`current_level: str [optional]`
  - `diagnosis: DiagnosisResult [required if modify]`、`existing_path_ref: str [required if modify]`
  - `user_feedback: str [optional]`
- **Outputs**:`PathDiff{add: list[PathItem], remove: list[item_id], reorder: list[{item_id, new_order}], rationale: str}`。
- **Tools**:UserProfile(读)、knowledge_atoms 掌握度(读)、RetrievalAgent(读,补充 atom 候选)。
- **Internal Logic**:
  ```
  atoms = read_atoms(filter by goal topics)
  if mode==modify: anchor on existing_path + diagnosis weakest topics
  rank by (priority = weakness * goal_relevance / mastery)
  bucket into days until deadline; emit diff (not full rewrite)
  if feedback conflicts diagnosis: prefer feedback, annotate(Proposal §7 edge)
  ```
- **System Prompt 大纲**:身份=学习规划师;边界=只排程不评判;输出=PathDiff JSON;反模式=不全量重写(只 diff)、不超 deadline 硬塞。
- **Bounds**:`timeout=20s`(Proposal §7)、`max_cost=$0.10`、单次 diff ≤50 items。
- **Failure Modes**:目标过大 → 输出裁剪 + "超出周期"提示;现状不足 → `status=needs_input` 触发 Manager 追问;空 diff → `status=ok` 附"无需调整"。

### 3.8 MockInterviewAgent(子系统外壳 / LangGraph 子图)

- **Role & Scope**:主持一场可中断、动态难度、有复盘的 mock。**不**改计划、不诊断长期弱点(只产本场弱点事件)。
- **Inputs**:`topic: str [required]`、`target_difficulty: int 1-5 = 3`、`max_turns: int = 10`、每轮 `user_answer: str`。
- **Outputs**:每轮 `{question:str}` 或 `{followup:str}`;终场 `{review: CoachReport, turn_scores: list[Score]}`;副作用 emit `interaction_event(mock_weakness)`。
- **Tools**:四子 agent + RetrievalAgent;LangGraph checkpoint(state 持久化,支持 interrupt/resume)。
- **Internal Logic(状态机见 §5.4)**。
- **Bounds**:`session_timeout=15min`(Proposal §7)、`max_turns=10`、`max_cost=$0.30`、单轮首字 `≤5s`。
- **Failure Modes**:子 agent 失败 → 跳过该轮重出题(max 2 次);session 超时 → 强制进入 review(给部分复盘,Proposal §7 edge)。

### 3.9 InterviewerAgent

- **Role & Scope**:出题与追问。**不**评分、**不**定策略。
- **Inputs**:`topic: str`、`difficulty: int`、`turn_history: list[Turn]`(本场,≤近 6 轮)、`retrieved: list[Chunk] [optional]`。
- **Outputs**:`{question: str, expected_points: list[str], atom_refs: list[atom_id]}`。
- **Tools**:LLM(Sonnet)、RetrievalAgent。
- **System Prompt 大纲**:身份=资深技术面试官;边界=只出题/追问,不透露评分;输出=题目 + 考点;反模式=不重复已问、不超指定难度。
- **Bounds**:`timeout=5s`、`max_cost=$0.03/轮`。
- **Failure Modes**:超时 → 从题库 atom 取预置题兜底。

### 3.10 JudgeAgent

- **Role & Scope**:独立评分(与出题分离,Proposal §5 决策 4)。**不**出题、**不**调难度。
- **Inputs**:`question: str`、`expected_points: list[str]`、`user_answer: str`。
- **Outputs**:`Score{overall: int 0-5, dims: {correctness:int, depth:int, clarity:int}, missed_points: list[str], confidence: float}`。
- **Tools**:LLM(Haiku)。
- **System Prompt 大纲**:身份=评分官;边界=只依据 expected_points 与回答;输出=固定 rubric JSON;反模式=不被回答的自信措辞带偏、不补充正确答案给用户。
- **Bounds**:`timeout=3s`、`max_cost=$0.008/轮`。
- **Failure Modes**:解析失败 → 重试 1 次;再失败给 `overall=null, confidence=0` 不计入一致性。

### 3.11 StrategistAgent

- **Role & Scope**:难度/选题策略 + 中断意图分类(Q4 边界)。**不**出题、**不**评分。
- **Inputs**:`score_history: list[Score]`、`user_interrupt: str [optional]`、`session_state: MockState`。
- **Outputs**:`{action: enum[continue|raise|lower|switch_topic|pause|end|escalate], next_topic: str|None, next_difficulty: int}`。
- **Tools**:LLM(Haiku)。
- **Internal Logic(Q4)**:`switch_topic|raise|lower|skip|pause` → 局部处理;`end` → 转 review;`escalate`(如"帮我改计划")→ 返回 Manager。难度规则:连续 2 轮 ≥4 升档,≤2 降档。
- **System Prompt 大纲**:身份=面试策略师;边界=只决策不作答;输出=action JSON;反模式=不把跨能力请求(改计划/诊断)私自处理,必须 escalate。
- **Bounds**:`timeout=2s`、`max_cost=$0.005/轮`。
- **Failure Modes**:意图歧义 → 默认 `continue` 并向用户澄清。

### 3.12 CoachAgent

- **Role & Scope**:终场复盘,聚合逐轮评分为可行动建议;产出本场弱点事件。
- **Inputs**:`turn_scores: list[Score]`、`topic_coverage: list[str]`。
- **Outputs**:`CoachReport{summary:str, strengths:list[str], weaknesses:list[{topic, evidence}], next_steps:list[str]}` + `interaction_events`。
- **Tools**:LLM(Sonnet)。
- **System Prompt 大纲**:身份=面试教练;输出=结构化复盘;反模式=不堆砌泛泛建议、每条 weakness 必带 evidence(引哪一轮)。
- **Bounds**:`timeout=8s`、`max_cost=$0.05`。
- **Failure Modes**:数据不足(<2 轮)→ 输出"样本不足"简报。

### 3.13 DiagnosisAgent(只读,Proposal §5 决策 5)

- **Role & Scope**:聚合长期信号产出弱点结论。**严格只读**,不写任何 state。
- **Inputs**:`time_window: enum[7d|30d|all] = 30d`、`focus_topics: list[str] [optional]`、`trigger: enum[user|post_mock|composite]`。
- **Outputs**:`DiagnosisResult{weak_atoms: list[{atom_id, topic, mastery, evidence_count}], clusters: list[{topic, severity:float}], recommendations: list[str], confidence: float}`。
- **Tools**:knowledge_atoms(读)、interaction_events(读)、RetrievalAgent(读)。
- **Internal Logic**:按 time_window 聚合 events,join atom 掌握度,弱点 score = `(1-mastery) * recency_weight * error_freq`;取 top-N 簇。
- **System Prompt 大纲**:身份=诊断师;边界=只读只输出结论,不改路径/atom;输出=结构化弱点 JSON;反模式=数据不足时不硬凑(声明低置信)。
- **Bounds**:`timeout=10s`、`max_cost=$0.05`。
- **Failure Modes**:数据不足 → `confidence<0.5` + 声明;无明显弱点 → 如实返回空簇。

---

## 4. Data Models

类 Pydantic 文字描述,不写实际代码。

### 4a) Agent 间消息协议

**AgentMessage(envelope)**
- `message_id: str [required]`
- `trace_id: str [required]`(同一用户请求全链路共享,Proposal §7 责任归属)
- `parent_message_id: str | None = None`
- `sender: AgentId [required]` / `recipient: AgentId [required]`
- `msg_type: enum[task|response|event] [required]`
- `payload: TaskPayload | ResponsePayload | EventPayload [required]`
- `context: ContextEnvelope [required]`
- `created_at: datetime [required]`
- `schema_version: str = "1.0"`

**TaskPayload**:`task_type: str`、`instruction: str`、`params: dict`、`budget: {max_cost_usd: float, timeout_s: float, max_iterations: int}`。

**ResponsePayload**:`status: enum[ok|error|partial|needs_input|escalate]`、`confidence: float 0-1`、`result: dict`、`cost_usd: float`、`tokens: {prompt:int, completion:int}`、`error: {code:str, message:str} | None`。

**ContextEnvelope(隔离核心,Proposal §5 决策 8)**:`global_static_ref: str`(版本号,不内联)、`handoff_summary: str`(≤512 tok,Q2 LLM 生成)、`scoped_slice: dict`(本任务专属切片)。

**EventPayload**:`event_type: enum[qa_signal|mock_weakness|path_changed]`、`topic: str`、`atom_refs: list[str]`、`signal: dict`。

### 4b) 共享状态(Shared State)

| 数据 | 字段(摘) | 类型 | 读 | 写 |
|---|---|---|---|---|
| UserProfile | user_id, goals, current_level, preferences | 单行对象 | 全部 worker | Manager |
| LearningPath | path_id, items[], updated_at | 对象 | Manager/Planning/Diagnosis | Manager(经 Planning diff) |
| Atom mastery | atom_id, mastery_score, mastery_tier | 字段 | 全部 worker | Manager(会话结算) |
| MockState | session_id, topic, difficulty, turn_scores | LangGraph checkpoint | MockInterview 子图 | MockInterview 子图 |

### 4c) 持久化数据(SQLite + sqlite-vec + FTS5)

- `knowledge_atoms`(主表,见 §4d)
- `atom_vectors`:sqlite-vec `vec0` 虚拟表,`(atom_id, embedding float[1024])`
- `atom_fts`:FTS5 虚拟表,索引 `canonical_question, canonical_answer, tags`
- `user_profile`:单行
- `learning_paths` / `path_items`:`path_items(item_id, path_id, atom_id, order_idx, day_index, status)`
- `interaction_events`:`(event_id, trace_id, event_type, topic, atom_refs json, signal json, created_at)`(事件日志,Proposal §4c)
- `mock_sessions` / `mock_turns`:逐轮题目与评分
- `diagnosis_reports`:诊断快照(只读 agent 产出,由 Manager 落)
- `agent_traces`:`(message_id, trace_id, sender, recipient, status, confidence, cost_usd, latency_ms, created_at)`(§9)

**索引策略**:
- 向量:`atom_vectors` HNSW(sqlite-vec),余弦相似度,`dim=1024`(对齐所选 embedding;若用 Voyage/OpenAI text-embedding-3-small 则 1536,见 §7a)
- 全文:FTS5(BM25 内建)
- 复合:`knowledge_atoms(topic, mastery_tier)`、`interaction_events(created_at, event_type)`、`agent_traces(trace_id)`

### 4d) Knowledge Atom 完整 schema

- `atom_id: str [required, PK]`
- `topic: str [required]`(如 "concurrency")
- `subtopic: str | None = None`
- `title: str [required]`
- `canonical_question: str [required]`
- `canonical_answer: str | None = None`
- `tags: list[str] = []`
- `difficulty: int [1-5] = 3`
- `mastery_score: float [0.0-1.0] = 0.0`(Q1 连续分)
- `mastery_tier: enum[unknown|learning|familiar|proficient|mastered] = unknown`(Q1 离散映射)
- `last_reviewed_at: datetime | None = None`
- `review_count: int = 0` / `correct_count: int = 0`
- `decay_rate: float = 0.05`(Q1 时间遗忘)
- `source_refs: list[str] = []`
- `created_at: datetime [required]` / `updated_at: datetime [required]`

**掌握度算法(Q1)**:
- 更新:`mastery_score = clamp(old*(1-α) + signal*α, 0, 1)`,权重 `α`:mock=0.5 > qa=0.3 > self=0.15。
- 遗忘:有效掌握 `effective = mastery_score * exp(-decay_rate * days_since_last_review)`,读取时计算。
- 分档:`[0,0.2)=unknown, [0.2,0.4)=learning, [0.4,0.6)=familiar, [0.6,0.85)=proficient, [0.85,1]=mastered`。

---

## 5. Key Workflows

每个含触发、Agent、状态机、决策点、中断恢复、延迟与成本。

### 5.1 问答

- **触发**:用户提问且无 active mock。
- **Agent**:Manager → QAAgent(Router/Retrieval/Synth/Verifier)。
- **状态机**:`S1 ROUTE → S2 RETRIEVE(可跳) → S3 SYNTH → S4 VERIFY(可跳) → S5 RETURN`。
- **决策点**:S1 决定 retrieve/verify 开关;chitchat 直接 S3→S5(轻路径,Proposal R1)。
- **中断恢复**:无多轮中断;单次请求超时即降级返回。
- **异常路径**:① 检索超时→无证据合成 + 强制 verify(§2c-C);② Synth 超时→partial;③ 全 claim 无证据→verdict=unverified。
- **延迟/成本**:p95 ≤8s;轻路径 ≤3s;成本 $0.01-0.03。

### 5.2 生成学习计划

- **触发**:用户给目标+现状,无既有 path。
- **Agent**:Manager → PlanningAgent(mode=generate)。
- **状态机**:`S1 READ_PROFILE → S2 SELECT_ATOMS → S3 RANK → S4 BUCKET_BY_DAY → S5 EMIT_DIFF → S6 MANAGER_COMMIT`。
- **决策点**:S2 目标过大→裁剪;现状不足→`needs_input`(Manager 追问)。
- **中断恢复**:S6 落库前可丢弃;落库后版本化。
- **异常路径**:① 现状缺失→needs_input;② atom 库覆盖不足→提示"知识库待补充"并给部分路径;③ commit 冲突→Manager 重读后重试 1 次。
- **延迟/成本**:p95 ≤20s;成本 $0.05-0.10。

### 5.3 修改学习计划

- **触发**:诊断结论到达 或 用户反馈("并发还是不会,重排")。
- **Agent**:Manager →(DiagnosisAgent 若需)→ PlanningAgent(mode=modify) → Manager commit。
- **状态机**:`S1 LOAD_PATH → S2 LOAD_DIAGNOSIS → S3 RANK_DIFF → S4 EMIT_DIFF → S5 COMMIT`。
- **决策点**:用户反馈与诊断冲突→以用户为准并标注(Proposal §7);空 diff→返回"无需调整"。
- **中断恢复**:同 5.2。
- **异常路径**:① 无既有 path→转 generate;② 诊断置信低→标注"基于低置信诊断";③ commit 冲突→重试。
- **延迟/成本**:p95 ≤20s;成本 $0.05-0.10(不含诊断)。

### 5.4 模拟面试(状态机重点 + interrupt)

- **触发**:用户请求 mock 或场景 A 后用户接受建议。
- **Agent**:Manager handoff → MockInterview 子图(Interviewer/Judge/Strategist/Coach)。
- **状态机**:
  - `S1 INIT(topic, difficulty)` → `S2 INTERVIEWER_TURN(出题)` → `S3 AWAIT_USER(interrupt 点)` → `S4 JUDGE(评分)` → `S5 STRATEGIST(决策)` →
    - action=continue/raise/lower/switch_topic → 回 S2
    - action=pause → 保存 checkpoint,等 resume
    - action=end 或 turn≥max_turns 或 timeout → `S6 COACH_REVIEW` → `S7 SETTLE(emit events, Manager 更新 mastery)`
    - action=escalate → 子图返回 Manager(S_EXIT)
- **决策点**:S5 是核心分叉(Q4 边界:局部 vs escalate);难度规则连续 2 轮 ≥4 升、≤2 降。
- **中断恢复**:合法中断点=S3;"换 topic/调难度/暂停/跳过"局部 resume,Judge 累积分不重置(Proposal §4d);"结束/改计划"escalate。pause 用 LangGraph checkpoint 持久化,可跨会话 resume。
- **异常路径**:① 子 agent 超时→兜底题/跳轮(max 2);② session 超时→强制 S6 给部分复盘;③ 用户答非所问→Interviewer 追问澄清(不计入升降档)。
- **延迟/成本**:单轮首字 ≤5s;单场 ≤15min、≤10 轮、$0.15-0.30。

### 5.5 诊断弱点(Q5 自动触发)

- **触发**:① 用户主动;② **每场 mock 结束自动触发**(post_mock);③ 复合任务内部。
- **Agent**:Manager → DiagnosisAgent(只读)。
- **状态机**:`S1 LOAD_EVENTS(window) → S2 JOIN_MASTERY → S3 CLUSTER → S4 RANK → S5 EMIT_RESULT`。
- **决策点**:数据不足→低置信声明;无弱点→空簇如实返回。
- **中断恢复**:无状态、可重跑。
- **异常路径**:① events 为空→`confidence<0.5`;② atom join 缺失→仅按 events 频次;③ 超时→返回已聚合部分。
- **延迟/成本**:p95 ≤10s;成本 $0.03-0.05。**注**:post_mock 自动触发使每场 mock 总成本上浮约 $0.05,需计入 mock 预算监控(§8、§10)。

### 5.6 复合端到端 Workflow:诊断 → 修改路径 →(建议)模拟面试

- **触发**:"快面试了帮我准备"。
- **状态机**:`P1 Manager.PLAN(DAG: Diag→Modify)` → `P2 DiagnosisAgent` → `P3 PlanningAgent(modify, 注入诊断)` → `P4 Manager.COMMIT path` → `P5 AGGREGATE + 建议 mock` →(用户接受)→ `P6 进入 5.4`。
- **关键决策点**:P3 诊断置信低→Planning 标注;P5 不自动启 mock(控制权交回,Proposal §4d 场景 1)。
- **中断恢复**:P1-P4 任一 worker 失败→Manager replan(≤2);P6 进入 mock 后归 5.4 的中断模型。
- **异常路径**:① 诊断空→跳过 modify,直接建议先做 mock 采集数据;② Planning needs_input→Manager 追问后续派;③ 全链路超 60s→返回已完成部分 + trace_id。
- **延迟/成本**:端到端 p95 ≈30-40s(诊断+规划串行);成本 $0.10-0.20(不含后续 mock)。

---

## 6. Communication & Context Management

### 6a) Message Passing 协议细节

- **同步 vs 异步**:Manager↔Worker、Worker↔sub-agent 为**同步请求-响应**(强类型,可枚举返回,Proposal §5 决策 7);弱点信号为**异步事件**写入 `interaction_events`(Proposal §4c)。
- **超时与重试**:每条 task 携带 `budget.timeout_s`;超时抛 `AgentTimeout`。重试策略:幂等只读 agent(Router/Retrieval/Judge)最多重试 1 次;有副作用的提交不自动重试,交 Manager replan。
- **投递语义**:同步调用进程内直接函数调用(LangGraph 节点),**exactly-once**;异步事件 append-only 日志,**at-least-once**,消费侧(Diagnosis)用 `event_id` 幂等去重。
- **消息丢失/重复**:同步无丢失;异步事件靠 `event_id` 幂等;trace 写入失败不阻断主流程(best-effort)。

### 6b) Context 分层与隔离(Proposal §5 决策 8)

- **全局静态**(所有 agent 共享,版本化引用):系统宪法(安全/格式规范)、Atom 分类法、当前日期。以 `global_static_ref` 传引用,不内联。
- **任务专属**(按 agent 注入 `scoped_slice`):QA 注入检索片段+相关 atom brief;Planning 注入诊断结论+现有 path;Mock 子 agent 注入本场 state。
- **agent 内部累积**(不外传):各 sub-agent 的中间推理、mock 完整对话历史(仅子图内可见,禁止漏给 QA/Diagnosis,Proposal R7)。
- **handoff_summary(Q2)**:总是由 **Haiku** 对父级上下文生成摘要,**上限 ≤512 token**;格式=`{intent, key_facts[], constraints[], open_items[]}`;跨子系统 handoff(如 Mock→Manager escalate)必带。

### 6c) Prompt Assembly 优先级与 KV cache

- 拼装顺序(从稳定到易变,KV-cache 友好 prefix):`System(身份/安全)` → `Skill(agent 专属能力)` → `Project Constitution(格式/反模式)` → `Memory(UserProfile 摘要)` → `Retrieved(检索片段)` → `Session(handoff_summary)` → `User Input`。
- **冲突解决**:越靠后优先级越高,但**安全/格式规范(System/Constitution)不可被 User Input 覆盖**;检索证据与模型先验冲突时以检索为准(RAG 原则)。
- **KV cache**:前 4 段(System/Skill/Constitution/Memory)对同一 agent 稳定,作为可缓存 prefix;只在尾部追加 Retrieved/Session/Input,最大化 prefix 命中。

---

## 7. Storage & Retrieval

### 7a) 向量索引设计

- 向量库:**sqlite-vec**(`vec0` 虚拟表);相似度:**余弦**;索引:**HNSW**。
- 维度:`1024`(默认配 bge-large/voyage-3-lite 类);若改用 OpenAI text-embedding-3-small 则 `1536`——**决策依据**:本地单用户优先小而快的本地/低价 embedding,最终维度在 Implementation 首个 spike 锁定。
- 关键参数:`M=16`、`ef_construction=200`、`ef_search=64`(召回/延迟平衡,对齐检索 ≤1.5s)。

### 7b) 混合检索策略(Q3)

- **本地 KB**:sqlite-vec 向量 + FTS5(BM25)各召回 `top_k*4`,用 **RRF(k=60)** 融合,取 top_k;**不上 rerank**(控延迟与成本)。
- **云端 KB(未来,§10 Gap)**:**混合排序 + cross-encoder rerank**(仅对 top_k*4 重排)。后端经 `backend` 参数切换,RetrievalAgent 接口不变。
- **字段权重**:FTS5 中 `canonical_question` 权重 > `canonical_answer` > `tags`。
- **rerank**:本地不用;云端用,理由=云端语料更杂、跨域噪声大,rerank 收益足以覆盖其延迟。

### 7c) 切片策略

- **来源类型**:`source_type ∈ {interview_post, blog, doc, atom}`。
- **切法**:文档/博客=**语义切**(按标题层级 + 512-token 窗,128 overlap);面经=**父子 chunk**(题目为父,解析为子);atom 本身即最小单位不再切。
- **metadata 字段**:`{source_type, topic, parent_chunk_id?, atom_refs[], token_count, created_at}`,供 filters 过滤与父子回溯。

---

## 8. Error Handling & Resilience

### 8a) Agent 级失败

- 超时:抛 `AgentTimeout`;只读幂等 agent 重试 1 次,其余交 Manager。
- 输出不合规(schema 校验失败):重试 1 次并在 prompt 追加格式纠正;再失败返回 `status=error`。
- 策略选择:**retry**(瞬时/格式)→ **fallback**(降级,如检索超时走无证据合成)→ **replan**(Manager 级,worker 持续失败)。

### 8b) Workflow 级失败

- 复合任务中途失败:已成功子任务结果保留(写操作未提交则丢弃 diff,保证不半写 path)。
- 部分成功呈现:Manager 返回 `status=partial`,明确告知"已完成 X,Y 失败",附 trace_id 供追溯。
- 回退:写操作走单点提交(Manager),失败即整体不落库,避免 path 半更新。

### 8c) 外部依赖失败

- **LLM API**:超时/429 → 指数退避重试 2 次;持续失败 → 该 agent fallback(Haiku 降级或模板)并标 `status=error`。
- **向量库**:异常 → 仅 FTS5(§3.6)。
- **数据库**:读失败重试 1 次;写失败回滚事务、不提交、向用户报错(单用户本地,无并发锁竞争问题)。

---

## 9. Observability & Evaluation

### 9a) 日志与追踪

- 每次 agent 调用写一条 `agent_traces`:`message_id, trace_id, sender, recipient, status, confidence, cost_usd, latency_ms`(Proposal §7)。
- 关键埋点:每能力端到端延迟(p50/p95)、每能力累计成本、检索 Recall 抽样、mock 评分一致性。
- 全链路 `trace_id` 串联,LangGraph 节点级 span。

### 9b) 评测体系

- **Agent 级**(Proposal §7,数据集见 9c):Router=分类准确率;Retrieval=Recall@8/MRR;Synthesizer=对参考答案准确率;Verifier=注入错误拦截率;Judge=评分一致性(同答案两评分档差≤1)≥80%;Strategist=难度调整人工评分;Planning=路径覆盖度/优先级合理性;Diagnosis=Recall@5≥70%。
- **系统级 / Trajectory Eval**:对端到端场景集(场景 A/B/C + 复合 5.6)回放,断言:(1)消息链拓扑正确;(2)闭环数据回流(mock/qa 后 mastery 更新、诊断后 path 重排);(3)无越权写(只 Manager 写)。
- **责任归属**:失败时按 `trace_id` 拉 `agent_traces`,定位首个 `status=error` 或 `confidence` 异常低节点,结合该 agent golden set 判定"自身错"还是"上游脏 context"。

### 9c) Golden Set 设计

- Router:≥150 条(四类问题均衡)。
- Retrieval:≥100 query + 标注相关 chunk。
- Synthesizer/Verifier:≥80 条(含 20 条注入错误/时效陷阱)。
- Judge:≥50 题 ×2-3 份不同质量回答(测一致性)。
- Planning:≥30 目标场景。
- Diagnosis:≥30 用户档案 + 人工标注弱点。
- 覆盖维度:正常 + 边界 + 对抗(无证据/冲突/数据不足)。

---

## 10. Open Issues & Next Steps

### 10a) Proposal Gaps(发现的遗漏/矛盾,不默改)

- **G1 云端知识库与 Non-Goal 冲突**:Proposal §2 Non-Goal 明确"只接本地知识库",但 Design 阶段澄清未来会接**云端 KB(混合排序+rerank)**。**临时处置**:MVP 严格只实现 LocalBackend(不违反 Non-Goal),RetrievalAgent 留 `backend` 参数与抽象接口,云端实现推迟到 MVP 之后;此 gap 需回写 Proposal Non-Goal 措辞("本期不实现云端,但接口前向兼容")。
- **G2 mastery 写入时机未明**:Proposal 说"会话结算写 mastery"但未定义"结算点"。**临时处置**:定为 mock S7-SETTLE 与 QA 返回后各触发一次 Manager 提交。
- **G3 Q5 自动诊断的成本归属**:Proposal §7 mock 预算 $0.30 未含 post_mock 自动诊断(+$0.05)。**临时处置**:mock 场景预算上调为 $0.35 或将诊断成本单列;需 Proposal 确认。
- **G4 RetrievalAgent 与 Manager 层级**:Proposal 说 Retrieval"不被 Manager 直接调度",但复合诊断中 Manager 可能需独立检索。**临时处置**:维持原则,Manager 需检索时经 DiagnosisAgent/QAAgent 间接调用,不破层级。

### 10b) Design 阶段遗留(留 Implementation 解决)

- embedding 模型与维度(1024 vs 1536)——首个 retrieval spike 锁定。
- handoff_summary 的 ≤512 token 是否足够保真——用评测集回归调(Proposal Q2/R6)。
- RRF k=60 与 ef_search=64 的具体调参——retrieval 评测集压测后定。
- StrategistAgent 升降档阈值(连续 2 轮)——mock 评测后微调。

### 10c) Task 阶段建议

- **拆分主轴:按"数据层 → 共享能力 → 域 worker → 编排"分层并行**:
  - T-A(数据层,无依赖,先行):SQLite schema + sqlite-vec/FTS5 + Atom 模型 + 仓储接口。
  - T-B(共享能力,依赖 T-A):RetrievalAgent(LocalBackend)+ 消息 envelope + ContextEnvelope。
  - T-C(域 worker,依赖 T-B,可并行):QA 子系统 / PlanningAgent / Mock 子图 / DiagnosisAgent 四条线并行。
  - T-D(编排,依赖 T-C):ManagerAgent + plan/replan + commit。
  - T-E(横切):observability(agent_traces)+ golden set + eval harness,与 T-C 并行。
- **关键依赖**:T-A → T-B → {T-C, T-D};T-E 可早启。
- **并行机会**:四个域 worker(T-C)接口由 §3/§4 锁定后完全并行;eval(T-E)与开发并行。

---

*本 Design 在 Proposal 决策之上细化了字段级 schema、状态机、边界与失败模式。完整 Pydantic 代码、DDL、单测、部署留待 Implementation。*
