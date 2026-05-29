# LearnForge Proposal —— 面向程序员的学习伙伴 Agent(MVP)

> 阶段:Proposal(决策依据,不是设计文档)
> 架构方向:Hierarchical Multi-Agent System
> 部署形态:Local-first 单用户(SQLite + sqlite-vec + FTS5)
> 本文目标:确定"做什么、为什么、不做什么、风险在哪",为 Design 阶段提供拍板依据

---

## 1. Problem Statement(问题陈述)

程序员的长期学习与面试准备,真实痛点不是"缺资料",而是"缺一个能记住我、持续诊断我、并据此调整我学习路径的闭环"。市面工具把每个环节割裂成孤立 App:刷题归刷题、笔记归笔记、模拟面试归模拟面试,数据互不流通,弱点永远靠自己手动盘点。

五个能力各自针对一个真实痛点:

- **问答能力**:用户卡在某个八股概念或自己项目的设计取舍上时,需要的不是搜索引擎那种"十个标签页自己拼答案",而是结合自身上下文、且对时效性问题(如某框架最新版本行为)不会一本正经胡说的可信回答。痛点:**减少"自己当裁判判断答案对不对"的认知负担**。
- **生成学习计划**:用户有目标(如"三周内准备后端 SDE 面试")但不知道从哪开始、什么该跳过。痛点:**把模糊焦虑翻译成有优先级、可执行、量化到天的路径**。
- **修改学习计划**:计划做完即失效是最大问题——真实学习是动态的。痛点:**让计划能随诊断结果和现实进度自动重排,而不是一张很快过期的死清单**。
- **模拟面试**:约真人贵且难安排,自己对着题库背又暴露不出"被追问时的崩溃点"。痛点:**用零成本、可随时进行的对抗式追问,逼出用户自己意识不到的弱点**。
- **诊断弱点**:用户答错了也往往不知道"为什么错、错在哪类知识、下一步该补什么"。痛点:**把散落在问答和 mock 中的错误信号,聚合成一句话能行动的结论**。

**这五个不是孤立点,而是一个闭环(Knowledge Atom Loop)**:用户在问答和 mock 中产生交互信号 → 这些信号沉淀为带掌握度标记的 Knowledge Atom → 诊断基于 Atom 聚合出弱点 → 学习计划据此重排优先级 → 用户按新计划学习并再次问答/mock → 信号回流。数据流的核心载体是 **Knowledge Atom 这一最小学习单位的掌握度状态**,它是把五个能力黏合成闭环、而非五个独立功能的关键。没有这个回流,本项目就退化成"五个 chatbot 拼盘"。

---

## 2. Goals & Non-Goals(目标与非目标)

### Goals(MVP 成功标准,可衡量)

- **功能完成度**:五个能力的 happy path 全部跑通,且"问答→诊断→改计划"和"mock→诊断→改计划"两条闭环至少各演示一次端到端数据回流。
- **质量门槛**:
  - 问答:对内置评测集事实准确率 ≥ 85%;时效性问题必须显式声明不确定或调用检索,严禁无依据编造(可验证答案 0 编造)。
  - 模拟面试:同一份回答两次独立评分的评分一致性(分档偏差 ≤ 1 档)≥ 80%。
  - 诊断:对人工标注的弱点集 Recall@5 ≥ 70%。
- **性能目标**:
  - 问答 p95 端到端 ≤ 8s(含一次检索 + 一次合成)。
  - 模拟面试单轮(出题/追问)首字延迟 p95 ≤ 5s;单场总时长可控在 ≤ 15 分钟。
  - 计划生成 p95 ≤ 20s。
- **成本目标**(单用户本地,成本=API token):
  - 问答单次 < $0.03;模拟面试单场(约 8-10 轮)< $0.30;计划生成单次 < $0.10;诊断单次 < $0.05。
  - 模型分级:路由/裁判/抽取用 Haiku,合成/出题/复盘用 Sonnet。

### Non-Goals(边界,比 Goals 更重要)

