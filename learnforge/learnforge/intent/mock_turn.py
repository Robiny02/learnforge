"""面试**进行中**的回合意图分类（answer/side/exit/pause/escalate + 换题/调难度）。

进行中的 mock 里，候选人这条消息可能是在作答当前题、临时插问、想退出、暂停、转去跨能力
（诊断/改计划），或要求**换个话题 / 调难度**。这些天然模糊，规则法易误判，故三层兜底：
① 确定性快路 → ② 轻量 Haiku 判一次 → ③ 启发式兜底，保证"链路永远通"。

里程碑 1 接通 answer/side/exit/pause/escalate/switch_topic/set_difficulty——后五者经
`user_interrupt` 直达 StrategistAction（见 agents/mock/strategist.py），无需改图。
skip/hint/repeat/reveal/redo/feedback 留待里程碑 2（需图支持，故此处暂不检测，避免误吞作答）。

历史入口 `agents/mock/intent.py` 已改为 shim 委托到本模块，保住既有 import 与测试。
"""

from __future__ import annotations

from pydantic import BaseModel

from ..contracts.enums import ModelTier

# 明确「插问」的开场词（无歧义 → 快路直判，不调 LLM）。
_SIDE_LEADS = (
    "顺便问", "顺便", "插一句", "插个问题", "插问", "我想问", "想先问", "另外问",
    "题外话", "换个问题问", "问一下", "问个题外", "帮我解释", "帮我科普", "科普一下",
)
# 明确「退出面试」的短语（多词、低误报；裸"结束/退出"交 LLM 结合题目上下文判）。
_EXIT_LEADS = (
    "结束面试", "退出面试", "停止面试", "结束模拟", "不练了", "不想练", "不面了",
    "今天到这", "今天先到这", "到此为止", "结束吧", "退出吧", "先到这", "就到这",
    "收工", "不练习了", "别练了", "结束这场", "退出这场",
)
_PAUSE_LEADS = ("暂停", "稍等", "等一下", "等等", "先等", "缓一下")
# 跨能力请求（应升级交回常规链路：诊断/改计划/重排，而非面试内处理，§6b/Q4）。
_ESCALATE_LEADS = (
    "改计划", "改下计划", "调整计划", "重排", "重新规划", "重新安排", "诊断", "弱点",
    "我的薄弱", "帮我规划", "制定计划", "换个方向", "复习计划", "学习计划",
)
# 「换个话题」——多词 lead，极少出现在真实作答里（避免误吞含"换"的答案）。
_SWITCH_LEADS = (
    "换个话题", "换一题", "换道题", "换个问题", "换题", "别问这个", "问点别的",
    "换一道", "下一个话题", "不想答这题",
)
# 「调难度」——多词 lead，同样避免命中含"简单/难"的正常作答。
_DIFFICULTY_LEADS = (
    "太难了", "难一点", "再难点", "再难一点", "更难", "出难一点", "来点难的",
    "简单点", "简单一点", "太简单", "降低难度", "换简单的", "容易点",
)
# 里程碑2：不消耗轮次的即时控制。全用多词 lead，避免误吞作答。
# 顺序敏感：redo（重答上一题）须先于 feedback（"上一题"重叠）；reveal 先于 hint。
_REDO_LEADS = ("重答上一题", "我重答", "重新答上", "上一题重答", "让我重答", "再答一次上一题", "redo")
_FEEDBACK_LEADS = (
    "刚才答得怎么样", "刚刚答得怎么样", "我答得怎么样", "我答得怎样", "答得如何",
    "评价一下我的回答", "点评一下我", "我上面答得", "刚才那题答得",
)
_REVEAL_LEADS = (
    "直接告诉我答案", "告诉我答案", "公布答案", "参考答案", "标准答案",
    "答案是什么", "看看答案", "直接给答案", "把答案给我", "正确答案是",
)
_HINT_LEADS = ("给点提示", "提示一下", "给个提示", "有没有提示", "提示下", "点拨一下",
               "卡住了", "想不出来", "给个思路", "hint")
_REPEAT_LEADS = ("再说一遍", "没听懂", "这题什么意思", "题目什么意思", "重复一下", "再读一遍",
                 "没看懂题", "再说一次", "什么意思啊", "没太懂题")
_SKIP_LEADS = ("跳过这题", "跳过", "下一题", "这题跳过", "换下一题", "不会这题", "过这题", "skip")

