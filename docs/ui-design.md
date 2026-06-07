# LearnForge 像素农场风 UI 设计方案

## 目标

为 LearnForge 增加一个“像素农场 RPG 风格”的本地 Web UI，让当前的多 agent 学习系统从命令行和 API 骨架升级成一个可交互、可观察、可复习、可管理记忆的学习工作台。

设计目标：

1. 保留 CLI 式提问体验，但用像素风场景包装成更有沉浸感的 `Farm Console`。
2. 清晰展示 LearnForge 的 agent 编排、RAG 召回、记忆写入、文件生成和 daemon 提取日志。
3. 允许用户浏览任意 markdown 文件，包括 daily memory、诊断报告、学习计划和生成文档。
4. 提供每日复习页，用 mastery、diagnosis、mock 和 QA 信号驱动可视化复习。
5. 预留 LeetCode/MCP 编程练习入口，并把本地练习结果回流到 LearnForge 的学习状态。

## 风格原则

整体风格采用“原创像素农场学习工作台”，可以受经典农场 RPG 的舒适氛围启发，但不直接复制任何商业游戏素材、角色、图标或 UI。

关键词：

- 像素农场
- 木屋书桌
- 夜晚窗光
- 任务公告板
- 知识作物
- 复习日历
- agent 村民
- 记忆水晶
- 学习矿洞

视觉上应该温暖、轻量、有游戏感，但不能影响学习工具的效率。学习相关信息需要清晰可扫读，不能被装饰元素淹没。

## 技术形态

建议做成本地 Web App，而不是纯终端 TUI。

原因：

- 需要 markdown 文件浏览和预览。
- 需要右侧实时 log 表。
- 需要知识图、雷达图、复习日历等可视化。
- 需要代码编辑器和 LeetCode 练习页面。
- 现有 FastAPI 可以直接扩展为 UI 后端。

推荐结构：

```text
learnforge/
  learnforge/
    app.py                 # 现有 FastAPI 入口，可扩展 API
    ui/                    # 新增前端静态资源或构建产物
    api/                   # 可选：拆分 UI API
  ui-design.md             # 当前设计文档
```

前端技术建议：

- 第一选择：React + Vite + TypeScript。
- 图表：Recharts / ECharts。
- Markdown：react-markdown + remark-gfm。
- 代码编辑器：Monaco Editor。
- 实时日志：Server-Sent Events 或 WebSocket。
- 像素 UI：CSS pixel borders + AI 生成 bitmap 资产。

## 信息架构

UI 分为 5 个主页面：

```text
Farm Console
Memory & Files
Daily Review
Knowledge Map
Practice
```

顶部或左侧使用像素风导航栏。主页面不做营销式首页，打开后直接进入 `Farm Console`。

## 页面一：Farm Console

`Farm Console` 是主 CLI 页面，用于用户提问和 agent 对话。

### 布局

```text
+---------------------------------------------------------------+
| Top Bar: LearnForge / 当前 session / trace / settings          |
+--------------------------+------------------------------------+
|                          | Activity Log                       |
| Pixel Background         |------------------------------------|
|                          | 时间 | 类型 | 内容 | 来源 | 状态     |
|  User Avatar     Agent   |                                    |
|  Bubble          Bubble  |                                    |
|                          |                                    |
|  Conversation Stream     |                                    |
|                          |                                    |
+--------------------------+------------------------------------+
| Mode Bar: QA | Mock | Diagnose | Plan | Review | Practice      |
+---------------------------------------------------------------+
| Input: ask LearnForge...                         Send Button    |
+---------------------------------------------------------------+
```

### 主视觉

背景是原创像素风学习农场，例如：

- 木屋书桌和窗外农田。
- 夜晚灯光和星空。
- 墙上的任务板和书架。
- 桌面上的书、键盘、咖啡、记忆水晶。

用户头像在左，agent 头像在右。对话气泡保持左右分栏：

