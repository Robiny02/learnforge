"""mock → 常规链路的交接摘要（escalate 时用，Design §6b）。

纯函数、无依赖：把一份 mock 子图状态浓缩成一句可读的中文上下文，
供 Manager/QA/诊断/规划在面试中断升级后"接着面试上下文继续"。
"""

from __future__ import annotations

from typing import Any, Dict


def build_handoff_summary(values: Dict[str, Any]) -> str:
    """从 mock 子图状态拼一句交接摘要（主题 / 轮次 / 均分 / 覆盖 / 最近一题与作答）。"""
    values = values or {}
    topic = values.get("topic") or "（未知主题）"
    scores = [s.get("overall") for s in (values.get("turn_scores") or [])
              if isinstance(s, dict) and s.get("overall") is not None]
    coverage = [t for t in (values.get("topic_coverage") or []) if t]
    turns = values.get("turns") or []
    last_q = values.get("current_question")
    last_a = turns[-1].get("user_answer") if turns and isinstance(turns[-1], dict) else None

    parts = [f"模拟面试交接：主题「{topic}」"]
    if scores:
        parts.append(f"已进行 {len(scores)} 轮，平均 {round(sum(scores) / len(scores), 1)}/5")
    if coverage:
        parts.append("覆盖：" + "、".join(coverage[:5]))
    if last_q:
        parts.append(f"最近一题：{str(last_q)[:80]}")
    if last_a:
        parts.append(f"考生作答：{str(last_a)[:80]}")
    return "；".join(parts) + "。"
