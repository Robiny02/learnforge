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


def report_generate_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    title = str(args.get("title") or "学习报告")
    summary = str(args.get("summary") or "")
    days = _normalize_days(args.get("days") or {})
    tips = [str(t) for t in (args.get("tips") or [])]
    lines: List[str] = [f"# 📘 {title}", "", f"> {summary}" if summary else "", ""]
    for d in sorted(days):
        lines.append(f"## 📅 Day {d + 1}")
        lines += [f"- [ ] {item}" for item in days[d]]
        lines.append("")
    if tips:
        lines.append("## 🚀 下一步建议")
        lines += [f"- {t}" for t in tips]
        lines.append("")
    lines.append(f"\n_生成于 {datetime.now().isoformat(timespec='seconds')}_")
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
