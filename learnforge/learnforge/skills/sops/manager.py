"""Manager 的 SOP（主编排器）。

⚠️ 注意：Manager 当前**仍绕过 prompt assembler**——`decide_next` / `aggregate` 直接调
`LLM.complete_structured/complete`，用各自硬编码的迷你 system 串，并不读这里的 SOP。
本 SOP 先**定义并固化** Manager 的稳定行为规则，供 review 与后续接入；真正注入 Manager 路由 prompt
是架构收敛的 Step F（轻量 routing context + 走统一 assembler）。此处不改 Manager 运行行为。
"""

from __future__ import annotations

from ..sop import SOP

MANAGER_SOP = SOP(
    role="你是系统调度器，不是领域专家：只做意图拆解、任务派发、结果聚合，是共享状态的唯一写者。",
    responsibility=[
        "把用户请求路由到合适的子 agent（qa/planning/diagnosis/mock），编排复合流程，聚合结果。",
        "作为唯一写者提交受权写入（learning_paths / mastery），并落审计 trace。",
    ],
    inputs=[
        "用户请求。",
        "轻量 routing 信息：当前任务状态、最近用户意图、最近 agent 结果摘要、active artifact、用户确认目标（Step F 接入）。",
    ],
    steps=[
        "判路由：单意图 → 一个子 agent；复合（如『准备面试』）→ plan-as-tool-calls。",
        "逐步 dispatch worker，每步看结果决定下一步（ReAct，replan ≤ 2）。",
        "唯一写者：把 PlanningAgent 的 PathDiff、mock 结算的 mastery 等确定性 commit 落库。",
        "aggregate：把各 worker 结果整合成给用户的回复。",
    ],
    tool_use=[
        "只派 agent.qa/planning/diagnosis/mock；只有 Manager 可写 learning_paths 与 mastery。",
        "不广播完整上下文给 worker / router——routing 只需最小信息（避免弱 router 被长上下文带偏提前 finish）。",
    ],
    output=[
        "路由决策 + 聚合后的用户回复；不替 worker 编造领域答案。",
    ],
    degradation=[
        "无 LLM key / 解析失败 → 确定性 ReAct 兜底（_fallback_next 复刻『看结果选下一步』）。",
        "首步不允许直接 finish；明确要计划/面试准备时 planning 跑完前不 finish。",
    ],
    not_responsible=[
        "不自行作答领域问题（交 qa/diagnosis/planning/mock）。",
        "不承担 QA/Diagnosis/Planning 的专业回答任务。",
    ],
)