- **不做完整 SRS(间隔重复)调度闭环**:Knowledge Atom 记录掌握度,但不实现 Anki 式复习时间排程。
- **不做笔记自动整理/知识库构建**:不主动抓取、清洗、归档用户的外部笔记。
- **不接外部 MCP / 第三方工具生态**:本期检索只接本地知识库 + 受控的时效性查询入口。
- **不做异步多用户协作 / 多租户**:单用户本地,不设账号体系、权限、并发隔离。
- **不做真人面试官替代级别的开放领域面试**:mock 限定在程序员技术面(八股 + 项目深挖),不覆盖行为面/系统设计白板的完整体验。
- **不做语音/实时音视频**:纯文本交互。
- **不追求 Agent 数量最大化**:不为了"看起来多 agent"而过度拆分(见 §5)。

---

## 3. Scope & User Stories(范围与用户故事)

**问答**
> 作为正在复习的程序员,我希望问"我项目里用乐观锁还是悲观锁更合适",得到结合我项目上下文的回答,以便不用自己在通用资料里翻找取舍。
> *涉及 Agent*:Manager 路由到 QA 子系统;内部 RouterAgent 判类型 → RetrievalAgent 取本地 Atom/项目上下文 → SynthesizerAgent 合成 → VerifierAgent 对可验证声明把关。

> 作为关心时效的用户,我希望问"某框架最新版默认行为变了吗",系统在不确定时明确说明或走时效查询,以便不被过期答案误导。
> *涉及 Agent*:RouterAgent 标记为时效类 → 触发时效检索通道 → VerifierAgent 强制要求来源或降级为"不确定"。

**生成学习计划**
> 作为三周后面试的人,我希望输入目标和现状就拿到分天、有优先级的路径,以便立刻知道今天该学什么。
> *涉及 Agent*:Manager → PlanningAgent;PlanningAgent 读取 UserProfile + 现有 Atom 掌握度,产出结构化路径写入共享状态。

**修改学习计划**
> 作为发现自己进度落后的用户,我希望说"我并发这块还是不会,重排一下",计划自动调整优先级,以便不必从头重写。
> *涉及 Agent*:Manager → DiagnosisAgent(读弱点)→ PlanningAgent(按 diff 重排);二者通过共享状态 + 诊断事件衔接。

**模拟面试**
> 作为想暴露弱点的用户,我希望进行会动态加难、被追问的 mock,并在结束拿到复盘,以便发现自己"会但说不清"的点。
> *涉及 Agent*:Manager 把会话 handoff 给 MockInterview 子系统;内部 InterviewerAgent 出题/追问、JudgeAgent 评分、StrategistAgent 调难度与选题、CoachAgent 终场复盘。

> 作为 mock 中途想换方向的用户,我希望说"换个 topic",系统平滑切换且不丢已积累的评分,以便按自己节奏练。
> *涉及 Agent*:StrategistAgent 接管中断意图,调整选题策略,局部影响 Interviewer,不重置 Judge 已累积状态。

**诊断弱点**
> 作为长期学习者,我希望系统定期或按需告诉我"你最弱的三类知识 + 下一步该补什么",以便把精力花在刀刃上。
> *涉及 Agent*:Manager → DiagnosisAgent;只读聚合 Atom 掌握度与历史信号,产出弱点结论 + 行动建议,不直接改任何状态。

---

## 4. Agent Architecture(Agent 架构设计)

本章是 Proposal 核心。**结论先行:我们不做"5 能力 → 5 Agent"的一对一映射。** 理由有三:(1)"生成计划"与"修改计划"共享同一套规划推理,差异只是输入上下文(从零 vs 带 diff),拆成两个 agent 会复制人格和提示词、制造一致性维护负担;(2)诊断不是一个独立用户入口,而是被"改计划"和"复盘"复用的横切能力,适合做成一个**只读分析 agent**;(3)检索在问答、诊断、mock 里都要用,应下沉为**共享子能力**而非每个域各写一份。

### a) Agent 分解

