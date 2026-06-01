# LearnForge 个人记忆系统 · 需求基线

> 本阶段目标：明确 **个人 local memory** 的基本要求，固化职责边界与差距清单。
> 本文档**只描述需求与约束，不含实现代码**。后续实现以此为对照 checklist。
>
> 范围限制：本阶段**只做个人 local memory**。shared memory、外部 content 知识库
> （GitHub / 小红书 / 小林 coding 等）、多用户共享权限模型，**均不在本阶段范围**，
> 见 §6「扩展位」。

> **术语约定**：本文中的「长期个人记忆」默认指 **daily memory**，即以 `memory/YYYY-MM-DD.md`
> 组织的长期历史记忆层。它不同于 `MEMORY.md`；`MEMORY.md` 是稳定规则层，daily memory 是可增长、
> 可召回的历史记录层。

---

## 1. 四个记忆文件的职责边界

四者职责清晰、互不重叠：

| 文件 / 层 | 受众 | 管什么 | 一句话 |
|---|---|---|---|
| `CLAUDE.md` | 开发 Agent（Claude Code） | 开发规则、Agent 架构边界、阶段范围、禁止事项 | **怎么开发这个项目** |
| `MEMORY.md` | 运行时 Agent | 用户长期偏好、学习目标、回答风格、硬性规则 | **用户长期希望被怎么服务** |
| `daily memory`（`memory/YYYY-MM-DD.md`） | 运行时 Agent（按需召回） | QA 总结、mock 反馈、薄弱点、学习进展、阶段性项目决策 | **发生过的重要事情** |
| `session_memory`（`session_state`） | 运行时 Agent（仅本会话） | 最近消息、当前任务、临时进展、压缩摘要 | **当前这次对话** |

**关键裁决**：
- 「不变的偏好 / 目标 / 风格 / 硬规则」→ `MEMORY.md`（稳定规则层）。
- 「有时间戳、会增长、按需召回的事件」（含**阶段性项目决策**、**学习进展**）→ daily memory。
- 项目决策**不进** `MEMORY.md`。

---

## 2. 记忆分层要求

系统区分三层 local memory，物理与逻辑均不混用：

1. **稳定长期规则**（`MEMORY.md`）——用户长期偏好、学习目标、项目约束。
2. **长期个人记忆**（daily）——历史问答、mock 结果、薄弱点、学习进展、阶段性决策。
3. **短期会话记忆**（session）——维持当前对话上下文，避免上下文过长后信息丢失。

三层之间**不互相覆盖、不互相污染**：短期会话记忆**不进**长期索引，长期记忆**不混入**会话上下文存储。

---

## 3. 稳定规则层（MEMORY.md）要求

- **R3.1 初始可空 + 初始化机制**：系统初始可无内容，但必须能自动初始化（缺失时按模板创建）。
- **R3.2 不频繁自动更新**：稳定规则不应被每轮对话自动改写。
- **R3.3 受控更新**：仅在 **① 用户明确要求记住**，或 **② 系统判定为长期稳定偏好** 时才允许更新。
- **R3.4 每轮加载**：稳定规则每次进入 prompt，作为稳定上下文（KV-cache 友好的可缓存前缀）。
- **R3.5 单一真值来源**：用户长期偏好以 `MEMORY.md` 为权威来源；`UserProfile` 仅作离线回退 / 展示，**不作为偏好的并行写入点**。

---

## 4. daily memory（长期个人记忆）要求

- **R4.1 可读 / 可追溯 / 可重建**：真值为人类可读 markdown；SQLite 索引为可重建的派生物。
- **R4.2 元信息**：每条记忆需含基本元信息——**时间、类型（kind）、主题（topic）、重要性（importance）**。
- **R4.3 记忆类型**：至少覆盖
  `qa`（问答总结）、`weak`（薄弱点）、`mock`（mock 反馈）、
  `progress`（学习进展）、`decision`（阶段性项目决策）。
