"""InterviewDirector —— 模拟面试的"智能规划"大脑（替代旧 Strategist + 固定边状态机）。

设计与 `ManagerAgent.decide_next/_fallback_next` 同构："不预拆死一张图，每步看结果再决定下一步"。
- `classify_interrupt(text)`：把用户中断语映射成 InterviewMove（换题/调难度/暂停/总结/escalate）——
  确定性、可解释，吸收旧 strategist 的关键词规则。
- `decide_next(state, last_score)`：候选人答完一题后，看整场状态（逐轮分/环节/计数/风险）从受限动作空间
  里选下一步面试官动作。Haiku 路由（输出单字段 move）+ 解析失败/无 key → `_fallback_move` 确定性兜底。

Director 只**选动作**，不生成题面/答案/点评——生成交给 Interviewer（强模型，见 interviewer.explain/run）。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from ...config import MOCK_PHASE_QUESTIONS
from ...contracts.agents.mock import MockState, Score
from ...contracts.enums import AgentId, InterviewMove, InterviewPhase
from ..base import BaseAgent

# 中断关键词 → InterviewMove（本地 vs escalate 的边界，Q4）。
# 顺序敏感：PAUSE 须先于 SUMMARIZE（"暂停"含"停"）；ESCALATE 的跨能力词先于 SWITCH 的"换"。
_INTERRUPT_RULES = [
    (InterviewMove.ESCALATE, ("改计划", "重排", "重新规划", "诊断", "弱点", "学习计划", "plan", "diagnos")),
    (InterviewMove.PAUSE, ("暂停", "稍后", "待会", "缓一下", "pause", "later")),
    (InterviewMove.SUMMARIZE, ("结束", "不练了", "停止", "退出", "到此", "收工", "总结", "收尾",
                               "复盘", "end", "stop")),
    (InterviewMove.SWITCH_TOPIC, ("换个话题", "换一题", "换道题", "换题", "切换", "问点别的",
                                  "别问这个", "switch")),
    (InterviewMove.RAISE, ("难一点", "更难", "再难", "加大", "harder", "raise")),
    (InterviewMove.LOWER, ("简单", "容易", "降低难度", "降难", "easier", "lower")),
]

_VALID_MOVES = {m.value for m in InterviewMove}
# 候选人答完一题后，Director 可选的下一步（不含 ESCALATE/PAUSE/SWITCH——那些由用户中断驱动）。
_DECIDE_MOVES = (
    InterviewMove.ASK, InterviewMove.FOLLOWUP, InterviewMove.PROBE,
    InterviewMove.CORRECT, InterviewMove.RAISE, InterviewMove.LOWER,
    InterviewMove.SUMMARIZE,
)


class DirectorDecision(BaseModel):
    """Director 单步决策（动作空间受限、安全）。"""

    move: str = InterviewMove.ASK.value
    reason: str = ""
    next_topic: Optional[str] = None


class InterviewDirector(BaseAgent):
    """复用 STRATEGIST 的 agent_id / skill 槽（策略师演化为更聪明的导演）。"""

    agent_id = AgentId.STRATEGIST

    # ------------------------------------------------------------ 中断分类（确定性）
    @staticmethod
    def classify_interrupt(text: str) -> Optional[InterviewMove]:
        """用户中断语 → InterviewMove；无匹配返回 None（视为继续当前题）。"""
        t = (text or "").lower()
        if not t:
            return None
        for move, kws in _INTERRUPT_RULES:
            if any(kw in t for kw in kws):
                return move
        return None

    # ------------------------------------------------------------ 智能选下一步
    def decide_next(self, state: MockState, last_score: Optional[Score] = None) -> DirectorDecision:
        """答完一题后选下一步面试官动作。Haiku 路由 + 确定性兜底。"""
        from ...llm.client import LLM, LLMStructuredError, LLMUnavailable

        fb = self._fallback_move(state, last_score)
        if not LLM.available or self.skill is None:
            return DirectorDecision(move=fb.value, reason="deterministic")

        prompt = self._decide_prompt(state, last_score)
        try:
            self.require_tool("llm.complete_structured")
            obj = self.llm_structured(prompt, DirectorDecision, max_tokens=96)
        except (LLMUnavailable, LLMStructuredError, Exception):  # noqa: BLE001 - 决策绝不阻断
            obj = None
        if obj is None:
            return DirectorDecision(move=fb.value, reason="fallback(parse)")
        move = (obj.move or "").strip().lower()
        if move not in _VALID_MOVES:
            return DirectorDecision(move=fb.value, reason="fallback(invalid)")
        # 护栏：未答满 max_turns 前不轻易 summarize（除非确实到环节尾），防过早收尾。
        if move == InterviewMove.SUMMARIZE.value and not self._may_summarize(state):
            return DirectorDecision(move=fb.value, reason="guard(early-summarize)")
        return DirectorDecision(move=move, reason=obj.reason or "llm", next_topic=obj.next_topic)

    # ------------------------------------------------------------ 确定性兜底
    @staticmethod
    def _fallback_move(state: MockState, last_score: Optional[Score]) -> InterviewMove:
        if state.turn_index >= state.max_turns:
            return InterviewMove.SUMMARIZE
        if (state.phase == InterviewPhase.SYSTEM_DESIGN.value
                and state.phase_question_count >= MOCK_PHASE_QUESTIONS):
            return InterviewMove.SUMMARIZE
        overall = last_score.overall if last_score else None
        conf = (last_score.confidence if last_score else 0.0) or 0.0
        # 仅在评分**有把握**地判错时纠正——低置信启发式打分（离线兜底 conf≈0.2-0.3）不据此判错，
        # 否则几乎每轮都"纠错"。把握不足的低分留给 followup 深挖。
        if overall is not None and overall <= 1 and conf >= 0.5:
            return InterviewMove.CORRECT          # 答错 → 高质量纠正
        # 难度规则（连续 2 轮）。
        scored: List[int] = [s.overall for s in state.turn_scores if s.overall is not None]
        if len(scored) >= 2:
            if all(v >= 4 for v in scored[-2:]):
                return InterviewMove.RAISE
            if all(v <= 2 for v in scored[-2:]):
                return InterviewMove.LOWER
        if last_score and last_score.risk_flags:
            return InterviewMove.PROBE            # 夸大/无证据 → 简历诚信点破
        # 环节内没问够 → 顺着深挖；问够 → 出新题（mock_agent 推进环节）。
        if state.phase_question_count < MOCK_PHASE_QUESTIONS:
            return InterviewMove.FOLLOWUP
        return InterviewMove.ASK

    @staticmethod
    def _may_summarize(state: MockState) -> bool:
        """是否允许收尾：到轮数上限，或已进系统设计环节且问够。"""
        if state.turn_index >= state.max_turns:
            return True
        return (state.phase == InterviewPhase.SYSTEM_DESIGN.value
                and state.phase_question_count >= MOCK_PHASE_QUESTIONS)

    @staticmethod
    def _decide_prompt(state: MockState, last_score: Optional[Score]) -> str:
        tail = state.turns[-3:]
        lines = []
        for t in tail:
            sc = t.score.overall if t.score else None
            risk = (t.score.risk_flags if t.score else None) or []
            lines.append(f"  Q:{(t.question or '')[:50]} | 答:{(t.user_answer or '')[:40]} "
                         f"| 分:{sc} | 风险:{risk}")
        transcript = "\n".join(lines) or "（暂无）"
        ls = (f"overall={last_score.overall} risk={last_score.risk_flags} "
              f"missed={last_score.missed_points[:2]}") if last_score else "无"
        return (
            f"目标主题：{state.topic}；当前环节：{state.phase}（已出 {state.phase_question_count} 题，"
            f"满 {MOCK_PHASE_QUESTIONS} 题进下一环节）；累计第 {state.turn_index}/{state.max_turns} 题。\n"
            f"最近问答：\n{transcript}\n本轮评分：{ls}\n\n"
            "你是面试导演，决定下一步动作 move（只在以下里选）：\n"
            "ask=出新题(可推进环节)；followup=顺着刚才的回答深挖；probe=候选人答不出简历/项目细节就点破诚信；"
            "correct=回答有明显技术错误就纠正；raise/lower=调难度；summarize=收尾出复盘。\n"
            "原则：直接不留情、追问到底、暴露真实水平。答得好就加深(followup/raise)找天花板；"
            "夸大无证据就 probe；答错就 correct；还没问够别 summarize。只输出 move(+可选 reason/next_topic)。"
        )
