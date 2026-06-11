# Memory / Context 模块更新日志

记录 `memory/` + 会话上下文（`dialogue_turns` / `session_state`）+ Manager 上下文治理的迭代。
这一轮起于一次**对照 Claude Code 的记忆/压缩审计**，落到四项改进（留头锚点、周期性全量重摘、
replan 可见性、借鉴 CC 的附件处理），并顺手修掉一个「LLM 可用时会话 summary 被原始 JSON 污染」的既有缺陷。

> 时间范围：2026-06-10（本轮改动尚未提交，下列按**变更点**组织；提交后可回填哈希）。
> 相关文件：`memory/{context_assembler,base,daily,decay,recall_gate}.py`、
> `storage/repositories.py`(`DialogueTurnRepository`)、`orchestration/manager.py`(`record_turn`/`decide_next`/`_dispatch_impl`)、
> `contracts/{message,attachment}.py`、`intent/dispatch.py`、`config.py`、`eval/memory_eval.py`(§14)。

---

## 总览：分层记忆架构（审计结论 = 整体合理）

```
稳定规则层  MEMORY.md（整段加载，不索引不衰减）→ memory_prefix()  ┐
长期语义记忆 chunks/chunk_vectors（daily/qa/mock/resume_diagnosis）├ 拼装顺序(KV-cache 友好)：
            ·去重(content hash)·衰减(kind 半衰期)·召回闸门(cosine 阈值/margin) │ System→Skill→Constitution→Memory
会话短期记忆 session_state(summary+recent6) + dialogue_turns(逐轮账本)┘ ｜ Retrieved→Session(handoff)→User
```

**对照 Claude Code「压中间、留首尾」**：留尾 ✅（recent 6 / derive 4）、压中间 ✅（溢出折叠）、
salience 锚点 ✅（thread_start/artifact/clarify，比纯位置更聪明）；**留头 ⚠️ 缺失**（首轮目标会被折叠+
`summary[-800:]` 双重丢失）。**工具轨迹隔离 ✅**（ReAct/retrieval trace 只写 `agent_traces`，从不回灌 prompt；
跨 agent 只传结构化结果 + handoff_summary）——这点比 CC 的「截断旧 tool 输出」更干净，但 replan 看不到子工具「为什么」。

本轮即针对上面 ⚠️ 与「replan 可见性」「附件跨轮引用」三处补齐。

---

## Item 1 — origin 头锚点（留头，CC「keep head」等价物）

- **问题**：首个 user 轮（原始诉求）和别的轮一样被折叠进 summary，且 `summary[-800:]` 还会把最旧的从
  summary 头部截掉——**双重丢失**。anchors 里的 `thread_start` 只钉**当前主线**起点，跨主线后原始目标就没了。
- **改法**：`DialogueTurnRepository.derive_context` 新增 `_origin_anchor`——按 `min(seq)` **实时查**首个实质
  （非空）user 轮，钉成 `kind="origin"` 的**永不过期**锚点（不随 scan 窗口滑动丢失），放锚点列表最前。
- **效果**：原始诉求「帮我准备字节后端面试，主攻 redis 和 mysql」无论过多少轮都还在路由窗口里。

## Item 2 — 周期性全量重摘（修复增量折叠的漂移/碎片化）

- **问题**：滚动 summary 是「只增不重折 + `[-800:]` 尾截断」——稳定但长会话会变成**丢了头、顺序乱的碎片袋**。
- **改法**：`record_turn` 在 `active_task` 里加 `fold_count`；每 `SESSION_RESUMMARIZE_EVERY=3` 次折叠，
  调 `_resummarize_session` 把「旧 summary + 新溢出」**重组**成结构化纯文本（目标/已决策/未决项/聊过的主题）。
  **仅 LLM 可用时触发**——离线严格保持纯增量，绝不把旧摘要喂回离线摘要器（避免递归套娃，§14-7）。
