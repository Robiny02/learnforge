# evidence/ — Evidence 研究工具（统一只读证据 worker）

`EvidenceResearchAgent` 是一个**临时隔离上下文**的只读证据 worker：由 Manager 调用（`agent.evidence`），
读取 resume / repo / file / attachment 等不同 `source_type` 的材料，筛证据、总结，回一个结构化
`EvidencePacket` —— **不把完整中间上下文/原始文件内容塞回 Manager / Conversation State**。

**三层**：契约 `../../contracts/agents/evidence.py` ｜ 运行时 skill `evidence.research.v1`（Haiku，只读）｜ 本文件。

## 职责边界

- **做**：在隔离上下文里调底层只读工具（`file.read` / `repo.search` / `attachment.recall` /
  `resume.recall` / `retrieval.search`），客观筛证据，产 `EvidencePacket`
  （summary / evidence_refs / source_refs / confidence / missing_info / warnings）。
- **不做（严格只读）**：**绝不**调 `file.write` / `file.edit` / `repository.write` / exec；
  不臆测、不带情绪、不被 raw chat 污染；不把原始全文回灌上游（只回片段 + 指针）。

## 为什么先做一个统一 worker（不过早拆分）

第一阶段**不**把读 repo / 读简历 / 读文件拆成多个 agent。先用一个统一 `EvidenceResearchAgent` 建立
扩展点（contract + 只读权限边界 + Manager 调用面）。后续若 repo 分析 / 简历分析 / 文档 OCR 复杂到值得
独立调参，再拆成 `RepoResearchAgent` / `ResumeResearchAgent` / `DocumentResearchAgent`，复用同一 `EvidencePacket` 契约。

## 工具与权限

- 底层工具登记在 `../../tools/registry.py`（`file.*` / `repo.search` / `attachment.recall` / `resume.recall`），
  handler 在 `../../tools/files.py` 与 `../../tools/evidence_sources.py`。
- skill 只声明 READ 工具；`file.write` / `file.edit` 虽在注册表（owner=evidence，未来归宿），但本 skill
  **不声明** → 运行期 `require_tool` 拒绝。即便 handler 被直接调用，`file.write/edit` 也默认 dry-run。
- 本期**不引入** shell/exec：注册表里没有这类工具。

## 降级（"链路永远通"）

- 有 `OPENROUTER_API_KEY` → `llm_structured` 客观总结成 `packet.summary`；
- 无 key / LLM 失败 → 确定性兜底：用证据片段拼客观 summary，仍产合法 `EvidencePacket`。
- 单个 source 失败/越权 → 记进 `source_refs.status` / `warnings`，不崩、不写。

## Manager 怎么用

`ManagerAgent.gather_evidence(EvidenceRequest) -> EvidencePacket`（经 `require_tool("agent.evidence")`）。
拿到 packet 后可用 `packet.artifact_text()` 作为**只读 artifact 摘要**注入 diagnosis 的 context
（`context["evidence_artifact"]`），让 Diagnosis 基于结构化证据做客观诊断，而不是吞原始文件/聊天。

## 改这个工具

- 改读哪些 source / 片段长度 → `evidence_agent.py`；改证据形状 → contract；改口径 → skill。
- 测试：`../../../tests/test_file_tools_and_evidence.py`。
