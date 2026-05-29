"""StrategistAgent —— 难度/选题策略 + 中断意图分类（Design §3.11，Q4 边界）。Phase 3。

只决策不作答。中断意图：局部（换topic/调难度/暂停/跳过）本地处理；
跨能力（结束/改计划/诊断）→ escalate 回 Manager（不私自处理）。
难度规则：连续 2 轮 ≥4 升档，≤2 降档。歧义 → continue 并澄清。
"""

from __future__ import annotations

from ...contracts.agents.mock import StrategistInput, StrategistOutput
from ...contracts.enums import AgentId, StrategistAction
from ..base import BaseAgent

# 中断关键词 → action（本地 vs escalate 的边界，Q4）。
# 顺序敏感：PAUSE 须先于 END（"暂停"含"停"，避免被 END 误吞）。
_INTERRUPT_RULES = [
    (StrategistAction.ESCALATE, ("改计划", "重排", "诊断", "弱点", "plan", "diagnos")),
    (StrategistAction.PAUSE, ("暂停", "稍后", "待会", "pause", "later")),
    (StrategistAction.END, ("结束", "不练了", "停止", "end", "stop", "退出")),
    (StrategistAction.SWITCH_TOPIC, ("换", "切换", "别的", "switch", "topic")),
    (StrategistAction.RAISE, ("难一点", "更难", "加大", "harder", "raise")),
    (StrategistAction.LOWER, ("简单", "容易", "降", "easier", "lower")),
]


class StrategistAgent(BaseAgent):
    agent_id = AgentId.STRATEGIST

    def run(self, payload: StrategistInput) -> StrategistOutput:
        cur_diff = int(payload.session_state.get("difficulty", 3) or 3)

        # 用户中断优先：先尝试关键词分类（确定性、可解释），再交 LLM 兜歧义。
        if payload.user_interrupt:
            action = self._classify_interrupt(payload.user_interrupt)
            if action is not None:
                return self._build(action, payload, cur_diff)
            out = self.llm_structured(
                f"用户中断语：{payload.user_interrupt}\n判定 action（continue|raise|lower|"
                f"switch_topic|pause|end|escalate）。跨能力请求必须 escalate。",
                StrategistOutput, max_tokens=128,
            )
            if out is not None:
                return out
            # 歧义兜底：continue（Design §3.11 Failure Modes）。
            return self._build(StrategistAction.CONTINUE, payload, cur_diff)

        # 无中断：按难度规则调档（连续 2 轮 ≥4 升、≤2 降）。
        return self._build(self._difficulty_action(payload), payload, cur_diff)

    @staticmethod
    def _classify_interrupt(text: str):
        t = text.lower()
        for action, kws in _INTERRUPT_RULES:
            if any(kw in t for kw in kws):
                return action
        return None

    @staticmethod
    def _difficulty_action(payload: StrategistInput) -> StrategistAction:
        scored = [s.overall for s in payload.score_history if s.overall is not None]
        if len(scored) >= 2:
            last2 = scored[-2:]
            if all(v >= 4 for v in last2):
                return StrategistAction.RAISE
            if all(v <= 2 for v in last2):
                return StrategistAction.LOWER
        return StrategistAction.CONTINUE

    @staticmethod
    def _build(action: StrategistAction, payload: StrategistInput, cur_diff: int) -> StrategistOutput:
        next_diff = cur_diff
        if action == StrategistAction.RAISE:
            next_diff = min(5, cur_diff + 1)
        elif action == StrategistAction.LOWER:
            next_diff = max(1, cur_diff - 1)
        # next_topic 由 LLM 抽取；确定性兜底无法可靠解析目标 topic，置 None（沿用当前 topic）。
        return StrategistOutput(action=action, next_topic=None, next_difficulty=next_diff)
