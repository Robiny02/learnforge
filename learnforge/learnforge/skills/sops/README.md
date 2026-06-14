# Agent SOP / Active Skill

每个 Agent 的**标准操作流程（SOP）** = Active Skill 层的稳定内容，描述"这个 Agent 应该怎么做事"。
本目录是 SOP 的**单一维护入口**，配合上下文系统的架构收敛（固定 Prompt Stack）。

## SOP 是什么、不是什么

| 层 | 描述 | 在哪 |
|---|---|---|
| **SOP（本目录）** | 这个 Agent **怎么做事**：职责/输入/步骤/工具使用策略/输出要求/降级/不负责 | `skills/sops/<area>.py` |
| Tool Registry | 它**能用什么工具** | `SkillSpec.allowed_tools` / `tool_descriptions` |
| Conversation State | **最近发生了什么**（compact summary + recent events） | `memory/session_context.py` |
| Evidence | **这轮回答基于什么材料**（检索/附件召回/repo/工具结果） | `retrieved`/Evidence 层 |

> SOP **只放稳定行为规则**。会话历史、附件内容、检索结果、最近工具调用等动态信息**绝不**写进 SOP。
> SOP 的 `tool_use` 只写"何时用工具"的策略，不重复罗列工具名（那是 Tool Registry）。

## 结构

`SOP`（见 `skills/sop.py`）是一个 dataclass，字段可独立增删改：

| 字段 | 含义 | 渲染 |
|---|---|---|
| `role` | 一句话身份/定位 | 开头一行 |
| `responsibility` | 负责什么 | 【职责】bullet |
| `inputs` | 输入来源有哪些 | 【输入来源】bullet |
| `steps` | 执行步骤（有序） | 【执行步骤】自动编号 |
| `tool_use` | 何时用工具（策略） | 【工具使用】bullet |
| `output` | 输出结构/质量要求 | 【输出要求】bullet |
| `degradation` | 信息不足/无 key 时降级 | 【降级策略】bullet |
| `not_responsible` | 不负责什么 | 【不负责】bullet |
| `examples` | few-shot（可选） | 【示例】块 |

`render_sop()` 是确定性纯函数（不读会话、不调 LLM、无副作用），空字段自动跳过。

## 文件 = 能力域（便于独立 review）

| 文件 | SOP |
|---|---|
| `qa.py` | `SYNTHESIZER_SOP`（答题）、`QA_SHELL_SOP`（编排） |
| `planning.py` | `PLANNING_SOP` |
| `diagnosis.py` | `DIAGNOSIS_SOP`（弱点）、`RESUME_DIAGNOSIS_SOP`（简历） |
| `mock.py` | `MOCK_SHELL_SOP`、`INTERVIEWER_SOP`、`COACH_SOP` |
| `manager.py` | `MANAGER_SOP` |

`__init__.py` 的 `SOPS`（按 skill name 索引）登记上述 SOP；每个 SOP 同时挂在对应 `SkillSpec.sop`。

**未迁移**（暂回退 `system_prompt`，后续按需迁移）：Router、Verifier、Retrieval、Judge、Strategist。

## 怎么修改一个 Agent 的 SOP

1. 打开对应文件（如改 QA 答题风格 → `qa.py` 的 `SYNTHESIZER_SOP`），改相应字段即可。
2. **不需要**改 prompt assembler 主链路，也不影响其它 Agent。
3. 跑测试：`pytest tests/test_sop.py` + 该 Agent 自己的测试（如 `tests/test_qa_*.py` / `test_planning*.py` / `test_diagnosis.py` / `test_mock_*.py`）。
4. 全量回归：`pytest`。

## 渲染路径（SOP 如何进入 prompt）

`SkillSpec.sop` →（优先于旧 `progressive_sections["sop"]`）`Skill._render_section("sop")` →
`Skill.load_instructions()` → `BaseAgent._skill_constitution()` → assembler 的 constitution 段 → 子 agent prompt。

> 注：Step B（assembler 收敛为固定 8-layer stack）会把 SOP 提升为独立的 **Active Skill** 层、把
> Tool Registry 拆成独立层。本阶段（Step A）SOP 先经现有 `load_instructions` 路径注入，assembler 主链路未改。

## ⚠️ Manager 例外

`MANAGER_SOP` 已定义，但 **Manager 当前仍绕过 assembler**（`decide_next`/`aggregate` 直接调 LLM，用各自的
迷你 system 串），所以 Manager SOP 暂**未注入** Manager 运行 prompt。真正接入是架构收敛的 **Step F**
（给 Manager 一个轻量 routing context 并走统一 assembler）。
