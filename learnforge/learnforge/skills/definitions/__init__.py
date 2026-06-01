"""各 agent 的 skill 定义（第一版 prompt，不调优）。

每个 SkillSpec 绑定 system_prompt（Design §3 各节"System Prompt 大纲"）+ model_tier +
input/output schema + steps。由 `register_default_skills()` 注册进 SKILL_REGISTRY。

Phase 2 定义：Manager / Router / Synthesizer / Verifier / Planning / Retrieval(改写)。
Phase 3 定义：Mock 子 agent(Interviewer/Judge/Strategist/Coach) 与 Diagnosis。
"""

from __future__ import annotations

from ...contracts.agents.diagnosis import DiagnosisInput, DiagnosisResult
from ...contracts.agents.mock import (
    CoachInput,
    CoachReport,
    InterviewerInput,
    InterviewerOutput,
    JudgeInput,
    MockInput,
    MockOutput,
    Score,
    StrategistInput,
    StrategistOutput,
)
from ...contracts.agents.planning import PlanningInput, PlanningOutput
from ...contracts.agents.qa import (
    QAInput,
    QAOutput,
    RouterInput,
    RouterOutput,
    SynthesizerInput,
    SynthesizerOutput,
    VerifierInput,
    VerifierOutput,
)
from ...contracts.enums import AgentId, ModelTier
from ..base import SkillSpec
from ..registry import SkillRegistry


NO_BASH = ["No bash access. Use only declared tools and subskills."]


def _front(name: str, role: str, purpose: str) -> dict:
    return {
        "name": name,
        "role": role,
        "purpose": purpose,
        "loading": "progressive",
    }


# --- Router (§3.3) ---
QA_SKILL = SkillSpec(
    name="qa.shell.v1",
    agent_id=AgentId.QA,
    model_tier=ModelTier.HAIKU,
    frontmatter=_front(
        "qa.shell.v1",
        "qa_orchestrator_subagent",
        "Answer programmer questions by selecting the lightest safe internal strategy.",
    ),
    system_prompt=(
        "你是 QA 子系统外壳。只编排 Router/Synthesizer/Verifier 与检索，"
        "不改学习计划、不做长期诊断、不写掌握度。"
    ),
    allowed_tools=[
        "agent.router",
        "agent.synthesizer",
        "agent.verifier",
        "retrieval.search",
        "mcp.web.fetch_url",
        "mcp.github.repo_summary",
        "mcp.github.list_tree",
        "mcp.github.read_file",
        "mcp.notion.search_pages",
        "mcp.notion.read_page",
    ],
    tool_descriptions={
        "retrieval.search": "只读检索 LOCAL/SHARED 知识库；不得写 mastery/path。",
        "mcp.web.fetch_url": "Fetch a user-provided URL into a reusable markdown artifact.",
        "mcp.github.repo_summary": "Inspect public GitHub repositories for project/code questions.",
        "mcp.github.list_tree": "List GitHub repository files before reading specific paths.",
        "mcp.github.read_file": "Read one GitHub source or docs file.",
        "mcp.notion.search_pages": "Search the user's Notion workspace when explicitly relevant.",
        "mcp.notion.read_page": "Read a selected Notion page as evidence.",
    },
    subskill_descriptions={
        "agent.router": "判断 q_type 与是否需要检索/校验。",
        "agent.synthesizer": "基于证据合成回答并抽取 claims。",
        "agent.verifier": "校验可验证 claims，必要时弱化无证据断言。",
    },
    bash=NO_BASH,
    workflow=[
        "route_question",
        "select_strategy_from_skill_rules",
        "retrieve_only_if_strategy_requires_it",
        "synthesize_with_available_context",
        "verify_only_if_strategy_requires_it",
    ],
    constraints=[
        "Do not write learning paths, mastery scores, or diagnosis reports.",
        "Prefer the cheapest complete strategy; chitchat must skip retrieval and verifier.",
        "If a selected strategy requires an undeclared tool, fail fast instead of improvising.",
    ],
    strategy_rules={
        "concept": {
            "retrieve": True,
            "verify": True,
            "scopes": ["shared"],
            "method": "hybrid",
            "tools": ["agent.router", "retrieval.search", "agent.synthesizer", "agent.verifier"],
        },
        "temporal": {
            "retrieve": True,
            "verify": True,
            "scopes": ["shared"],
            "method": "hybrid",
            "tools": ["agent.router", "retrieval.search", "agent.synthesizer", "agent.verifier"],
        },
        "project": {
            "retrieve": True,
            "verify": True,
            "scopes": ["local", "shared"],
            "method": "hybrid",
            "tools": ["agent.router", "retrieval.search", "agent.synthesizer", "agent.verifier"],
        },
        "chitchat": {
            "retrieve": False,
            "verify": False,
            "scopes": [],
            "method": "keyword",
            "tools": ["agent.router", "agent.synthesizer"],
        },
    },
    input_schema=QAInput,
    output_schema=QAOutput,
    steps=["route", "retrieve_if_needed", "synthesize", "verify_if_needed"],
)


