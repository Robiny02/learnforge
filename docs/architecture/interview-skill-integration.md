# LLMInternSkill → LearnForge mock interview 接入说明

接入对象：开源 Codex/Claude Skill **LLMInternSkill**
(<https://github.com/couragec/llm-intern-skill>, MIT)。归属见
`learnforge/agents/mock/skill_pack/NOTICE.md`。

## 1. 这个 Skill 是什么

它本质是一个 **证据约束式求职评审知识包**（markdown 提示词 + 角色参考 + 模板 + 示例），
面向大模型实习求职，核心能力：

- `jd-analysis` —— 从 JD 识别角色类型（rag/agent/agentic-rl/posttraining/search-ranking/…），给 fit verdict。
- `materials-audit` / `evidence-contract` —— 抽取简历/项目证据，按 C0-C3 给 claim 定级。
- `truth-boundary` —— 防夸大：主导→参与、上线→demo、提升30%→样例对比观察。
- `interview-grilling` —— 五轮拷打：真实边界 → 技术深度 → JD 深挖 → 情景题 → 风险总结，**跟着候选人的 claim 追问**。
- `answer-cards` —— 把高风险问题转成 危险/及格/强 三档回答 + 该补的证据。
- `resume-polish` / `resume-tailoring` / `project-scout` / LaTeX 导出 —— 简历润色与开源项目补强。

## 2. 接入判断

**最高价值、且与现有架构最契合的点是 `interview-grilling` + `evidence-contract` +
`truth-boundary` + `answer-cards` + 角色识别**，正好补齐 LearnForge mock interview 当前的最大缺口：

> 现状：mock 只有 `topic + difficulty`，出的是泛泛的「请介绍 X」，
> 没有简历/项目/JD/岗位概念，不追问，不识别夸大，不给更优回答。

因此我们 **不把它当 RAG 文档粗暴塞入知识库**，而是把它的 **评审逻辑蒸馏成确定性 Python 规则**
(`agents/mock/interview_skill.py`)，让它同时驱动：

1. 各子 agent 的 **LLM 提示词**（skill system_prompt 注入拷打方法论）；
2. 无 API key 时的 **确定性兜底**（延续 "链路永远通" 不变量）。

简历润色 / JD 定制 / LaTeX / project-scout 这些与 mock 无关、且偏"生成简历产物"的能力**暂不接入**
（避免为接入而大规模重构）。如果将来要做，建议落成一个独立的只读能力（类似 ResearchAgent），
而非塞进 mock 子系统。

## 3. 改了什么

| 层 | 文件 | 改动 |
|---|---|---|
| 规则（新） | `agents/mock/interview_skill.py` | 蒸馏自 skill 的纯函数：角色识别 / 风险标签 / 降级 / 拷打轮次 / answer card / 证据式追问 |
| 归属（新） | `agents/mock/skill_pack/NOTICE.md` | MIT 归属与范围说明 |
| 契约 | `contracts/agents/mock.py` | 新增 `InterviewContext` / `AnswerCard`；`Score.risk_flags`；`InterviewerInput`/`JudgeInput`/`CoachInput`/`MockInput`/`CoachReport` 扩展（全部可选，向后兼容） |
| Agent | `agents/mock/interviewer.py` | 证据式追问（基于上一轮回答 + claim + 角色）；离线兜底走 `grounded_followup` |
| Agent | `agents/mock/judge.py` | 评分附 `risk_flags`（确定性兜底恒补全） |
| Agent | `agents/mock/coach.py` | 高风险/低分轮产出 `answer_cards` |
| 子图 | `orchestration/graph/mock_graph.py` | 串 `context` + 上一轮 Q/A + `turns` 累积；Judge 传 `role_type`；Coach 传 `context`/`turns` |
| 外壳 | `agents/mock/mock_agent.py` | `start()` 接受并持久化 `context` |
| 编排 | `orchestration/manager.py` | mock 派发可从 `context["interview_context"]` 注入 |
| 提示词 | `skills/definitions/__init__.py` | Interviewer/Judge/Coach system_prompt 注入拷打方法论 |
| CLI | `cli.py` | 逐轮展示风险标签；复盘渲染 answer cards |
| 测试 | `tests/test_mock_grilling.py` | 纯规则层 + 接入层共 10 用例 |

## 4. 怎么用

```python
from learnforge.agents.mock import MockInterviewAgent
from learnforge.contracts.agents.mock import MockInput, InterviewContext

m = MockInterviewAgent()
ctx = InterviewContext(
    target_role="RAG 算法实习",
    jd_text="负责 RAG 召回、rerank、citation 评估",
    resume_claims=["做过企业文档问答 RAG demo"],
)
out = m.run(MockInput(topic="RAG", session_id="s1", context=ctx))   # 首题已围绕 claim/角色
out = m.answer("s1", user_answer="我主导上线了企业知识库，显著提升准确率")
# out.turn_scores[-1].risk_flags 标出夸大/无证据；下一题会逼问证据
out = m.answer("s1", user_interrupt="结束")
# out.review.answer_cards 给出 危险/及格/强 三档回答
```

经 Manager：`dispatch("mock", topic, context={"interview_context": {...}})`。
不传 `context` 时行为与接入前完全一致。

## 5. 验证

```bash
cd learnforge
.venv/bin/python -m pytest tests/test_mock_grilling.py tests/test_mock_subgraph.py -q   # 16 passed
.venv/bin/ruff check learnforge/agents/mock learnforge/contracts/agents/mock.py
```

设计取舍：所有新字段可选且有默认值 → 现有 237 个测试不受影响；规则层是纯函数，
不依赖 LLM/DB，单测稳定；风险标签与 answer card 即使在有 API key 时也有确定性兜底补全。
