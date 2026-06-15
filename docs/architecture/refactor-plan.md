# LearnForge 轻量 DDD + 可插拔 Handler 重构方案

> Phase 0 产出。本文档**只描述边界与计划,不改任何业务行为**。
> 目标:让代码边界更清楚、后续新增能力不必频繁改 Manager 主循环。**不是**企业级重分层。
> 约束总纲:不大爆炸、不先搬文件、不改业务行为、不破坏测试、子 Agent 不互调、
> Diagnosis 不提权、Planning 不直写 path、Evidence 本体不进 Conversation State、不引入 shell/exec。

---

## 现状速写(生产链路)

```
app/server.py  →  intent/dispatch.py (Route)  →  graph/main_graph (shim → orchestration/graph)
               →  orchestration/manager.py::ManagerAgent (decide → dispatch → apply, 1316 行)
权限层: tools/registry.py (能力目录) + skills/ + agents/base.py::require_tool  ← 现有最大优点,保留
```

子 Agent:`agents/{qa,diagnosis,planning,mock,evidence}`。其中 **EvidenceResearchAgent
已完整实现(契约+agent+skill+只读工具+`Manager.gather_evidence`),但除测试外无任何真实调用** ——
`_dispatch_impl` 的 diagnosis 分支从不采证据,`execute_dynamic`/`plan_execute` 从不构造 `EvidenceRequest`。
即:统一只读证据 worker 是半成品(Phase 5 补接)。

---

## 1. 最混乱的边界

- **B1 三 registry + 两 descriptor 命名地狱**
  - `tools/registry.py::MCP_REGISTRY`(`MCPRegistry`)= 内部**能力权限目录**(ToolSpec:effect/owner/audit),却叫 MCP。
  - `tools/collection.py::TOOLS`(`ToolCollection`)= 可执行 **handler 仓库**。
  - `tools/runtime.py::DEFAULT_TOOL_REGISTRY` = TOOLS 的薄 facade。
  - `tools/mcp/`(registry/executor/runtime)= 真·外部 MCP server 运行时,自带一套注册。
  - `mcp/`(顶层)= re-export shim。
  - 两种 descriptor:`spec.py::ToolSpec`(权限/元数据) vs `base.py::BaseTool`(schema/handler),互不校验。
- **B2 Manager 上帝对象**:编排 + 唯一写者 + **硬编码 if/elif 派发(`_dispatch_impl`)** + 会话记忆压缩 +
  daily 记忆 + 聚合渲染 + mock 结算 + handoff + 埋点全在一个类。新增能力必须改 `_dispatch_impl`。
- **B3 路由/编排散在 4 处**:`intent/dispatch.py`、`manager.py`(`_wants_plan`/`decide_next`/`plan_execute`)、
  `orchestration/planner.py`、**`app/server.py::_ui_chat_dispatch`**(简历/附件/mock 逐轮/escalate 全手写,绕过 Manager)。
- **B4 `ResponsePayload.result: Dict` 不透明 + 旁路 dict 传状态**:evidence/artifact/控制信号靠
  无类型 `context`(`["diagnosis"]`/`["evidence_artifact"]`)和 `meta`(`["notion_url"]`/`["path_committed"]`/
  `["suggest_mock"]`…)。`MockOutput.status` 是裸字符串需手工映射;confidence 各 agent 硬编码。
- **B5 并行轨道未隔离**:`orchestrator/`(独立重建轨,python3.11)、`agents/research/`(未实现)、
  `agents/react/`、`orchestration/graph/` vs 顶层 `graph/`(shim)。

---

## 2. 应收敛成统一 Handler 的模块

| 现状(散落) | 收敛为 | 价值 |
|---|---|---|
| `_dispatch_impl` 四段 `if agent==...` | **CapabilityHandler 注册表** | 新增能力=注册一个 handler,不动主循环 |
| `evidence_agent._collect_*` + `tools/evidence_sources.py` + `diagnosis/resume.load_resume_text` + `_qa_with_attachments` 读附件 + `BaseAgent.recall` | **EvidenceProviderHandler(按 source_type)**,由 EvidenceResearchAgent 编排 | 4~5 条读取路径 → 1 条 |
| `ToolCollection.handlers` + `ToolSpec` + `tools/mcp` + `diagnosis.*` 私有读工具 | **统一 ToolHandler + 单一注册表** | file/mcp/local 同一注册与权限闸 |
| `manager.aggregate/_direct_reply/_llm_aggregate/_template_reply` + `app/server.py` 各 builder | **ResponsePresenter(application)** | 回复装配脱离 Manager |
| `record_turn/_resummarize_session/_summary_to_text/load_session_memory/daily` | **SessionMemory 服务(application)** | Manager 不背记忆压缩 |