**ManagerAgent(编排者 / Orchestrator)**
- 角色定位:系统唯一对外入口,负责意图理解、任务分派、跨 worker 结果聚合与失败重规划。
- 核心能力:① 把用户复合请求拆成有依赖关系的子任务;② 维护会话级编排状态;③ 处理 worker 失败时的有限次重规划。
- 独立人格:有,且是"调度型"提示词——只做编排不做领域推理,刻意不内置八股/面试知识,避免它越权替 worker 干活。
- 是否有 sub-agent:无;它统辖 worker,但自身不再下挂 sub-agent。

**QAAgent 子系统(知识问答)**
- 角色定位:回答八股、项目相关、时效性三类问题,并对答案可信度负责。
- 核心能力:① 问题分类与路由;② 检索增强合成;③ 对可验证声明的事实把关。
- 独立人格:子系统对外是一个人格(回答风格统一),内部分 sub-agent。
- sub-agent:**RouterAgent**(判问题类型/是否需检索/是否时效)、**SynthesizerAgent**(合成回答)、**VerifierAgent**(只在可验证/时效类问题上启用,做事实与来源把关)。检索不在此列,见"共享子能力"。

**PlanningAgent(学习路径规划,合并"生成+修改")**
- 角色定位:从目标/现状产出可执行学习路径,或基于诊断/反馈重排已有路径。
- 核心能力:① 目标拆解与优先级排序;② 路径 diff 重排(增删改 Atom 顺序);③ 把路径写入共享状态。
- 独立人格:有,"规划师"人格,不负责评判对错(那是诊断的事),只负责排程与取舍。
- sub-agent:MVP 不拆;生成与修改共用同一推理,以"是否带现有路径+诊断结果"作为分支,而非两个 agent。

**MockInterviewAgent 子系统(模拟面试)**
- 角色定位:主持一场有动态难度、可被中断、有终场复盘的对抗式技术面试。
- 核心能力:① 出题与追问;② 回答评分;③ 难度/选题策略调整与中断处理;④ 终场复盘。
- 独立人格:子系统对外是"面试官"人格,内部多 sub-agent 分工。
- sub-agent:**InterviewerAgent**(出题/追问,直接对话用户)、**JudgeAgent**(每轮独立评分,刻意与 Interviewer 分离以防"出题者给自己题打分"的偏置)、**StrategistAgent**(根据 Judge 评分调难度、决定下题方向、处理"换 topic"等中断)、**CoachAgent**(终场把逐轮评分聚合成可行动复盘)。

**DiagnosisAgent(弱点诊断,只读)**
- 角色定位:横切的分析者,把散落信号聚合成弱点结论与行动建议。
- 核心能力:① 聚合 Atom 掌握度与历史交互信号;② 识别薄弱知识簇;③ 产出"弱点+建议"结构化结论。
- 独立人格:有,"诊断师"人格,强约束为**只读**——只输出结论,不直接改任何路径或 Atom(写操作必须回到 Manager→Planning,见 §5)。
- sub-agent:MVP 不拆。

**共享子能力:RetrievalAgent(检索)**
- 角色定位:跨 QA / Diagnosis / Mock 复用的检索能力,封装 sqlite-vec 向量检索 + FTS5 关键词检索的混合召回。
- 在 §5 中讨论它应是 Agent 还是工具——结论是**作为带轻量人格的检索 agent**而非裸工具,以便承担查询改写与混合排序的小规模推理。

### b) Agent 层级

- **第 0 层 Manager**:plan-and-execute 为主、对失败有限循环监督(见 §5 决策 1)。它是唯一调度者。
- **Manager 直属 Worker(第 1 层)**:QAAgent、PlanningAgent、MockInterviewAgent、DiagnosisAgent 四个域 worker。
- **拥有自身 sub-agent 的 Worker**:QAAgent(Router/Synthesizer/Verifier)与 MockInterviewAgent(Interviewer/Judge/Strategist/Coach)。PlanningAgent 与 DiagnosisAgent 在 MVP 内为单体 worker。
- **横切共享层**:RetrievalAgent 被 QAAgent、DiagnosisAgent、MockInterviewAgent 调用,但**不被 Manager 直接调度**——它是 worker 内部按需调用的共享子能力,这样 Manager 的编排面保持在四个域 worker,层级清晰。

