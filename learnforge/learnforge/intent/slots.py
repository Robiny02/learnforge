"""纯确定性槽位抽取——无 LLM、可离线、可回归。

意图层的"快路"：从用户消息里抽出类型化槽位（topic / 岗位 / JD / 简历 claim / 难度 /
轮次 / deadline / goal）。规则刻意小而稳，复用 `interview_skill.detect_role_type`
做角色识别、`extract_claims` 做 claim 归一。歧义留给 resolver 的 LLM 层裁决。
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..agents.mock.interview_skill import detect_role_type, extract_claims, role_focus
from ..contracts.intent import MockSlots, PlanSlots

# ---- topic 命中表（从 orchestrator/router 的 _TOPIC_HINTS 移植，作单一来源）----
TOPIC_HINTS = (
    "python", "asyncio", "java", "golang", "go", "rust", "kubernetes", "k8s", "docker",
    "sql", "mysql", "redis", "react", "http", "tcp", "system design", "rag", "agent",
    "transformer", "llm", "embedding", "rerank", "向量", "检索", "排序", "推荐",
    "kafka", "spark", "flink", "elasticsearch", "es", "mongodb", "nginx", "grpc", "gpu",
    "并发编程", "并发", "网络", "算法", "数据结构", "系统设计", "操作系统", "数据库",
    "分布式", "微服务", "多模态", "强化学习", "微调", "预训练", "缓存", "消息队列",
)

# deadline 正则（从 _DEADLINE_RE 移植）。
DEADLINE_RE = re.compile(
    r"(\d+)\s*(个月|周|天|月|days?|weeks?|months?)", re.IGNORECASE
)

# 难度词 → 1-5。命中"难"加、"简单"减，显式数字优先。
_HARDER = ("难一点", "更难", "再难", "加大", "难度高", "高难", "hard", "harder", "挑战")
_EASIER = ("简单", "容易", "降低", "降一点", "基础", "入门", "easy", "easier", "轻松")
_DIFF_NUM_RE = re.compile(r"难度\s*([1-5])|([1-5])\s*档")

# 轮次/时长 → max_turns。"练 5 道/题/轮" 直接计数；"20 分钟" 粗估每题 ~4 分钟。
_COUNT_RE = re.compile(r"(\d+)\s*(道|题|轮|个问题|questions?)", re.IGNORECASE)
_DURATION_RE = re.compile(r"(\d+)\s*(分钟|min|minutes?)", re.IGNORECASE)

# 面试岗位锚点：在这些引导词后捕获一小段作为 target_role。
_ROLE_LEAD_RE = re.compile(
    r"(?:我?面|面试|应聘|投递?|应征|目标岗位是?|岗位[:：]?|方向是?|准备)\s*"
    r"([一-龥A-Za-z0-9 /\-]{2,20}?(?:实习|岗位?|工程师|算法|开发|研究员|engineer|intern)?)"
    r"(?:的?(?:面试|岗位|实习|工作)|[，,。.!！?？]|$)"
)
# 拿简历/项目拷打的意图线索。
RESUME_GRILL_CUES = (
    "拷打", "拿我", "按我的", "针对我", "我的简历", "我的项目", "结合简历",
    "结合我的", "用我的简历", "根据我的", "我的经历",
)
_RESUME_WORDS = ("简历", "项目", "经历", "做过", "我负责", "我参与")
# JD 粘贴线索（出现这些段落标记，或文本足够长 → 当 JD 原文）。
_JD_MARKERS = ("岗位职责", "任职要求", "工作职责", "职位描述", "我们希望", "加分项",
               "responsibilities", "requirements", "qualifications", "你将")

# 面试触发词（用于识别"想开面试"）。
MOCK_START_CUES = (
    "模拟面试", "mock", "出题", "考我", "考考我", "面试我", "面我", "刷题",
    "来一场面试", "来场面试", "开始面试", "练习面试", "面试练习", "面试我",
)
# 剥掉这些触发词得到 topic（从 server._extract_mock_topic 移植，作单一来源）。
_TOPIC_STRIP = (
    "开始模拟面试", "模拟面试", "开始面试", "练习面试", "面试练习",
    "来一场面试", "来场面试", "面试我", "面我", "考考我", "考我", "出题", "刷题", "练题",
    "mock", "面试", "帮我", "请", "我要", "我想", "想", "来一场", "来场", "给我来",
)

# 岗位名词白名单：捕获到的短语须含其一（或能推断出 role_type）才算真 target_role，
# 避免把"试我 Python 并发""面试"这类误当岗位。
_ROLE_NOUNS = (
    "实习", "岗", "工程师", "算法", "开发", "研究员", "engineer", "intern",
    "后端", "前端", "测试", "运维", "数据", "机器学习", "深度学习", "nlp", "cv",
    "推荐", "搜索", "全栈", "架构", "大模型", "ai", "实习生", "校招", "社招",
)


def _norm(text: Optional[str]) -> str:
    return (text or "").strip()


# 剥词后若只剩这些填充词/碎片，则视为无实质主题（返回 None，退回通用题）。
_TOPIC_JUNK = ("道", "题", "轮", "个", "难", "简单", "一点", "方向", "学习", "计划",
               "规划", "复习", "一场", "一道", "还没", "好", "但", "的", "些", "来")


def extract_topic(text: str) -> Optional[str]:
    """先查命中表（最稳），否则剥掉触发词/计数/难度碎片留下主题；无实质内容返回 None。"""
    low = text.lower()
    for hint in TOPIC_HINTS:
        if hint in low:
            return hint
    stripped = text
    for cue in _TOPIC_STRIP:
        stripped = stripped.replace(cue, "")
    # 去掉"5道/20分钟/难度3"等配置碎片，免得当成主题。
    for rx in (_COUNT_RE, _DURATION_RE, _DIFF_NUM_RE):
        stripped = rx.sub("", stripped)
    for lead in _HARDER + _EASIER:
        stripped = stripped.replace(lead, "")
    stripped = re.sub(r"\d+", "", stripped).strip(" ：:，,。.、-—")
    # 仅剩填充词/碎片 → 无主题。
    residue = stripped
    for j in _TOPIC_JUNK:
        residue = residue.replace(j, "")
    if len(residue.strip()) < 2:
        return None
    return stripped or None


def extract_difficulty(text: str) -> Optional[int]:
    """难度词/数字 → 1-5。显式数字优先；否则难→4、简单→2。"""
    m = _DIFF_NUM_RE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    low = text.lower()
    if any(c in low for c in _HARDER):
        return 4
    if any(c in low for c in _EASIER):
        return 2
    return None


def extract_max_turns(text: str) -> Optional[int]:
    """计数词直接用；时长按每题 ~4 分钟粗估；夹到 [1, 30]。"""
    m = _COUNT_RE.search(text)
    if m:
        return max(1, min(30, int(m.group(1))))
    d = _DURATION_RE.search(text)
    if d:
        return max(1, min(30, round(int(d.group(1)) / 4)))
    return None


def extract_target_role(text: str) -> Optional[str]:
    """从"我面 RAG 算法实习/应聘后端岗"等捕获目标岗位短语；去掉尾部助词。

    保守：捕获短语须含岗位名词（或能从中推断 role_type）才算真岗位，否则返回 None，
    避免"面试我 Python 并发"里把"试我 Python 并发"误当岗位。
    """
    m = _ROLE_LEAD_RE.search(text)
    if not m:
        return None
    role = m.group(1).strip(" 的了吧呢啊，,。.")
    if len(role) < 2 or role in ("面试", "岗位", "实习", "工作"):
        return None
    low = role.lower()
    if any(n in low for n in _ROLE_NOUNS):
        return role
    if detect_role_type(target_role=role):  # 含 rag/agent 等方向词也算
        return role
    return None


def looks_like_jd(text: str) -> bool:
    """是否像粘贴的 JD 原文：含段落标记，或够长且含岗位线索。"""
    low = text.lower()
    if any(m in low for m in _JD_MARKERS):
        return True
    return len(text) >= 120 and any(c in low for c in ("岗位", "职责", "要求", "经验"))


def wants_resume_grill(text: str) -> bool:
    """是否要求拿简历/项目针对性拷打。"""
    low = text.lower()
    return any(c in low for c in RESUME_GRILL_CUES)


def mentions_resume(text: str) -> bool:
    """是否提到简历/项目（用于判断是否该索要材料）。"""
    return any(w in text for w in _RESUME_WORDS)


def has_mock_signal(text: str) -> bool:
    """无 mock 关键词时，仍能从面试**信号**判出想面试：拷打 / 真岗位锚点 /
    粘 JD / 出题配置（N 道题 + 难度）。让"我面 RAG 实习，拿我项目拷打我"不漏判。"""
    if wants_resume_grill(text) or extract_target_role(text) or looks_like_jd(text):
        return True
    has_q = ("题" in text) or ("面" in text)
    return has_q and (extract_max_turns(text) is not None or extract_difficulty(text) is not None)


def has_plan_signal(text: str) -> bool:
    """无 planning 关键词时，deadline + 实质目标 → 仍倾向规划（如"两周内补齐 redis"）。"""
    return DEADLINE_RE.search(text) is not None and len(text) >= 5


def build_mock_slots(text: str, profile_claims: Optional[List[str]] = None) -> MockSlots:
    """把一条开场消息抽成 MockSlots（含角色推断与 focus 回填）。

    profile_claims：用户档案里已有的简历/项目要点（库中已存的材料），供拷打锚定。
    """
    text = _norm(text)
    jd = text if looks_like_jd(text) else None
    target_role = extract_target_role(text)
    role_type = detect_role_type(jd_text=jd, target_role=target_role or text)
    claims: List[str] = []
    if profile_claims and (wants_resume_grill(text) or mentions_resume(text)):
        claims = extract_claims(resume_claims=list(profile_claims))
    slots = MockSlots(
        topic=extract_topic(text) if not jd else None,
        target_role=target_role,
        jd_text=jd,
        role_type=role_type,
        resume_claims=claims,
        focus=role_focus(role_type),
        difficulty=extract_difficulty(text),
        max_turns=extract_max_turns(text),
    )
    return slots


# 规划触发/填充词：剥掉后若无实质内容，则 goal 视为缺失（→ 触发澄清）。
_PLAN_STRIP = (
    "帮我", "请", "给我", "我要", "我想", "想", "一下", "一个", "做个", "做一个",
    "制定", "安排", "规划", "计划", "学习", "复习", "路径", "路线", "roadmap",
    "plan", "schedule", "的",
)


# ---- 多轮承接/切换线索（跨能力通用，供意图层借用上文）----
# 承接：本句在延续/追加上一轮（无自身能力关键词时，倾向沿用上一轮能力）。
CONTINUATION_CUES = (
    "再", "还", "又", "接着", "那就", "继续", "还要", "加上", "顺便再", "然后",
    "接下来", "并且", "另外", "顺带", "再来", "也要", "顺便",
)
# 切换：本句在切换/改换主题或方向（跨能力——计划换主题、面试换 topic 都算）。
# 注意：聊聊/说说/讲讲 是"解释类"动词（→ 自包含问答），不算能力切换，故不放这里。
SWITCH_CUES = (
    "换成", "换到", "换个", "换一个", "改成", "改为",
    "转到", "不聊", "换", "切到", "改下", "改一下",
)

# 生成类任务（八股之外另一种高频"跳脱"插入）：生成 md / 学习笔记 / 文档 / 报告。
_NOTE_FORMAT = ("笔记", "md", "markdown", "文档", "note", "报告", "总结成", "整理成")
_NOTE_VERB = ("生成", "写", "做一", "做个", "做份", "出一", "出份", "给我一", "给我份",
              "整理", "总结", "导出", "汇总", "记一份")

# 自包含的"强问句"模式：含其一即视为独立八股问题（不该被多轮上下文劫持）。
_STRONG_Q = ("什么是", "是什么", "为什么", "怎么实现", "如何实现", "怎么做", "怎么办到",
             "的区别", "区别是", "有什么区别", "的原理", "原理是", "解释一下", "介绍一下",
             "是啥", "啥意思", "什么意思", "怎么理解")


def is_note_request(text: str) -> bool:
    """是否在让 AI 生成 md/笔记/文档/报告（生成动词 + 文档格式词）。"""
    low = (text or "").lower()
    return any(f in low for f in _NOTE_FORMAT) and any(v in low for v in _NOTE_VERB)


def is_self_contained(text: str) -> bool:
    """是否是一条自包含的请求（独立八股问句 / 生成任务）——这类不应借用多轮上下文，
    直接按本句意图答即可（QA / 生成笔记），也不应污染正在进行的主线意图。"""
    return is_note_request(text) or any(p in (text or "") for p in _STRONG_Q)


def extract_known_topic(text: str) -> Optional[str]:
    """只认命中表里的主题（可靠，不返回剥词残渣）——供上下文借用注入 topic 槽位。"""
    low = (text or "").lower()
    for hint in TOPIC_HINTS:
        if hint in low:
            return hint
    return None


def is_continuation(text: str) -> bool:
    """是否在延续上一轮（承接词，或"……呢"式省略追问）。"""
    t = (text or "").strip()
    return any(c in t for c in CONTINUATION_CUES) or t.endswith(("呢", "呢?", "呢？"))


def detect_topic_switch(text: str):
    """(是否在切换/指定新主题, 抽出的新主题或 None)。跨能力通用，不限面试。"""
    t = (text or "").strip()
    switched = (any(c in t for c in SWITCH_CUES)
                or t.endswith(("呢", "呢?", "呢？")))
    return switched, extract_known_topic(t)


def build_plan_slots(text: str) -> PlanSlots:
    """规划槽位：deadline 正则 + topic 命中 + 实质 goal（纯触发词 → goal 缺失）。"""
    text = _norm(text)
    m = DEADLINE_RE.search(text)
    deadline = f"{m.group(1)}{m.group(2)}" if m else None
    topic = extract_topic(text)
    residue = text
    for w in _PLAN_STRIP:
        residue = residue.replace(w, "")
    residue = re.sub(r"\d+", "", residue).strip(" ：:，,。.、-—")
    # 有 deadline 或剥词后仍有实质内容 → 当目标；否则缺目标。
    goal = text if (len(residue) >= 2 or deadline) else None
    return PlanSlots(goal=goal, deadline=deadline, topic=topic)
