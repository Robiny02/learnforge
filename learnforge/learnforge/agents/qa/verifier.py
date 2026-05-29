"""VerifierAgent —— 事实/来源把关（Design §3.5）。Phase 2：Haiku 逐 claim 校验 + 透传降级。"""

from __future__ import annotations

from ...contracts.agents.qa import VerifierInput, VerifierOutput
from ...contracts.enums import AgentId, Verdict
from ..base import BaseAgent


class VerifierAgent(BaseAgent):
    agent_id = AgentId.VERIFIER

    def run(self, payload: VerifierInput) -> VerifierOutput:
        claims = "\n".join(f"- [{c.claim_id}] {c.text}" for c in payload.claims) or "（无 claim）"
        evidence = "\n".join(f"- [{c.chunk_id}] {c.text[:200]}" for c in payload.retrieved[:8]) or "（无证据）"
        prompt = (
            f"答案草稿：\n{payload.draft}\n\n"
            f"待校验 claims：\n{claims}\n\n"
            f"检索证据：\n{evidence}\n\n"
            "逐 claim 比对证据：无支撑则弱化并标记；全部无支撑则 verdict=unverified。"
        )
        out = self.llm_structured(prompt, VerifierOutput, max_tokens=1536)
        if out is not None:
            return out
        # 回退：透传 draft 标 unverified（不阻断用户，Design §3.5 Failure Modes）。
        verdict = Verdict.UNVERIFIED if payload.claims else Verdict.UNCERTAIN
        return VerifierOutput(verified_answer=payload.draft, verdict=verdict, flagged=[])