层级关系用文字描述为:Manager 在顶,四个域 worker 在中,其中两个域 worker 各自下挂一组 sub-agent;RetrievalAgent 作为共享子能力悬挂在中层侧边,被需要它的域 worker 调用。**严格 hierarchical:sub-agent 只对其父 worker 负责,worker 只对 Manager 负责,不存在 worker 之间或 sub-agent 之间的横向直连**(诊断与规划的衔接也经由共享状态 + Manager,而非直接互调)。

### c) 通信机制选型

三种机制混合,各有明确分工:

- **Message Passing(结构化消息,主力)**:用于 Manager↔Worker 的任务分派与结果回收,以及 worker↔其 sub-agent 的调用。消息是显式的 `task / context / response` 三段式信封(Pydantic 结构化,见 §5 决策)。**为什么**:分派需要可枚举的成功/失败返回、可重放、可单测——这是 hierarchical 区别于 swarm 的根本,必须强类型同步调用。
- **Shared State(共享状态)**:用于跨 agent 的长期数据——**UserProfile、LearningPath、Knowledge Atom 掌握度**——统一落 SQLite。**为什么**:这些数据生命周期跨越多次会话和多个 agent,若靠消息层层传递会造成上下文爆炸和不一致;放共享存储让各 agent 按需"作用域查询"自己关心的切片,而不是被广播全量上下文。**关键约束**:共享 ≠ 广播——每个 sub-agent 只读到自己作用域内的切片 + 一个 `handoff_summary`,不给完整库。
- **Event Log / Pub-Sub(事件日志,轻量)**:问答和 mock 产生的弱点信号以"事件"追加写入一个交互事件日志;DiagnosisAgent 按需消费该日志做聚合。**为什么**:诊断与信号产生是时间解耦的(用户可能学一周后才诊断),用事件日志而非同步消息,避免问答/mock 每次都强耦合调用诊断。

跨 agent 共享数据的归属:UserProfile / LearningPath / Atom 由 **Manager 拥有写权限的边界**(经 Planning 写路径、经会话结算写 Atom 掌握度);DiagnosisAgent 只读,Retrieval 只读。

### d) Agent 间数据流(两个典型场景)

**场景 1 —— 用户:"快面试了帮我准备"**
1. Manager 识别这是复合意图,plan 出有依赖的子任务序列:先诊断现状 → 再据此(重)排计划 → 给出"建议先做一场 mock"的下一步。
2. Manager 以 message passing 派发任务给 DiagnosisAgent(只读,产出弱点结论)。
3. Manager 把诊断结论作为 context 派发给 PlanningAgent,后者读共享状态里的现有路径,做 diff 重排,写回 LearningPath。
4. Manager 聚合两者结果,向用户输出"已按你最弱的三块重排了三周计划,建议明天先做一场并发主题 mock",并不自动启动 mock(把控制权交回用户)。
- 聚合点只有一个:Manager。worker 之间不互相聚合,失败可定位到具体子任务。

**场景 2 —— 用户在 mock 中途:"换个 topic"**
1. 此时控制权已 handoff 给 MockInterviewAgent 子系统,Manager 处于"已让渡、监督中"状态。
2. 中断意图由 **StrategistAgent** 接收(它本就负责选题策略),它更新本场选题状态:切换主题、调整难度基线。
3. 影响范围被刻意限制为局部:InterviewerAgent 据新主题出下一题;**JudgeAgent 已累积的逐轮评分不重置**(否则前面的练习数据白费);CoachAgent 终场仍能给出跨主题的完整复盘。
4. Manager 不介入——除非 StrategistAgent 判定中断意图其实是"想结束 mock"这类超出本子系统职责的请求,才上抛给 Manager。这体现了 handoff 后的自治边界:局部中断局部处理,不打扰顶层。

---

## 5. Key Design Decisions(关键决策)