ROUTER_SKILL = SkillSpec(
    name="qa.router.v1",
    agent_id=AgentId.ROUTER,
    model_tier=ModelTier.HAIKU,
    frontmatter=_front("qa.router.v1", "classification_subagent", "Classify a question and set QA switches."),
    system_prompt=(
        "你是问答路由分类器。判定问题类型并设置下游开关，不作答、不解释、不臆测时效性。\n"
        "规则：temporal（时效类）强制 need_retrieval=need_verifier=True；"
        "concept（八股概念）默认 need_retrieval=True, need_verifier=True；"
        "project（项目相关）need_retrieval=True；chitchat（闲聊）全部 False 走轻路径。\n"
        "只输出固定枚举 JSON。"
    ),
    allowed_tools=["llm.complete_structured"],
    tool_descriptions={"llm.complete_structured": "Haiku JSON classification only."},
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["inspect_question", "classify_q_type", "set_retrieve_and_verify_switches"],
    constraints=["Do not answer the user question.", "Temporal questions must retrieve and verify."],
    input_schema=RouterInput,
    output_schema=RouterOutput,
    steps=["classify_type", "set_switches"],
)

# --- Synthesizer (§3.4) ---
SYNTHESIZER_SKILL = SkillSpec(
    name="qa.synthesizer.v1",
    agent_id=AgentId.SYNTHESIZER,
    model_tier=ModelTier.SONNET,
    frontmatter=_front("qa.synthesizer.v1", "answer_synthesis_subagent", "Draft grounded answers with claims."),
    system_prompt=(
        "你是面向程序员的问答专家。只基于给定证据 + 常识合成回答；"
        "无检索证据时必须显式声明并降低断言强度，禁止无依据断言时效性、禁止编造引用。\n"
        "每条事实性陈述尽量绑定 source_chunk_id，输出答案正文 draft + 可验证 claims 列表。"
    ),
    allowed_tools=["llm.complete_structured"],
    tool_descriptions={"llm.complete_structured": "Sonnet structured answer drafting."},
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["read_evidence", "draft_answer", "extract_verifiable_claims"],
    constraints=[
        "Do not fabricate citations.",
        "State uncertainty when evidence is missing.",
        "Do not judge final factuality; leave that to verifier.",
    ],
    input_schema=SynthesizerInput,
    output_schema=SynthesizerOutput,
    steps=["read_evidence", "draft_answer", "extract_claims"],
)

# --- Verifier (§3.5) ---
VERIFIER_SKILL = SkillSpec(
    name="qa.verifier.v1",
    agent_id=AgentId.VERIFIER,
    model_tier=ModelTier.HAIKU,
    frontmatter=_front("qa.verifier.v1", "verification_subagent", "Check claims against evidence."),
    system_prompt=(
        "你是事实审查员。逐 claim 比对检索证据：无证据支撑的 claim 降级为不确定并在答案中弱化；"
        "全部 claim 无支撑则 verdict=unverified。只判可验证 claim，主观表述放行，"
        "不放过无依据的时效性断言。输出 verified_answer + verdict + flagged。"
    ),
    allowed_tools=["llm.complete_structured", "retrieval.search"],
    tool_descriptions={
        "llm.complete_structured": "Haiku structured claim verification.",
        "retrieval.search": "Optional read-only supplemental evidence lookup.",
    },
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["check_claims", "flag_unsupported", "weaken_answer_if_needed"],
    constraints=["Do not rewrite style broadly.", "Do not ignore unsupported temporal claims."],
    input_schema=VerifierInput,
    output_schema=VerifierOutput,
    steps=["check_claims", "decide_verdict", "weaken_unsupported"],
)