# 控制 action 集合（供子图/测试判定是否走 control_action 通道）——单一来源在动作注册表，此处转出。
from ..agents.mock.actions import CONTROL_ACTION_NAMES as CONTROL_ACTIONS  # noqa: E402,F401
# 软退出迹象：出现这些 token 时，即便没有疑问词也不要快路当作答 → 交 LLM 结合上下文判。
_SOFT_STOP_TOKENS = (
    "结束", "退出", "停", "算了", "累", "先这样", "就这样", "到这", "不想",
    "下次", "拜拜", "收工", "done", "结了", "不继续", "不做了",
)
# 疑问信号（用于快路判定「明显是作答」：完全没有这些且足够长 → 直接 answer）。
_QUESTION_SIGNALS = (
    "?", "？", "什么", "为什么", "怎么", "如何", "吗", "呢", "能不能", "是不是",
    "区别", "解释", "啥", "怎样", "可不可以",
)

# 退出确认的回复线索（continue 先判：含"不/取消/继续"等优先不结束，避免误结束）。
_CONTINUE_CUES = ("继续", "不", "取消", "再来", "再问", "等", "先别", "不要", "no",
                  "还没", "接着", "保留", "换一", "别结束", "先不")
_CONFIRM_CUES = ("确定", "确认", "是的", "对", "嗯", "好的", "好", "行", "可以", "可",
                 "结束", "到这", "收工", "yes", "ok", "就这样", "没问题", "够了")


class MockIntent(BaseModel):
    kind: str  # answer | side | exit | pause | escalate | switch_topic | set_difficulty


class ExitConfirm(BaseModel):
    kind: str  # "confirm" | "continue"


def _control_lead(t: str):
    """确定性命中各类控制意图的多词 lead；命中返回 kind，否则 None。

    顺序敏感（高特异性在前）：
      escalate > exit > pause > redo > feedback > reveal > hint > repeat > skip
      > switch_topic > set_difficulty > side。
    （escalate 的"换个方向"须先于 switch 的"换"；redo 的"重答上一题"须先于 feedback 的"上一题"。）
    """
    if any(c in t for c in _ESCALATE_LEADS):
        return "escalate"
    if any(c in t for c in _EXIT_LEADS):
        return "exit"
    if any(c in t for c in _PAUSE_LEADS):
        return "pause"
    if any(c in t for c in _REDO_LEADS):
        return "redo"
    if any(c in t for c in _FEEDBACK_LEADS):
        return "feedback"
    if any(c in t for c in _REVEAL_LEADS):
        return "reveal"
    if any(c in t for c in _HINT_LEADS):
        return "hint"
    if any(c in t for c in _REPEAT_LEADS):
        return "repeat"
    if any(c in t for c in _SKIP_LEADS):
        return "skip"
    if any(c in t for c in _SWITCH_LEADS):
        return "switch_topic"
    if any(c in t for c in _DIFFICULTY_LEADS):
        return "set_difficulty"
    if any(c in t for c in _SIDE_LEADS):
        return "side"
    return None


# ----------------------------------------------------------------- 作答/插问/退出/暂停/换题/调难度
def _heuristic(message: str) -> str:
    """离线兜底：控制类多词 lead 优先，其次问号/疑问词 → side，否则 answer。"""
    t = (message or "").strip().lower()
    if not t:
        return "answer"
    lead = _control_lead(t)
    if lead is not None:
        return lead
    if t.endswith("?") or t.endswith("？"):
        return "side"
    return "side" if any(c.lower() in t for c in _QUESTION_SIGNALS) else "answer"


def fast_kind(message: str):
    """确定性快路：无歧义直接返回 kind，省一次 LLM 往返；歧义返回 None。"""
    t = (message or "").strip().lower()
    if not t:
        return "answer"
    lead = _control_lead(t)
    if lead is not None:
        return lead
    # 无疑问信号、无软退出迹象、且足够长 → 几乎必然在作答（多数轮次走这条，省 LLM）。
    if (len(t) >= 10
            and not any(c.lower() in t for c in _QUESTION_SIGNALS)
            and not any(tok in t for tok in _SOFT_STOP_TOKENS)):
        return "answer"
    return None  # 短句 / 含疑问信号 / 含软退出迹象 → 交 LLM 结合题目上下文定夺


_VALID_KINDS = ("escalate", "exit", "pause", "redo", "feedback", "reveal", "hint", "repeat",
                "skip", "switch_topic", "set_difficulty", "side", "answer")