- 用户消息：左侧蓝绿色或浅木色气泡。
- Agent 消息：右侧羊皮纸或木牌气泡。
- 系统事件：中间小标签，例如 `diagnosis started`、`retrieval degraded to FTS`。

### 底部模式栏

底部固定显示当前学习模式：

```text
Mode: QA | Mock | Diagnose | Plan | Review | Practice
```

每个模式改变输入框行为：

| 模式 | 输入行为 | 后端入口 |
| --- | --- | --- |
| QA | 普通问答，允许 RAG 引用 | `/qa` 或主图 |
| Mock | 多轮模拟面试，支持 answer/pause/end | `/mock` |
| Diagnose | 读取事件与 mastery 做弱点诊断 | `/diagnose` |
| Plan | 生成或修改学习路径 | `/plan` |
| Review | 跳转复习页，输入作为复习反馈 | 新增 review API |
| Practice | 跳转本地刷题页 | 新增 practice API |

### 右侧 Activity Log

右侧 log 表用于展示系统内部活动，让用户看见 agent 不是“黑箱”。

展示字段：

| 字段 | 示例 |
| --- | --- |
| 时间 | `21:34:08` |
| 类型 | `RAG`, `Memory`, `Daemon`, `File`, `Trace`, `Diagnosis` |
| 内容 | `召回 4 条 local memory` |
| 来源 | `RetrievalAgent`, `MEMORY_LOG`, `daemon` |
| 状态 | `ok`, `degraded`, `skipped`, `error` |

可显示的事件：

- RAG 召回了哪些 chunk。
- 检索方法是否从 vector 降级到 FTS。
- daily memory 是否写入。
- daemon 提取了哪些重要内容。
- 生成了哪些 markdown/json 文件。
- mock 是否写入 weakness event。
- diagnosis 是否发现弱点。
- planning 是否提交 path diff。

后端数据来源：

- `MEMORY_LOG`
- `agent_traces`
- `interaction_events`
- `diagnosis_reports`
- `data/memory/*.md`
- 未来 daemon 输出表或文件。

推荐实现：

```text
GET /ui/events/stream      # SSE 实时事件流
GET /ui/logs               # 初始日志列表
GET /ui/traces/{trace_id}  # 某轮详细 trace
```

## 页面二：Memory & Files

用户可以自主查看任何 markdown 文件。

### 布局

```text
+--------------------+------------------------------------------+
| File Tree          | Markdown Preview                         |
|                    |                                          |
| data/memory/       | # 2026-05-31                             |
|   MEMORY.md        | ...                                      |
|   2026-05-31.md    |                                          |
| reports/           |                                          |
| plans/             |                                          |
+--------------------+------------------------------------------+
```

### 功能

- 浏览 `.md`、`.json`、`.jsonl` 文件。
- markdown 渲染预览。
- 搜索文件名和正文。
- 显示文件来源：用户写入、daemon 生成、agent 生成、系统报告。
- 支持“加入复习”按钮，将文档中的 topic/atom 加入复习队列。
- 支持“解释这段内容”按钮，把选中文本送入 QAAgent。

### API

```text
GET /ui/files
GET /ui/files/read?path=...
POST /ui/files/explain
POST /ui/review/from-file
```

安全边界：

- 默认只允许读取项目目录内白名单路径。
- 不允许任意读取用户系统文件。
- 写文件必须通过明确 action，不做自动覆盖。

## 页面三：Daily Review

每日复习页用于把 LearnForge 的 mastery、diagnosis、mock 和 QA 信号转化成当天任务。

### 页面模块

1. 今日复习任务
2. 知识掌握六芒星
3. 最近弱点
4. 学习 streak
5. 今日完成情况
6. 推荐下一步

### 今日复习任务

任务来源：

- `knowledge_atoms` 中 effective mastery 低的 atom。
- `interaction_events` 中近期弱点。
- mock 结算产生的 weakness。
- QA 中 `unverified` 或 `uncertain` 的 topic。
- 用户手动加入复习的 md 内容。

任务卡片示例：

