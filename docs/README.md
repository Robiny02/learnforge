# docs/ — 文档地图

LearnForge 的全部文档,按用途归档分类。

| 目录 | 内容 | 何时看 |
|---|---|---|
| **architecture/** | 系统架构与设计文档(现行) | 想了解结构、设计取舍、重构方案时 |
| **updates/** | 重构 / 阶段更新日志(HTML,每次一份,最新在上) | 想知道"某次重构改了什么";开 [`updates/index.html`](updates/index.html) |
| **module-updates/** | 各模块演进记录(per-module,HTML/MD) | 想追某个模块(diagnosis/memory/mock…)的演化史 |
| **archive/** | 已归档/被取代的早期设计(Proposal/Design/Tasks 等) | 追溯最初的提案与任务拆解 |
| **assets/** | 架构图等图片资源 | 引用图时 |

## architecture/

| 文件 | 说明 |
|---|---|
| [`architecture.md`](architecture/architecture.md) | 系统总体架构 |
| [`agent-architecture.html`](architecture/agent-architecture.html) | Agent 架构图解 |
| [`ui-design.md`](architecture/ui-design.md) | UI / 终端工作台设计 |
| [`learnforge_research_diagnosis_report_design.md`](architecture/learnforge_research_diagnosis_report_design.md) | research/诊断报告设计 |
| [`refactor-plan.md`](architecture/refactor-plan.md) | 轻量 DDD + 可插拔 Handler 重构总方案(Phase 0–7) |
| [`cleanup-manifest.md`](architecture/cleanup-manifest.md) | 待清理/已清理文件清单 |
| [`interview-skill-integration.md`](architecture/interview-skill-integration.md) | mock 拷打 skill(LLMInternSkill 蒸馏)集成设计 |

## 约定

- **现行架构/设计** → `architecture/`;**逐次重构记录** → `updates/`(见其 [README](updates/README.md));
  **模块演进史** → `module-updates/`;**早期/被取代的方案** → `archive/`。
- 模块级工程文档(职责/不变量/坑)在各代码目录的 `CLAUDE.md`,不在此重复。
- **运行时数据不在 docs/**:用户长期记忆在 `learnforge/data/memory/`;出图资产由 `integrations/gpt_image.py`
  写入仓库根 `docs/assets/`(本目录);app 生成的报告写入 `learnforge/docs/`(仅运行时,不手动维护)。
