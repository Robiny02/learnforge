# mock/ — Mock 工具（多轮模拟面试）

证据式拷打的多轮面试子系统。LangGraph 子图 + `interrupt()/resume`。Manager 调它开场/结算。

**三层**：契约 `../../contracts/agents/mock.py` ｜ 运行时 skill（4 子步骤各一档）｜ 本文件。

## 职责边界

- **做**：出题、claim 锚定追问、打分、复盘；可按岗位/JD/简历针对性拷打。
- **不做**：不写掌握度（结算时 **Manager** 提交，唯一写者）；不诊断（结算后由 Manager 触发 post_mock diagnose）。

## 子步骤（工具内部，非对外工具）

| 子步骤 | 档 | 角色 |
|---|---|---|
| Interviewer | Sonnet | claim 锚定出题/追问（用 last_question/last_answer/context） |
| Judge | Haiku | 打分 + 回填 `Score.risk_flags`（确定性兜底总会补） |
| Strategist | Haiku | 决定继续/换题/调难度/升级 |
| Coach | Sonnet | 复盘，产 `CoachReport.answer_cards`（dangerous/passable/strong + evidence_needed） |

## tool 契约 & 状态机

- 入口：`MockInterviewAgent.run(MockInput) -> MockOutput`。`MockInput.context: InterviewContext`（可选，
  target_role/jd_text/role_type/resume_claims/projects；**全可选**，省略则退回纯主题面试）。
- 子图（`../../graph/mock_graph.py`）：S1_INIT→S2_INTERVIEWER→S3_AWAIT(interrupt)→S4_JUDGE→S5_STRATEGIST→(loop|S6_COACH→S7_SETTLE)。
- 面试**进行中**的回合意图分类在 `../../intent/mock_turn.py`（answer/side/exit/pause/escalate/skip/hint/repeat/reveal/redo/feedback），
  不消耗轮次的即时控制经 `control.py`/`S_CONTROL`。

## grilling 规则（`interview_skill.py`）

LLMInternSkill 蒸馏成的纯规则模块（离线确定性，attribution 见 `skill_pack/NOTICE.md`）：
`detect_role_type` / `pick_grill_round`(truth_boundary→technical_depth→jd_deep_dive→scenario) /
`risk_flags_for_answer` / `build_answer_card`。同时驱动 LLM prompt 与无 key 兜底。

## 不变量 & 降级

- "链条总过"：每个子步骤无 key 都有确定性兜底；Judge 的 risk_flags 即使 LLM 打分也确定性回填。
- 写边界：mock 自身不提交 mastery；`status=review` 时 `Manager.settle_mock()` 提交并触发 post_mock diagnose。
- **范围**：只接了 grilling 规则；简历润色/JD 定制/LaTeX/project-scout **故意没接**（见 `../../docs/interview-skill-integration.md`）。

## 改这个工具

- 改出题/追问/复盘风格 → 对应子步骤的 skill；改状态机 → `graph/mock_graph.py`；改回合分类 → `intent/mock_turn.py`。
- 测试：`tests/test_mock*.py`、`tests/test_mock_control.py`、`tests/test_mock_action_registry.py`、`tests/test_mock_visuals.py`。