---

## 3. 应抽成 domain contract 的概念

放 `domain/`(纯值对象 + 协议,无 IO/LLM/SQLite;`contracts/` 已是事实 domain 层,Phase 1 只新增不搬):

- **AgentResult**(泛化 `ResponsePayload`):`status / confidence / payload / evidence_refs / artifact_refs /
  next_request / reason / cost_usd`。让 B4 旁路 dict 全部进类型化字段。
- **ArtifactRef**(新):`kind`(notion/report/plan_image/committed_path/mock_session/diagnosis_report/…)+ `ref` + `meta`。
- **NextRequest**(新):`kind`(need_evidence/need_clarification/suggest_capability)+ payload。
  让 Diagnosis 证据不足时返回 `need_evidence` 而**不是自己读 source / spawn**。
- 已存在、提升为一等公民:`EvidenceRef/EvidencePacket/SourceRef/EvidenceRequest`(在 `contracts/agents/evidence.py`)、
  `ToolResult/ToolCall`(在 `tools/base.py`)。
- **Capability** 枚举(已在 `contracts/intent.py`)作为 handler 注册键的单一真值。
- 协议:`CapabilityHandler` / `EvidenceProviderHandler` / `ToolHandler`。

---

## 4. Manager↔Agent 统一为 AgentResult

统一接口:`CapabilityHandler.run(request, ctx) -> AgentResult`。
- 子 Agent(或一层 adapter)直接产 `AgentResult`,把现在 `_dispatch_impl` 里“status/confidence/reason 映射
  + emit + persist”收进各自 handler。
- Manager 主循环只读 `status / payload / evidence_refs / artifact_refs / next_request / reason`,
  **不知道**底下是 qa 还是 mock。
- 铁律:子 Agent 互不直调(现状已满足);唯一写者不变(handler 产 diff/mastery updates,Manager 落库);
  `next_request=need_evidence` → Manager 调 EvidenceResearchAgent 再回灌。

---

## 5. Evidence/Artifact/Attachment/Repo/File 读取收敛

唯一路径:**调用方 → EvidenceResearchAgent(隔离上下文)→ 多个 EvidenceProviderHandler →
EvidencePacket(refs+pointers,非全文)→ 注入下游为只读 artifact 摘要**。
- resume/repo/file/attachment/retrieval 各实现一个 provider(合并 `tools/evidence_sources.py` 与 `_collect_*`)。
- Diagnosis/Planning/QA 需外部材料 → 返回 `need_evidence` 或经 `recall()`,不再各自读 source。
- 边界:Conversation State 只存 `summary + recent + ArtifactRef`;完整内容只进 Evidence 层并截断
  (`_MAX_SNIPPET`/`_MAX_READ_BYTES` 提到 provider 统一);大内容按需拉取。

---

## 6. Tool/MCP/Skill 权限统一(参考 OpenClaw)

保留现有全部优点:`ToolEffect`、`owner_agents`、`audit_required`、declarable namespace、`require_tool`、
`file.write/edit` 默认 dry-run + 沙箱 + `FILE_WRITE_ENABLED` 双闸、注册表无 shell/exec、Diagnosis 无写权限。
补:
1. 合并 `ToolSpec`(权限)+`BaseTool`(schema/handler)→ 一个 `ToolHandler` + 一个注册表。
2. 外部 MCP 进同一闸(`effect=EXTERNAL` + `require_tool` 全程校验)。
3. 统一 `sanitize_tool_result`(截断/限大小)供所有 handler 共用。
4. 改名 `MCP_REGISTRY→ToolCatalog`/`CAPABILITY_REGISTRY`(留 shim)。
5. file 工具维持受控、非 shell;权限继续由 `skill.allowed_tools` 控制。

---

## 7. 轻量 DDD 组件边界(指导,不强搬目录)

