"""SynthesizerAgent —— 检索增强合成（Design §3.4）。Phase 2：Sonnet RAG + 无证据声明。"""

from __future__ import annotations

from ...contracts.agents.qa import SynthesizerInput, SynthesizerOutput
from ...contracts.enums import AgentId
from ..base import BaseAgent


class SynthesizerAgent(BaseAgent):
    agent_id = AgentId.SYNTHESIZER

    def run(self, payload: SynthesizerInput) -> SynthesizerOutput:
        evidence = self._format_evidence(payload)
        atoms = ", ".join(a.title for a in payload.scoped_atoms) if payload.scoped_atoms else "无"
        prompt = (
            f"问题：{payload.question}\n"
            f"检索证据：\n{evidence}\n"
            f"相关 Atom：{atoms}\n"
            f"项目上下文：{payload.project_context or '无'}\n"
            "基于证据合成回答；无证据则显式声明并弱化断言。输出 draft 与 claims。"
        )
        out = self.llm_structured(prompt, SynthesizerOutput, max_tokens=1536)
        if out is not None:
            return out
        # 回退：无 LLM 时给占位草稿（链路通）。
        note = "" if payload.retrieved else "（未检索到本地证据，以下为通用回答）"
        return SynthesizerOutput(draft=f"[stub synthesizer]{note}", claims=[])

    @staticmethod
    def _format_evidence(payload: SynthesizerInput) -> str:
        if not payload.retrieved:
            return "（无）"
        return "\n".join(
            f"- [{c.chunk_id}] {c.text[:200]}" for c in payload.retrieved[:8]
        )
