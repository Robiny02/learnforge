# 清理清单 (Cleanup Manifest)

未来要清理 / 收敛的文件与符号。配套重构计划见 [`refactor-plan.md`](./refactor-plan.md)。

**约定**：每个待清理处在源码里都打了 `LF-CLEANUP` 标记，可一键查全：

```bash
grep -rn "LF-CLEANUP" learnforge --include="*.py"
```

> 原则:本清单只**标记不删除**。真正的 redirect/删除发生在 Phase 6(改 import + 改名)与
> Phase 7(删旧路径),且每步保持全测试绿、留兼容期。

## A. 兼容 shim(确认转发,可在 Phase 6 redirect、Phase 7 删除)

| 文件 / 符号 | 现状 | 真实 importer | 清理动作 | Phase |
|---|---|---|---|---|
| ~~`agents/manager.py`~~ | **✅ 已删** | importer(cli/app/eval/memory + 11 测试)已改指 `orchestration.manager`;`agents.__init__.__getattr__` 也改指实现层 | done | done |
| ~~`mcp/`(整包)~~ | **✅ 已删** | importer(agents/base、skills/registry、agents/{qa,diagnosis,react}、integrations×3 + 4 测试)已改指 `tools.*` | done | done |
| `graph/main_graph.py` + `graph/__init__.py` | 转发到 `orchestration.graph` | app/server.py | **保留**:`python -m learnforge.graph.main_graph` 是 CLAUDE.md 记录的 smoke 入口,非纯 debris | 留✓ |
| `tools/runtime.py::DEFAULT_TOOL_REGISTRY` | 看似 facade,**实为默认工具 handler 的注册落点**(`_register_defaults`→TOOLS) | bootstrap 入口 | **保留**:是 bootstrap 入口而非死代码(评估后改判) | 留✓ |

## B. 命名收敛(Phase 6,带兼容 shim)

| 现名 | 问题 | 目标 | 备注 |
|---|---|---|---|
| ~~`tools/registry.py::MCP_REGISTRY` / `MCPRegistry`~~ | **✅ 已改名** `CAPABILITY_REGISTRY` / `CapabilityRegistry`;第一方代码(agents/base、skills/registry、tools/collection、tools/__init__ + 3 测试)全部改指新名 | done | 留 `MCP_REGISTRY = CAPABILITY_REGISTRY`、`MCPRegistry = CapabilityRegistry` 别名兼容 |
| `tools/spec.py::ToolSpec`(权限) ↔ `tools/base.py::BaseTool`(schema/handler) | 两套并行 descriptor,互不校验(B1) | 合并为单一 `ToolHandler` 描述 | 配合 Phase 2 的 `contracts.handlers.ToolHandler` 协议;留待后续 |

## C. 旁路 dict / 字段(Phase 7,确认无引用后删)

| 位置 | 现状 | 替代 | Phase |
|---|---|---|---|
| `orchestration/manager.py` 的 `context["diagnosis"/"evidence_artifact"]`、`meta["notion_url"/"report_path"/"path_committed"/"suggest_mock"/"plan_image_*"]` | 无类型 grab-bag 传 evidence/artifact/控制信号(B4) | `AgentResult.{evidence_refs, artifact_refs, next_request}`(Phase 1 已建) | 7 |
| `contracts/message.py::ResponsePayload` | 仍是线上类型;Phase 3 起 handler 内部已产 `AgentResult`,`_dispatch_impl` 投影回 RP | 逐步全量切 `AgentResult`,确认 app/server 等读取点迁完再删 | 7 |
| ~~`_dispatch_impl` 四段硬编码 if/elif~~ | **✅ Phase 3 已清理** → `HandlerRegistry` 分发,逻辑搬入 `orchestration/capability_handlers.py` | — | done |
| `MockOutput.status: str`(裸字符串) | 映射成 `Status` 现在在 `MockCapabilityHandler` 内（已脱离 Manager if/elif） | 后续可在 mock 子系统源头归一 | 3✓部分 |

## D. 半成品 / 待接线(非删除,是"接上"或"实现")

| 位置 | 现状 | 动作 | Phase |
|---|---|---|---|
| ~~`agents/evidence/` + `Manager.gather_evidence`~~ | **✅ Phase 5 已接通**:Diagnosis 信号不足(conf<0.5,非复合)→`NextRequest(need_evidence)`→`_dispatch_impl` 调 `gather_evidence`→注入 `evidence_artifact`→复跑一次 | done | done |
| ~~`evidence_agent._collect_*`~~ | **✅ Phase 4 已收敛**成 `EvidenceProviderHandler`(`agents/evidence/providers.py`:file/repo/attachment/resume);`run()` 改为编排 provider | done | done |
| `tools/evidence_sources.py` | 仍是 provider 底层调用的工具 handler(repo.search/attachment.recall/resume.recall),保留 | provider 经 `call_tool` 调它,职责清晰,**留** | 留 |
| `agents/research/`(仅 CLAUDE.md) | 未实现的 ReAct research tool | 决策:并入 EvidenceResearch 或正式实现;暂留占位 | 5+ |
| `tools/spec.py::MCPClient` | `NotImplementedError` 占位 | 接真外部 MCP 时实现,或与 `tools/mcp/` 统一 | 6+ |

## E. 并行轨道(本次重构**不动**,仅记录)

| 目录 | 说明 | 处置 |
|---|---|---|
| `orchestrator/`(2107 行) | 独立重建轨(python3.11),**不被生产 import** | 隔离保留,排除在本重构外;去留另行决策 |

---

更新本清单:动到任一标记文件时,同步勾掉对应行并在更新日志(`docs/updates/`)记一笔。
