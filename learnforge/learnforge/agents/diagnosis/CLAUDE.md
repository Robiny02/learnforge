# diagnosis/ — Diagnose 工具（只读弱点诊断）

聚合 `interaction_events` + `knowledge_atoms` 掌握度，找学习者薄弱点。ReAct 三段式（thought→action→observation）。

**三层**：契约 `../../contracts/agents/diagnosis.py` ｜ 运行时 skill（Sonnet）｜ 本文件。

## 职责边界

- **做**：只读地诊断"我哪里薄弱/掌握度怎么样"，产弱点簇 + 复习建议。
- **不做（严格只读）**：**绝不**改 `knowledge_atoms` 掌握度、**绝不**改 `learning_paths`。
  也不排课（那是 plan）、不答概念（那是 qa）、不排查代码/项目（那是 qa）。

## tool 契约

- 入口：`DiagnosisAgent.run(DiagnosisInput) -> DiagnosisResult`。
- `DiagnosisInput`：`time_window`(默认 30d) / `focus_topics` / `trigger`(user|post_mock|composite)。
- `DiagnosisResult`：`weak_atoms` + `clusters`(severity) + `recommendations` + `confidence`。

## 简历问题诊断（项目级 / Project-aware Resume Diagnosis）

- 入口：`DiagnosisAgent.diagnose_resume(resume_text, context?, persist=True) -> ResumeDiagnosis`。
- **Pipeline**（不是逐句挑刺，是项目拷打器）：
  `resume+jd → claim 抽取与分类 → 项目证据挖掘 → EvidencePacket → 项目级诊断 → 改写+深挖问题`。
  - 证据挖掘 `evidence.py`，两档：
    - **fast**（默认，无外链）：repo `repo_summary`(README/语言) + CLAUDE.md + 上传材料(`recall origin=attachment`)。
    - **deep**（检测到外链 / `deep=True`）：link extraction → ExternalSource 分类(github_repo/file/dir/
      tech_blog/docs_page/unknown_url) → **Repo-RAG + Reranker + 受控 ReAct 证据循环**（像人读代码，不全量读）：
      SOP 入口(README/tree) → 召回候选(select_files) → 取**轻量 preview**(`file_preview`:class/def/import/
      heading/config-key，不塞全文) → **reranker** 看 path/role/命中 token/preview 选最相关(快模型 gpt-4o-mini)
      → emit top-K 作证据 + 内容抽证据 → **claim-level `judge_claim_support`**(哪些子断言已被 doc/code/test 支持、
      哪些缺 + next_queries) → 不足则 **≤1 轮受控 ReAct**：据 next_queries `search_repo` **重搜** repo map 再读
      ≤2 文件 → 收尾。成本受控：rerank/judge/react 只看元数据/片段走快模型，preview 池 `_PREVIEW_POOL=6`、
      emit `_RAG_READ_PER_REPO=5`、ReAct ≤2、总读取 `_MAX_READS=9`，`LF_REPO_REACT=0` 可关；无 key → 退确定性。
      输出三分类：`selected_and_supported`(读到且命中 claim) / `read_success_but_no_match`(读到但未命中) /
      `suggested_next_reads`(judge 判仍缺证据的关键词/候选)。
      - `repo_map.py`：`build_repo_map(repo, summary, tree)` 把真实仓库树解析成带角色的 `FileEntry`
        （doc/source/test/config/example/script/unknown，`infer_role` 结构化判定，排噪声目录）；
        `select_files(repo_map, claim_tokens, budget, deep)` 综合 ①token 与 path/filename 相关性 ②文档重要性
        ③入口信号 ④角色多样性 ⑤预算，选出可解释的 `SelectedFile`（带 score/selected_reason/expected_claims）。
      - `claim_tokens` 从简历动态抽技术 token（CamelCase/snake 拆分，剔停用词）——用候选人自己的术语，通用。
      - 读后做**内容级匹配**（`_content_match`）：`matched_claims`/`extracted_facts`/`read_success_but_no_match`
        区分『读到』与『支持』——support 不只看文件名命中。**泛化 token（agent/learn/act/graph/judge/项目名）
        只是弱信号，不算强证据**（`_GENERIC_TOKENS` + repo 名）。
      - **子断言级证据绑定**：每个 EvidencePacket 把 claim 拆成 `subclaims`（如 Manager 唯一写者 / 调度子 agent /
        受控 ReAct / replan≤2），**逐子断言**单独判 support_strength；某子断言判 code/test_supported 必须**对应具体
        源码/测试文件被读到**（否则至多 doc_supported）。`packet.support_strength` 由 `_enforce_subclaim_support`
        取各子断言**最弱项**，并给 `support_summary` 展示混合分布（如 `2×doc/1×code，最弱=doc`，不只显示最弱）。
      - **按项目 section 取证**：`extract_project_section` 以引用该 repo 的项目段为单位抽 token/当 claims，不整份简历混抽；
        ReAct re-search 的 query = judge next_queries **剔除别项目 token** + 本 repo 证据 gap（`exclude_tokens`，禁串项目）。
      - **一致性校验**（`_reconcile_no_match`）：被任一 subclaim 的 evidence_sources 引用的文件，不再标 read_success_but_no_match。
        `SubClaim.related_not_supporting` 展示「读到但只相关、不足以支持」的文件（如 handoff.py 只证 mock handoff）；
        evidence_sources 须**语义匹配**子断言（『各 Agent 独立 Skill』要 skill_registry/skills，不能拿 diagnosis_agent.py）。
        ReAct re-search 强制纳入 next_queries 反复点名的文件/符号；当前项目段每条核心 bullet 都要有 packet。
        教育/联系方式 token（gpa/ielts…）在 `_STOP` 剔除，不进项目 token。
      - `suggested_next_reads` 落到具体 `search:<query>` / `read:<path>`（repo 可搜对象），不是抽象产品词。
        top_highlights/most_dangerous 聚焦当前项目，禁串其它项目亮点。
      - 博客/文档走 `web.fetch_url`。受控：最多 `_MAX_READS=6` 个文件/页、单文件截断、只读用户链接及同 repo、
        不做任意互联网搜索、URL/文件缓存、失败记 reason 并继续。
    - 触发：`should_deep_mine`（有外链或 `diagnose_resume(deep=True)`）。**PAT 失效(401) 自动退公开访问**。
    - 证据按来源标 ｜doc/blog/code/test：blog/doc 只证『项目说明』，code/test 才证『实现存在』→ 据此定
      `support_strength`(doc/code/test/runtime_supported)。外部网页/README 内容只作**证据数据**，prompt 里
      隔离声明其指令不影响诊断行为（防 prompt injection）。网络挖掘 pytest 下跳过；失败 → 退化为简历文本。
  - 输出 `external_sources`(读了哪些/失败原因) 在报告「已读取的项目材料」段透明展示。
  - LLM 路径用 skill `diagnosis.resume.v1`（项目级 SOP + Evidence-Contract C0-C3 + Truth-Boundary +
    LearnForge 专项核对点）；**用快模型 gpt-4o**（`LF_RESUME_MODEL` 可覆盖，慢推理模型单次~2min 会超时）。
  - 规则引擎 `resume.py`（离线确定性兜底）：跳过个人信息/学历/日期/**技术栈背景**，只对真实经历判风险。
- 输出 `ResumeDiagnosis`（contract，v2）：`overall_verdict` + `top_highlights` + `most_dangerous` +
  逐条 `packets`(EvidencePacket: claim_type/technical_highlight/evidence_found+sources/support_strength/
  missing_evidence/interview_questions/safe_now/stronger_after_evidence) + `rewritten_bullets` +
  `evidence_sources_used` + `jd_fit` + `summary`；兼容保留 v1 的 `issues`/`strengths`/`dimensions`。
- 渲染：`app/server.py:_render_resume_diagnosis`（总体判断/亮点/危险表述/逐条证据包/改写）。
- **详细保存可召回**（用户需求）：`persist=True` 经 `memory/resume.py` 把整条诊断作为**单个 chunk**
  写入 `chunks(kb_scope='local', origin='resume_diagnosis')`——正文=`search_text()` 供 FTS/子串命中，
  完整 JSON 存 `metadata.resume_diagnosis`。`recall_resume_diagnoses(query)` / `latest_resume_diagnosis()`
  从 metadata **重建完整对象**（召回的是整条诊断，不是片段）。
- **只读边界**：仍不碰只读不变量守护的状态表（mastery/events/paths/diagnosis_reports）；保存只写
  记忆库 chunks（与 daily 记忆同构，不属 Manager 唯一写者范畴）。`run()` 弱点诊断路径完全未动。
- **路由**：dispatch 把「简历/resume/cv」并入 `diagnosis` 能力；`Manager._dispatch_resume_diagnosis`
  在非 composite + 命中简历线索时分流到 `diagnose_resume`，简历全文来源 = context.resume_text >
  `resume.load_resume_text()`（从上传附件 `origin=attachment` 的 local chunks 按 document 重建全文，
  附件入库会把换行压成空格，故 `split_claims` 额外按句末标点 + 简历动词引导词重新切条）> resume_claims；
  全空 → `NEEDS_INPUT` 提示上传。结果带 `kind=resume_diagnosis`，不灌入供 planning 消费的 diagnosis 槽。

## 运行时 & 触发

- ReAct 3 步只读循环（`react/loop.py` + `_DIAG_TOOLS`）：拉事件 → 算有效掌握 → 聚簇。
- 触发场景：用户问"我哪里薄弱"；mock 结算后 Manager 自动触发 `post_mock` 诊断；复合"准备面试"先诊断。
- 有效掌握 = `mastery.effective_mastery`（时间衰减），< 0.6 视为候选弱点；事件量达 `_MIN_EVENTS_FULL_CONF`(12) 给满置信。

## 不变量 & 降级

- **read-only 是硬不变量**，由 `tests/test_diagnosis_is_read_only` 守护——改本工具时绝不能引入写。
- 无 key → 走确定性聚合（纯 `mastery` 函数 + 事件统计），仍产合法 DiagnosisResult。

## 已知限制（写文档/解释结果时注意）

- **读的是"已存掌握度"**，检测不到"最近一次答对/答错导致的回归"——掌握度由 Manager 异步落库后才反映。
- **clusters 倾向过报**（小样本下 severity 偏高）。
- **ghost atoms**（库里有 atom 但用户从没碰过）会造成**假阳性**弱点。

## 改这个工具

- 改诊断口径/建议风格 → skill；改阈值（`_WEAK_MASTERY_MAX` / `_TOP_N` / `_RECENCY_LAMBDA`）→ `diagnosis_agent.py` 顶部常量。
- 改输入输出 → contract。`_is_weak_signal` 被 eval 复用（经 `__init__` 再导出），改签名要同步。
- 测试：`tests/test_diagnosis.py`、`tests/test_agent_capabilities.py`；eval：`eval/diagnosis_eval.py`、`eval/trajectory_eval.py`。