def classify_mock_input(question: str, message: str) -> str:
    """返回 answer|side|exit|pause|escalate|switch_topic|set_difficulty。三层省 LLM。"""
    fast = fast_kind(message)
    if fast is not None:
        return fast

    from ..llm.client import LLM

    if not getattr(LLM, "available", False):
        return _heuristic(message)
    prompt = (
        "你是模拟面试的输入分类器，只判断候选人这条消息的意图。\n"
        f"面试官当前的问题：\n{(question or '（暂无问题）').strip()}\n\n"
        f"候选人这条消息：\n{(message or '').strip()}\n\n"
        "在以下类别里选一个 kind：\n"
        "- answer：在回答上面的面试题（哪怕不完整、跑题、答错都算作答）；\n"
        "- side：临时在问一个新知识点 / 请求解释 / 与作答无关的小问题；\n"
        "- exit：想结束 / 退出本场模拟面试；\n"
        "- pause：想暂停一下；\n"
        "- switch_topic：想换个话题 / 不想答这道题；\n"
        "- set_difficulty：嫌太难或太简单、想调整难度；\n"
        "- skip：跳过当前这道题、直接下一题；\n"
        "- hint：卡住了，想要提示但不要直接答案；\n"
        "- repeat：没听懂题、要求复述或解释这道题的意思；\n"
        "- reveal：要求直接公布这道题的参考答案；\n"
        "- redo：想重做 / 重答上一题；\n"
        "- feedback：问自己刚才那题答得怎么样、要点评；\n"
        "- escalate：想转去做诊断 / 改学习计划 / 重新规划等跨能力请求。\n"
        "只输出 kind。"
    )
    try:
        obj, _ = LLM.complete_structured(
            prompt, MockIntent, model_tier=ModelTier.HAIKU,
            system="你是简洁的意图分类器，只输出 JSON。",
            max_tokens=60, timeout_s=12.0,
        )
        kind = (obj.kind or "").strip().lower()
        for k in _VALID_KINDS:
            if k in kind:
                return k
    except Exception:
        pass
    return _heuristic(message)


# ----------------------------------------------------------------- 退出确认（confirm/continue）
def _confirm_heuristic(message: str) -> str:
    t = (message or "").strip().lower()
    if not t:
        return "continue"
    if any(c in t for c in _CONTINUE_CUES):  # 先判 continue：避免"不结束"被当成确认
        return "continue"
    if any(c in t for c in _CONFIRM_CUES):
        return "confirm"
    return "continue"  # 歧义 → 默认不结束，避免误丢面试


def classify_exit_confirmation(message: str) -> str:
    """系统问"确定结束吗？"后，判断候选人回复是 confirm（结束）还是 continue（继续）。"""
    from ..llm.client import LLM

    if not getattr(LLM, "available", False):
        return _confirm_heuristic(message)
    prompt = (
        "刚才系统问候选人：『确定结束本场模拟面试吗？』\n"
        f"候选人的回复：\n{(message or '').strip()}\n\n"
        "判断是 confirm（同意结束面试）还是 continue（不结束 / 想继续作答）。只输出 kind。"
    )
    try:
        obj, _ = LLM.complete_structured(
            prompt, ExitConfirm, model_tier=ModelTier.HAIKU,
            system="你是简洁的意图分类器，只输出 JSON。",
            max_tokens=40, timeout_s=12.0,
        )
        kind = (obj.kind or "").strip().lower()
        if "confirm" in kind:
            return "confirm"
        if "continue" in kind:
            return "continue"
    except Exception:
        pass
    return _confirm_heuristic(message)


# 控制 action → user_interrupt 短语（Strategist 已能据此映射成 StrategistAction）。
_INTERRUPT_PHRASE = {
    "switch_topic": "换个话题",
    "set_difficulty": "",   # 由调用方据"太难/太简单"决定 raise/lower，见 interrupt_for
    "pause": "暂停",
    "exit": "结束",
}


def interrupt_for(action: str, message: str) -> str:
    """把面试内控制 action 映射成 Strategist 能识别的 user_interrupt 短语。

    set_difficulty 需区分 raise/lower：含"简单/容易"→ lower，否则 raise。
    """
    if action == "set_difficulty":
        low = (message or "").lower()
        if any(c in low for c in ("简单", "容易", "降", "easier", "基础")):
            return "简单一点"
        return "难一点"
    return _INTERRUPT_PHRASE.get(action, message or "")
