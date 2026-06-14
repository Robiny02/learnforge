"""InterviewerAgent —— 出题与追问（Design §3.9）。Phase 3：Sonnet 出题 + 题库兜底。

只出题/追问，不评分、不定策略。超时/无 LLM → 从候选 atom 取预置题兜底。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ...config import mock_answer_model
from ...contracts.agents.mock import InterviewerInput, InterviewerOutput
from ...contracts.agents.retrieval import RetrievalInput
from ...contracts.enums import AgentId, InterviewPhase, KnowledgeScope, RetrievalMethod
from . import control as CTRL
from . import interview_skill as IS
from ..base import BaseAgent
from ..retrieval import RetrievalAgent

logger = logging.getLogger(__name__)


def _answer_model():
    """高质量生成（出题/解答）的模型覆盖；None → 用 skill 的 model_tier（SONNET）。"""
    return mock_answer_model()


# 环节 → 出题侧重（接入 tech-interview skill 三环节）。
_PHASE_BRIEF = {
    InterviewPhase.BASICS.value: "当前环节=基础知识：考简历技术栈的基础概念，由浅入深、可对比辨析。",
    InterviewPhase.PROJECT.value: "当前环节=项目深挖：围绕简历核心项目追问架构决策/技术难点/个人贡献/权衡取舍。",
    InterviewPhase.SYSTEM_DESIGN.value: "当前环节=系统设计/编码：按资历给设计题或编码题，引导思考不直接给答案。",
}
# 出题模式 → 指令。
_MODE_BRIEF = {
    "followup": "模式=追问深挖：紧扣候选人上一轮回答里最薄弱/最含糊的点继续逼问，不要换新话题。",
    "probe": "模式=简历诚信点破：候选人答不出简历/项目写的内容细节，直接指出差距并给更真实的表述建议。",
    "ask": "模式=出新题。",
}


class InterviewerAgent(BaseAgent):
    agent_id = AgentId.INTERVIEWER

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self._db_path = db_path  # 供统一 recall 接口惰性建检索器时透传库路径
        self.retrieval = RetrievalAgent(db_path=db_path)
        self._topic_cache: Dict[str, List] = {}
        self._materials_cache: Dict[str, str] = {}

    def run(self, payload: InterviewerInput) -> InterviewerOutput:
        chunks = payload.retrieved
        if not chunks:
            chunks = list(self._topic_cache.get(payload.topic) or [])
        if not chunks:
            try:
                self.require_tool("retrieval.search")
                # 出题参考共享知识库（题库/面经/知识点）。同 topic 首轮检索后缓存，
                # 后续追问复用，避免每轮重复触发向量/全文检索。
                chunks = self.retrieval.run(
                    RetrievalInput(
                        query=payload.topic,
                        top_k=4,
                        scopes=[KnowledgeScope.SHARED],
                        # HYBRID(FTS+向量 RRF)而非纯 FULLTEXT：FTS5 不切中文，中文主题靠向量召回兜底，
                        # 避免无证据 → 退化成泛泛题（见 agents/retrieval/CLAUDE.md）。
                        method=RetrievalMethod.HYBRID,
                    )
                ).chunks
                self._topic_cache[payload.topic] = list(chunks)
            except Exception:
                chunks = []

        asked = [t.question for t in payload.turn_history]
        evidence = "\n".join(f"- {c.text[:200]}" for c in chunks) or "（无检索证据）"
        # 按需检索上传材料（简历/JD/项目文档，local + origin=attachment），出更贴近材料的追问。
        # 同 topic 仅检索一次后缓存，后续追问复用（与共享题库检索一致，避免每轮重复触发）。
        if payload.topic not in self._materials_cache:
            self._materials_cache[payload.topic] = self.recall(
                payload.topic, scopes=[KnowledgeScope.LOCAL], origin="attachment", top_k=3).text
        materials = self._materials_cache[payload.topic]
        if materials:
            evidence = f"{evidence}\n{materials}"
        prompt = (
            f"主题：{payload.topic}\n难度(1-5)：{payload.difficulty}\n"
            f"{self._phase_brief(payload)}"
            f"已问过（勿重复）：{asked or '无'}\n参考资料：\n{evidence}\n"
            f"{self._bank_brief(payload)}"
            f"{self._grilling_brief(payload)}"
            "出一道该难度的面试题/追问，给出考点 expected_points 与相关 atom_refs。"
        )
        # 出题走可配置强档（mock_answer_model 覆盖；None → skill 的 SONNET 档）——保证题目质量。
        # max_tokens 提到 768：题目 + expected_points + atom_refs 的 JSON 在 512 下偶尔截断，
        # 截断 → 解析失败 → llm_structured 返回 None → 静默退化到模板题。
        out = self.llm_structured(prompt, InterviewerOutput, max_tokens=768,
                                  model=_answer_model())
        if out is not None and out.question:
            return out

        from ...llm.client import LLM
        if LLM.available:
            # LLM 可用却没产出有效题（截断/解析失败），说明不是离线降级而是真失败——出声以便排查，
            # 不再静默把模板题当成「LLM 出的题」。
            logger.warning(
                "Interviewer LLM produced no usable question (topic=%r, turn=%d); "
                "falling back to template. Likely truncation or parse failure.",
                payload.topic, payload.turn_index,
            )
        # 兜底：证据式追问（接入 LLMInternSkill）；无上下文时退回题库/模板题。
        atom_refs: List[str] = [c.chunk_id for c in chunks[:2]]
        return self._fallback_question(payload, atom_refs)

    def _grilling_brief(self, payload: InterviewerInput) -> str:
        """把候选人材料 + 角色 + 上一轮回答注入提示词，引导证据式追问（接入 LLMInternSkill）。"""
        ctx = payload.context
        role_type = self._role_type(payload)
        rnd = IS.pick_grill_round(payload.turn_index)
        lines = [
            f"拷打轮次：{rnd['round']} —— {rnd['intent']}",
            "原则：跟着候选人的 claim 追问，不出泛泛的『请介绍 X』；逼出真实边界与证据。",
        ]
        if role_type:
            focus = IS.role_focus(role_type)
            lines.append(f"目标角色：{role_type}；重点考察：{('、'.join(focus)) or '该方向硬要求'}")
        if ctx and (ctx.resume_claims or ctx.projects):
            claims = IS.extract_claims(ctx.resume_claims, ctx.projects)
            lines.append("候选人 claim（围绕这些追问真实性/证据）：" + "；".join(claims[:4]))
        if payload.last_answer:
            risks = IS.risk_flags_for_answer(payload.last_answer)
            lines.append(f"上一题：{payload.last_question}")
            lines.append(f"上一轮回答：{payload.last_answer[:300]}")
            if risks:
                lines.append(f"上一轮风险：{risks}（针对风险点追问证据/降级表达）")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _phase_brief(payload: InterviewerInput) -> str:
        """环节 + 模式提示（接入 tech-interview skill 三环节 + 追问/点破模式）。"""
        bits = []
        if payload.phase and payload.phase in _PHASE_BRIEF:
            bits.append(_PHASE_BRIEF[payload.phase])
        mode = payload.mode or "ask"
        if mode in _MODE_BRIEF and mode != "ask":
            bits.append(_MODE_BRIEF[mode])
        return ("\n".join(bits) + "\n") if bits else ""

    def _bank_brief(self, payload: InterviewerInput) -> str:
        """高频题库切片（按 role/topic 召回，作 few-shot 参考，不照抄）。"""
        role = self._role_type(payload)
        bank = IS.load_question_patterns(role_type=role, topic=payload.topic, limit=3)
        if not bank:
            return ""
        lines = [f"- {b['q']}（难度{b.get('difficulty')}）" for b in bank]
        return "高频题参考（可改编，勿照抄、勿超难度）：\n" + "\n".join(lines) + "\n"

    def explain(self, kind: str, question: str, expected_points: Optional[List[str]] = None,
                user_answer: Optional[str] = None, role_type: Optional[str] = None) -> str:
        """高质量"面试解答/提示/纠错"——双角色切换（面试官↔导师），强模型生成。

        kind ∈ {hint, reveal, correct}。无 key → 退回 control.py 的确定性文案（链路永远通）。
        接入 tech-interview skill 的双角色格式：`---` + 💡面试官提示 + `---`。
        """
        expected_points = expected_points or []
        from ...llm.client import LLM

        if not LLM.available or self.skill is None:
            return self._explain_fallback(kind, question, expected_points, user_answer)
        try:
            self.require_tool("llm.complete")
            instr = {
                "hint": "给候选人提示但**不要给出完整答案**：点出 1-2 个思考方向，逼他自己组织。",
                "reveal": "公布高质量参考答案：准确、有深度、点出关键权衡与常见误区，像资深面试官会认可的回答。",
                "correct": ("候选人回答有技术错误。先简短指出错在哪、为什么错，再给出正确且有深度的解释，"
                            "最后一句建议怎么真正理解它。"),
            }.get(kind, "给一个简洁有深度的说明。")
            prompt = (
                f"面试题：{question}\n考点：{expected_points or '（无）'}\n"
                f"候选人回答：{user_answer or '（未作答）'}\n目标角色：{role_type or '通用'}\n\n"
                f"任务：{instr}\n"
                "用 tech-interview 面试官的双角色格式输出（中文、直接、不留情、不空泛）：\n"
                "用一行 `---` 开头，第二行写 `💡 **面试官提示**`，正文给出内容，最后再用一行 `---` 收尾。"
            )
            resp = LLM.complete(
                prompt, model_tier=self.skill.spec.model_tier,
                system=self.skill.spec.system_prompt, max_tokens=700, model=_answer_model(),
            )
            txt = (getattr(resp, "text", "") or "").strip()
            if txt:
                return txt
        except Exception:  # noqa: BLE001 - 解答失败不阻断面试，退回确定性文案
            pass
        return self._explain_fallback(kind, question, expected_points, user_answer)

    @staticmethod
    def _explain_fallback(kind: str, question: str, expected_points: List[str],
                          user_answer: Optional[str]) -> str:
        """无 LLM 兜底：复用 control.py 的确定性文案，包成双角色格式。"""
        if kind == "hint":
            body = CTRL.build_hint(question, expected_points)
        elif kind == "correct":
            safer = IS.downgrade(user_answer)
            body = CTRL.build_reveal(question, expected_points)
            if safer:
                body += f"\n（你刚才的表述偏夸大，建议降级为「{safer}」更真实。）"
        else:  # reveal
            body = CTRL.build_reveal(question, expected_points)
        return f"---\n💡 **面试官提示**\n{body}\n---"

    def _fallback_question(self, payload: InterviewerInput, atom_refs: List[str]) -> InterviewerOutput:
        ctx = payload.context
        role_type = self._role_type(payload)
        claims = IS.extract_claims(ctx.resume_claims, ctx.projects) if ctx else []
        if claims or payload.last_answer or role_type:
            f = IS.grounded_followup(
                topic=payload.topic, difficulty=payload.difficulty,
                last_question=payload.last_question, last_answer=payload.last_answer,
                claims=claims, role_type=role_type, turn_index=payload.turn_index,
            )
            return InterviewerOutput(question=str(f["question"]),
                                     expected_points=list(f["expected_points"]),
                                     atom_refs=atom_refs)
        n = len(payload.turn_history) + 1
        return InterviewerOutput(
            question=f"（第{n}题 · 难度{payload.difficulty}）请讲讲「{payload.topic}」的核心原理与典型应用场景。",
            expected_points=[f"{payload.topic} 的定义", "适用场景", "常见误区"],
            atom_refs=atom_refs,
        )

    @staticmethod
    def _role_type(payload: InterviewerInput) -> Optional[str]:
        ctx = payload.context
        if ctx is None:
            return None
        return ctx.role_type or IS.detect_role_type(ctx.jd_text, ctx.target_role)