# --- Planning (§3.7) ---
PLANNING_SKILL = SkillSpec(
    name="planning.v1",
    agent_id=AgentId.PLANNING,
    model_tier=ModelTier.SONNET,
    frontmatter=_front("planning.v1", "learning_planner_subagent", "Produce path diffs without committing writes."),
    system_prompt=(
        "你是学习规划师。只排程不评判对错。根据目标/现状（generate）或诊断+现有路径（modify），"
        "对候选 Atom 按 (priority = weakness * goal_relevance / mastery) 排序，按天分桶到 deadline，"
        "只产出增量 PathDiff（add/remove/reorder + rationale），不全量重写、不超 deadline 硬塞。\n"
        "用户反馈与诊断冲突时以用户为准并在 rationale 标注。"
    ),
    allowed_tools=["llm.complete_structured", "repository.read.atoms", "retrieval.search",
                   "notion.sync", "report.generate", "plan.read_candidates",
                   "mcp.notion.search_pages", "mcp.notion.read_page", "mcp.notion.create_learning_note",
                   "mcp.web.fetch_url", "mcp.github.repo_summary", "mcp.github.list_tree",
                   "mcp.github.read_file"],
    tool_descriptions={
        "repository.read.atoms": "Read mastery and atom metadata.",
        "retrieval.search": "Read candidate public/local material.",
        "llm.complete_structured": "Sonnet structured PathDiff generation.",
        "notion.sync": "Publish the produced plan as a beautiful Notion note (external; never writes LF state).",
        "report.generate": "Render the plan into a local structured Markdown report under docs/reports/.",
        "plan.read_candidates": "Read real candidate atoms by topics before ranking.",
        "mcp.notion.search_pages": "Find prior learning pages that can inform a plan.",
        "mcp.notion.read_page": "Read a selected prior learning page.",
        "mcp.notion.create_learning_note": "Create a Notion learning note through the MCP path.",
        "mcp.web.fetch_url": "Fetch external syllabus/blog/docs material as an artifact.",
        "mcp.github.repo_summary": "Inspect a target repo before planning a repo study path.",
        "mcp.github.list_tree": "List repo files to choose study targets.",
        "mcp.github.read_file": "Read README/docs/source files for repo-specific plans.",
    },
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["load_candidates", "rank_by_goal_and_weakness", "bucket_by_day", "emit_path_diff", "publish_to_notion"],
    constraints=["Never commit the path directly.", "Only emit incremental PathDiff, not full replacement."],
    input_schema=PlanningInput,
    output_schema=PlanningOutput,
    steps=["read_atoms", "rank", "bucket_by_day", "emit_diff"],
)

# --- Manager (§3.1) ---
MANAGER_SKILL = SkillSpec(
    name="manager.v1",
    agent_id=AgentId.MANAGER,
    model_tier=ModelTier.SONNET,
    frontmatter=_front("manager.v1", "top_level_orchestrator", "Route tasks and commit authorized writes."),
    system_prompt=(
        "你是系统调度器，不是领域专家。只做意图拆解、任务派发、结果聚合，"
        "禁止自行作答领域问题、禁止替 worker 编造答案、禁止广播完整上下文。\n"
        "把用户请求拆成有依赖关系的子任务 DAG，每个子任务只给出 {agent, task_type, deps}。"
        "可派的 agent：qa / planning / diagnosis / mock。"
    ),
    allowed_tools=[
        "llm.complete_structured",
        "llm.complete",
        "agent.qa",
        "agent.planning",
        "agent.diagnosis",
        "agent.mock",
        "repository.read.profile",
        "repository.read.atoms",
        "repository.write.learning_path",
        "repository.write.mastery",
        "repository.write.events",
        "repository.write.traces",
        "repository.write.diagnosis_reports",
    ],
    tool_descriptions={
        "agent.qa": "Delegate question answering.",
        "agent.planning": "Delegate path diff generation.",
        "agent.diagnosis": "Delegate read-only weakness diagnosis.",
        "agent.mock": "Delegate mock interview sessions.",
        "repository.write.learning_path": "Commit validated path diffs.",
        "repository.write.mastery": "Commit mastery updates from trusted signals.",
    },
    subskill_descriptions={
        "qa.shell.v1": "Question answering shell.",
        "planning.v1": "Path diff planner.",
        "diagnosis.v1": "Read-only diagnosis.",
        "mock.shell.v1": "Interruptible mock interview shell.",
    },
    bash=NO_BASH,
    workflow=["plan_dag", "dispatch_workers", "commit_authorized_writes", "aggregate_response"],
    constraints=[
        "Do not answer domain questions yourself.",
        "Only Manager may write learning paths and mastery.",
        "Replan at most twice.",
    ],
    steps=["plan_dag", "dispatch", "aggregate", "replan_on_failure"],
)