| 层 | 现有归属 | 动作 |
|---|---|---|
| interfaces | `app/`、CLI、`graph/`(shim) | 不动;后续把 `_ui_chat_dispatch` 编排下沉 |
| application | `orchestration/manager.py`、`planner.py` | 拆出 HandlerRegistry / ResponsePresenter / SessionMemory;Manager 瘦身为编排+唯一写者 |
| domain | `contracts/` | 新增 AgentResult/ArtifactRef/NextRequest + 三 Protocol;提升 Evidence/ToolResult |
| capabilities | `agents/{qa,diagnosis,planning,mock,evidence}` | 各加 `*Handler` 适配壳,内部不动 |
| tooling | `tools/` + `tools/mcp/` | 合并 descriptor/registry,统一权限闸与结果清理 |
| evidence | `agents/evidence/` + `tools/evidence_sources.py` | EvidenceResearchAgent 编排 provider |
| prompt_runtime | `skills/`、`memory/context_assembler` | 基本不动(已是 8 层 Prompt Stack) |
| infrastructure | `storage/`、`llm/`、`knowledge/`、`tools/mcp/runtime`、`multimodal/` | 不动 |
| shared | `config.py`、`memory/log` | 不动 |

不做:为分层造空目录;`orchestrator/` 本期不并入,仅标注“非生产轨”。

---

## 8. 分阶段计划

- **Phase 0(本文档)** — 边界分析。不改代码。
- **Phase 1 — 核心契约**:新增 AgentResult/ArtifactRef/NextRequest + Protocols;提升 Evidence/ToolResult。
  **纯加法,零调用方改动**,`ResponsePayload` 暂留 + 双向 adapter。
- **Phase 2 — 最小 handler 抽象**:三个 Protocol + 空注册表,不接线。
- **Phase 3 — Manager 经 registry 派发**:`_dispatch_impl` 四段 if/elif → `registry.get(cap).run(...)`,
  **行为字节级不变**(handler 内即搬运原逻辑)。
- **Phase 4 — EvidenceProvider 收敛**:resume/repo/file/attachment/retrieval 成 provider;`evidence_agent` 改编排。
- **Phase 5 — 接通 EvidenceResearchAgent**:Diagnosis(证据不足)→`need_evidence`→Manager `gather_evidence`→注入 artifact。
- **Phase 6 — 目录/import 整理**:改名 + 合并 registry,全程 shim;`app/server.py` 编排下沉。
- **Phase 7 — 清理旧路径**:删 `ResponsePayload` 旧字段/`meta` grab-bag(确认无引用后),补文档与测试;标注 `orchestrator/`。

每个 Phase 独立可合、可回滚、全测试绿。

---

## 9. 每阶段测试

- **P1**:AgentResult↔ResponsePayload 往返等价;ArtifactRef/NextRequest 序列化。
- **P2**:注册表注册/解析/未知能力报错。
- **P3**:黄金回归——`manager.handle()` 对 qa/diagnosis/planning/mock/复合“准备面试”五条输入,
  `reply_text/plan/next_actions/status` 与重构前逐字节一致(先抓基线快照);复用 `test_manager_composite`、
  `test_agent_capabilities`、`test_dispatch_phase2`。
- **P4**:每 provider 的 ok/empty/error/denied;EvidencePacket 只回 refs 不回全文(扩 `test_file_tools_and_evidence`)。
- **P5**:Diagnosis 缺证→need_evidence→gather→注入端到端;守 `test_diagnosis_is_read_only`。
- **P6**:旧 import 经 shim 仍通;权限闸对外部 MCP 生效。
- 全程:`pytest` 维持当前基线(`test_embeddings_base_url.py` 两个已知失败除外)。

---

## 10. 风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| P3 悄悄改行为(早停/防死循环/suggest_mock/§5.6 空诊断跳过) | 先落黄金快照基线;handler 内“搬运非重写” | 单 commit `git revert` |
| `meta`/`context` 隐藏读取点(app/server 读 mock/settlement) | P1–5 不删旧字段,只并行加新字段;P7 才删 | 保留旧字段即兼容 |
| 改名破坏 import | 全程 re-export shim(`mcp/registry.py` 已是先例) | 删新名 |
| 误给 Diagnosis 写权限 / Planning 直写 path | skill `allowed_tools` 不变 + `test_diagnosis_is_read_only`;handler 仅产 diff,落库在 Manager | 测试红即拦 |
| Evidence 全文入 Conversation State | provider 强制截断 + `artifact_text()` 只出 summary+指针 + 断言测试 | 单 Phase 回滚 |
| 触碰 `orchestrator/` 独立轨 | 本期完全不动 | N/A |

---

## 附:Phase 1 落地清单(本次)

新增(纯加法,无调用方改动):
- `contracts/result.py` — `ArtifactKind/ArtifactRef`、`NextRequestKind/NextRequest`、`AgentResult`,
  及与 `ResponsePayload` 的双向 adapter。
- `tests/test_agent_result_contract.py` — 往返等价 + 字段/序列化测试。

不动:`ResponsePayload`、Manager、各子 Agent、tools、skills。