**决策 1:Manager 用 plan-and-execute + 有限失败循环,而非纯 ReAct 循环。**
- 理由:在 ReAct(每步都问 LLM 下一步)与一次性 plan-and-execute 之间选了"先 plan,执行中仅在 worker 失败时有限重规划"。纯 ReAct 每步一次 LLM 调用,延迟和成本随步数线性膨胀且决策路径不可枚举;一次性 plan 又无法应对 worker 失败。折中方案保留可枚举性又有容错。
- 代价:面对训练分布外的复合请求,初版 plan 可能不优;需要一个重规划次数上限(如 ≤ 2)防止死循环。

**决策 2:合并"生成计划"与"修改计划"为单一 PlanningAgent。**
- 理由:在"两个独立 agent"与"一个 agent 两分支"之间选后者。二者共享 90% 的规划推理,差异仅在输入(是否带现有路径+诊断)。拆开会复制人格与提示词,埋下两套逻辑漂移的隐患。
- 代价:PlanningAgent 提示词需处理两种输入形态,略复杂;但远低于维护两个 agent 一致性的成本。

**决策 3:设独立 VerifierAgent,但只在 QA 的可验证/时效类问题上启用,不全局加 Critic。**
- 理由:在"全系统统一 Critic 层""不要 Verifier""按场景选择性 Verifier"之间选第三者。八股事实和时效性是最容易出现自信编造、且后果最严重的地方,值得一个独立把关者;但对主观性强的环节(如 mock 复盘措辞)加 Critic 收益低、徒增成本。
- 代价:增加一次 LLM 调用(用 Haiku 控成本);需要明确"什么算可验证声明"的判定逻辑。

**决策 4:模拟面试中 Judge 与 Interviewer 必须分离,Strategist 独立。**
- 理由:在"Interviewer 兼任评分""Judge 独立""Strategist 并入 Interviewer"之间,选择三 agent 分立(+终场 Coach)。出题者给自己出的题打分有结构性偏置;难度策略若混进 Interviewer,会让"出题"和"决定下一步难度"耦合,难以单测和评测。分离后每个 sub-agent 职责单一、可独立评分一致性测试。
- 代价:单轮涉及多次 sub-agent 调用,延迟与成本上升;需用 Haiku 承担 Judge/Strategist 的高频小推理来对冲。

**决策 5:DiagnosisAgent 严格只读,所有写操作必经 Manager→Planning。**
- 理由:在"诊断可直接改计划"与"诊断只读、写经 Manager"之间选后者。让一个分析 agent 同时有写权限会模糊"谁改了路径"的责任归属,也容易在用户没确认时擅自改路径。只读 + 写权集中,使状态变更可审计、可追溯。
- 代价:多一跳(诊断→Manager→规划),增加一次编排;但换来清晰的写边界,值得。

**决策 6:RetrievalAgent 作为共享检索 agent,而非裸工具。**
- 理由:在"纯函数工具"与"轻量检索 agent"之间选后者。检索质量取决于查询改写和"向量+FTS5 混合排序"的小推理;做成裸工具会把这些逻辑散落进每个调用方。封装成共享 agent 可统一改写策略、统一被 QA/诊断/mock 复用。
- 代价:比纯工具多一层抽象和可能的一次轻量 LLM 调用(查询改写);需防止它变成"什么都往里塞"的上帝对象——其职责严格限定为"召回+排序",不做合成。

**决策 7:Agent 间消息一律结构化(Pydantic `task/context/response` 信封)。**
- 理由:在"自由文本 handoff"与"结构化 schema"之间选后者。自由文本在 swarm 里常见,但会让失败模式不可枚举、接口隐性漂移。结构化信封让每条消息可校验、可日志、可单测,是 hierarchical 可调试性的基础。
- 代价:接口变更需改 schema,初期迭代摩擦略大;通过把 `context` 设计成带版本的松散载荷区缓解。