```text
[数据库] MVCC 可见性判断
状态: due today
掌握度: 0.42
来源: mock weakness + QA uncertain
动作: 复习 / 提问 / 出一道题 / 加入 mock
```

### 知识六芒星

六个默认维度：

1. 算法
2. 系统设计
3. 数据库
4. 网络
5. 并发
6. 语言基础

每个维度分数由相关 atom 的 effective mastery 聚合：

```text
dimension_score = average(effective_mastery(atom_i))
```

也可以加入 recency penalty：

```text
review_score = mastery * recency_weight
```

视觉设计：

- 六芒星或雷达图。
- 低分维度用暖色提示。
- 点击某个维度展开对应 atom 和复习任务。

### 学习农场隐喻

可以把知识点设计成农田：

- `unknown`：未开垦土地。
- `learning`：刚发芽。
- `familiar`：小苗。
- `proficient`：成熟作物。
- `mastered`：金色作物。

弱点像杂草，复习和 mock 可以清除杂草。这个隐喻可以提升趣味，但不要替代真实数据展示。

## 页面四：Knowledge Map

知识图页面展示 topic、atom、文档、mock 和 QA 之间的关系。

### 节点类型

| 节点 | 含义 |
| --- | --- |
| Topic | 知识主题 |
| Atom | 具体知识点 |
| Chunk | RAG 文档切片 |
| QA | 历史问答 |
| Mock | 模拟面试回合 |
| File | markdown 文件 |

### 视觉规则

- 节点颜色表示 mastery tier。
- 节点大小表示复习重要性或 evidence count。
- 边表示引用、归属、共同出现、依赖关系。
- 点击节点显示详情和操作。

可用操作：

- 提问这个知识点。
- 生成复习卡。
- 加入今日复习。
- 开始相关 mock。
- 查看相关 markdown 文件。

## 页面五：Practice / LeetCode

Practice 页面用于本地练习编程题，并为 LeetCode/MCP 集成预留入口。

### 本地练习优先

第一阶段先做本地练习：

- 题目列表。
- 题目详情。
- Monaco 代码编辑器。
- 本地 sample test。
- agent 提示。
- 错因总结。
- 复杂度分析。
- 练习结果写入 `interaction_events`。

### LeetCode/MCP 集成策略

可以接 LeetCode MCP，但建议分阶段：

1. 拉取题目信息和用户公开状态。
2. 本地练习并记录结果。
3. 用户确认后打开 LeetCode 题目页。
4. 可选：在用户授权 cookie/session 下尝试同步提交。

自动提交需要谨慎，因为 LeetCode 并没有稳定官方公开提交 API，很多 MCP 或 SDK 依赖非官方 GraphQL、cookie 或网页协议。产品上建议默认采用“用户确认提交”，避免自动批量提交或 contest 自动化。

### Practice 到 LearnForge 的回流

练习结束后写入：

- `interaction_events`：题目结果、错因、topic。
- `qa_history`：用户提问和 agent 解释。
- `daily memory`：值得记住的错因。
- `knowledge_atoms` mastery：由 Manager 根据练习信号更新。

## 像素素材方案

不需要用户必须提供素材。建议用“AI 生成原创素材 + CSS 像素 UI”的方式。

需要的素材：

| 素材 | 用途 |
| --- | --- |
| 主背景 | Farm Console 背景 |
| 用户头像 | 左侧对话 |
| Agent 头像 | 右侧对话 |
| 图标 | 模式栏和按钮 |
| 木牌面板 | log、任务卡、文件树 |
| 羊皮纸气泡 | agent 回复 |
| 作物状态 | mastery tier |
| 记忆水晶 | memory 相关事件 |
| 矿洞入口 | weakness/mock |

建议统一规格：

- 基础网格：16px 或 32px。
- 背景：1920x1080，像素风，但导出为高清 bitmap。
- 头像：128x128。
- 图标：32x32 或 48x48。
- UI 边框：9-slice PNG 或 CSS pixel border。

## 交互细节

### 输入框

不同 mode 下 placeholder 不同：

