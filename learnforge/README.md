# LearnForge (MVP Skeleton)

面向程序员的学习伙伴 —— 严格 hierarchical multi-agent 系统的**架构骨架**。

> 当前阶段:**Phase 1-3 已落地**。架构完整、五能力链路全通,且在无 ANTHROPIC_API_KEY 时
> 优雅降级到确定性兜底("链路永远通")。上游依据见 `docs/LearnForge-{Proposal,Design,Tasks}.md`。

## 架构总览

- **ManagerAgent**（唯一调度者，plan-and-execute + replan≤2，唯一写者）统辖四个域 worker：
  - **QAAgent**（外壳）→ RouterAgent / SynthesizerAgent / VerifierAgent
  - **PlanningAgent**（generate + modify 合并）
  - **MockInterviewAgent**（LangGraph 子图）→ InterviewerAgent / JudgeAgent / StrategistAgent / CoachAgent
  - **DiagnosisAgent**（只读）
- **RetrievalAgent**：横切共享子能力（sqlite-vec + FTS5，RRF 融合），不被 Manager 直接调度。

共 **13 个 agent**。

## 目录结构

```
learnforge/
├── contracts/        # ★ 所有数据契约（先于实现）：消息信封 / Atom / 状态 / 各 agent in&out
├── agents/           # 13 个 agent 实现（Phase 1 为 stub）
│   ├── qa/           #   QAAgent + Router/Synthesizer/Verifier
│   └── mock/         #   MockInterviewAgent + Interviewer/Judge/Strategist/Coach
├── graph/            # LangGraph 主图骨架（compile + START→END）+ mock 子图占位
├── storage/          # SQLite DDL（atoms/chunks/agent_traces + FTS5 + sqlite-vec）+ 仓储 stub
├── llm/              # Haiku/Sonnet client stub + 成本统计
├── skills/           # ★ 预留：agent 专属能力（system_prompt + 模型 + 工具 + workflow），由 SkillRegistry 驱动
├── mcp/              # ★ 预留：外部工具 / MCP 接入（MVP Non-Goal，仅留接口前向兼容）
├── memory/           # ★ 预留：长期记忆 + 上下文分层 + prompt 拼装（design §6c）
├── config.py         # 模型分级 + 各 agent Bounds 常量
└── app.py            # FastAPI 入口（/qa /plan /mock 占位）
```

## 本地验证（骨架可 compile + START→END）

```bash
python -m venv .venv && source .venv/bin/activate
pip install pydantic langgraph
python -m learnforge.graph.main_graph    # 编译主图并跑一次 START→END（mock 节点）
```

## Phase 路线

1. **Phase 1（已完成）**：契约 + 骨架 + DDL + 可编译主图。
2. **Phase 2（已完成）**：自底向上实装 Retrieval → Manager → QA 子系统 → Planning。
3. **Phase 3（已完成）**：
   - **掌握度算法**（`mastery.py`）：指数更新(mock0.5/qa0.3/self0.15) + 时间遗忘 + 5 档映射。
   - **DiagnosisAgent（ReAct 三段式）**：`events → mastery(effective) → cluster/rank → 建议`，
     弱点 score=`(1-mastery)*recency*error_freq`，**严格只读**（有读前后 DB 不变断言）。
   - **Mock 子图（interrupt/resume）**：`graph/mock_graph.py` 的 S1-S7 状态机 + checkpoint;
     S3 `interrupt()` 等用户作答、`Command(resume=...)` 续跑;换 topic 不重置累积分;
     pause 可跨调用 resume;end/超 max_turns → 复盘结算;改计划/诊断 → escalate 回 Manager。
   - **复合 workflow 5.6**：诊断→改路径→建议 mock(控制权交回,不自动启 mock);诊断为空跳过
     modify 改建议先 mock;Manager 唯一写者 `commit_mastery`;mock 结算后 post_mock 自动诊断;
     `make_handoff_summary`(≤512tok) 供跨子系统 handoff。

### 验证

```bash
python -m learnforge.graph.main_graph   # 主图 plan→execute→aggregate（含复合 5.6）
# mock 多轮 interrupt/resume：见 agents/mock/mock_agent.py 的 start()/answer()
```