**决策 8:上下文隔离按"作用域切片 + handoff_summary",默认隔离、按需放行。**
- 理由:在"广播全量上下文"与"严格作用域隔离"之间选后者。全量广播会造成 prompt 污染(mock 的对话历史污染问答)、成本膨胀、且违反多 agent 隔离原则。每个 sub-agent 只拿到完成任务所需的最小切片 + 一份父级提供的 `handoff_summary`。
- 代价:切片给少了会导致 agent 决策信息不足(见 §6 风险)。`handoff_summary` 的大小是需在 Design 阶段调的关键旋钮(初定上限,如 ≤ 500 token,**待 Design 阶段确定**)。

**决策 9:Runtime 选 LangGraph,放弃 AutoGen 与纯自研。**
- 理由:在 LangGraph / AutoGen / 自研之间选 LangGraph。它的图式编排天然贴合 hierarchical(节点=agent、边=显式控制流),内置 state、checkpoint 与 **interrupt/resume**——后者正好满足模拟面试"中途中断、换 topic、可恢复"的硬需求。AutoGen 偏对话式自由协作,更接近我们要规避的 swarm,失败模式难枚举;纯自研要重造 checkpoint/interrupt/状态机,在单用户 MVP 阶段不划算。
- 代价:绑定 LangGraph 的抽象与版本;消息信封(决策 7)需在 LangGraph state 之上自建一层,避免被其默认 state 模型绑死。

**决策 10:存储用 SQLite + sqlite-vec + FTS5,本地单库。**
- 理由:契合 local-first 单用户定位,零运维、单文件可移植。pgvector 适合多用户/SaaS,本期 Non-Goal。sqlite-vec 管向量、FTS5 管关键词,正好支撑 RetrievalAgent 的混合召回。
- 代价:未来转 SaaS 需迁移到 PostgreSQL+pgvector;通过把存储访问收敛到一层仓储接口,降低未来迁移成本。

---

## 6. Risks & Open Questions(风险与未决问题)

### Risks

- **R1 多 agent 调度延迟累积**:plan-and-execute + 多 sub-agent,单个复合请求可能串起 5+ 次 LLM 调用,p95 超标。*影响*:用户体感慢,尤其问答这类期待快的场景。*缓解*:问答走"轻路径"(Router 判定简单事实问题时可跳过 Verifier);高频小推理统一用 Haiku;可并行的子任务(如检索与无依赖分析)并行化。
- **R2 消息协议设计不当导致频繁改接口**:`task/context/response` 信封若一开始约束太死,域 worker 演进时会反复改 schema。*影响*:开发摩擦、回归风险。*缓解*:核心字段强类型 + `context` 留版本化松散载荷区;接口变更走兼容性审查。
- **R3 Manager 决策错误导致协作失败**:plan 拆错任务或派错 worker。*影响*:整条闭环产出错误结果且不易察觉。*缓解*:重规划上限 + 每个 worker 返回带 `status` 与 `confidence`,Manager 据此决定是否重派;关键路径留人工可见的编排日志。
- **R4 多 agent 系统评测复杂、责任归属难**:整体答错时分不清是检索、合成还是路由的锅。*影响*:质量问题难定位、迭代慢。*缓解*:结构化消息全程留痕 + 每个 agent 独立评测集(见 §7);失败时按消息链回溯。
- **R5 LLM 成本因多 agent 翻倍**:相比 single-agent,一次交互多次调用。*影响*:超出单次成本上限。*缓解*:模型分级(Haiku/Sonnet)、上下文作用域裁剪、对可缓存的检索结果做缓存;为每个能力设硬成本预算并监控。
- **R6 上下文隔离过严 → agent 决策信息不足**:如 PlanningAgent 拿不到足够诊断细节而排错优先级。*影响*:闭环产出质量下降。*缓解*:`handoff_summary` 大小做成可调旋钮,用评测集回归确定下限。
- **R7 上下文隔离过松 → prompt 污染**:mock 的长对话历史漏进问答或诊断,带偏结论。*影响*:答非所问、诊断失真。*缓解*:默认隔离、按需放行;sub-agent 只接收父级显式构造的切片,禁止直接读其他 worker 的会话历史。

### Open Questions(留待 Design 阶段)