- **效果（实测）**：fold 3 触发后 summary 从碎片变成
  `目标：…／已决策：…／聊过：…` 的干净结构化文本。

## Item 3 — replan 看到子工具「为什么」

- **问题**：`decide_next` 给 replan LLM 只喂 `str(r.result)[:100]`（结构化结论），看不到**结论背后的依据**
  （diagnosis 为何信号不足、qa 为何降级）。这是 agent-as-tool 隔离工具轨迹的副作用。
- **改法**：`ResponsePayload` 加 `reason` 字段；`_dispatch_impl` 经 `_reason_for(agent, out)` 给每个子工具
  填一句依据（≤160 字，只读稳定字段）；`decide_next` 把 reason 一并喂给 replan。
- **效果**：replan 现在看到如 `diagnosis: conf=0.45；2簇[redis、tcp]；信号不足`、
  `qa: 降级(无证据→降断言)；核验=uncertain`，而非一行 dict。

## Item 4 — 借鉴 Claude Code 的附件处理

- **CC 思路**：①解析成内容但**留轻量引用**（不每轮重发全文）；②**截断带指针**（告知「用 X 取剩余」，不默默吞）；
  ③**跨轮可引用**（几轮后仍能「总结那个 PDF」）；④文本进证据、图片走 vision。
- **现状已很接近**：`ingest_attachments` 解析→入 `chunks(local)`（document_id + content_hash 去重）→返回轻量
  `manifest`；当前轮 flatten（文本进 `retrieved`、图片走 vision）。**缺的两点已补**：
  - **跨轮引用**：`_select_anchors` 新增 `kind="attachment"` 锚点——上传的 document/image 作为可跨轮引用的关键节点
    进路由窗口（带文件名，如 `【已上传附件：resume.pdf】`）。
  - **截断带指针**：`Attachment.as_evidence` 截断时附「完整内容已入库，可按文件名/document_id 检索取回」。
- 路由窗口（`dispatch.py`）给所有锚点加 kind 标签（原始诉求/已上传附件/主线起点/已有产物/待澄清），让路由器看清早于近窗的节点。

## Item 5 — 修复 §14-7（LLM 模式下会话 summary 被 JSON 污染）

- **发现**：跑 `memory_eval` 带 key 时 **§14-7（会话 summary 无 JSON 套娃）FAIL**——`MEMORY.summarize` 有 key 时
  返回 ` ```json {…"key_facts":…} ``` `，**原始 JSON（围栏/转义引号/key_facts）直接灌进会话 summary 污染上下文**。
  与本轮其它改动无关（8 轮 eval 触发不到周期重摘），但正是「碎片袋污染上下文」的实例。
- **改法**：增量折叠输出过一道 `_summary_to_text`——剥 markdown 围栏 → 解析 JSON → 渲染成可读纯文本（解析失败再去结构噪声兜底）。
- **效果**：`session_recap；用户：问题1 / 回复：回答1 ｜ …`（干净）。**§14-7 在离线与 LLM 两种模式下都通过**，
  `memory_eval` 第一阶段结论从 ❌ 变 **全部通过 ✅**。

---

## 现状速览（一句话）

记忆分层（MEMORY.md 稳定层 + chunks 长期语义 + session_state/dialogue_turns 短期），拼装 KV-cache 友好、
工具轨迹隔离不回灌；本轮补齐 **留头(origin 锚点)** + **周期性结构化重摘** + **replan 看到子工具理由(reason)** +
**CC 式附件跨轮引用/截断指针**，并修掉 LLM 模式下会话 summary 的 JSON 污染（§14-7）。
验证：**离线 309 passed**、`memory_eval` 带 key **§14 全部通过 ✅**（唯一无关失败为缺 `pypdf` 的多模态测试）。

> 两套短期存储（`session_state.recent_messages` 给 Manager 答题、`dialogue_turns` 给意图路由）职责不同、各自受益；
> 若要进一步收敛，可让 `derive_context` 复用 session 的重摘 summary（`_session_summary` 已在读它）。
