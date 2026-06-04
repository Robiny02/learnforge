"""InterviewerAgent —— 出题与追问（Design §3.9）。Phase 3：Sonnet 出题 + 题库兜底。

只出题/追问，不评分、不定策略。超时/无 LLM → 从候选 atom 取预置题兜底。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ...contracts.agents.mock import InterviewerInput, InterviewerOutput
from ...contracts.agents.retrieval import RetrievalInput
from ...contracts.enums import AgentId, KnowledgeScope, RetrievalMethod
from . import interview_skill as IS
from ..base import BaseAgent
from ..retrieval import RetrievalAgent


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
                        method=RetrievalMethod.FULLTEXT,
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
            f"已问过（勿重复）：{asked or '无'}\n参考资料：\n{evidence}\n"
            f"{self._grilling_brief(payload)}"
            "出一道该难度的面试题/追问，给出考点 expected_points 与相关 atom_refs。"
        )
        out = self.llm_structured(prompt, InterviewerOutput, max_tokens=512)
        if out is not None and out.question:
            return out

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
