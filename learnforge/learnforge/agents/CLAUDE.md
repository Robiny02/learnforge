# agents/ — 工具层（tools the orchestrator calls）

LearnForge 是 **orchestrator + tools** 架构。`ManagerAgent`（实现在 `../orchestration/manager.py`，
此处 `manager.py` 仅兼容 shim）是**主 agent / 唯一写者**，把每个子能力当作一个 **tool 调用**
（agent-as-tool，参考 Reactor 的 AgentDispatchService）。

## 当前工具（4 个，research 待实现）

| 工具 | 目录 | 角色 | 运行档 |
|---|---|---|---|
| **qa** | `qa/` | 检索增强问答（Router→Synthesizer→Verifier） | Haiku/Sonnet 混合 |
| **diagnose** | `diagnosis/` | 只读弱点诊断（ReAct 三段式） | Sonnet |
| **plan** | `planning/` | 生成/修改学习路径（只产增量 PathDiff） | Sonnet |
| **mock** | `mock/` | 多轮模拟面试（InterviewDirector 智能规划：Interviewer/Judge/Coach） | Haiku/Sonnet/强档 |
| **research** | `research/` | *未实现* — 首个真 tool-calling ReAct（只读） | — |
| retrieval | `retrieval/` | **共享能力**，非 Manager 直接调度，被多个工具复用 | Haiku |

> 注意：不要再称之为 “13-agent system”。Router/Synthesizer/Judge/Coach 等是**工具内部的子步骤**，
> 不是对外工具。对外工具就是上表这几个。

## 每个工具 = 三层（务必分清）

| 层 | 是什么 | 在哪 |
|---|---|---|
| **tool 契约** | name/description + 输入/输出 schema | `../contracts/agents/<tool>.py`（Input/Output Pydantic） |
| **运行时人格** | system_prompt + 工具权限 + model_tier | `../skills/definitions/__init__.py`（SkillRegistry） |
| **工程文档** | 职责/不变量/降级/坑/改动指南 | 各目录的 `CLAUDE.md`（本类文件） |

CLAUDE.md **不是** agent 的 system prompt——运行时人格在 skill 里。改"它怎么答"改 skill；
改"它的输入输出形状"改 contract；改"怎么维护它"看 CLAUDE.md。

## 公共机制

- **BaseAgent**（`base.py`）：`llm_structured()` 经注册的 skill 拼 prompt（`memory/context_assembler.assemble_prompt`），
  带 KV-cache 友好 prefix（system→skill→constitution→memory）。`require_tool()` 运行期权限门禁。
- **ReAct**（`react/loop.py`）：diagnosis / 未来 research 用的只读 thought→action→observation 循环。
- **降级（"链路永远通"）**：无 `OPENROUTER_API_KEY` 时 `LLM.available=False`，每个工具都有确定性 stub 兜底；
  所有测试可离线跑。
- **写权限**：**只有 Manager 写** `knowledge_atoms` 掌握度与 `learning_paths`。工具自身只读或只产增量
  （PlanningAgent 产 PathDiff，由 Manager 落库）。DiagnosisAgent **严格只读**（`test_diagnosis_is_read_only` 守护）。

## 调度入口

意图层（`../intent/`）把用户输入判成单/多意图 → 单意图 dispatch 到一个工具，多意图走
`../orchestration/planner.py` 的 plan-as-tool-calls。详见 `../intent/dispatch.py`。