# --- Retrieval 改写子调用 (§3.6) ---
RETRIEVAL_REWRITE_SKILL = SkillSpec(
    name="retrieval.rewrite.v1",
    agent_id=AgentId.RETRIEVAL,
    model_tier=ModelTier.HAIKU,
    frontmatter=_front("retrieval.rewrite.v1", "retrieval_subagent", "Retrieve and optionally rewrite queries."),
    system_prompt=(
        "你是查询改写器。把长或模糊的检索 query 改写为单条更精确的 query，"
        "不扩展成多 query、不臆造术语。只输出改写后的单个 query 字符串。"
    ),
    allowed_tools=["llm.complete", "retrieval.search", "mcp.web.fetch_url",
                   "mcp.github.repo_summary", "mcp.github.list_tree", "mcp.github.read_file"],
    tool_descriptions={
        "retrieval.search": "Read-only retrieval over configured knowledge sources.",
        "llm.complete": "Optional short query rewrite for long/fuzzy queries.",
        "mcp.web.fetch_url": "Fetch a URL supplied by the user as external evidence.",
        "mcp.github.repo_summary": "Inspect GitHub repositories as external evidence.",
        "mcp.github.list_tree": "List GitHub repository trees.",
        "mcp.github.read_file": "Read selected GitHub files.",
    },
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["rewrite_if_needed", "search_sources", "fuse_results", "return_ranked_chunks"],
    constraints=["Do not synthesize answers.", "Do not write user or knowledge state."],
    steps=["rewrite_query"],
)

# --- Interviewer (§3.9) ---
MOCK_SKILL = SkillSpec(
    name="mock.shell.v1",
    agent_id=AgentId.MOCK,
    model_tier=ModelTier.HAIKU,
    frontmatter=_front("mock.shell.v1", "mock_interview_shell", "Run one interruptible mock interview session."),
    system_prompt=(
        "你是 MockInterview 子系统外壳。只驱动本场面试子图、checkpoint 与本场事件；"
        "不直接改学习计划或掌握度，结算交给 Manager。"
    ),
    allowed_tools=[
        "agent.interviewer",
        "agent.judge",
        "agent.strategist",
        "agent.coach",
        "retrieval.search",
        "mock.checkpoint",
        "repository.write.mock_sessions",
        "repository.write.events",
    ],
    tool_descriptions={
        "mock.checkpoint": "Persist/resume LangGraph session state.",
        "repository.write.mock_sessions": "Append mock session/turn records only.",
        "repository.write.events": "Append mock weakness events only.",
        "retrieval.search": "Read question material.",
    },
    subskill_descriptions={
        "agent.interviewer": "Ask questions.",
        "agent.judge": "Score answers.",
        "agent.strategist": "Select next interview action.",
        "agent.coach": "Produce final review.",
    },
    bash=NO_BASH,
    workflow=["start_or_resume", "interrupt_for_user", "score_and_strategy", "review_or_escalate"],
    constraints=["Do not directly update mastery.", "Escalate cross-capability requests to Manager."],
    input_schema=MockInput,
    output_schema=MockOutput,
    steps=["start_or_resume", "interrupt", "settle_or_escalate"],
)


