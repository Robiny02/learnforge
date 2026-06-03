"""本地 Markdown 报告生成（report.generate 工具）。

把学习计划/诊断结论渲染成一篇结构化 .md 存到 docs/reports/，返回路径。
COMPUTE 工具：只写本仓库 docs/ 下的报告文件，不碰任何 LearnForge 状态（mastery/path）。
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime
from typing import Any, Dict, List

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # .../learnforge(项目根)
REPORTS_DIR = REPO_ROOT / "docs" / "reports"


def _slug(text: str) -> str:
    s = re.sub(r"[^\w一-鿿-]+", "-", (text or "report").strip())
    return (s[:40] or "report").strip("-")


def _normalize_days(days: Any) -> Dict[int, List[str]]:
    out: Dict[int, List[str]] = {}
    if isinstance(days, dict):
        for k, v in days.items():
            try:
                idx = int(k)
            except Exception:
                idx = len(out)
            out[idx] = [str(x) for x in (v or [])]
    elif isinstance(days, list):
        for i, v in enumerate(days):
            if isinstance(v, dict) and "items" in v:
                out[int(v.get("day", i))] = [str(x) for x in (v.get("items") or [])]
            elif isinstance(v, list):
                out[i] = [str(x) for x in v]
    return out


def _item_focus(item: str) -> str:
    text = item.strip()
    if "]" in text and text.startswith("["):
        return text.split("]", 1)[-1].strip() or text
    return text


def report_generate_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    title = str(args.get("title") or "学习报告")
    summary = str(args.get("summary") or "")
    days = _normalize_days(args.get("days") or {})
    tips = [str(t) for t in (args.get("tips") or [])]
    total_items = sum(len(items) for items in days.values())
    lines: List[str] = [
        f"# {title}",
        "",
        "> LearnForge study plan: diagnosis-driven, output-oriented, and mock-reviewable.",
        "",
        "## Overview",
        "",
        f"- Summary: {summary or '按弱点优先级生成的分天复习计划。'}",
        f"- Duration: {len(days)} day(s)",
        f"- Knowledge points: {total_items}",
        "- Loop: learn -> explain -> drill -> mock -> write weak memory",
        "",
        "## Priority Logic",
        "",
        "- Put blocking fundamentals before high-frequency interview drills.",
        "- Keep each day small enough to produce an interview-ready answer, not just passive reading.",
        "- Use mock/QA feedback to decide whether a point is done or should return to weak memory.",
        "",
    ]
    for d in sorted(days):
        items = days[d]
        focus = " / ".join(_item_focus(x) for x in items[:2]) or "Review"
        lines += [
            f"## Day {d + 1}: {focus}",
            "",
            "### Learning Targets",
        ]
        lines += [f"- {item}" for item in items] or ["- Review and consolidate previous weak points."]
        lines += [
            "",
            "### Practice Tasks",
            "- Write a 60-second oral explanation for each target.",
            "- Add one mechanism diagram or step-by-step flow in your own words.",
            "- Answer one follow-up: failure mode, tradeoff, or production debugging signal.",
            "",
            "### Acceptance Criteria",
            "- Can explain definition, mechanism, tradeoff, and common pitfall without notes.",
            "- Can answer at least one mock follow-up without becoming vague.",
            "- Any uncertain answer is recorded as a `weak` memory note for the next diagnosis.",
            "",
        ]
    if tips:
        lines.append("## Next Actions")
        lines.append("")
        lines += [f"- {t}" for t in tips]
        lines.append("")
    lines += [
        "## Review Cadence",
        "",
        "- After every 2 study days: run a 3-5 turn mock interview.",
        "- After every mock: inspect score dimensions and risk flags.",
        "- Before changing the plan: run diagnosis first, then modify path.",
        "",
        f"_Generated at {datetime.now().isoformat(timespec='seconds')}_",
    ]
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{_slug(title)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        out = REPORTS_DIR / fname
        out.write_text("\n".join(x for x in lines if x is not None), encoding="utf-8")
        return {"ok": True, "path": str(out), "observation": f"已生成报告 {out}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


_PARAMS = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "days": {"type": "object",
                 "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        "tips": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "days"],
}


def register() -> None:
    from ..mcp import tools as toolmod

    if not toolmod.has_handler("report.generate"):
        toolmod.register_tool("report.generate", report_generate_handler, parameters=_PARAMS,
                              description="把学习计划渲染成本地结构化 Markdown 报告(docs/reports/)。")
