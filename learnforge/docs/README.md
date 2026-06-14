# docs/ — 文档地图

LearnForge 的文档按用途归类。

| 目录 / 文件 | 内容 | 何时看 |
|---|---|---|
| **architecture/** | 系统架构与设计 | 想了解结构、重构计划、待清理项时 |
| ├─ [`refactor-plan.md`](architecture/refactor-plan.md) | 轻量 DDD + 可插拔 Handler 重构总方案（Phase 0–7） | 理解重构整体思路 |
| ├─ [`cleanup-manifest.md`](architecture/cleanup-manifest.md) | 待清理/已清理文件清单（shim、命名、旁路 dict…） | 决定动哪些历史 debris |
| └─ [`interview-skill-integration.md`](architecture/interview-skill-integration.md) | mock 面试拷打 skill（LLMInternSkill 蒸馏）的集成设计 | 改 mock grilling 时 |
| **updates/** | HTML 更新日志（每次更新一份，最新在上） | 想知道"每次改了什么"；打开 [`updates/index.html`](updates/index.html) |
| **assets/** | 运行时生成的信息图（gpt-image 输出，`integrations/gpt_image.py`） | 一般不手动维护（生成物） |

## 约定

- **架构/设计取舍** → `architecture/`；**逐次变更记录** → `updates/`（见其 [README](updates/README.md)）。
- 模块级工程文档（职责/不变量/坑）仍在各代码目录的 `CLAUDE.md`，不在此处重复。
- 用户长期记忆是运行时数据，存 `data/memory/`（**不是** docs/）。