- **R4.4 支持召回**：长期记忆需支持后续按需召回。
- **R4.5 多维召回**：召回应支持按 **语义、关键词、主题、类型** 等维度找到相关记忆。
- **R4.6 旧记忆不删除、降权**：旧记忆不直接删除，但召回排序上随时间逐渐降低权重。
- **R4.7 重要性分级**：mock 薄弱点、长期偏好、重要项目决策（`weak`/`progress`/`decision`）应比普通 `qa`
  **衰减更慢、权重更高**。

---

## 5. 短期会话记忆（session）要求

- **R5.1 仅服务当前会话**：不跨会话、不永久保存。
- **R5.2 保留最近若干轮**：保存最近 N 轮原文对话。
- **R5.3 溢出压缩**：上下文过长时，把较早内容压缩成摘要。
- **R5.4 与长期记忆隔离**：不进入长期索引，不与长期记忆混存。
- **R5.5 目标**：保证当前任务连续，而非永久保存。

---

## 6. 写入要求

- **R6.1 不无脑写**：不每轮都写长期记忆。
- **R6.2 只写有长期价值的信息**。
- **R6.3 写前判定**：写入前判断信息是否 **重要 / 重复 / 稳定**。
- **R6.4 候选记忆**：QA 结果、mock 总结、用户明确偏好、重要项目决策（含学习进展）可作为候选。
- **R6.5 排除项**：普通闲聊、临时报错、无价值短句不沉淀为长期记忆。
- **R6.6 写入决策权在系统（Agent）**：`qa`/`weak`/`mock`/`progress`/`decision` 由 Agent 按规则判定沉淀，
  **不依赖用户显式触发**；用户显式「记住」通道保留给 `MEMORY.md` 稳定层（与 R3.3 一致）。
- **R6.7 两阶段写入策略**：
  - **本阶段（同步 / inline）**：在已有节点（QA 收尾、mock 结算、路径修改等）由 Agent best-effort
    判定并写 daily；同步路径保持轻量判定。
  - **后续（daemon，扩展位，不在本阶段）**：后台进程做「回顾式沉淀」——周期性扫描会话 / 事件，
    二次判断哪些值得提炼为 `progress`/`decision`，承载更重的去重 / 重要性聚合 / 稳定性识别，
    并可**建议**将反复出现的稳定偏好升级进 `MEMORY.md`。复杂判定留给 daemon，同步路径不背负。

---

## 7. 召回要求

- **R7.1 找到相关长期记忆**：用户提问时能召回相关长期记忆。
- **R7.2 相关优先于时间**：召回应基于与当前问题的相关性，而非简单按时间取最近。
- **R7.3 综合排序**：召回综合考虑 **相关性 + 重要性 + 时间** 三因素。
- **R7.4 辅助而非覆盖**：召回内容作为辅助上下文，**不得覆盖用户当前意图**（用户输入优先级最高）。

---

## 8. 阶段范围与扩展位

**本阶段必须闭环**（个人记忆基本闭环）：
初始化 → 写入 → 保存 → 召回 → 会话摘要。

**明确不在本阶段**（扩展位，登记备查，暂不实现）：
- daemon 异步回顾式沉淀（见 R6.7）。
- shared memory（共享知识库）。
- 外部 content 知识库（GitHub / 小红书 / 小林 coding 等）。
- 多用户共享权限模型。

保持当前简洁可维护，不为上述扩展位提前引入复杂架构。

---

## 9. 现状差距清单（实现 checklist）

基于对现有 `memory/` 代码的评估，本阶段待补项（状态截至本阶段实现）：

