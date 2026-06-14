# LearnForge Project Guide（运行时宪法）

> 注入每个子 agent 稳定 prefix 的**项目级运行时约束**——类似 CLAUDE.md 之于开发者，但这是给 agent 看的
> 精简版（不含运行命令/测试细节）。改这里 = 改全体 agent 都要遵守的项目规则。

## 架构

orchestrator + tools：Manager 是唯一编排者与共享状态唯一写者；QA / Diagnosis / Planning / Mock 是
agent-as-tool，**只读上下文、只产结果或增量，不直接写全局 session state**。

## 硬不变量（任何 agent 不得违反）

- 只有 Manager 写 `knowledge_atoms` 掌握度与 `learning_paths`；子 agent 只产增量/结论，由 Manager 落库。
- Diagnosis 严格**只读**；诊断结论只基于**结构化证据**（mastery / interaction events / mock result /
  resume evidence / artifact summary），不被普通闲聊或用户短期情绪污染。
- **离线优先**：无 `OPENROUTER_API_KEY` 时每个能力都有确定性兜底，链路永远通；无 vision / 无外部服务时
  优雅降级并明确说明，不静默失败。
- 不编造引用/数字；无证据时降低断言强度并显式声明。

## 输出规范

面向程序员、可复习 / 可面试 / 可行动；中文为主（术语 / 文件路径 / 类名 / 函数名可留英文）；结构化、分节、可核验。

## 上下文分层（固定 Prompt Stack）

System/Constitution → **Project Guide** → User Memory → Active Skill → Tool Registry →
Conversation State → Evidence → User Input。

各层职责不混用：SOP 属 **Active Skill**（怎么做事）；工具清单与边界属 **Tool Registry**（能用什么）；
"最近发生了什么 / 有哪些 artifact"属 **Conversation State**；"这轮回答基于什么材料"属 **Evidence**。