| Mode | Placeholder |
| --- | --- |
| QA | `问一个概念、项目或复习问题...` |
| Mock | `回答当前面试题，或输入 /pause /end...` |
| Diagnose | `输入你想聚焦诊断的 topic，可留空...` |
| Plan | `描述你的学习目标和截止时间...` |
| Review | `记录今天的复习反馈...` |
| Practice | `搜索题目或输入 LeetCode 编号...` |

### Agent 状态

Agent 头像可以有状态：

- idle：眨眼或轻微浮动。
- thinking：头顶出现像素省略号。
- retrieving：翻书动画。
- writing memory：记忆水晶发光。
- diagnosing：放大镜动画。
- mock：拿题板或计时器。

### 日志可展开

Activity Log 默认显示简短内容，点击后展开：

- 输入 query。
- 检索 scopes。
- method_used。
- degraded 状态。
- chunk ids。
- 写入文件路径。
- trace id。

## 后端接入建议

新增 UI API 时，不建议直接把前端绑死到内部 Python 类。建议加一层薄 API adapter。

推荐 API：

```text
POST /ui/chat
POST /ui/mode
GET  /ui/session/{session_id}
GET  /ui/logs
GET  /ui/events/stream
GET  /ui/files
GET  /ui/files/read
GET  /ui/review/today
GET  /ui/review/radar
GET  /ui/knowledge-map
GET  /ui/practice/problems
POST /ui/practice/run
POST /ui/practice/submit
```

其中 `/ui/chat` 可以内部转发到现有主图：

```text
compile_main_graph().invoke({
  "user_input": text,
  "session_id": session_id,
  "trace_id": trace_id,
  "active_mock_session_id": active_mock
})
```

## MVP 范围

第一版建议只做：

1. Farm Console 主界面。
2. 底部 mode 切换。
3. 右侧 Activity Log。
4. Markdown 文件浏览。
5. Daily Review 雷达图和任务列表。

暂缓：

- 完整 LeetCode 自动提交。
- 复杂知识图布局。
- 多套季节主题。
- 大量动画。

## 任务难度

| 模块 | 难度 | 原因 |
| --- | --- | --- |
| 像素风主界面 | 中 | 视觉统一和响应式需要打磨 |
| 对话 + mode 切换 | 中 | 需要对接现有主图和 mock 子图 |
| 实时 Activity Log | 中高 | 要整理事件源并推送到前端 |
| Markdown 文件浏览 | 中 | 文件安全边界和 preview 需要处理 |
| Daily Review | 高 | 需要从 mastery/event/diagnosis 聚合任务 |
| Knowledge Map | 高 | 图数据建模和可视化复杂 |
| LeetCode/MCP | 高 | 涉及非官方接口、授权、提交边界 |
| 完整像素资产 | 中高 | AI 可生成，但统一风格需要迭代 |

粗略排期：

| 阶段 | 内容 | 时间 |
| --- | --- | --- |
| Phase 1 | Console + mode bar + 静态像素风 UI | 3-5 天 |
| Phase 2 | 接入主图、mock、Activity Log | 5-8 天 |
| Phase 3 | Markdown viewer + Daily Review | 5-10 天 |
| Phase 4 | Knowledge Map + 复习任务闭环 | 1-2 周 |
| Phase 5 | Practice + LeetCode/MCP | 1-2 周 |
| Polish | 像素资产、动画、主题、细节 | 1-2 周 |

整体难度为中高。核心难点不是画 UI，而是把 LearnForge 的 agent 编排、记忆、诊断、路径、mock、文件生成和未来 LeetCode 练习统一成一个清晰的用户体验。

## 推荐落地顺序

1. 先做没有复杂动画的 `Farm Console`。
2. 把 `MEMORY_LOG`、`agent_traces`、`interaction_events` 接到右侧 log。
3. 做 markdown viewer，让用户能看见系统到底记了什么。
4. 做 Daily Review，把 mastery 数据产品化。
5. 再做 Knowledge Map。
6. 最后接 LeetCode/MCP，并默认采用用户确认提交。