| # | 差距项 | 关联需求 | 状态 |
|---|---|---|---|
| G1 | 稳定层缺「用户明确记住」写入口 | R3.3 | ✅ `files.append_root_memory` + `Manager.maybe_remember_rule`（明确触发词→写 MEMORY.md「用户补充偏好」段，去重） |
| G2 | `MEMORY.md` 与 `UserProfile` 双真值 | R3.5 | ✅ `memory_prefix` 明确以 MEMORY.md 为权威、UserProfile 仅回退（docstring 固化） |
| G3 | daily 缺 `progress` / `decision` 两类 kind | R4.3、R6.4 | ✅ `Manager.remember_decision/remember_progress`；路径生成/调整成功自动沉淀 `decision` |
| G4 | 长期记忆缺显式 `importance` 元信息 | R4.2、R4.7、R7.3 | ✅ `decay.importance_for` + 索引写 `metadata.importance`；召回排序 = 相关性×衰减×重要性（温和系数） |
| G5 | 召回缺「按类型 / kind」过滤维度 | R4.5 | ✅ `RetrievalFilters.kind` + `_chunk_filter_sql` 按 `metadata.kind` 过滤 |
| G6 | 写入缺去重判断 | R6.3 | ✅ `daily.record` 写前按 kind+topic+归一化正文 SHA1 去重；重复返回 `duplicate=True` 不落库 |
| G7 | `qa_history` 与 daily 双表 | — | ✅（澄清边界）qa_history=导入/seed 历史问答，daily=运行时 QA；同一条不重复，召回层汇合。完全合并（删 qa_history）属更大改动，故意推迟以免破坏既有 seed/测试 |
| G8 | `progress` / `decision` 纳入慢衰减档 | R4.7 | ✅ 已加入 `MEMORY_STICKY_KINDS` |

> daemon（R6.7 后续阶段）可承载更强的去重（语义近似）与重要性聚合；本阶段同步路径只做轻量版本
> （精确 hash 去重、kind→importance 表）。

---

## 10. 记忆日志面板（可观测，第一版）

记忆系统的可观测入口：每轮对话记录记忆的「读取 / 注入 / 写入 / 维护」操作，让用户与开发者看到
「这轮有没有想起过去？想起了哪些？有没有记下新内容？为什么记 / 不记」。

- **收集器**：`memory/log.py` 的 `MEMORY_LOG` 单例（按轮 `reset()`），`MemoryEvent{category,action,result,reason,count,ts}`；
  四类 `read/inject/write/maintain`（对应需求 §3）。零依赖、不入循环导入。
- **接线点**：
  - 读取/注入：`Manager.begin_memory_turn`（稳定记忆 MEMORY.md + 会话记忆）、`load_session_memory`、
    `RetrievalAgent.run`（LOCAL = 搜索长期记忆，记命中/采用）。
  - 写入：`daily.record`（保存成功 / 跳过重复 / 写入失败，单点覆盖所有写路径）、
    `remember_qa`（提取候选 / 价值不足跳过）、`maybe_remember_rule`（稳定规则保存/已存在）。
  - 维护：`record_turn`（压缩会话记忆）。
- **第一版最小字段（§4）**：`MemoryLog.summary()` → 是否加载 MEMORY.md / 会话记忆、是否搜索长期记忆、
  最终注入条数、是否提取候选、是否保存成功或跳过原因。
- **展示**：CLI 每轮渲染「memory · 本轮记忆日志」面板（`render_memory_panel`）+ `/memory` 命令展示
  记忆来源(§2.1)与长期记忆分类(§2.2)概览（`memory_files_overview` / `memory_index_overview`）。
- **演示**：`python -m learnforge.memory.demo_panel` —— 往记忆库 seed（MEMORY.md + 多类 daily + 会话），
  再模拟一轮检索/读写并打印面板（全离线）。

范围：本版聚焦个人 local memory 的流程可读与可调试；不做可视化统计图表、不展示底层索引细节。

---

## 11. 评测脚手架与第一阶段验收（测试说明 §4–§16）