INTERVIEWER_SKILL = SkillSpec(
    name="mock.interviewer.v1",
    agent_id=AgentId.INTERVIEWER,
    model_tier=ModelTier.SONNET,
    frontmatter=_front("mock.interviewer.v1", "interviewer_subagent", "Ask grounded technical interview questions."),
    system_prompt=(
        "你是资深技术面试官。只出题与追问，不透露评分、不给标准答案。"
        "按指定主题与难度出题，结合参考资料但不照抄；不重复已问过的题、不超出指定难度。\n"
        "输出题目 + 考点 expected_points + 相关 atom_refs。"
    ),
    allowed_tools=["llm.complete_structured", "retrieval.search"],
    tool_descriptions={
        "retrieval.search": "Read shared question material.",
        "llm.complete_structured": "Sonnet structured question generation.",
    },
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["read_history", "retrieve_material", "draft_question", "extract_expected_points"],
    constraints=["Do not score.", "Do not reveal expected_points to the user as an answer key."],
    input_schema=InterviewerInput,
    output_schema=InterviewerOutput,
    steps=["read_history", "draft_question", "extract_points"],
)

# --- Judge (§3.10) ---
JUDGE_SKILL = SkillSpec(
    name="mock.judge.v1",
    agent_id=AgentId.JUDGE,
    model_tier=ModelTier.HAIKU,
    frontmatter=_front("mock.judge.v1", "judge_subagent", "Score one answer against expected points."),
    system_prompt=(
        "你是评分官。只依据 expected_points 与考生回答评分，不被自信措辞带偏、"
        "不向考生补充正确答案。输出固定 rubric JSON：overall(0-5) + dims(correctness/depth/clarity) "
        "+ missed_points + confidence。无法判定时给低 confidence。"
    ),
    allowed_tools=["llm.complete_structured"],
    tool_descriptions={"llm.complete_structured": "Haiku rubric scoring."},
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["compare_answer_to_points", "score_dimensions", "list_missed_points"],
    constraints=["Do not ask questions.", "Do not coach or reveal full correct answer."],
    input_schema=JudgeInput,
    output_schema=Score,
    steps=["compare_points", "score_dims", "list_missed"],
)

# --- Strategist (§3.11) ---
STRATEGIST_SKILL = SkillSpec(
    name="mock.strategist.v1",
    agent_id=AgentId.STRATEGIST,
    model_tier=ModelTier.HAIKU,
    frontmatter=_front("mock.strategist.v1", "interview_strategy_subagent", "Choose next mock action."),
    system_prompt=(
        "你是面试策略师。只决策不作答。根据评分历史与用户中断语，决定 action："
        "continue|raise|lower|switch_topic|pause|end|escalate。\n"
        "难度规则：连续 2 轮 ≥4 升档、≤2 降档。跨能力请求（改计划/诊断）必须 escalate，"
        "不得私自处理；意图歧义时 continue 并澄清。"
    ),
    allowed_tools=["llm.complete_structured"],
    tool_descriptions={"llm.complete_structured": "Haiku structured action classification when heuristics are inconclusive."},
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["classify_interrupt", "apply_difficulty_rule", "emit_next_action"],
    constraints=["Do not answer interview content.", "Escalate planning/diagnosis requests."],
    input_schema=StrategistInput,
    output_schema=StrategistOutput,
    steps=["classify_interrupt", "apply_difficulty_rule", "decide_action"],
)

# --- Coach (§3.12) ---
COACH_SKILL = SkillSpec(
    name="mock.coach.v1",
    agent_id=AgentId.COACH,
    model_tier=ModelTier.SONNET,
    frontmatter=_front("mock.coach.v1", "coach_subagent", "Summarize one mock interview into actionable review."),
    system_prompt=(
        "你是面试教练。聚合逐轮评分产出结构化终场复盘：summary / strengths / "
        "weaknesses / next_steps。每条 weakness 必带 evidence（引哪一轮），不堆砌泛泛建议。"
        "样本不足（<2 轮）时如实说明不下结论。"
    ),
    allowed_tools=["llm.complete_structured"],
    tool_descriptions={"llm.complete_structured": "Sonnet structured review generation."},
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["aggregate_scores", "extract_evidence_backed_weaknesses", "draft_next_steps"],
    constraints=["Each weakness needs evidence.", "If fewer than two scored turns, state sample is insufficient."],
    input_schema=CoachInput,
    output_schema=CoachReport,
    steps=["aggregate_scores", "extract_weakness", "draft_next_steps"],
)

