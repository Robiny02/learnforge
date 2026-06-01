# LearnForge 评测机制设计

> 目标:对 13-agent 系统做三层评测——**单 agent / 多轮 / 整体系统**——每层都给出
> 测试样例构造法、评测指标、独立基线与通过门槛。设计的第一原则是
> **反循环**(见 `memory/eval-circularity-diagnosis.md`):任何"看起来满分"的结果,
> 若 gold 由实现规则推导而来,只算**规则回归**,不算**真实能力**。

---

## 0. 三层总览

| 层 | 测什么 | 单位 | 已有 | 本设计新增 |
|---|---|---|---|---|
| **L1 单 agent** | 单个 agent 的输出质量 | 指标(可调) | diagnosis / retrieval / semantic / memory | qa / planning / judge / strategist / coach |
| **L2 多轮** | 一整场 mock 面试 episode 的对话级表现 | 指标(episode 级) | — | `mock_episode_eval` |
| **L3 整体系统** | 端到端任务达成 + 系统不变量 | 不变量(布尔) + 任务达成(指标) | trajectory(不变量) | `system_task_eval`(任务达成质量) |

所有评测**全程离线**(无 `ANTHROPIC_API_KEY`,走确定性兜底),保证可复现;有 key 时可选重跑取真实质量(见 §6)。

---

## 1. 反循环协议(贯穿三层,先立规矩)

每条评测都要标注它属于哪一类,报告时分开统计:

1. **REGRESSION(规则回归)**:gold 与实现规则同源,只证明"代码自洽"。可以有,但不许当能力证据。
2. **CAPABILITY(真实能力)**:gold 来自**独立的隐变量生成模型**,与 agent 的打分/排序规则无关。

落实手段:
- **隐技能生成数据**:每个模拟用户有一个 latent skill 向量(per topic ∈ [0,1])。事件/答案的好坏从 latent 采样(+噪声)。gold 弱点 = latent 低的 topic,**不是** agent 算出来的弱点。
- **多个互相独立的基线**:每个指标配 ≥2 个规则可与 gold 冲突的基线(如 `recent_error`、`topic_error_count`、`fixed_difficulty`、`keyword_only`)。**如实报告 agent 输给基线的情况**。
- **脏数据场景**:ghost atom(引用 DB 不存在的 atom)、冲突证据、空答案、全噪声答案。
- **校准无关的成对指标**:当绝对 gold 不可靠时,用 pairwise/ranking(强答案分 > 弱答案分),绕开绝对刻度。
- **留出泛化集**:测试样例不与 prompt 共同设计;用改写题(paraphrase)单独跑一遍,看是否掉点。

---

## 2. L1 — 单 agent 评测

新增 5 个 agent。每个给:数据构造、指标、基线、门槛。

### 2.1 QAAgent(Router→Retrieval→Synthesizer→Verifier)
`learnforge/eval/qa_eval.py` + `golden/qa.json`

**样例构造**(每题独立建库灌入 chunks):
- A 类:KB 内有充分证据。
- B 类:**KB 内无证据**(应降断言,verdict=uncertain,不编引用)。
- C 类:证据**过时/冲突**(应标 unverified 或降强度)。

**指标**:
| 指标 | 定义 | 反循环点 |
|---|---|---|
| answer key-point Recall | 答案命中 gold 要点数 / gold 要点总数 | gold 要点独立标注,非 synthesizer 产出 |
| citation precision | 引用的 chunk_id ∈ 实际召回集 的比例 | 抓"编造引用" |
| hallucination rate | 无证据支撑的断句数 / 总断句 | B 类必测 |
| verdict accuracy | Verifier 判定 vs gold(B/C 类应非 verified) | 测"无证据降断言强度" |
| route accuracy | Router 选的 method/scope vs gold | 分类指标 |

**基线**:`keyword_only_retrieval`(只关键词召回)、`always_verified`(永远判 verified——故意烂,用来暴露 Verifier 的真实校准价值)。
**门槛**:key-point Recall@? ≥ 0.7;citation precision ≥ 0.9;B 类 hallucination ≤ 0.1。

### 2.2 PlanningAgent(generate / modify,产 PathDiff)
`learnforge/eval/planning_eval.py` + `golden/planning.json`

**样例构造**:给定 (弱点 atom 集 + 独立的先修依赖图 + deadline/每日容量)。先修图**独立于** planner 的排序启发式。

