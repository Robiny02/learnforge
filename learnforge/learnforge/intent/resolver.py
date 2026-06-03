"""意图解析器：自然语言（+上下文）→ 结构化 IntentFrame（三层兜底，可离线）。

层次：
  ① 确定性快路——显式命令 /mock、关键词命中能力、确定性槽位抽取、复合"准备面试"识别；
  ② 不确定时一次 Haiku 结构化增强（无 key 自动跳过）；
  ③ 启发式兜底（关键词优先级，行为稳定可回归）。
并在产出 frame 后过澄清门控（clarify.needs_clarification），缺关键锚点 → 写入一句澄清。

`resolve()` 是纯函数：相同 (message, context) 给相同结论，便于回归。
`resume_pending()` 处理澄清的下一轮：把用户回复填进 pending_slot 后重解析。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from pydantic import BaseModel

from ..contracts.enums import ModelTier
from ..contracts.intent import Capability, IntentFrame, MockAction
from . import slots as S
from .clarify import needs_clarification

# 能力关键词（dict 顺序即优先级：mock > diagnosis > planning）。与历史路由一致。
_CAP_KEYWORDS: Dict[str, tuple] = {
    "mock": ("面试", "mock", "模拟面试", "模拟", "刷题", "出题", "interview", "practice",
             "考我", "考考我", "面试我", "面我"),
    "diagnosis": ("诊断", "弱点", "薄弱", "复盘", "短板", "diagnose", "weakness", "assess"),
    "planning": ("计划", "规划", "学习路径", "路线", "安排", "路径", "plan", "roadmap", "schedule"),
}
# 显式命令：开头 /cmd 直接命中能力。
_COMMANDS: Dict[str, str] = {
    "qa": "qa", "ask": "qa", "plan": "planning", "planning": "planning",
    "diagnose": "diagnosis", "diagnosis": "diagnosis", "mock": "mock", "interview": "mock",
}
_EXPLICIT_RE = re.compile(r"^/(\w+)")

# 复合"准备面试"线索（→ diagnosis 先行，planning 后继；见 §5.6 interview-prep skill）。
_PREP_CUES = ("准备面试", "面试冲刺", "帮我准备", "备战面试", "面试准备")
# 角色式面试意图线索（暗示要按岗位/方向出题 → 缺岗位时澄清）。
_ROLE_INTENT_CUES = ("应聘", "投递", "目标岗位", "岗位", "方向", "实习", "校招", "社招",
                     "我面", "面的是", "想面")

# 低置信阈值：仅当兜底落到 qa(conf<0.5) 时才触发"消歧/反问"，关键词/信号路由(≥0.6)不受影响。
_LOW_CONFIDENCE = 0.5
# 含糊求助线索：想做事却没说清（既无能力关键词也无具体主题）——这类才反问，普通问句/闲聊照常 qa。
_VAGUE_HELP_CUES = ("帮帮我", "帮我", "帮一下", "帮个忙", "弄一下", "搞一下", "准备一下",
                    "看看我", "给我点", "给我来点", "随便", "不知道", "怎么办", "我好菜",
                    "我太菜", "变强", "提升一下", "想进步", "学点东西", "我想学", "学啥", "学什么")


class _CapPick(BaseModel):
    capability: str  # qa | planning | diagnosis | mock | unclear


def _matched_caps(low: str) -> Dict[str, int]:
    """各能力关键词命中的最早位置（用于多意图排序）。"""
    out: Dict[str, int] = {}
    for cap, kws in _CAP_KEYWORDS.items():
        pos = [low.find(k) for k in kws if k in low]
        if pos:
            out[cap] = min(pos)
    return out


class IntentResolver:
    """统一意图层入口。`profile_claims` 为可注入的用户档案简历/项目要点（拷打锚定用）。"""

    def __init__(self, profile_claims: Optional[List[str]] = None,
                 has_profile_material: bool = False):
        self.profile_claims = list(profile_claims or [])
        self.has_profile_material = has_profile_material or bool(profile_claims)

    # ---------------------------------------------------------------- public
    def resolve(self, message: str, context: Optional[Dict] = None) -> IntentFrame:
        ctx = context or {}
        text = (message or "").strip()
        if not text:
            return IntentFrame(capability=Capability.QA, confidence=0.3, signals=["empty"])

        # 澄清续接：上一轮挂着 pending_intent → 把本条当作缺失槽位的答案。
        pending = ctx.get("pending_intent")
        if isinstance(pending, dict) and pending.get("pending_slot"):
            return self.resume_pending(pending, text)

        low = text.lower()

        # ① 显式命令优先。
        m = _EXPLICIT_RE.match(low)
        if m and m.group(1) in _COMMANDS:
            cap = _COMMANDS[m.group(1)]
            body = text[m.end():].strip()
            return self._frame_for(Capability(cap), body or text, ["explicit"], conf=1.0)

        matched = _matched_caps(low)

        # ② 复合"准备面试"：diagnosis 主、planning 后继（与 manager 复合编排对齐）。
        if any(c in low for c in _PREP_CUES):
            f = IntentFrame(capability=Capability.COMPOSITE, confidence=0.8,
                            handoffs=["diagnosis", "planning"], signals=["prep_composite"])
            return f

        # ③ 单意图：按优先级选主能力。
        if matched:
            primary = next(c for c in _CAP_KEYWORDS if c in matched)
            rest = sorted((c for c in matched if c != primary), key=lambda c: matched[c])
            return self._frame_for(Capability(primary), text, ["keyword"],
                                   conf=0.85 if not rest else 0.7, handoffs=rest)

        # ④ 无能力关键词，但有强信号 → 仍判 mock/planning（拷打/岗位/JD/deadline…）。
        if S.has_mock_signal(text):
            return self._frame_for(Capability.MOCK, text, ["mock_signal"], conf=0.65)
        if S.has_plan_signal(text):
            return self._frame_for(Capability.PLANNING, text, ["plan_signal"], conf=0.6)

        # ④b 多轮承接/切换：本句无自身关键词/信号，但出现承接/切换迹象或正处于 mock →
        #     借用上文能力（LLM 结合历史优先，规则兜底），跨能力实现 topic 切换/延续。
        carried = self._carry_from_context(text, ctx)
        if carried is not None:
            return carried

        # ⑤ 兜底 → qa（叶子答题能力）；意义不明时再细判：澄清 or 走 qa。
        frame = self._frame_for(Capability.QA, text, ["fallback"], conf=0.4)
        return self._disambiguate_low_confidence(frame, text, history=ctx.get("history"))

    def mock_frame(self, text: str) -> IntentFrame:
        """强制按"开面试"解析（前端已点 mock 模式时用，绕过能力判定，仍抽槽位+澄清）。"""
        return self._frame_for(Capability.MOCK, (text or "").strip(), ["forced_mock"], conf=0.9)

    def resume_pending(self, pending: Dict, reply: str) -> IntentFrame:
        """澄清下一轮：把用户回复填进 pending_slot，重建已补全的 frame。"""
        slot = pending.get("pending_slot")
        cap = Capability(pending.get("capability", "qa"))
        reply = reply.strip()
        if cap == Capability.MOCK:
            base = pending.get("slots", {}) or {}
            if slot == "target_role":
                merged = f"我面{reply}"  # 复用岗位抽取，顺带推断 role_type
                f = self._frame_for(Capability.MOCK, merged, ["resumed"], conf=0.8)
                # 保留上一轮已抽到的难度/轮次。
                for k in ("difficulty", "max_turns"):
                    if base.get(k) and not f.slots.get(k):
                        f.slots[k] = base[k]
                return f
            if slot == "resume_claims":
                claims = [c.strip() for c in re.split(r"[;；\n,，]", reply) if c.strip()]
                ms = S.build_mock_slots(reply, profile_claims=claims)
                ms.resume_claims = claims or ms.resume_claims
                return IntentFrame(capability=Capability.MOCK, action=MockAction.START.value,
                                   slots=ms.model_dump(exclude_none=True),
                                   confidence=0.8, signals=["resumed"])
        if cap == Capability.PLANNING and slot == "goal":
            ps = S.build_plan_slots(reply)
            return IntentFrame(capability=Capability.PLANNING,
                               slots=ps.model_dump(exclude_none=True),
                               confidence=0.8, use_retrieval=bool(ps.topic), signals=["resumed"])
        # 兜底：当作新一轮普通输入重解析。
        return self.resolve(reply)

    # ---------------------------------------------------------------- 低置信消歧
    def _disambiguate_low_confidence(self, frame: IntentFrame, text: str,
                                     history: Optional[List[Dict]] = None) -> IntentFrame:
        """兜底落到 qa 且置信低时细判：含糊求助→LLM消歧/反问；普通问句/闲聊→照常 qa。

        "链路永远通"：无 key 时不调 LLM——含糊线索命中就反问，否则安全落 qa。
        含糊优先于问句：「不知道学啥」虽含"啥"，本质是要指引，应反问而非当成八股答。
        """
        if frame.confidence >= _LOW_CONFIDENCE:
            return frame
        low = text.lower()
        # 非含糊（明显问句 / 噪声闲聊）→ 安全落 qa，不打扰、不花 LLM。
        if not any(c in low for c in _VAGUE_HELP_CUES):
            return frame
        # 含糊求助：先试 LLM 消歧（判出具体能力就改路由）；判不清/无 key → 反问一句。
        cap = self._llm_capability(text, history=history)
        if cap == "qa":
            return frame
        if cap is not None:
            return self._frame_for(Capability(cap), text,
                                   frame.signals + ["llm_disambig"], conf=0.6)
        return self._clarify_intent(frame)

    def _carry_from_context(self, text: str, ctx: Dict) -> Optional[IntentFrame]:
        """多轮承接/切换：本句无自身关键词/信号时，借上文判断意图（跨能力 topic 切换/延续）。

        触发条件：出现承接/切换迹象，或正处于 mock。能力判定 LLM 带历史优先、规则兜底
        （active_mock→mock，否则沿用上一轮能力）。判不出 → None，交回 qa 兜底。
        """
        last_cap = (ctx.get("last_capability") or "").lower()
        active_mock = bool(ctx.get("active_mock"))
        last_topic = ctx.get("last_topic")
        history = ctx.get("history")
        switched, new_topic = S.detect_topic_switch(text)
        if not (S.is_continuation(text) or switched or active_mock):
            return None

        cap = self._llm_capability(text, history=history, last_capability=last_cap)
        if cap is None:  # 无 LLM/判不清 → 规则兜底。
            cap = "mock" if active_mock else (
                last_cap if last_cap in ("qa", "planning", "diagnosis", "mock") else None)
        if cap is None:
            return None

        signals = ["context_carry"] + (["topic_switch"] if switched else [])
        frame = self._frame_for(Capability(cap), text, signals, conf=0.6)
        topic = new_topic or (last_topic if isinstance(last_topic, str) else None)
        if topic and not frame.slots.get("topic"):
            frame.slots["topic"] = topic
            if cap in ("qa", "planning"):
                frame.use_retrieval = True
        return frame

    @staticmethod
    def _llm_capability(text: str, history: Optional[List[Dict]] = None,
                        last_capability: Optional[str] = None) -> Optional[str]:
        """让 Haiku 判一次能力 ∈ {qa,planning,diagnosis,mock}；含糊/失败/无 key → None。

        可带最近对话与上一轮能力：用于多轮承接/切换（"再加上并发"承接 planning、
        "那 rerank 呢"承接 qa、"换 kafka 出题"切到 mock 等）。
        """
        from ..llm.client import LLM

        if not getattr(LLM, "available", False):
            return None
        ctx_lines = ""
        if history:
            rows = [f"  上一轮用户「{h.get('text', '')}」(当时判为 {h.get('capability', '?')})"
                    for h in list(history)[-3:] if h.get("text")]
            if rows:
                ctx_lines += "最近对话：\n" + "\n".join(rows) + "\n"
        if last_capability:
            ctx_lines += f"我上一轮在做：{last_capability}\n"
        prompt = (
            "判断这句话最想要哪种帮助，只输出 capability：\n"
            "- qa：在问一个概念/技术问题，或只是打招呼闲聊；\n"
            "- planning：想制定/调整学习计划、复习安排；\n"
            "- diagnosis：想知道自己的薄弱点 / 让人诊断学习情况；\n"
            "- mock：想做模拟面试 / 出题练习；\n"
            "- unclear：太含糊，看不出想做什么。\n"
            "若这句是在延续/追加/换主题上一轮的事（如「再加上X」「换成X」「那X呢」），"
            "请沿用上一轮对应的能力。\n\n"
            f"{ctx_lines}这句话：{(text or '').strip()}\n只输出 capability。"
        )
        try:
            obj, _ = LLM.complete_structured(
                prompt, _CapPick, model_tier=ModelTier.HAIKU,
                system="你是简洁的意图分类器，只输出 JSON。",
                max_tokens=40, timeout_s=12.0,
            )
            cap = (obj.capability or "").strip().lower()
            if cap in ("qa", "planning", "diagnosis", "mock"):
                return cap
        except Exception:  # noqa: BLE001 - 消歧绝不能打断路由
            pass
        return None

    @staticmethod
    def _clarify_intent(frame: IntentFrame) -> IntentFrame:
        """意义不明的求助 → 反问一句，列出能帮的方向；下一轮按回复重解析。"""
        frame.clarification = (
            "想做点什么呢？我可以帮你：① 答疑解惑　② 排学习计划　③ 诊断薄弱点　④ 模拟面试"
            "——说一句你的目标就行（比如「帮我排个 RAG 复习计划」「我哪里薄弱」）。"
        )
        frame.pending_slot = "intent"  # resume_pending 未命中专门分支 → 回退整句重解析
        if "intent" not in frame.missing_required:
            frame.missing_required.append("intent")
        frame.signals = frame.signals + ["clarify_intent"]
        return frame

    # ---------------------------------------------------------------- internal
    def _frame_for(self, cap: Capability, text: str, signals: List[str],
                   conf: float, handoffs: Optional[List[str]] = None) -> IntentFrame:
        signals = list(signals)
        handoffs = list(handoffs or [])
        if cap == Capability.MOCK:
            low = text.lower()
            if S.wants_resume_grill(text):
                signals.append("resume_grill")
            if any(c in low for c in _ROLE_INTENT_CUES) or S.extract_target_role(text):
                signals.append("role_intent")
            ms = S.build_mock_slots(text, profile_claims=self.profile_claims)
            f = IntentFrame(
                capability=cap, action=MockAction.START.value,
                slots=ms.model_dump(exclude_none=True),
                confidence=conf, handoffs=handoffs, signals=signals,
            )
        elif cap == Capability.PLANNING:
            ps = S.build_plan_slots(text)
            f = IntentFrame(
                capability=cap, slots=ps.model_dump(exclude_none=True),
                confidence=conf, handoffs=handoffs,
                use_retrieval=bool(ps.topic), signals=signals,
            )
        elif cap == Capability.QA:
            f = IntentFrame(capability=cap, confidence=conf, handoffs=handoffs,
                            use_retrieval=True, signals=signals)
        else:  # diagnosis：只读用户事件，无槽位、无 RAG。
            f = IntentFrame(capability=cap, confidence=conf, handoffs=handoffs, signals=signals)

        return needs_clarification(f, has_profile_material=self.has_profile_material)
