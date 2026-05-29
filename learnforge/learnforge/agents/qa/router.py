"""RouterAgent —— 问题分类与下游开关（Design §3.3）。Phase 2：Haiku 分类 + 保守回退。"""

from __future__ import annotations

from ...contracts.agents.qa import RouterInput, RouterOutput
from ...contracts.enums import AgentId, QType
from ..base import BaseAgent


class RouterAgent(BaseAgent):
    agent_id = AgentId.ROUTER

    def run(self, payload: RouterInput) -> RouterOutput:
        prompt = (
            f"问题：{payload.question}\n"
            f"最近主题：{', '.join(payload.recent_topics) if payload.recent_topics else '无'}\n"
            "判定 q_type(concept|project|temporal|chitchat) 与下游开关。"
        )
        out = self.llm_structured(prompt, RouterOutput, max_tokens=64)
        if out is not None:
            return self._enforce_rules(out)
        # 回退（解析失败/无 key）：保守全开（Design §3.3 Failure Modes）。
        return RouterOutput(
            q_type=QType.CONCEPT, need_retrieval=True, need_verifier=True, rewrite_needed=False
        )

    @staticmethod
    def _enforce_rules(out: RouterOutput) -> RouterOutput:
        """硬规则覆盖（Design §3.3）：temporal 强制全开；chitchat 全关。"""
        if out.q_type == QType.TEMPORAL:
            out.need_retrieval = True
            out.need_verifier = True
        elif out.q_type == QType.CHITCHAT:
            out.need_retrieval = False
            out.need_verifier = False
        return out