- **Q1 Knowledge Atom 的掌握度如何量化与更新?** 是离散分档还是连续分数?问答正确、mock 答对、自评分别给多大权重?这是闭环数据流的核心,需单独设计。
- **Q2 `handoff_summary` 的最优大小与生成方式?** 由父 worker 用规则截取还是用一次 LLM 摘要?成本与信息保真的权衡待实测。
- **Q3 RetrievalAgent 的混合召回排序策略?** 向量与 FTS5 结果如何融合(RRF?加权?),查询改写是否值得那一次 LLM 调用,需基于检索评测集定。
- **Q4 模拟面试的中断意图边界如何界定?** 哪些中断由 Strategist 局部处理、哪些必须上抛 Manager,需要一份意图分类标准。
- **Q5 诊断的触发时机?** 纯按需,还是在每场 mock/一定问答量后自动触发?自动触发会影响成本与事件日志设计。

---

## 7. Success Criteria & Evaluation(成功标准与评测)

### 功能验收(happy path + edge case)

- **问答**:happy 路径——八股问题给出准确回答;edge——(a)时效性问题在无依据时显式声明不确定;(b)项目相关问题能引用本地上下文;(c)超出知识范围时拒答而非编造。
- **生成计划**:happy——目标+现状产出分天路径;edge——(a)目标过大时给出"超出周期"提示并裁剪;(b)现状信息不足时主动追问。
- **修改计划**:happy——按诊断结果重排;edge——(a)用户反馈与诊断冲突时以用户为准并标注;(b)空改动(无需调整)时明确告知。
- **模拟面试**:happy——多轮出题+追问+终场复盘;edge——(a)中途换 topic 不丢已累积评分;(b)用户答非所问时 Interviewer 追问澄清;(c)用户提前结束仍给部分复盘。
- **诊断**:happy——产出 top-N 弱点+行动建议;edge——(a)数据不足时声明置信度低;(b)无明显弱点时如实反馈而非硬凑。

### 质量指标(目标值)

- 问答事实准确率 ≥ 85%,可验证答案 0 编造。
- mock 评分一致性(同答案两评分档差 ≤ 1)≥ 80%。
- 诊断 Recall@5 ≥ 70%(对人工标注弱点集)。

### Agent 级评测

- **每个 Agent 可独立评测**:RouterAgent 用问题分类准确率;RetrievalAgent 用 Recall@k / MRR;SynthesizerAgent 用基于参考答案的准确率;VerifierAgent 用对注入错误声明的拦截率;JudgeAgent 用评分一致性;StrategistAgent 用难度调整合理性的人工评分;PlanningAgent 用路径覆盖度/优先级合理性。
- **整体协作评测**:用端到端场景集(如场景 1、场景 2)做闭环回归,检验数据是否正确回流(Atom 掌握度是否随 mock/问答更新、计划是否随诊断重排)。

### 性能指标

- 整体 p95:问答 ≤ 8s、mock 单轮首字 ≤ 5s、计划生成 ≤ 20s。
- 关键 agent 子调用 p95:检索 ≤ 1.5s、单次 LLM 合成 ≤ 4s(**具体阈值待 Design 压测后确定**)。

### 成本指标(单次平均上限)

- 问答 < $0.03;mock 单场 < $0.30;计划生成 < $0.10;诊断 < $0.05。每能力设硬预算并在编排层埋点统计 token。

### 责任归属机制

- 全链路结构化消息留痕:每条 `task/context/response` 带 `agent_id`、`status`、`confidence`、`trace_id`,同一用户请求共享 `trace_id`。
- 协作失败时按 `trace_id` 回溯消息链,定位首个 `status=error` 或 `confidence` 异常低的 agent;结合各 agent 独立评测集判断是该 agent 自身问题还是上游传入的脏 context,从而把责任精确归到具体 agent 而非"系统整体"。

---

*以上为 Proposal 阶段决策依据。Knowledge Atom 掌握度模型、消息 schema、handoff_summary 生成、检索融合策略等实现细节留待 Design 阶段。*
