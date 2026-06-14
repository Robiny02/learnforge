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


def _image_block(image: str, title: str) -> List[str]:
    """信息图区块：有图(URL/路径)就嵌入 Markdown 图片，无图给一句占位提示。

    参考 gpt-image house style（runtime/skills/gpt-image-2-style-library）——报告顶部用一张
    flat-vector 学习路线图统领全篇。LF_GPT_IMAGE_AUTO 关闭时通常无图，此处优雅留白。
    """
    if image:
        # 本地绝对路径转成前端可取的 /assets/<file> URL；已是 /assets URL 或 http(s) 则原样用。
        src = image
        if image.startswith("/") and not image.startswith("/assets/"):
            src = f"/assets/{pathlib.Path(image).name}"
        return [f"![{title} — learning roadmap]({src})", ""]
    return [
        "> 💡 信息图未生成。开启 `LF_GPT_IMAGE_AUTO=1` 或用前端「生成信息图」按钮，"
        "可在此嵌入一张 house-style 学习路线图。",
        "",
    ]


def report_generate_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    title = str(args.get("title") or "学习报告")
    summary = str(args.get("summary") or "")
    days = _normalize_days(args.get("days") or {})
    tips = [str(t) for t in (args.get("tips") or [])]
    rationale = str(args.get("rationale") or "").strip()  # 模型产出的计划设计说明
    image = str(args.get("image") or "").strip()          # 信息图 URL(/assets/..) 或路径；有图才嵌
    total_items = sum(len(items) for items in days.values())
    lines: List[str] = [
        f"# {title}",
        "",
        "> LearnForge study plan: diagnosis-driven, output-oriented, and mock-reviewable.",
        "",
    ]
    # 信息图：嵌入已生成的 infographic（house style PNG）；无图则留占位提示，不阻断。
    lines += _image_block(image, title)
    lines += [
        "## Overview",
        "",
        f"- Summary: {summary or '按弱点优先级生成的分天复习计划。'}",
        f"- Duration: {len(days)} day(s)",
        f"- Knowledge points: {total_items}",
        "- Loop: learn -> explain -> drill -> mock -> write weak memory",
        "",
    ]
    # 计划设计说明：直接渲染模型 rationale（优先于静态模板，保留模型的排序/验收/复盘设计）。
    if rationale:
        lines += ["## Plan Design", "", rationale, ""]
    lines += [
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
            f"- Write a 60-second oral explanation of **{focus}** (definition -> mechanism -> tradeoff).",
            f"- Draw one mechanism diagram or step-by-step flow for {focus} in your own words.",
            "- Answer one follow-up: failure mode, tradeoff, or production debugging signal.",
            "",
            "### Acceptance Criteria",
            f"- Can explain {focus} (definition, mechanism, tradeoff, common pitfall) without notes.",
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
        "rationale": {"type": "string",
                      "description": "计划设计说明（排序依据/每日目标/验收/复盘节点），渲染成 Plan Design 段。"},
        "image": {"type": "string",
                  "description": "学习路线图信息图的 /assets URL 或本地路径，有则嵌入报告顶部。"},
    },
    "required": ["title", "summary", "days"],
}


def register() -> None:
    from ..tools import openai_bridge as toolmod

    if not toolmod.has_handler("report.generate"):
        toolmod.register_tool("report.generate", report_generate_handler, parameters=_PARAMS,
                              description="把学习计划渲染成本地结构化 Markdown 报告(docs/reports/)。")