**指标**:
| 指标 | 定义 |
|---|---|
| weak-point coverage Recall | PathDiff.add 覆盖的弱点 atom / 应覆盖总数 |
| prereq-order violation rate | 违反"先修在前"的相邻对比例(用独立先修图判) |
| deadline-fit | 排程是否塞得进 days × 每日容量(布尔/比例) |
| diff minimality(modify) | 是否只动相关项、未churn无关项 |
| rationale-present | 每个 diff 是否带理由 |

**基线**:`dump_all_weak`(把所有弱点按任意序堆进去)——planning 必须在 order-violation 与 deadline-fit 上**显著优于**它,否则 planning 无增量价值。
**门槛**:coverage ≥ 0.8;order-violation ≤ 0.1;deadline-fit = 1.0。

### 2.3 JudgeAgent(单轮 0-5 打分)
`learnforge/eval/judge_eval.py` + `golden/mock_answers.json`

**样例构造**:同一题配 (strong / medium / weak) 三档参考答案,每档由人工标注 gold 分与 missed_points。

**指标**(优先成对,绕开绝对校准):
| 指标 | 定义 | 类型 |
|---|---|---|
| pairwise ranking acc | strong 分 > weak 分 的对比例 | **CAPABILITY**(校准无关) |
| per-dim Spearman ρ | Judge 各维分 vs gold 分的秩相关 | CAPABILITY |
| per-dim MAE | 绝对误差 | REGRESSION(刻度敏感) |
| missed_points Recall | 命中 gold 漏点比例 | CAPABILITY |

**门槛**:pairwise ≥ 0.9(强必须压过弱);ρ ≥ 0.6;missed_points Recall ≥ 0.6。

### 2.4 StrategistAgent(下一步动作决策)
`learnforge/eval/strategist_eval.py`

**样例构造**:构造 score_history 轨迹 + 可选 user_interrupt → gold action。
- 连续高分 → ESCALATE 难度
- 连续低分 → DEESCALATE 或 SWITCH_TOPIC
- interrupt="结束" → END;"换话题" → SWITCH_TOPIC

**指标**:action accuracy(多分类)、interrupt-honor rate(中断必须被尊重)。
**基线**:`always_continue`、`random_action`。Strategist 必须在 action acc 上压过两者。
**门槛**:action acc ≥ 0.8;interrupt-honor = 1.0。

### 2.5 CoachAgent(终场复盘)
`learnforge/eval/coach_eval.py`

**样例构造**:给定 turn_scores 序列(含若干低分轮)→ gold 弱点 topic 集。

**指标**:
| 指标 | 定义 |
|---|---|
| weakness Recall | 命中低分轮所属 topic 比例 |
| **evidence-grounding rate** | 每条 weakness 是否都引了**真实存在**的轮次(Weakness.evidence) |
| fabricated-strength rate | strengths 是否引用了实际不存在的高分(应为 0) |

**门槛**:weakness Recall ≥ 0.7;evidence-grounding = 1.0;fabricated = 0。

---

## 3. L2 — 多轮 mock 面试 episode 评测(核心新增)

`learnforge/eval/mock_episode_eval.py` + `golden/mock_candidates.json`

这是当前**最大缺口**:`mock_graph.py` 的 S1-S7 状态机从没被作为"一整场对话"评过。

### 3.1 模拟考生(user simulator)——反循环的关键
每个考生 = latent skill 向量 `{topic: skill∈[0,1]}` + 噪声 σ。
驱动 `MockInterviewAgent` 跑完整 episode:`start → answer×N → settle`。
每轮模拟器根据当前题的 topic 的 latent skill **生成对应质量的答案文本**(高 skill→覆盖要点;低 skill→缺漏/空)。
**gold = latent,不是 Judge/Coach 的输出。**

考生档案(test cases):
| 档案 | latent 设定 | 考察点 |
|---|---|---|
| strong-all | 全高 | 难度应上升;弱点应少 |
| weak-one-topic | concurrency 低,其余高 | Coach 必须**只**标 concurrency(精确率) |
| improving | latent 随轮次上升(边练边会) | 测近因/学习曲线 |
| noisy | 高方差 | 分数应跟 latent 均值,鲁棒 |
| adversarial-empty | 答案为空/答非所问 | Judge 低分,Strategist 降难度或结束 |

