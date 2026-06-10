# Diagnose 模块更新日志

记录 `agents/diagnosis/` 的迭代。Diagnose 工具原本只做**学习弱点诊断**（聚合 events + 掌握度）；
这一轮新增并打磨了**简历 / 项目级诊断**（`diagnose_resume`），把它从「简历文本审稿器」升级成
「读真实代码仓库、按子断言绑定证据」的**项目拷打器**。

> 时间范围：2026-06-06 ~ 2026-06-09。每条带提交哈希，可 `git show <hash>` 查看。
> 相关文件：`agents/diagnosis/{diagnosis_agent,resume,evidence,repo_map}.py`、
> `contracts/agents/diagnosis.py`、`agents/diagnosis/CLAUDE.md`、`tests/test_resume_diagnosis.py`。

---

## 总览：能力演进路线

```
文本审稿器（逐句挑刺）
  → 项目级 pipeline（claim 抽取 → 证据包 → 改写/追问）
    → 读真实 GitHub 仓库（固定读 README/CLAUDE.md）
      → Repo Map（仓库树解析 + 动态选文件，去硬编码）
        → Repo-RAG + Reranker + 受控 ReAct 证据循环（像人读代码）
          → 子断言级证据绑定（per-subclaim support，证据落到具体文件 + 语义匹配）
            → 按项目 section 隔离 + 强制补读 + 覆盖核心 bullet
```

---

## Phase 0 — 简历诊断地基（2026-06-06）

- **`627d87a` Add resume problem diagnosis**：新增 `DiagnosisAgent.diagnose_resume`，蒸馏自
  llm-intern-skill（overclaim/no_evidence/vague + 安全降级）。详细 `ResumeDiagnosis` schema；
  结果**整条存记忆库可召回**（chunks/local，召回时从 metadata 重建完整对象）。严格只读学习状态。

## Phase 1 — 路由、附件、深度/延迟/PDF（2026-06-08）

- **`4308add` 路由 + 附件自动加载**：dispatch 把「简历/resume/cv」并入 diagnosis；
  Manager 子路由分流到 `diagnose_resume`；从上传附件（`origin=attachment`）自动重建简历全文。
- **`44520a2` 修深度**：LLM 路径静默失败 → 退回浅规则引擎的问题修掉；规则引擎跳过个人信息/学历；
  OCR 抗截断。
- **`8e581d3` skill 强化**：接入 llm-intern-skill 的 Evidence-Contract（C0–C3）+ Truth-Boundary。
- **`1116a06` / `f7a1834` 降延迟**：OCR 换快模型；**简历诊断结构化调用从 claude-sonnet-4.6（~128s）
  换 gpt-4o（~8s）**；前端超时 120s→180s。
- **PDF 读取**：`44520a2` 起处理乱码；Overleaf/LaTeX 中文 PDF 用 **pymupdf + vision OCR** 兜底
  （字体子集无 ToUnicode 时渲染页图识别）。
- **`f9767c9` / `acbc1fb` 修截断**：模型把整篇塞进单字段被 max_tokens 截断 → 约束结构化字段 +
  提高额度；无附件路径走主图时被 `_llm_aggregate` 二次重写截断 → 改为**直接结构化渲染**（共享渲染器）。
- **`418f0b5` 升级为项目级 pipeline**：claim 抽取与分类（架构/实现/指标/贡献/技术栈，技术栈不当风险）→
  证据挖掘 → `EvidencePacket`（证据/强度/缺口/亮点/追问/改写）→ overall_verdict/highlights/rewrites。
- **`37bbb46` / `5ecb890` 输出质量**：改写信息密度更高且保留关键实体；证据强度按**来源**细分
  （doc/code/test/runtime_supported）；架构 claim 不强求性能指标；输出语言跟随简历（中文简历→中文）；
  无 JD 时按「求职意向」评估，不输出 unknown。

## Phase 2 — Repo-RAG：读真实仓库（2026-06-09）

