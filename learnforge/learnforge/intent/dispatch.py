"""轻量意图分发器：自然语言（+会话上下文）→ `Route`（单意图走节点 / 多意图走 plan）。

设计动机（参考 Reactor 的 AgentDispatchService：按复杂度选 ReAct 单步 vs PlanSolve 多步）：
大多数输入其实是 qa / 聊 project，不该为每条话跑重型多字段判定。本分发器只做一件事——
**判单意图还是多意图，单意图给出 capability**——把槽位填充 / 澄清 / 多轮承接等下沉到节点，
意图层因此退化成一个真正的薄 router。

三层（与"链路永远通"一致，无 key 全程确定性）：
  ① L0 确定性短路（无 LLM）：空串 / 澄清续接 / 进行中的 mock / 显式 /命令 / 前端强制 mode；
  ② L1 一次轻量分类（2 字段：is_multi + capability，用 _INTENT_MODEL）；无 key/失败 → ③；
  ③ 离线规则兜底（复用 slots 的确定性信号 + 能力关键词）。
最后过一条 `_apply_carry` 承接规则（读 last_capability，无 LLM），承接句借上文能力。

`route()` 纯函数：相同 (text, ctx) 给相同结论，便于回归与准确率评测。
槽位/澄清不在此产出——由对应节点在真正要用时懒触发（见 server 的 mock/plan 入口）。
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from pydantic import BaseModel

from ..contracts.enums import ModelTier
from . import slots as S

# 单意图 capability 集合（multi 时 capability=None）。note 是 qa 的呈现变体。
_CAPS = ("qa", "planning", "diagnosis", "mock", "note")

# 能力关键词（dict 顺序即优先级：mock > diagnosis > planning），与历史路由一致。
_CAP_KEYWORDS: Dict[str, tuple] = {
    "mock": ("面试", "mock", "模拟面试", "模拟", "刷题", "出题", "interview", "practice",
             "考我", "考考我", "面试我", "面我"),
    "diagnosis": ("诊断", "弱点", "薄弱", "复盘", "短板", "diagnose", "weakness", "assess"),
    "planning": ("计划", "规划", "学习路径", "路线", "安排", "路径", "plan", "roadmap", "schedule"),
}
# 复合"准备面试"线索 → 多意图（diagnosis 先行、planning 后继，见 §5.6）。
_PREP_CUES = ("准备面试", "面试冲刺", "帮我准备", "备战面试", "面试准备")
# 显式命令：开头 /cmd 直接命中能力。
_COMMANDS: Dict[str, str] = {
    "qa": "qa", "ask": "qa", "plan": "planning", "planning": "planning",
    "diagnose": "diagnosis", "diagnosis": "diagnosis", "mock": "mock", "interview": "mock",
    "note": "note",
}
_EXPLICIT_RE = re.compile(r"^/(\w+)")

# 承接句借上文能力的主线集合（与 dialogue_turns 的 _THREAD_CAPS 对齐）。
_THREAD_CAPS = ("planning", "diagnosis", "mock")

# 意图分类模型（可经 env 覆盖）。实测：native function-calling 下 gpt-4o 精度≈gpt-5-mini
# 但延迟 ~5x 更快（0.6s vs 3.2s），故默认 gpt-4o——路由不该比干活还慢。
_INTENT_MODEL = os.getenv("LF_INTENT_MODEL", "openai/gpt-4o")

# 路由工具（Claude Code 式：把"选能力"交给 LLM 的 native function-calling，而非手搓 JSON 分类；
# function-calling 是一等训练能力，快模型也准——实测比同模型的 JSON 分类精度翻倍）。
_ROUTE_TOOL = [{"type": "function", "function": {
    "name": "route",
    "description": "把用户这条消息路由到一个能力，或标记为多意图。",
    "parameters": {"type": "object", "properties": {
        "capability": {"type": "string", "enum": list(_CAPS),
                       "description": "qa=问概念/技术点/聊或排查项目/闲聊; "
                       "planning=要学习计划/复习排期/到某 deadline 补齐; "
                       "diagnosis=想知道自己哪里薄弱/诊断掌握度/复盘回顾我的学习(不是排查代码); "
                       "mock=想立刻开始模拟面试/出题/被考/练习; note=生成 md/笔记/文档/报告"},
        "is_multi": {"type": "boolean",
                     "description": "一句话要连着做多件事=true。尤其「准备面试/面试冲刺/帮我准备(面试)/"
                     "备战面试」这类=先诊断薄弱再排计划=true，不要直接当 mock"},
    }, "required": ["capability", "is_multi"]}}}]
_ROUTE_CHOICE = {"type": "function", "function": {"name": "route"}}


class Route(BaseModel):
    """分发结论：单意图(+capability) 或 多意图。不携带槽位/澄清（下沉到节点）。"""

    mode: str = "single"                 # single | multi
    capability: Optional[str] = None     # qa|planning|diagnosis|mock|note；multi 时 None
    confidence: float = 0.5
    signals: List[str] = []              # 命中的规则/来源，供 trace/评测


class _RouteDraft(BaseModel):
    """L1 轻量分类器产物（刻意只 2 字段——单/多 + 能力，便宜且稳）。"""

    is_multi: bool = False
    capability: str = "qa"               # 仅 single 有意义；qa|planning|diagnosis|mock|note


class Dispatcher:
    """统一意图分发入口。`route(text, ctx)` 返回 `Route`。

    ctx 约定键（均可选，来自 DialogueTurnRepository.derive_context）：
      history(最近若干轮) / anchors(关键节点，可早于近窗) / summary(会话概要) /
      last_capability / last_topic / active_mock / forced_mode / pending_intent。
    """

    def route(self, text: str, context: Optional[Dict] = None) -> Route:
        ctx = context or {}
        t = (text or "").strip()
        if not t:
            return Route(mode="single", capability="qa", confidence=0.3, signals=["empty"])

        # ── L0 确定性短路（无 LLM） ──
        pending = ctx.get("pending_intent")
        if isinstance(pending, dict) and pending.get("pending_slot"):
            cap = str(pending.get("capability", "qa"))
            return Route(mode="single", capability=cap, confidence=0.9, signals=["pending_resume"])

        if ctx.get("active_mock"):
            return Route(mode="single", capability="mock", confidence=0.95, signals=["active_mock"])

        m = _EXPLICIT_RE.match(t.lower())
        if m and m.group(1) in _COMMANDS:
            return Route(mode="single", capability=_COMMANDS[m.group(1)],
                         confidence=1.0, signals=["explicit"])

        forced = ctx.get("forced_mode")
        if forced in _CAPS:
            return Route(mode="single", capability=forced, confidence=0.95, signals=["forced_mode"])

        # 自包含纯概念问句（八股「什么是 X / 介绍一下 X / 为什么…」）→ 直接 qa，免一次 LLM 路由
        # 往返。这是最常见路径，省掉 _INTENT_MODEL（默认 gpt-4o）的网络调用，显著降延迟；自包含句
        # 本就不借上文（见 _apply_carry），短路与既有承接语义一致。带能力关键词/信号的不走此路。
        if S.is_self_contained(t) and not self._looks_like_other_capability(t):
            return Route(mode="single", capability="qa", confidence=0.7, signals=["concept_shortcut"])

        # ── L1 一次轻量分类（LLM 2 字段）；无 key/失败 → ③ ──
        draft = self._classify(t, ctx)
        if draft is None:
            route = self._rules_route(t)
        elif draft.is_multi or draft.capability == "composite":
            route = Route(mode="multi", confidence=0.85, signals=["llm_classify"])
        else:
            cap = draft.capability if draft.capability in _CAPS else "qa"
            route = Route(mode="single", capability=cap, confidence=0.85, signals=["llm_classify"])

        # ── 承接规则（无 LLM，读 last_capability） ──
        return self._apply_carry(route, t, ctx)

    # ------------------------------------------------------------------ L1 LLM
    @staticmethod
    def _classify(text: str, ctx: Dict) -> Optional["_RouteDraft"]:
        """LLM 路由（Claude Code 式 native function-calling，强制 `route` 工具）。

        把"选能力"全交给 LLM，无硬匹配；function-calling 比手搓 JSON 分类更准更快
        （实测同模型精度翻倍、gpt-4o ~0.6s）。带紧凑对话窗口。无 key/异常 → None（退回规则）。
        """
        import json

        from ..llm.client import LLM

        if not getattr(LLM, "available", False):
            return None
        # 上下文窗口 = 会话概要(④) + 关键节点(①，含早于近窗的转折) + 最近若干轮 + 主线。
        win = ""
        if ctx.get("summary"):
            win += f"会话概要：{ctx['summary']}\n"
        anchors = [f"  「{a.get('text', '')}」(判为 {a.get('capability', '?')})"
                   for a in (ctx.get("anchors") or []) if a.get("text")]
        if anchors:
            win += "关键节点(可能早于近窗)：\n" + "\n".join(anchors) + "\n"
        rows = [f"  「{h.get('text', '')}」(判为 {h.get('capability', '?')})"
                for h in list(ctx.get("history") or [])[-3:] if h.get("text")]
        if rows:
            win += "最近对话：\n" + "\n".join(rows) + "\n"
        if ctx.get("last_capability"):
            win += f"当前主线在做：{ctx['last_capability']}\n"
        system = (
            "你是意图路由器，调用 route 工具。看动作意图、别只看主题词，qa 是大多数。"
            "承接/追加上一轮（「再加上X」「那它呢」「改成X」）沿用主线能力，算单意图。"
        )
        try:
            msg, _ = LLM.complete_with_tools(
                messages=[{"role": "user", "content": f"{win}消息：{text.strip()}"}],
                tools=_ROUTE_TOOL, model_tier=ModelTier.HAIKU, model=_INTENT_MODEL,
                system=system, max_tokens=120, timeout_s=20.0, tool_choice=_ROUTE_CHOICE,
                temperature=0.0,   # 路由要确定：同输入同结论，便于复现/回归
            )
            tc = (msg.get("tool_calls") or [{}])[0]
            args = json.loads(tc.get("function", {}).get("arguments", "{}"))
            cap = (args.get("capability") or "qa").strip().lower()
            return _RouteDraft(is_multi=bool(args.get("is_multi")),
                               capability=cap if cap in _CAPS else "qa")
        except Exception:  # noqa: BLE001 - 路由失败不阻断，退回规则
            return None

    @staticmethod
    def _looks_like_other_capability(text: str) -> bool:
        """本句是否带非 qa 能力线索（复合/计划/诊断/mock/笔记）——带则不走概念短路。"""
        low = (text or "").lower()
        if any(c in low for c in _PREP_CUES):
            return True
        for kws in _CAP_KEYWORDS.values():
            if any(k in low for k in kws):
                return True
        return S.has_mock_signal(text) or S.has_plan_signal(text) or S.is_note_request(text)

    # ------------------------------------------------------------------ ③ rules
    @staticmethod
    def _rules_route(text: str) -> Route:
        """离线确定性兜底：复合 cue → multi；否则关键词/信号/笔记 → 单意图；末了 qa。"""
        low = text.lower()
        if any(c in low for c in _PREP_CUES):
            return Route(mode="multi", confidence=0.8, signals=["prep_composite"])
        for cap, kws in _CAP_KEYWORDS.items():       # 按优先级 mock>diagnosis>planning
            if any(k in low for k in kws):
                return Route(mode="single", capability=cap, confidence=0.8, signals=["keyword"])
        if S.has_mock_signal(text):
            return Route(mode="single", capability="mock", confidence=0.65, signals=["mock_signal"])
        if S.has_plan_signal(text):
            return Route(mode="single", capability="planning", confidence=0.6, signals=["plan_signal"])
        if S.is_note_request(text):
            return Route(mode="single", capability="note", confidence=0.75, signals=["note_gen"])
        return Route(mode="single", capability="qa", confidence=0.4, signals=["fallback"])

    # ------------------------------------------------------------------ carry
    @staticmethod
    def _apply_carry(route: Route, text: str, ctx: Dict) -> Route:
        """承接规则：分类落到 qa 但本句像承接、且上文主线在别的能力 → 借上文能力。

        这就是"多轮承接下沉给节点用 last_capability 兜"——不在分类器里花 LLM 判承接。
        自包含请求（八股问句/生成笔记）永不借用上文。
        """
        if route.mode != "single" or route.capability != "qa":
            return route
        if S.is_self_contained(text):
            return route
        last = ctx.get("last_capability")
        if S.is_continuation(text) and last in _THREAD_CAPS:
            return Route(mode="single", capability=last, confidence=0.6,
                         signals=route.signals + ["context_carry"])
        return route
