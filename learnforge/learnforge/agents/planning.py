"""PlanningAgent —— 学习路径规划（生成+修改合并，Design §3.7）。

Phase 2：两分支统一推理，差异在输入（generate: goal/现状 ; modify: 诊断+现有路径）。
prompt + Sonnet + 结构化 PathDiff（只 diff 不全量重写）。不调优、不测试。
"""

from __future__ import annotations

import re
import uuid
from typing import Dict, List, Optional

from ..contracts.agents.planning import PathDiff, PathItem, PlanningInput, PlanningOutput
from ..contracts.atom import KnowledgeAtom
from ..contracts.enums import AgentId, PlanMode, Status
from ..integrations import notion as _notion
from ..storage.repositories import AtomRepository
from .base import BaseAgent


class PlanningAgent(BaseAgent):
    agent_id = AgentId.PLANNING

    def __init__(self, db_path: Optional[str] = None) -> None:
        super().__init__()
        self._db_path = db_path
        self.last_notion_url: Optional[str] = None
        self.last_report_path: Optional[str] = None
        _notion.register()  # 把 notion.sync 接入运行时工具表（幂等）
        from ..integrations import report as _report
        _report.register()  # report.generate（本地 Markdown 报告）

    def run(self, payload: PlanningInput) -> PlanningOutput:
        self.last_notion_url = None
        # 必填校验（Design §3.7 / §5.2）：缺现状 → needs_input。
        if payload.mode == PlanMode.GENERATE and not payload.goal:
            return PlanningOutput(diff=PathDiff(rationale="缺少目标，需追问"), status=Status.NEEDS_INPUT)
        if payload.mode == PlanMode.MODIFY and not payload.existing_path_ref:
            return PlanningOutput(diff=PathDiff(rationale="缺少现有路径引用"), status=Status.NEEDS_INPUT)

        atoms = self._candidate_atoms(payload)
        prompt = self._build_prompt(payload, atoms)
        out = self.llm_structured(prompt, PlanningOutput, max_tokens=2048)
        if out is not None:
            # 关键：模型常编造不存在的 atom_id（如 '1','2'），落库会被外键过滤成空计划。
            # 这里把 add 约束到真实候选 atom_id，过滤后为空但有候选 → 确定性兜底，保证计划可落库。
            result = self._finalize_diff(out, atoms)
        elif atoms:
            result = PlanningOutput(
                diff=self._deterministic_diff(atoms, "[fallback] 依据候选知识点(按掌握度从低到高)生成的复习顺序"),
                status=Status.OK,
            )
        else:
            # 实在无候选（库空）才 no-op。
            result = PlanningOutput(diff=PathDiff(rationale="[stub] no-op plan"), status=Status.OK)
        self._apply_day_limit(result.diff, payload)

        # 自主发布：作为 tool-calling agent，产出可落库计划后，自己决定发 Notion 和/或 生成本地报告。
        if result.diff.add:
            self._autonomous_publish(result.diff, atoms)
        return result

    # ---------------- 自主 tool-calling 发布（LLM 自选 notion/report + 确定性兜底）----------------
    def _autonomous_publish(self, diff: PathDiff, atoms: List[KnowledgeAtom]) -> None:
        title_by_id = {a.atom_id: f"[{a.topic}] {a.title}" for a in atoms}
        days: Dict[int, List[str]] = {}
        for it in diff.add:
            days.setdefault(it.day_index, []).append(title_by_id.get(it.atom_id, it.atom_id))
        plan_text = "\n".join(f"Day {d + 1}: " + "; ".join(v) for d, v in sorted(days.items()))

        # 暴露给模型的工具 = 已授权 + 可用的发布工具。
        tools: List[str] = []
        if self.has_tool("notion.sync") and _notion.available():
            tools.append("notion.sync")
        if self.has_tool("report.generate"):
            tools.append("report.generate")

        # 1) 自主路径：ReactRunner 让模型决定调 notion.sync / report.generate（可都调）。
        if tools:
            try:
                from .react.loop import ReactRunner

                res = ReactRunner(max_steps=3).run(
                    self,
                    user_prompt=(
                        f"我已生成如下可执行的学习计划：\n{plan_text}\n\n"
                        "请发布它：可调用 notion.sync 发成 Notion 笔记、和/或 report.generate 生成本地 Markdown 报告。"
                        "参数 days 用 {天序号(从0):[条目]} 结构，summary 一句话，tips 给 1-2 条。"
                    ),
                    tool_names=tools,
                    system="你是计划发布助手。用工具把计划发布出去，不要只用文字回答。",
                )
                for tr in res.tool_results:
                    data = tr.get("result") or {}
                    if tr.get("tool") == "notion.sync" and data.get("url"):
                        self.last_notion_url = data["url"]
                    if tr.get("tool") == "report.generate" and data.get("path"):
                        self.last_report_path = data["path"]
            except Exception:
                pass

        # 2) 确定性兜底：保证"有计划 → 至少有一份产物"（优先 Notion，没 token 则本地报告）。
        if not self.last_notion_url and self.has_tool("notion.sync") and _notion.available():
            out = _notion.create_learning_note(
                title="LearnForge 学习计划",
                summary="基于弱点诊断生成的复习计划，按天分桶。",
                days=days,
                tips=["完成每日 checkbox 后做一场模拟面试巩固", "薄弱点会自动进入下次诊断"],
            )
            if isinstance(out, dict) and not out.get("error"):
                self.last_notion_url = out.get("url")
        if not self.last_notion_url and not self.last_report_path and self.has_tool("report.generate"):
            from ..integrations.report import report_generate_handler
            out = report_generate_handler({"title": "LearnForge 学习计划",
                                           "summary": "按天分桶的复习计划。", "days": days})
            if out.get("ok"):
                self.last_report_path = out.get("path")

    def _finalize_diff(self, out: PlanningOutput, atoms: List[KnowledgeAtom]) -> PlanningOutput:
        """把 add 约束到真实候选 atom_id 并规范化 item_id/order/day；为空则确定性兜底。"""
        candidate_ids = {a.atom_id for a in atoms}
        kept = [it for it in out.diff.add if it.atom_id in candidate_ids]
        if not kept:
            if atoms:
                out.diff = self._deterministic_diff(
                    atoms, out.diff.rationale or "依据候选知识点生成的复习顺序"
                )
            return out
        final: List[PathItem] = []
        for i, it in enumerate(kept):
            day = it.day_index if isinstance(it.day_index, int) and it.day_index >= 0 else i // 2
            final.append(PathItem(item_id=str(uuid.uuid4()), atom_id=it.atom_id,
                                  order_idx=i, day_index=day, status="pending"))
        out.diff.add = final
        return out

    @staticmethod
    def _deterministic_diff(atoms: List[KnowledgeAtom], rationale: str, limit: int = 6) -> PathDiff:
        """按掌握度从低到高取前 N 个候选 atom，生成可直接落库的 PathDiff。"""
        ordered = sorted(atoms, key=lambda a: a.mastery_score)[:limit]
        items = [
            PathItem(item_id=str(uuid.uuid4()), atom_id=a.atom_id,
                     order_idx=i, day_index=i // 2, status="pending")
            for i, a in enumerate(ordered)
        ]
        return PathDiff(add=items, rationale=rationale)

    @staticmethod
    def _requested_days(payload: PlanningInput) -> Optional[int]:
        text = " ".join(x for x in [payload.goal, payload.user_feedback] if x)
        if not text:
            return None
        m = re.search(r"(\d+)\s*(?:天|日|day|days)", text, re.IGNORECASE)
        if m:
            return max(1, int(m.group(1)))
        if re.search(r"(?:一天|1天|one\s+day)", text, re.IGNORECASE):
            return 1
        return None

    def _apply_day_limit(self, diff: PathDiff, payload: PlanningInput) -> None:
        days = self._requested_days(payload)
        if not days or not diff.add:
            return
        for i, item in enumerate(diff.add):
            item.day_index = min(item.day_index, days - 1)
            item.order_idx = i

    def _candidate_atoms(self, payload: PlanningInput) -> List[KnowledgeAtom]:
        """读候选 Atom（Design §3.7：按 goal/诊断主题过滤）。DB 为空时返回 []。"""
        self.require_tool("repository.read.atoms")
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
            + "产出增量 PathDiff（add/remove/reorder + rationale），按天分桶（day_index 从 0 起），不全量重写。\n"
            + "【硬约束】add 里每个条目的 atom_id 必须取自上面候选 Atom 列表中的真实 atom_id，"
            "禁止编造新 id（如 '1'、'2'）；若候选为空再不添加。"
        )