- **脚手架**：`eval/memory_eval.py`（`python -m learnforge.eval.memory_eval` 出 §13 报告）+
  `tests/test_eval_memory.py`（pytest 守第一阶段 §14）。固定 seed 覆盖 qa/weak/mock/progress/decision
  + 衰减/冲突/负样本场景，全离线确定性。
- **第一阶段 §14：10/10 全通过**。指标：hit@5=1.0、MRR@5=1.0、kind/topic_accuracy=1.0、
  trigger_precision/recall=1.0、negative_no_recall=1.0；重要性/衰减影响排序、单源不被 RRF 抹平、
  session 第7轮触发且摘要无 JSON 套娃、日志覆盖读/搜/注/写。
- **本轮修复的三个真实缺陷**（均由实测/评测发现）：
  1. 单源 LOCAL 召回被外层 `rrf_fuse` 抹平衰减/重要性强度 → 单源时跳过二次 RRF，保留源内真实分（`agents/retrieval.py`）。
  2. 离线 session 摘要递归套娃、无限膨胀 → 只摘要新溢出轮并追加、长度封顶；离线兜底改可读文本（`agents/manager.py`、`memory/base.py`）。
  3. FTS `_like_fallback` 单字符 ASCII 词（如 "1"）导致过召回 → 丢弃单字符 ASCII 词、保留 CJK（`storage/repositories.py`）。
- **明确未验证（留待第二阶段 §15）**：本环境无 sqlite-vec / embedding（`chunk_vectors` 表不存在），
  故 **vector KNN、hybrid RRF 融合、语义去重、LLM 最终回答 groundedness/hallucination** 均未验证，
  报告会显式标注 `hybrid_ready=False`，绝不冒称已验证。

---

## 12. 对抗语义召回评测（真实 embedding，离线 cosine）

- **脚手架**：`eval/semantic_eval.py` + `eval/golden/semantic.json`（24 记忆/12 个 topic distractor 对、
  52 困难 query、32 负样本，含 redis 锁续期 vs 缓存雪崩失败回归对）+ `tests/test_eval_semantic.py`
  （有 `OPENAI_API_KEY` 才跑，无则 skip，**不记录 key**；base_url 经 `LF_OPENAI_BASE_URL`，支持 OpenRouter）。
- **阈值闸门**：`memory/recall_gate.py`（`gate_recall`）—— top1<`MEMORY_RECALL_MIN_SIM` → "没有找到明确记忆"；
  top1-top2<`MEMORY_RECALL_MARGIN` → uncertain（交 rerank/保守处理）。阈值**可配、不写死**，
  默认 0.33/0.05 标注来源为小规模 eval（`MEMORY_RECALL_THRESHOLD_SOURCE`）。`tests/test_recall_gate.py` 离线覆盖。
- **实测（text-embedding-3-small via OpenRouter，52 query）**：vector hit@1≈**0.96**、hit@3=1.0、MRR≈0.98、
  cross_lingual hit@1=1.0、intra_topic=0.92；**FTS hit@1≈0.21**（无字面重叠中文改写下基本失败）。
  2 例同/跨主题误召回均被 margin 闸门标记 `uncertain`，未直接采纳。
- **重要的不理想发现**：负样本**不可线性分离**——负样本 top1 sim 最大 0.469 > 正样本 top1 sim 最小 0.361，
  固定阈值 0.33 仅能 abstain 53% 负样本；近域硬负样本（Rust/Spring Cloud/epoll 等库中无记录）embedding
  贴近真实记忆。**结论：τ=0.33 不通用，需更大集校准 + margin + rerank**。
- **最终口径**：真实 embedding 离线语义召回有效，hit@1 约 0.85–0.96；FTS fallback 在无字面重叠中文改写下
  失败；纯向量仍有同主题细粒度误召回，需 rerank / margin / hybrid 补强；**完整 in-DB hybrid 尚未验证**
  （待 Python 支持 sqlite-vec 后补 BM25-only / vector-only / hybrid / hybrid+decay 对照）。
