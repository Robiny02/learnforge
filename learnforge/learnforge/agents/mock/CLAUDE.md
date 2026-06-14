# mock/ — Mock 工具（多轮模拟面试）

证据式拷打的多轮面试子系统。**InterviewDirector 智能规划**（替代旧 LangGraph 状态机）+
**MockStateStore** 跨调用持久化（替代 checkpointer）。Manager 调它开场/结算。

> 重构（2026-06）：旧 S1–S7 LangGraph 子图 + SqliteSaver interrupt/resume 已删除。原因：状态机太僵硬。
> 现在与 `orchestration/manager.py` 的手搓 ReAct 循环同构——"智能不在图里，在 Python 里"。
> 面试官人格全量接入开源 **tech-interview-skill**（直接不留情/追问到底/简历诚信/双角色提示/三环节/评分 rubric）。

**三层**：契约 `../../contracts/agents/mock.py` ｜ 运行时 skill（见 `skills/definitions`）｜ 本文件。

## 职责边界

- **做**：出题、claim 锚定追问、简历诚信点破、高质量解答/纠错、打分、复盘；可按岗位/JD/简历针对性拷打。
- **不做**：不写掌握度（结算时 **Manager** 提交，唯一写者）；不诊断（结算后由 Manager 触发 post_mock diagnose）。

## 子步骤（工具内部，非对外工具）

| 子步骤 | 档 | 角色 |
|---|---|---|
| **InterviewDirector** (`director.py`) | Haiku | **智能选下一步动作**（ask/followup/probe/correct/raise/lower/summarize…）+ 中断分类；LLM 路由 + 确定性兜底。复用 `AgentId.STRATEGIST` 槽 |
| Interviewer (`interviewer.py`) | 强档* | 出题/追问 + `explain()` 高质量解答/提示/纠错（双角色 `---`/💡） |
| Judge (`judge.py`) | Haiku | 打分 + 回填 `Score.risk_flags`（确定性兜底总会补） |
| Coach (`coach.py`) | Sonnet | 复盘，产 `CoachReport.answer_cards`（dangerous/passable/strong + evidence_needed），采用 skill 评分 rubric |

\* 强档 = `config.mock_answer_model()`（`LF_MOCK_ANSWER_MODEL` 可升 Opus）；为空 → skill 的 SONNET 档。
**模型分层**：路由/分类/评分 Haiku，出题/解答/纠错 强档，复盘 Sonnet。

## tool 契约 & 引擎（无状态机）

- 入口：`MockInterviewAgent.run(MockInput) -> MockOutput`。`MockInput.context: InterviewContext`（全可选）。
  对外 `status ∈ {active, paused, review, escalate, expired}` 契约不变（server/cli 经 `actions.py` channel 路由调用）。
- **每个调用 = 跑一轮**（`mock_agent.py`）：`store.load(sid)` →（按 control_action / user_interrupt /
  user_answer / pending 分流）→ Judge 评分 → `Director.decide_next` 选 move → 执行（出题/解答/收尾/escalate）
  → `store.save`。状态存 `MockStateStore`（`state.py`，SQLite 自愈表 `mock_session_state` + dict 兜底）。
- **退出**：用户中断（`结束/总结/退出…`）→ Director.classify_interrupt=SUMMARIZE → 复盘。
- **空转主动询问**：连续 `MOCK_IDLE_ROUNDS_THRESHOLD`（默认 3）轮"没出新题"（hint/reveal/repeat/feedback/redo）
  → `pending=continue_decision`，followup 问"继续拷打还是总结收尾"；下一轮在引擎内消化（server 无需感知）。
- **环节推进**：每环节问够 `MOCK_PHASE_QUESTIONS`（默认 3）题 → basics→project→system_design。
- 面试**进行中**的回合意图分类仍在 `../../intent/mock_turn.py`（answer/side/exit/pause/escalate/skip/hint/…），
  即时控制名单的单一来源在 `actions.py:CONTROL_ACTION_NAMES`。

## grilling 规则 & 题库（`interview_skill.py`）

LLMInternSkill 蒸馏的纯规则（`detect_role_type`/`pick_grill_round`/`risk_flags_for_answer`/`build_answer_card`）
+ tech-interview-skill 蒸馏的 `QUESTION_PATTERNS` 高频题库与 `load_question_patterns`（离线兜底题源 + few-shot）。
attribution 见 `skill_pack/NOTICE.md`、`skill_pack/question-patterns.md`。同时驱动 LLM prompt 与无 key 兜底。

## 不变量 & 降级

- "链条总过"：Director/Interviewer/Judge/Coach 无 key 都有确定性兜底（`_fallback_move` / 题库模板题 /
  Judge 启发式 / `explain` 退回 `control.py` 文案）；Judge 的 risk_flags 即使 LLM 打分也确定性回填。
- Director 仅在评分**有把握**（`confidence ≥ 0.5`）判错时才 CORRECT，避免离线低置信打分每轮"纠错"。
- 写边界：mock 自身不提交 mastery；`status=review` 时 `Manager.settle_mock()` 读 `mastery_updates(sid)` 提交并触发 post_mock diagnose。
- 持久化缺失（不同库/会话不存在）→ `answer` 返回 `status=expired`，不抛异常。

## 改这个工具

- 改下一步**决策逻辑** → `director.py`（`decide_next` 的 prompt / `_fallback_move` 规则）。
- 改**出题/解答/追问风格** → `interviewer.py` + `INTERVIEWER_SKILL`（人格）。改复盘 → `coach.py` + `COACH_SKILL`。
- 改**状态字段/持久化** → `contracts/agents/mock.py:MockState` + `state.py`。改回合分类 → `intent/mock_turn.py`。
- 调阈值 → `config.py`（`MOCK_PHASE_QUESTIONS` / `MOCK_IDLE_ROUNDS_THRESHOLD` / `LF_MOCK_ANSWER_MODEL`）。
- 测试：`tests/test_mock_director.py`（引擎/边界/多轮状态）、`tests/test_mock*.py`、`test_mock_control.py`、
  `test_mock_action_registry.py`、`test_mock_persistence.py`、`test_mock_visuals.py`。
