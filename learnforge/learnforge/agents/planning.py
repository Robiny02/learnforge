"""PlanningAgent —— 学习路径规划（生成+修改合并，Design §3.7）。

Phase 2：两分支统一推理，差异在输入（generate: goal/现状 ; modify: 诊断+现有路径）。
prompt + Sonnet + 结构化 PathDiff（只 diff 不全量重写）。不调优、不测试。
"""

from __future__ import annotations

from typing import List, Optional

from ..contracts.agents.planning import PathDiff, PlanningInput, PlanningOutput
from ..contracts.atom import KnowledgeAtom
from ..contracts.enums import AgentId, PlanMode, Status
from ..storage.repositories import AtomRepository
from .base import BaseAgent


class PlanningAgent(BaseAgent):
    agent_id = AgentId.PLANNING

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self._db_path = db_path

    def run(self, payload: PlanningInput) -> PlanningOutput:
        # 必填校验（Design §3.7 / §5.2）：缺现状 → needs_input。
        if payload.mode == PlanMode.GENERATE and not payload.goal:
            return PlanningOutput(diff=PathDiff(rationale="缺少目标，需追问"), status=Status.NEEDS_INPUT)
        if payload.mode == PlanMode.MODIFY and not payload.existing_path_ref:
            return PlanningOutput(diff=PathDiff(rationale="缺少现有路径引用"), status=Status.NEEDS_INPUT)

        atoms = self._candidate_atoms(payload)
        prompt = self._build_prompt(payload, atoms)
        out = self.llm_structured(prompt, PlanningOutput, max_tokens=2048)
        if out is not None:
            return out
        # 回退：空 diff（链路通；Design §3.7 空 diff → ok "无需调整"）。
        return PlanningOutput(diff=PathDiff(rationale="[stub] no-op plan"), status=Status.OK)

    def _candidate_atoms(self, payload: PlanningInput) -> List[KnowledgeAtom]:
        """读候选 Atom（Design §3.7：按 goal/诊断主题过滤）。DB 为空时返回 []。"""
        topics: List[str] = []
        if payload.diagnosis:
            topics = [c.topic for c in payload.diagnosis.clusters]
        try:
            repo = AtomRepository(db_path=self._db_path)
            out: List[KnowledgeAtom] = []
            for t in topics:
                out.extend(repo.list_by_topic(t))
            return out
        except Exception:
            return []

    @staticmethod
    def _build_prompt(payload: PlanningInput, atoms: List[KnowledgeAtom]) -> str:
        atom_lines = "\n".join(
            f"- {a.atom_id} | {a.topic}/{a.title} | mastery={a.mastery_score:.2f}" for a in atoms[:50]
        ) or "（无候选 Atom，可基于目标主题自行规划占位条目）"
        if payload.mode == PlanMode.GENERATE:
            head = (
                f"模式=generate\n目标：{payload.goal}\n"
                f"截止：{payload.deadline or '未指定'}\n现状：{payload.current_level or '未知'}\n"
            )
        else:
            diag = payload.diagnosis.model_dump() if payload.diagnosis else {}
            head = (
                f"模式=modify\n现有路径引用：{payload.existing_path_ref}\n"
                f"诊断结论：{diag}\n用户反馈：{payload.user_feedback or '无'}\n"
            )
        return (
            head
            + f"候选 Atom：\n{atom_lines}\n"
            + "产出增量 PathDiff（add/remove/reorder + rationale），按天分桶，不全量重写。"
        )
