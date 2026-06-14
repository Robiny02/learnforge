# Module Updates / 模块更新日志

按**模块**记录 LearnForge 各能力的迭代历程（设计动机 + 关键变更 + 提交追溯），便于回顾每个模块
「为什么变成现在这样」。区别于根目录 `CHANGELOG`（按版本）——这里是**按模块**的设计演进笔记。

## 索引

| 模块 | 文件 | 说明 |
|---|---|---|
| Diagnose / Resume Diagnosis | [diagnosis.md](diagnosis.md) | 简历/项目级诊断：从「文本审稿」→「Repo-RAG 项目拷打器」的演进 |
| Memory / Context | [memory.md](memory.md) | 记忆/上下文治理：对照 Claude Code 的「留首尾·压中间」审计 + 留头锚点/周期重摘/replan 可见性/附件借鉴 |
| Memory / Context | [context-compaction.html](context-compaction.html) | 会话级 compaction（按 token 阈值触发）+ 重要结果 pin 保护 |
| Memory / Context | [session-context-injection.html](session-context-injection.html) | 阶段一：把已实现却从未进 prompt 的会话记忆真正接进 QA 的 session 槽（pinned/summary/recent/最近调用/附件引用 + 统一预算 + 诚实面板） |
| Skills / Context | [prompt-stack-convergence.html](prompt-stack-convergence.html) | 架构收敛 Step A/B/G：结构化 SOP（Active Skill）+ 固定 8 层 Prompt Stack（新增 Project Guide、拆出 Active Skill/Tool Registry）+ 面板镜像真实 8 层逐层报 token |
| Mock / Interview | [mock-director-refactor.html](mock-director-refactor.html) | 重构：删 LangGraph S1–S7 状态机 + checkpointer，换 InterviewDirector 智能规划（每轮选 move）+ MockStateStore（SQLite）；面试官人格全量接入 tech-interview-skill；模型分层 + 高质量解答 + 空转主动询问 |

> 新增模块更新时：在此表加一行，并新建对应 `<module>.md`。每个条目尽量带提交哈希，便于 `git show`。
