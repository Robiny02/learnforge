"""gpt-image 出图集成（gpt_image.generate 工具）+ 学习视觉 house style 拼装。

这是 LearnForge 把「学习计划 / 诊断结论」渲染成美观信息图 PNG 的唯一入口，
与 notion.sync / report.generate 同构：只读外部 API、只写本仓库 docs/assets/ 下的图片，
绝不碰任何 LearnForge 状态（mastery / path）。

出图走 OpenRouter（OpenAI 兼容网关）chat/completions + 图像模型（默认 openai/gpt-5-image-mini），
密钥读 OPENROUTER_API_KEY（环境变量或仓库根 .env）。无 key → available()=False，
调用方（PlanningAgent / settle_mock）优雅降级回 Markdown 报告，"chain always passes"。

风格事实源：runtime/skills/gpt-image-2-style-library/SKILL.md（flat vector / 浅底 / indigo-teal-amber）。
本模块的 prompt 拼装即该 house style 在「学习路线图 / 诊断仪表盘」两类图上的落地。
"""

from __future__ import annotations

import base64
import os
import pathlib
from datetime import datetime
from typing import Any, Dict, List, Optional

def _find_repo_root() -> pathlib.Path:
    """向上找含 .env / .git 的目录作为仓库根（嵌套 learnforge/learnforge 布局下，
    .env 与既有 docs/assets 出图约定都在最外层仓库根）。找不到则退回包根。"""
    here = pathlib.Path(__file__).resolve()
    for base in here.parents:
        if (base / ".env").exists() or (base / ".git").exists():
            return base
    return here.parents[2]


REPO_ROOT = _find_repo_root()
ASSETS_DIR = REPO_ROOT / "docs" / "assets"
ASSET_URL_PREFIX = "/assets"  # FastAPI 把 ASSETS_DIR 挂在这里，前端用此 URL 取图


def asset_url(path: Optional[str]) -> Optional[str]:
    """把 docs/assets 下的绝对路径转成前端可取的 HTTP URL（/assets/<filename>）。"""
    if not path:
        return None
    return f"{ASSET_URL_PREFIX}/{pathlib.Path(path).name}"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-5-image-mini"
_TIMEOUT = float(os.environ.get("LF_GPT_IMAGE_TIMEOUT", "180"))

# ---- house style（与 gpt-image-2-style-library 同一套配色/形态，便于全站风格一致）----
_STYLE = (
    "Clean flat vector infographic, off-white background (#F7F9FC), generous whitespace, "
    "rounded-rectangle cards with thin 1.5px borders and very subtle shadows, "
    "sans-serif labels (Inter/Helvetica style), monospace for tech terms (JetBrains Mono style). "
    "Color system: indigo #4F46E5 (primary), teal #0EA5A4 (secondary), amber #F59E0B (accent/warning), "
    "slate #1E293B (text), gray #64748B (muted). Same semantics use same color. "
    "Professional, minimal, high readability. "
    "Do NOT: photorealism, 3D, neon gradients, dark cyberpunk, cartoon characters, "
    "long paragraphs inside the image, tangled crossing arrows."
)


# ----------------------------------------------------------------- 出图核心
def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    # 兜底：从仓库根 .env 读一次（不依赖 python-dotenv）。
    env_path = REPO_ROOT / ".env"
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in {"OPENROUTER_API_KEY", "OPENAI_API_KEY"}:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _disabled() -> bool:
    """显式关闭或测试环境 → 不出图。保证 "tests run fully offline" 不变量：
    仓库 .env 带 OPENROUTER_API_KEY，但测试绝不应打真实网络。"""
    if os.environ.get("LF_GPT_IMAGE", "").strip().lower() in {"0", "false", "no", "off"}:
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def available() -> bool:
    """是否具备出图能力（有 key 且未被关闭/非测试）。否则调用方降级回 Markdown。

    注意：available 仅表示"能出图"（按需出图端点据此判断），不代表"自动出图"。
    """
    return bool(_api_key()) and not _disabled()


def auto_enabled() -> bool:
    """是否在 agent 链路里**自动**出图。默认关（出图慢/费钱，改按需触发）；
    设 LF_GPT_IMAGE_AUTO=1 可恢复自动。"""
    flag = os.environ.get("LF_GPT_IMAGE_AUTO", "").strip().lower()
    return available() and flag in {"1", "true", "yes", "on"}


def generate_image_core(prompt: str, filename: str = "diagram.png",
                        model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """生成一张图并存到 docs/assets/<filename>，返回 {ok, path|error}。"""
    key = _api_key()
    if not key:
        return {"ok": False, "error": "OPENROUTER_API_KEY 未设置（无 key，跳过出图）。"}
    if not filename.lower().endswith(".png"):
        filename += ".png"
    try:
        import httpx

        resp = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "modalities": ["image", "text"],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return {"ok": False, "error": str(data["error"])[:300]}
        imgs = (data.get("choices") or [{}])[0].get("message", {}).get("images") or []
        if not imgs:
            return {"ok": False, "error": f"响应无图像：{str(data)[:200]}"}
        url = imgs[0].get("image_url", {}).get("url", "")
        if "base64," in url:
            url = url.split("base64,", 1)[1]
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        out = ASSETS_DIR / filename
        out.write_bytes(base64.b64decode(url))
        return {"ok": True, "path": str(out), "observation": f"已生成信息图 {out}"}
    except Exception as e:  # noqa: BLE001 —— 任何失败都降级，不阻断主链路
        detail = getattr(getattr(e, "response", None), "text", "") or str(e)
        return {"ok": False, "error": f"{type(e).__name__}: {detail[:300]}"}


# ----------------------------------------------------------------- 五段式 prompt 拼装
def _slug(text: str) -> str:
    import re

    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip().lower())
    return s.strip("-")[:40] or "untitled"


