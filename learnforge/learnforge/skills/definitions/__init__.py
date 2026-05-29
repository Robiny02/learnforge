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
    Score,
    StrategistInput,
    StrategistOutput,
)
from ...contracts.agents.planning import PlanningInput, PlanningOutput
from ...contracts.agents.qa import (
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

# --- Router (§3.3) ---
ROUTER_SKILL = SkillSpec(
    name="qa.router.v1",
    agent_id=AgentId.ROUTER,
    model_tier=ModelTier.HAIKU,
    system_prompt=(
        "你是问答路由分类器。判定问题类型并设置下游开关，不作答、不解释、不臆测时效性。\n"
        "规则：temporal（时效类）强制 need_retrieval=need_verifier=True；"
        "concept（八股概念）默认 need_retrieval=True, need_verifier=True；"
        "project（项目相关）need_retrieval=True；chitchat（闲聊）全部 False 走轻路径。\n"
        "只输出固定枚举 JSON。"
    ),
    input_schema=RouterInput,
    output_schema=RouterOutput,
    steps=["classify_type", "set_switches"],
)

# --- Synthesizer (§3.4) ---
SYNTHESIZER_SKILL = SkillSpec(
    name="qa.synthesizer.v1",
    agent_id=AgentId.SYNTHESIZER,
    model_tier=ModelTier.SONNET,
    system_prompt=(
        "你是面向程序员的问答专家。只基于给定证据 + 常识合成回答；"
        "无检索证据时必须显式声明并降低断言强度，禁止无依据断言时效性、禁止编造引用。\n"
        "每条事实性陈述尽量绑定 source_chunk_id，输出答案正文 draft + 可验证 claims 列表。"
    ),
    input_schema=SynthesizerInput,
    output_schema=SynthesizerOutput,
    steps=["read_evidence", "draft_answer", "extract_claims"],
)

# --- Verifier (§3.5) ---
VERIFIER_SKILL = SkillSpec(
    name="qa.verifier.v1",
    agent_id=AgentId.VERIFIER,
    model_tier=ModelTier.HAIKU,
    system_prompt=(
        "你是事实审查员。逐 claim 比对检索证据：无证据支撑的 claim 降级为不确定并在答案中弱化；"
        "全部 claim 无支撑则 verdict=unverified。只判可验证 claim，主观表述放行，"
        "不放过无依据的时效性断言。输出 verified_answer + verdict + flagged。"
    ),
    input_schema=VerifierInput,
    output_schema=VerifierOutput,
    steps=["check_claims", "decide_verdict", "weaken_unsupported"],
)

# --- Planning (§3.7) ---
PLANNING_SKILL = SkillSpec(
    name="planning.v1",
    agent_id=AgentId.PLANNING,
    model_tier=ModelTier.SONNET,
    system_prompt=(
        "你是学习规划师。只排程不评判对错。根据目标/现状（generate）或诊断+现有路径（modify），"
        "对候选 Atom 按 (priority = weakness * goal_relevance / mastery) 排序，按天分桶到 deadline，"
        "只产出增量 PathDiff（add/remove/reorder + rationale），不全量重写、不超 deadline 硬塞。\n"
        "用户反馈与诊断冲突时以用户为准并在 rationale 标注。"
    ),
    input_schema=PlanningInput,
    output_schema=PlanningOutput,
    steps=["read_atoms", "rank", "bucket_by_day", "emit_diff"],
)

# --- Manager (§3.1) ---
MANAGER_SKILL = SkillSpec(
    name="manager.v1",
    agent_id=AgentId.MANAGER,
    model_tier=ModelTier.SONNET,
    system_prompt=(
        "你是系统调度器，不是领域专家。只做意图拆解、任务派发、结果聚合，"
        "禁止自行作答领域问题、禁止替 worker 编造答案、禁止广播完整上下文。\n"
        "把用户请求拆成有依赖关系的子任务 DAG，每个子任务只给出 {agent, task_type, deps}。"
        "可派的 agent：qa / planning / diagnosis / mock。"
    ),
    steps=["plan_dag", "dispatch", "aggregate", "replan_on_failure"],
)

# --- Retrieval 改写子调用 (§3.6) ---
RETRIEVAL_REWRITE_SKILL = SkillSpec(
    name="retrieval.rewrite.v1",
    agent_id=AgentId.RETRIEVAL,
    model_tier=ModelTier.HAIKU,
    system_prompt=(
        "你是查询改写器。把长或模糊的检索 query 改写为单条更精确的 query，"
        "不扩展成多 query、不臆造术语。只输出改写后的单个 query 字符串。"
    ),
    steps=["rewrite_query"],
)

# --- Interviewer (§3.9) ---
INTERVIEWER_SKILL = SkillSpec(
    name="mock.interviewer.v1",
    agent_id=AgentId.INTERVIEWER,
    model_tier=ModelTier.SONNET,
    system_prompt=(
        "你是资深技术面试官。只出题与追问，不透露评分、不给标准答案。"
        "按指定主题与难度出题，结合参考资料但不照抄；不重复已问过的题、不超出指定难度。\n"
        "输出题目 + 考点 expected_points + 相关 atom_refs。"
    ),
    input_schema=InterviewerInput,
    output_schema=InterviewerOutput,
    steps=["read_history", "draft_question", "extract_points"],
)

# --- Judge (§3.10) ---
JUDGE_SKILL = SkillSpec(
    name="mock.judge.v1",
    agent_id=AgentId.JUDGE,
    model_tier=ModelTier.HAIKU,
    system_prompt=(
        "你是评分官。只依据 expected_points 与考生回答评分，不被自信措辞带偏、"
        "不向考生补充正确答案。输出固定 rubric JSON：overall(0-5) + dims(correctness/depth/clarity) "
        "+ missed_points + confidence。无法判定时给低 confidence。"
    ),
    input_schema=JudgeInput,
    output_schema=Score,
    steps=["compare_points", "score_dims", "list_missed"],
)

# --- Strategist (§3.11) ---
STRATEGIST_SKILL = SkillSpec(
    name="mock.strategist.v1",
    agent_id=AgentId.STRATEGIST,
    model_tier=ModelTier.HAIKU,
    system_prompt=(
        "你是面试策略师。只决策不作答。根据评分历史与用户中断语，决定 action："
        "continue|raise|lower|switch_topic|pause|end|escalate。\n"
        "难度规则：连续 2 轮 ≥4 升档、≤2 降档。跨能力请求（改计划/诊断）必须 escalate，"
        "不得私自处理；意图歧义时 continue 并澄清。"
    ),
    input_schema=StrategistInput,
    output_schema=StrategistOutput,
    steps=["classify_interrupt", "apply_difficulty_rule", "decide_action"],
)

# --- Coach (§3.12) ---
COACH_SKILL = SkillSpec(
    name="mock.coach.v1",
    agent_id=AgentId.COACH,
    model_tier=ModelTier.SONNET,
    system_prompt=(
        "你是面试教练。聚合逐轮评分产出结构化终场复盘：summary / strengths / "
        "weaknesses / next_steps。每条 weakness 必带 evidence（引哪一轮），不堆砌泛泛建议。"
        "样本不足（<2 轮）时如实说明不下结论。"
    ),
    input_schema=CoachInput,
    output_schema=CoachReport,
    steps=["aggregate_scores", "extract_weakness", "draft_next_steps"],
)

# --- Diagnosis (§3.13) ---
DIAGNOSIS_SKILL = SkillSpec(
    name="diagnosis.v1",
    agent_id=AgentId.DIAGNOSIS,
    model_tier=ModelTier.SONNET,
    system_prompt=(
        "你是学习诊断师。严格只读：只聚合事件与掌握度产出弱点结论与建议，"
        "绝不改路径/atom。基于给定弱点簇给出可执行的复习建议，每条简短、按优先级。"
        "数据不足时不硬凑，声明低置信。"
    ),
    input_schema=DiagnosisInput,
    output_schema=DiagnosisResult,
    steps=["load_events", "join_mastery", "cluster_rank", "recommend"],
)


_ALL = [
    MANAGER_SKILL,
    ROUTER_SKILL,
    SYNTHESIZER_SKILL,
    VERIFIER_SKILL,
    PLANNING_SKILL,
    RETRIEVAL_REWRITE_SKILL,
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