- **`8dede2d` 受控外链取证**：link extraction + ExternalSource 分类（github_repo/file/dir、tech_blog、
  docs_page、unknown_url）；按 claim 找源码/测试；PAT 失效自动退**公开访问**；prompt-injection 隔离。
- **`46efefb` 去硬编码**：删掉写死的文件名/主题映射，改为**从真实仓库树自主发现**（结构信号挑文档 +
  简历技术 token 匹配源码）。对任意项目通用（不止 Agent 系统）。
- **`3faa669` Repo Map**：`build_repo_map` 把仓库树解析成带角色的 `FileEntry`
  （doc/source/test/config/example/script/unknown）；`select_files` 可解释动态选文件
  （token 相关性 + 文档重要性 + 入口信号 + 角色多样性 + 预算）。
- **`899ca56` Repo-RAG + Reranker + 受控 ReAct**：SOP 入口(README/tree) → 召回候选 → reranker
  选最相关（快模型 gpt-4o-mini，只看元数据）→ 读 top + 抽证据 → 判断充分性 → ≤1 轮 ReAct 追读。
  成本受控、缓存、失败降级、`LF_REPO_REACT=0` 可关。
- **`bf2a529` 循环增强**：claim-level `judge_claim_support`（哪些子断言已被 doc/code/test 支持、缺什么 +
  next_queries）；reranker 加 **lightweight preview**（class/def/import/heading/config-key，不塞全文）；
  ReAct 据 next_queries **重搜** repo map；输出三分类 selected_and_supported / read_success_but_no_match /
  suggested_next_reads。

## Phase 3 — 证据精度：子断言级（2026-06-09）

- **`e33f389` 子断言级证据绑定**：每条 claim 拆 `subclaims`（如 Manager 唯一写者 / 调度子 agent /
  受控 ReAct / replan≤2），**逐子断言**判 support_strength；`packet.support_strength` 取最弱项
  （不因某子点有代码就整条 code）；泛化词（agent/learn/act/graph/judge/项目名）只是弱信号；
  top_highlights 聚焦当前项目、禁串项目。
- **`f07aa1d` 修路由**：「分析我的项目 / github 链接」误入**学习弱点诊断** → 修 `looks_like_resume_request`
  覆盖项目分析意图 + dispatcher L0 短路到 diagnosis。
- **`6cb8708` 按项目 section 隔离**：`extract_project_section` 以引用该 repo 的项目段为单位抽 token，
  不整份简历混抽；ReAct query 剔除别项目 token（`exclude_tokens`）；evidence_sources 与 no_match
  一致性校验；`suggested_next_reads` 落到 `search:<query>` / `read:<path>`；`support_summary` 展示混合分布。
- **`55b450c` 收紧**：ReAct 强制补读被反复点名的文件/符号；教育/联系方式 token（gpa/ielts…）剔除；
  evidence_sources 须**语义匹配**子断言（『各 Agent 独立 Skill』要 skill_registry，不能拿 diagnosis_agent.py）；
  `SubClaim.related_not_supporting` 展示「读到但只相关、不足以支持」（如 handoff.py 只证 mock handoff）；
  当前项目段每条核心 bullet 都要有 packet。

---

## 现状速览（一句话）

`diagnose_resume`：检测简历语言/求职意向 → 按**项目 section** 抽 token → 对每个 GitHub repo 走
**Repo-RAG + reranker + ≤1 轮受控 ReAct**（快模型只看元数据/preview）取证 → LLM 产出**子断言级**
`EvidencePacket`（证据落到具体文件、语义匹配、混合支持度）+ 改写 + 追问 → 渲染分区展示
（已读材料 / 逐条证据包 / selected_and_supported / read_but_no_match / suggested_next_reads）。
严格只读学习状态；无 key/失败全程优雅降级；成本受控（读取上限 + 快模型 + 缓存）。

设计细节见 `agents/diagnosis/CLAUDE.md`。