def build_plan_prompt(title: str, days: Dict[int, List[str]],
                      summary: Optional[str] = None, tips: Optional[List[str]] = None) -> str:
    """学习路线图：按天分桶的里程碑时间线（每天一张卡，卡内列知识点）。"""
    day_blocks: List[str] = []
    for d in sorted(days):
        items = "; ".join(str(x) for x in days[d][:5]) or "(review)"
        day_blocks.append(f"Day {d + 1}: {items}")
    timeline = " | ".join(day_blocks) or "Day 1: review"
    tip_line = ""
    if tips:
        tip_line = " Footer next-step tips: " + "; ".join(t for t in tips[:2]) + "."
    return (
        "A flat vector learning roadmap / study timeline infographic for a programmer.\n"
        f"TITLE (top, centered, bold): {title}.\n"
        f"{('SUBTITLE: ' + summary + '.') if summary else ''}\n"
        "COMPOSITION: a left-to-right horizontal timeline / winding path connecting the days as "
        "milestone nodes (numbered circles ①②③...). Each day is a rounded-rectangle card placed "
        "along the path, card header shows the day number in indigo, body lists its topics as short "
        "bullet chips. Keep topic labels exactly as given, short, no paragraphs. "
        "Use teal for completed-feel accents and amber to highlight the hardest day. "
        "Add a small legend box at bottom-right (day = milestone, chip = knowledge point)." + tip_line + "\n"
        f"DAYS DATA: {timeline}\n"
        f"STYLE: {_STYLE}\n"
        "FORMAT: landscape 1536x1024."
    )


def build_diagnosis_prompt(clusters: List[Dict[str, Any]],
                           weak_atoms: Optional[List[Dict[str, Any]]] = None,
                           recommendations: Optional[List[str]] = None) -> str:
    """诊断仪表盘：弱点簇严重度横向条形 + 关键薄弱知识点掌握度，警示色编码。"""
    bars: List[str] = []
    for c in sorted(clusters, key=lambda x: -float(x.get("severity", 0)))[:6]:
        sev = int(round(float(c.get("severity", 0)) * 100))
        bars.append(f"{c.get('topic', '?')} = {sev}% severity")
    bar_data = " | ".join(bars) or "No weak clusters"
    atoms_line = ""
    if weak_atoms:
        chips = "; ".join(
            f"{a.get('topic', '?')} {int(round(float(a.get('mastery', 0)) * 100))}%"
            for a in weak_atoms[:5]
        )
        atoms_line = f"\nWEAKEST KNOWLEDGE POINTS (mastery %): {chips}"
    rec_line = ""
    if recommendations:
        rec_line = "\nFooter recommendation chips: " + "; ".join(r for r in recommendations[:3]) + "."
    return (
        "A flat vector skills-diagnosis dashboard infographic for a programmer after a mock interview.\n"
        "TITLE (top, centered, bold): Weakness Diagnosis.\n"
        "COMPOSITION: left panel = horizontal bar chart of weak-topic severity (higher = longer bar), "
        "bars colored on a teal→amber→red scale by severity (low to high). "
        "Right panel = small cards for the weakest knowledge points showing a mastery percentage and a "
        "5-segment progress meter. Keep all topic labels exactly as given, short, no paragraphs. "
        "Add a legend explaining the severity color scale." + rec_line + "\n"
        f"SEVERITY DATA: {bar_data}" + atoms_line + "\n"
        f"STYLE: {_STYLE}\n"
        "FORMAT: landscape 1536x1024."
    )


# ----------------------------------------------------------------- 高层封装（供 agent 直接调用）
def generate_plan_infographic(title: str, days: Dict[int, List[str]],
                              summary: Optional[str] = None,
                              tips: Optional[List[str]] = None) -> Dict[str, Any]:
    if not available():
        return {"ok": False, "error": "no key"}
    fname = f"learning-plan-{_slug(title)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    return generate_image_core(build_plan_prompt(title, days, summary, tips), fname)


def generate_diagnosis_chart(clusters: List[Dict[str, Any]],
                             weak_atoms: Optional[List[Dict[str, Any]]] = None,
                             recommendations: Optional[List[str]] = None) -> Dict[str, Any]:
    if not available():
        return {"ok": False, "error": "no key"}
    fname = f"diagnosis-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    return generate_image_core(
        build_diagnosis_prompt(clusters, weak_atoms, recommendations), fname
    )


# ----------------------------------------------------------------- 工具注册（与 report/notion 同构）
def gpt_image_generate_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(args.get("prompt") or "")
    if not prompt:
        return {"ok": False, "error": "缺少 prompt"}
    filename = str(args.get("filename") or "diagram.png")
    model = str(args.get("model") or DEFAULT_MODEL)
    return generate_image_core(prompt, filename, model)


_PARAMS = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "完整英文出图描述（按 house style 拼装）。"},
        "filename": {"type": "string", "description": "输出 PNG 文件名（kebab-case）。"},
        "model": {"type": "string", "description": "OpenRouter 图像模型 id。"},
    },
    "required": ["prompt"],
}


def register() -> None:
    from ..mcp import tools as toolmod

    if not toolmod.has_handler("gpt_image.generate"):
        toolmod.register_tool(
            "gpt_image.generate", gpt_image_generate_handler, parameters=_PARAMS,
            description="用 gpt 图像模型(经 OpenRouter)把学习计划/诊断渲染成信息图 PNG(docs/assets/)。",
        )