# --- Diagnosis (§3.13) ---
DIAGNOSIS_SKILL = SkillSpec(
    name="diagnosis.v1",
    agent_id=AgentId.DIAGNOSIS,
    model_tier=ModelTier.SONNET,
    frontmatter=_front("diagnosis.v1", "diagnosis_subagent", "Read signals and report weaknesses without writes."),
    system_prompt=(
        "你是学习诊断师。严格只读：只聚合事件与掌握度产出弱点结论与建议，"
        "绝不改路径/atom。基于给定弱点簇给出可执行的复习建议，每条简短、按优先级。"
        "数据不足时不硬凑，声明低置信。"
    ),
    allowed_tools=[
        "llm.complete_structured",
        "repository.read.events",
        "repository.read.atoms",
        "retrieval.search",
        "diagnosis.search_events",
        "diagnosis.get_mastery_snapshot",
        "diagnosis.search_qa_history",
        "diagnosis.search_mock_turns",
        "diagnosis.retrieve_knowledge",
        "diagnosis.analyze_code_static",
        "mcp.web.fetch_url",
        "mcp.github.repo_summary",
        "mcp.github.read_file",
        "mcp.notion.search_pages",
        "mcp.notion.read_page",
    ],
    tool_descriptions={
        "repository.read.events": "Read interaction events.",
        "repository.read.atoms": "Read atom mastery and metadata.",
        "retrieval.search": "Read supporting knowledge if needed.",
        "llm.complete_structured": "Sonnet recommendation generation.",
        "diagnosis.search_events": "Load filtered interaction events as diagnosis evidence.",
        "diagnosis.get_mastery_snapshot": "Join requested atoms with effective mastery.",
        "diagnosis.search_qa_history": "Search local QA history for corroborating evidence.",
        "diagnosis.search_mock_turns": "Search local mock interview turns for corroborating evidence.",
        "diagnosis.retrieve_knowledge": "Retrieve public/local knowledge to ground a weakness explanation.",
        "diagnosis.analyze_code_static": "Compute deterministic static code findings when code is supplied.",
        "mcp.web.fetch_url": "Fetch explicit external evidence supplied by the user.",
        "mcp.github.repo_summary": "Inspect a GitHub project when diagnosis is project-specific.",
        "mcp.github.read_file": "Read selected GitHub files for code/project diagnosis.",
        "mcp.notion.search_pages": "Search prior learning notes when the user asks to use Notion context.",
        "mcp.notion.read_page": "Read selected prior learning notes.",
    },
    subskill_descriptions={},
    bash=NO_BASH,
    workflow=["load_events", "join_mastery", "cluster_rank", "recommend"],
    triggers={
        "intents": ["diagnose", "weakness_analysis", "prepare_interview"],
        "keywords": ["诊断", "薄弱", "弱点", "错因", "weakness", "diagnose"],
        "state_events": ["mock_settled", "qa_wrong_answer"],
    },
    max_react_steps=5,
    progressive_sections={
        "react_sop": (
            "Diagnosis ReAct SOP:\n"
            "1. Call diagnosis.search_events first; do not infer from memory alone.\n"
            "2. Call diagnosis.get_mastery_snapshot only for atom_refs observed in evidence.\n"
            "3. Rank weaknesses by evidence frequency, recency, and low effective mastery.\n"
            "4. Use retrieval/code tools only when the evidence needs grounding or code-specific analysis.\n"
            "5. Return DiagnosisResult; never write mastery, events, reports, or learning paths."
        )
    },
    constraints=["Strictly read-only.", "Do not modify path, mastery, or events."],
    input_schema=DiagnosisInput,
    output_schema=DiagnosisResult,
    steps=["load_events", "join_mastery", "cluster_rank", "recommend"],
)


_ALL = [
    MANAGER_SKILL,
    QA_SKILL,
    ROUTER_SKILL,
    SYNTHESIZER_SKILL,
    VERIFIER_SKILL,
    PLANNING_SKILL,
    RETRIEVAL_REWRITE_SKILL,
    MOCK_SKILL,
    INTERVIEWER_SKILL,
    JUDGE_SKILL,
    STRATEGIST_SKILL,
    COACH_SKILL,
    DIAGNOSIS_SKILL,
]


def register_default_skills(registry: SkillRegistry) -> None:
    for spec in _ALL:
        if registry.get(spec.name) is None:
            registry.register_spec(spec)
