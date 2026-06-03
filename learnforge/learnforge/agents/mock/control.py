"""面试内即时控制的**确定性**文案生成（里程碑2，不消耗轮次）。

hint/repeat/reveal/feedback/skip/redo 六类控制的回应都在此生成——纯函数、可离线、可回归，
保证"链路永远通"。其中 hint/reveal 复用面试官给的 expected_points 作要点，feedback 复用
上一轮 Score。LLM 可用时调用方可另行增强，但默认这套确定性文案就够用。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

# 无 expected_points 时的通用作答框架（界定→权衡→举证）。
_GENERIC_FRAME = ["先一句话界定概念/边界", "再说关键权衡与代价", "最后举一个你真正做过的例子并给证据"]


def build_hint(question: str, expected_points: Sequence[str] = ()) -> str:
    """给提示但不给答案：露 1-2 个考察方向，逼候选人自己组织。"""
    pts = [p for p in expected_points if p][:2]
    if pts:
        body = "；".join(pts)
        return f"提示：可以从这些方向切入——{body}。先自己组织一下再答。"
    frame = "；".join(_GENERIC_FRAME)
    return f"提示：按这个框架展开——{frame}。"


def build_repeat(question: str) -> str:
    """复述/澄清当前题，不改变题目本身。"""
    q = (question or "").strip() or "（当前没有待答的问题）"
    return f"我再说一遍这道题：{q}\n（题目不变，按你的理解作答即可。）"


def build_reveal(question: str, expected_points: Sequence[str] = ()) -> str:
    """公布参考答案要点（露 expected_points）。无要点时给通用框架。"""
    pts = [p for p in expected_points if p]
    if pts:
        lines = "\n".join(f"- {p}" for p in pts)
        return f"参考答案要点：\n{lines}\n（这只是要点提纲，真实面试要展开并配你自己的例子。）"
    lines = "\n".join(f"- {p}" for p in _GENERIC_FRAME)
    return f"参考答案没有标准模板，建议按要点提纲组织：\n{lines}"


_DIM_LABELS = {"correctness": "正确性", "depth": "深度", "clarity": "表达"}
_RISK_LABELS = {"overclaim": "夸大无证据", "no_evidence": "缺证据链", "vague": "含糊/过短"}


def build_feedback(last_turn: Optional[dict]) -> str:
    """点评上一轮作答（用已记录的 Score）。没有上一轮则如实说明。"""
    if not last_turn or not last_turn.get("score"):
        return "还没有可点评的作答——先答一题，我再给你反馈。"
    score = last_turn["score"] or {}
    overall = score.get("overall")
    dims = score.get("dims") or {}
    parts: List[str] = []
    head = f"上一题{'得分 ' + str(overall) + '/5' if overall is not None else '未能给出总分'}"
    parts.append(head)
    dim_str = "  ".join(
        f"{_DIM_LABELS[k]}{dims.get(k, 0)}" for k in ("correctness", "depth", "clarity")
        if k in _DIM_LABELS
    )
    if dim_str:
        parts.append(dim_str)
    risks = [_RISK_LABELS.get(r, r) for r in (score.get("risk_flags") or [])]
    if risks:
        parts.append("风险：" + "、".join(risks))
    missed = [m for m in (score.get("missed_points") or []) if m][:3]
    if missed:
        parts.append("漏点：" + "；".join(missed))
    return "；".join(parts) + "。"


def build_skip_note(next_hint: str = "") -> str:
    return "好的，这题跳过，换一道。"


def build_redo_note(prev_question: str = "") -> str:
    q = (prev_question or "").strip()
    tail = f"：{q}" if q else "。"
    return f"好的，我们重做上一题{tail}\n（之前那次作答与评分已撤销，请重新作答。）"