### 3.2 Episode 级指标
| 指标 | 定义 | 反循环 |
|---|---|---|
| **diagnostic validity** | Coach 弱点 vs latent 低 topic 的 Recall / Precision | gold=latent,CAPABILITY |
| **score validity** | 每轮 Judge 分 与 该题 topic latent 的相关性 ρ | CAPABILITY |
| **adaptivity** | 难度轨迹 与 latent skill 的相关性 | CAPABILITY |
| topic coverage | max_turns 内实际覆盖的目标 topic 比例 | — |
| probe-the-gap rate | Judge 报 missed_points 后,下一题是否追问该缺口 | 测 Interviewer↔Judge 协同 |
| interrupt robustness | 中途注入 interrupt,状态机是否正确 resume/honor | 测 S3/S_PAUSE interrupt/resume |
| termination soundness | episode ≤ max_turns 收敛;settle 只 commit 一次 mastery | 联动 L3 |

### 3.3 基线(adaptivity 专用)
- `fixed_difficulty_3`(永远中等)、`random_difficulty`。
- Strategist 的难度轨迹必须比这两者更贴合 latent(相关性更高),否则自适应无价值。

**门槛**:diagnostic validity Recall ≥ 0.7 且 weak-one-topic 档 Precision ≥ 0.7;score validity ρ ≥ 0.5;adaptivity 相关性 > 两基线。

---

## 4. L3 — 整体系统评测

### 4.1 已有:`trajectory_eval`(系统不变量,保留)
拓扑 DAG、QA→event→Diagnosis 闭环、路径/掌握度写回流、**唯一写者 / 诊断只读 / 复合部分失败不半写**、trace 全链路可拉。这些是**布尔不变量**,继续作为硬门槛(全 PASS 才放行)。

### 4.2 新增:`system_task_eval`(任务达成质量)
`trajectory_eval` 只证明"链路对",不证明"结果好"。补一层:对每个规范用户目标,从**最终状态**用 rubric 判达成度。

| 用户目标 | 任务达成 rubric(查 final state) |
|---|---|
| "诊断我的弱点" | `report.weak_points` 覆盖种入的 latent 弱点(Recall);经 Manager 全链路,非直接调 Diagnosis |
| "准备面试"(复合 §5.6) | 改后路径覆盖被诊断的弱点 **且** 建议(非自动开始)mock;诊断空时跳过 modify 并改建议 mock |
| "制定学习计划到 deadline" | 路径塞得进 deadline **且** 覆盖请求 topic |
| 多意图 / 模糊输入 | Manager replan ≤2 后仍产出合法 plan |

**鲁棒性子集**:malformed 输入、空 KB、ghost atom(见 `memory/learnforge-diagnosis-known-limits.md`)、离线兜底与有 key 的结果一致性(offline parity)。

**反循环**:种子用 latent 生成,gold 弱点≠诊断规则;诊断这条同时跑独立基线 `recent_error`(它能抓"高存量掌握度但近期回归"的盲区——agent 在此**会输**,如实报告)。

---

## 5. 落地结构

```
learnforge/eval/
  qa_eval.py            planning_eval.py
  judge_eval.py         strategist_eval.py      coach_eval.py
  mock_episode_eval.py  (含 user simulator)
  system_task_eval.py
  baselines.py          (recent_error / topic_error_count / keyword_only / fixed_difficulty …)
  scorecard.py          (run_all → 聚合三层记分卡,标注 REGRESSION vs CAPABILITY)
  golden/
    qa.json  planning.json  mock_answers.json  mock_candidates.json
```

- **复用现有模式**:沿用 `CheckResult` / `EvalReport` dataclass 与 `format_report` 风格,`python -m learnforge.eval.<x>` 可独立跑。
- **CI 门禁**:每个 eval 配 `tests/test_eval_<x>.py`,以**门槛**断言(像现有 `test_eval_diagnosis.py`)。不变量类硬失败;指标类按门槛。
- **记分卡**:`python -m learnforge.eval.scorecard` 输出一张表,三层指标 + 每项 REGRESSION/CAPABILITY 标签 + 基线对比,**显式列出 agent 输给基线的项**。

---

## 6. 离线 vs 在线

离线(无 key):所有 agent 走确定性兜底 → 指标可复现,适合 CI 回归。
在线(有 key):同一套 golden 重跑取真实 LLM 质量;Judge/Coach/QA 这类生成质量只有在线才有意义。
报告里必须标注本次是 `offline-stub` 还是 `online-llm`——**不可把 stub 跑分当能力证据**。

---

## 7. 实施优先级

1. **mock_episode_eval + user simulator**(最大缺口,L2 全靠它)。
2. **judge_eval / coach_eval**(mock 质量的基石,且喂给 L2 解释)。
3. **qa_eval / planning_eval**(补齐 L1)。
4. **system_task_eval + baselines + scorecard**(把三层串成一张记分卡)。
5. strategist_eval(较小,可并入 mock)。
