"""证据约束式面试拷打规则（接入 LLMInternSkill，纯函数，离线可跑）。

把开源 Skill `llm-intern-skill`(MIT) 的核心评审逻辑蒸馏成确定性 Python：
- 角色类型识别（JD/岗位方向 → rag/agent/agentic-rl/...，见 jd-analysis.md）
- 简历 claim 抽取与角色关注点（evidence-contract.md / roles/*.md）
- 回答风险标签 overclaim/no_evidence/vague（truth-boundary.md / evidence-contract.md）
- 危险表达 → 安全降级（truth-boundary.md 的 Common Downgrades）
- 拷打轮次 truth_boundary → technical_depth → jd_deep_dive → scenario（interview-grilling.md）
- Answer Card：dangerous / passable / strong + evidence_needed（answer-cards.md）

设计原则（与上游一致）：**不编造，先诊断后润色；强 claim 必须有证据，识别夸大而非奖励夸大。**

这些函数是 Interviewer/Judge/Coach 的 LLM 提示词依据，同时充当无 API key 时的确定性兜底，
延续 LearnForge "链路永远通" 的不变量。归属见 skill_pack/NOTICE.md。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# --- 角色类型识别（jd-analysis.md / roles/*.md） ---------------------------------
# 取命中关键词最多的角色类型；无信号返回 None（沿用纯主题模式）。
ROLE_KEYWORDS: Dict[str, List[str]] = {
    "rag": ["rag", "检索", "知识库", "召回", "rerank", "重排", "citation", "引用",
            "chunk", "切分", "向量", "幻觉", "hallucinat", "拒答"],
    "agent": ["agent", "智能体", "tool", "工具调用", "function call", "planner",
              "规划", "memory", "记忆", "trace", "workflow", "人审"],
    "agentic-rl": ["agentic rl", "trajectory", "轨迹", "reward", "奖励", "verifier",
                   "grpo", "ppo", "long-horizon", "tool-use rl"],
    "posttraining": ["sft", "dpo", "rlhf", "rlaif", "reward model", "偏好", "对齐",
                     "alignment", "instruction tuning", "指令微调"],
    "pretraining": ["pretrain", "预训练", "mid-training", "data engine", "数据引擎",
                    "tokenizer", "分布式", "scaling", "污染", "contamination"],
    "llm-app": ["llm 应用", "llm app", "structured output", "结构化输出", "prompt 版本",
                "成本", "延迟", "latency", "api 集成"],
    "llm-algorithm": ["transformer", "lora", "微调", "fine-tune", "训练配置", "评测",
                      "推理", "inference"],
    "search-ranking": ["搜索", "排序", "推荐", "ranking", "召回", "粗排", "精排",
                       "query-doc", "ndcg", "mrr", "ctr", "doc 理解", "相关性"],
    "aigc": ["aigc", "生成", "文生图", "图像生成", "视频生成", "内容安全", "人工复核"],
    "multimodal": ["多模态", "multimodal", "vlm", "ocr", "layout", "跨模态", "视觉"],
    "backend-ai": ["后端", "backend", "api", "队列", "queue", "可观测", "observability",
                   "部署", "deploy", "权限"],
}

# 各角色面试官最关注的考察维度（roles/*.md "What Big Companies Care About" 蒸馏）。
ROLE_FOCUS: Dict[str, List[str]] = {
    "rag": ["chunk/embedding/top_k/rerank 的取舍", "citation 与拒答", "评估集与召回/引用指标", "bad case 与权限/时效"],
    "agent": ["tool schema 与参数校验", "trace/重试/状态机", "失败类型与人工复核"],
    "agentic-rl": ["environment 与 reward/verifier 设计", "trajectory 与任务成功率", "GRPO/PPO 训练稳定性"],
    "posttraining": ["SFT/DPO/RLHF 数据与流程", "偏好数据质量", "对齐评估与回归"],
    "pretraining": ["数据引擎与污染检查", "tokenizer/训练配置", "loss/分布式工程"],
    "llm-app": ["结构化输出与 prompt 版本", "bad case 闭环", "成本/延迟权衡"],
    "llm-algorithm": ["训练/微调配置与日志", "评测口径", "推理/优化理解"],
    "search-ranking": ["召回→粗排→精排→rerank 链路", "NDCG/MRR/CTR 口径与 baseline", "DOC 质量与长尾 bad case"],
    "aigc": ["生成链路与质量评估", "内容安全与人工复核", "成本/延迟"],
    "multimodal": ["OCR/layout/VLM 链路", "跨模态检索", "视觉 bad case"],
    "backend-ai": ["API/队列/重试", "可观测与权限", "部署/成本/延迟"],
}

# --- 真实性边界：夸大词 / 含糊词 / 降级映射（truth-boundary.md） -------------------
OVERCLAIM_TERMS: List[str] = [
    "主导", "上线", "生产", "显著提升", "大幅", "sota", "最优", "最佳", "首个",
    "训练大模型", "千万级", "亿级", "百万用户", "行业领先", "完全自动",
]
VAGUE_TERMS: List[str] = [
    "感觉", "差不多", "应该是", "可能", "大概", "好像", "也许", "似乎", "反正", "随便",
]
# (触发词组, 安全降级写法)：命中任一触发词即可建议降级（truth-boundary.md Common Downgrades）。
DOWNGRADE_MAP: List[Dict[str, object]] = [
    {"triggers": ["主导", "负责整个", "独立完成系统"], "safer": "参与模块开发 / 负责某一子模块"},
    {"triggers": ["上线", "生产", "线上业务"], "safer": "完成 demo / 内部试用 / 本地验证"},
    {"triggers": ["提升", "提高", "%", "百分", "倍"], "safer": "基于固定样例对比观察到效果改善（无线上指标不写比例）"},
    {"triggers": ["训练大模型", "从零训练"], "safer": "复现小模型训练 / 做 LoRA 微调 / 调用模型 API"},
    {"triggers": ["agent 自动", "全自动", "自动完成任务"], "safer": "在限定流程内完成工具调用并人工复核"},
    {"triggers": ["优化搜索", "优化排序"], "safer": "梳理 query-doc bad case / 复现 rerank baseline"},
]

# --- 拷打轮次（interview-grilling.md：truth → depth → jd → scenario） ---------------
GRILL_ROUNDS: List[Dict[str, str]] = [
    {"round": "truth_boundary",
     "intent": "确认真实边界：这件事你到底做了哪一部分、谁主导、有什么产物为证。"},
    {"round": "technical_depth",
     "intent": "技术深挖：输入输出、数据、模型、系统、指标，每一步如何证明有效。"},
    {"round": "jd_deep_dive",
     "intent": "对齐目标岗位的硬要求，追问该方向最容易被问穿的点。"},
    {"round": "scenario",
     "intent": "情景题：失败、延迟、质量下降、数据漂移、权限等异常如何处理。"},
]


def _norm(text: Optional[str]) -> str:
    return (text or "").lower()


def detect_role_type(jd_text: Optional[str] = None,
                     target_role: Optional[str] = None) -> Optional[str]:
    """从 JD 原文 + 岗位方向识别角色类型；命中关键词最多者胜，无信号返回 None。"""
    blob = f"{_norm(jd_text)} {_norm(target_role)}"
    if not blob.strip():
        return None
    best: Optional[str] = None
    best_hits = 0
    for role, kws in ROLE_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in blob)
        if hits > best_hits:
            best, best_hits = role, hits
    return best if best_hits > 0 else None


def role_focus(role_type: Optional[str]) -> List[str]:
    """该角色面试官的核心考察维度（供出题/复盘聚焦）。"""
    return ROLE_FOCUS.get(role_type or "", [])


def extract_claims(resume_claims: Sequence[str] = (),
                   projects: Sequence[str] = ()) -> List[str]:
    """归一化候选人 claim 列表（简历要点 + 项目），去空去重，供证据式追问锚定。"""
    seen: List[str] = []
    for c in list(resume_claims) + list(projects):
        c = (c or "").strip()
        if c and c not in seen:
            seen.append(c)
    return seen


def pick_grill_round(turn_index: int) -> Dict[str, str]:
    """按轮次循环选择拷打轮次（第 1 轮先探真实边界，逐步深挖）。"""
    if not GRILL_ROUNDS:
        return {"round": "technical_depth", "intent": ""}
    return GRILL_ROUNDS[turn_index % len(GRILL_ROUNDS)]


def risk_flags_for_answer(user_answer: Optional[str],
                          expected_points: Sequence[str] = ()) -> List[str]:
    """识别回答风险标签（evidence-contract.md / truth-boundary.md）。

    - overclaim：用了夸大/结果性措辞却无量化口径或证据描述。
    - no_evidence：声称做过但没有任何证据词（数据/日志/对比/指标/代码…）。
    - vague：含糊措辞或过短，无法支撑判断。
    """
    answer = (user_answer or "").strip()
    flags: List[str] = []
    if not answer:
        return ["no_evidence", "vague"]
    low = answer.lower()

    evidence_markers = ["指标", "数据", "日志", "对比", "实验", "ndcg", "mrr", "acc",
                        "样例", "case", "baseline", "代码", "复现", "配置", "记录", "report"]
    has_evidence = any(m in low for m in evidence_markers)

    if any(t in low for t in OVERCLAIM_TERMS) and not has_evidence:
        flags.append("overclaim")
    # 声称做过某事却给不出任何证据。
    did_markers = ["我做了", "我负责", "我实现", "我优化", "我提升", "完成了", "实现了"]
    if any(m in answer for m in did_markers) and not has_evidence:
        if "no_evidence" not in flags:
            flags.append("no_evidence")
    if any(t in answer for t in VAGUE_TERMS) or len(answer) < 25:
        flags.append("vague")
    return flags


def downgrade(user_answer: Optional[str]) -> Optional[str]:
    """若回答含夸大表达，给出更安全的降级写法建议；否则 None。"""
    answer = user_answer or ""
    low = answer.lower()
    for rule in DOWNGRADE_MAP:
        triggers = rule["triggers"]  # type: ignore[index]
        if any(t.lower() in low for t in triggers):  # type: ignore[union-attr]
            return str(rule["safer"])
    return None


def build_answer_card(question: str,
                      expected_points: Sequence[str] = (),
                      user_answer: Optional[str] = None,
                      role_type: Optional[str] = None) -> Dict[str, object]:
    """把高风险问题转成 dangerous/passable/strong 三档回答建议（answer-cards.md）。

    确定性兜底版：不编造事实，只给出"如何更安全地表达 + 该补什么证据"的骨架。
    """
    points = [p for p in expected_points if p]
    risks = risk_flags_for_answer(user_answer, points)
    focus = role_focus(role_type)
    why_bits: List[str] = []
    if "overclaim" in risks:
        why_bits.append("措辞偏夸大但缺量化口径/证据")
    if "no_evidence" in risks:
        why_bits.append("声称做过却给不出证据链")
    if "vague" in risks:
        why_bits.append("表达含糊或过短，撑不住追问")
    if not why_bits:
        why_bits.append("考点覆盖不足，易被深挖问穿")

    safer = downgrade(user_answer)
    passable = "先承认边界，只讲真实做过的部分"
    if points:
        passable += "：围绕 " + "、".join(points[:3]) + " 说清输入输出与做法"
    if safer:
        passable += f"；把夸大表达降级为「{safer}」"

    strong = "在 passable 基础上补：固定样例/数据上的对比、bad case 分类、取舍与下一步"
    if focus:
        strong += f"；对齐岗位关注点：{focus[0]}"

    evidence_needed = list(points[:3]) or ["可复现的样例或数据", "对比 baseline", "bad case 记录"]

    return {
        "question": question,
        "why_risky": "；".join(why_bits),
        "dangerous": "继续用结果性/夸大措辞掩盖证据缺口（会被追问问穿）",
        "passable": passable,
        "strong": strong,
        "evidence_needed": evidence_needed,
    }


def grounded_followup(topic: str,
                      difficulty: int,
                      last_question: Optional[str],
                      last_answer: Optional[str],
                      claims: Sequence[str] = (),
                      role_type: Optional[str] = None,
                      turn_index: int = 0) -> Dict[str, object]:
    """无 LLM 兜底：基于上一轮回答 + claim + 角色，生成一条证据式追问。

    返回 {question, expected_points}。优先追问上一轮回答里的风险点；否则按拷打轮次推进。
    """
    rnd = pick_grill_round(turn_index)
    focus = role_focus(role_type)
    risks = risk_flags_for_answer(last_answer) if last_answer else []
    anchor = (claims[0] if claims else None)

    if last_answer and ("overclaim" in risks or "no_evidence" in risks):
        safer = downgrade(last_answer)
        q = (f"你刚才提到的做法，具体到「{topic}」：哪一部分是你亲手做的？"
             "有没有数据/日志/对比/指标能证明有效？")
        if safer:
            q += f"（如果没有线上指标，是不是该表述成「{safer}」？）"
        return {"question": q,
                "expected_points": ["真实负责的边界", "可复现的证据", "无证据时的安全表述"]}

    if anchor:
        q = (f"针对你的经历「{anchor[:40]}」，请把它拆到 {topic} 这个点上深挖："
             f"{rnd['intent']}")
        pts = [f"{topic} 的关键决策", "证据/指标", "取舍与失败case"]
        if focus:
            pts.append(focus[0])
        return {"question": q, "expected_points": pts}

    # 无 claim 锚点：按角色关注点 + 拷打轮次出题。
    target = focus[turn_index % len(focus)] if focus else f"{topic} 的核心原理"
    q = f"（难度{difficulty}）围绕 {topic}：请就「{target}」展开，{rnd['intent']}"
    return {"question": q, "expected_points": [target, "证据/指标", "常见误区"]}
