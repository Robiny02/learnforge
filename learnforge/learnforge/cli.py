"""LearnForge 交互式 CLI —— Farm Console 终端版（纯 ANSI 256 色，零依赖）。

把 ui-design.md 中的像素农场学习工作台压缩到终端里：顶部是 Farm Console 状态台，
底部是模式栏，中间展示 Manager 的 PLAN→EXECUTE→AGGREGATE，以及一份 Activity Log。
全程离线可跑（无 ANTHROPIC_API_KEY 走确定性兜底）。

运行：
    python -m learnforge.cli                 # 默认库 learnforge.db（缺表自动建）
    python -m learnforge.cli --db /path.db   # 指定 SQLite 库

交互：
    直接输入问题 / 需求          → Manager 自动 PLAN→EXECUTE→AGGREGATE
    /mock <topic>                → 进入多轮模拟面试（interrupt/resume）
    /diagnose                    → 只读弱点诊断
    /plan <goal>                 → 生成学习计划
    /help  /clear  /quit
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import uuid
from datetime import datetime
from typing import Dict, List, Optional

# ----------------------------------------------------------------- ANSI 基元
ESC = "\033["
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def fg(n: int) -> str:
    return f"\033[38;5;{n}m"


def bg(n: int) -> str:
    return f"\033[48;5;{n}m"


def vis_len(s: str) -> int:
    """可见宽度（剔除 ANSI；CJK 记 2 宽，块字符记 1）。"""
    s = _ANSI_RE.sub("", s)
    w = 0
    for ch in s:
        w += 2 if _is_wide(ch) else 1
    return w


def _is_wide(ch: str) -> bool:
    o = ord(ch)
    # 常见 CJK / 全角区间（够用，不引 unicodedata 的 East Asian Width 全表）。
    return (
        0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
        or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFF60
        or 0xFFE0 <= o <= 0xFFE6 or 0x20000 <= o <= 0x3FFFD
    )


# 主题色（cozy farm：夜色底 + 木牌琥珀 + 青绿色记忆水晶）
_EMBER = [29, 35, 71, 107, 143, 179, 215, 220]   # 横向渐变
BORDER = 65      # 苔绿色边框
TITLE = 81       # 亮青标题
ACCENT = 215     # 琥珀强调
MUTED = 244      # 次要灰
WOOD = 130       # 木牌
LEAF = 71        # 作物/通过
SOIL = 94        # 土壤
SKY = 24         # 夜色
OK_C, WARN_C, ESC_C, ERR_C = 35, 220, 141, 203


def term_width(cap: int = 96) -> int:
    return min(shutil.get_terminal_size((80, 24)).columns, cap)


# ----------------------------------------------------------------- 像素块标题
# 5 行高 block-font，仅含 LEARNFORGE 所需字母。'#' = 亮块，' ' = 空。
_FONT: Dict[str, List[str]] = {
    "L": ["#   ", "#   ", "#   ", "#   ", "####"],
    "E": ["####", "#   ", "### ", "#   ", "####"],
    "A": [" ## ", "#  #", "####", "#  #", "#  #"],
    "R": ["### ", "#  #", "### ", "# # ", "#  #"],
    "N": ["#  #", "## #", "# ##", "#  #", "#  #"],
    "F": ["####", "#   ", "### ", "#   ", "#   "],
    "O": [" ## ", "#  #", "#  #", "#  #", " ## "],
    "G": [" ###", "#   ", "# ##", "#  #", " ## "],
}


def render_banner(word: str = "LEARNFORGE") -> List[str]:
    """把单词渲染为 5 行像素块，块字符按横向位置上 ember 渐变色。"""
    gap = " "
    rows: List[str] = []
    # 先算总块宽（用于渐变归一化）。
    glyphs = [_FONT[c] for c in word]
    total_cols = sum(len(g[0]) for g in glyphs) + len(glyphs) - 1
    for r in range(5):
        line = ""
        col = 0
        for gi, g in enumerate(glyphs):
            for ch in g[r]:
                if ch == "#":
                    frac = col / max(1, total_cols - 1)
                    color = _EMBER[min(len(_EMBER) - 1, int(frac * len(_EMBER)))]
                    line += fg(color) + "█" + RESET
                else:
                    line += " "
                col += 1
            if gi != len(glyphs) - 1:
                line += gap
                col += 1
        rows.append(line)
    return rows


# ----------------------------------------------------------------- 面板/排版
def hr(width: int, color: int = BORDER, ch: str = "─") -> str:
    return fg(color) + ch * width + RESET


def panel(title: str, body: List[str], width: int, color: int = BORDER,
          title_color: int = TITLE) -> List[str]:
    """圆角面板；body 行可含 ANSI（按可见宽度对齐）。"""
    inner = width - 2
    out: List[str] = []
    tt = f"{fg(title_color)}{BOLD}{title}{RESET}" if title else ""
    head_fill = inner - 1 - vis_len(tt) - 1 if title else inner
    top = (fg(color) + "╭─" + RESET + (f" {tt} " if title else "")
           + fg(color) + "─" * max(0, head_fill) + "╮" + RESET)
    out.append(top)
    for raw in body:
        for seg in _wrap_visible(raw, inner - 2):
            pad = inner - 2 - vis_len(seg)
            out.append(fg(color) + "│ " + RESET + seg + " " * max(0, pad)
                       + fg(color) + " │" + RESET)
    out.append(fg(color) + "╰" + "─" * inner + "╯" + RESET)
    return out


def _pad_right(line: str, width: int) -> str:
    return line + " " * max(0, width - vis_len(line))


def hstack(left: List[str], right: List[str], gap: int = 2) -> List[str]:
    """并排摆放两个固定宽度面板。"""
    left_w = max((vis_len(x) for x in left), default=0)
    right_w = max((vis_len(x) for x in right), default=0)
    n = max(len(left), len(right))
    out: List[str] = []
    for i in range(n):
        l = left[i] if i < len(left) else " " * left_w
        r = right[i] if i < len(right) else " " * right_w
        out.append(_pad_right(l, left_w) + " " * gap + r)
    return out


def _wrap_display(text: str, width: int) -> List[str]:
    """按显示宽度换行（CJK 记 2 宽）；优先在空格处断行，否则按字符硬断。"""
    out: List[str] = []
    for para in text.split("\n"):
        cur, curw, last_space = "", 0, -1
        for ch in para:
            w = 2 if _is_wide(ch) else 1
            if curw + w > width and cur:
                if last_space > 0 and not _is_wide(ch):  # ASCII：回退到上一个空格断行
                    out.append(cur[:last_space])
                    cur, curw = cur[last_space + 1:], vis_len(cur[last_space + 1:])
                    last_space = -1
                else:
                    out.append(cur)
                    cur, curw, last_space = "", 0, -1
            if ch == " ":
                last_space = len(cur)
            cur += ch
            curw += w
        out.append(cur)
    return out or [""]


def _clip(s: str, width: int) -> str:
    """把字符串裁到 ≤width 显示宽度（CJK 记 2 宽），超出加省略号。"""
    out, w = "", 0
    for ch in s:
        cw = 2 if _is_wide(ch) else 1
        if w + cw > width:
            return out + "…"
        out += ch
        w += cw
    return out


def _wrap_visible(line: str, width: int) -> List[str]:
    """按显示宽度换行；含 ANSI 的行不二次切分（调用方用 _clip 预裁，避免拆断转义码）。"""
    if not line:
        return [""]
    if _ANSI_RE.search(line):
        return [line]
    return _wrap_display(line, width)


def badge(status: str) -> str:
    s = (status or "ok").lower()
    color = {"ok": OK_C, "partial": WARN_C, "needs_input": WARN_C,
             "escalate": ESC_C, "error": ERR_C}.get(s, MUTED)
    return f"{bg(color)}{fg(0)}{BOLD} {s.upper()} {RESET}"


def conf_bar(conf: float, width: int = 12) -> str:
    fill = int(round(conf * width))
    color = OK_C if conf >= 0.7 else WARN_C if conf >= 0.4 else ERR_C
    return (fg(color) + "▰" * fill + fg(MUTED) + "▱" * (width - fill) + RESET
            + f" {conf:.2f}")


# ----------------------------------------------------------------- 头部/帮助
def print_lines(lines: List[str]) -> None:
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def header(width: int, db_path: str) -> None:
    print_lines([""])
    for row in render_banner():
        pad = (width - vis_len(row)) // 2
        print_lines([" " * max(0, pad) + row])

    sub = f"{fg(ACCENT)}{ITALIC}原创像素农场学习工作台{RESET} {fg(MUTED)}· observable multi-agent companion{RESET}"
    print_lines([_center(sub, width)])
    print_lines([""])
    print_lines(farm_scene(width, db_path))
    print_lines(mode_bar(width, "QA"))
    print_lines([""])


def _center(s: str, width: int) -> str:
    pad = (width - vis_len(s)) // 2
    return " " * max(0, pad) + s


def mode_bar(width: int, active: str = "QA") -> List[str]:
    modes = ["QA", "Mock", "Diagnose", "Plan", "Review", "Practice"]
    parts: List[str] = []
    for m in modes:
        if m.lower() == active.lower():
            parts.append(f"{bg(LEAF)}{fg(0)}{BOLD} {m} {RESET}")
        else:
            parts.append(f"{fg(MUTED)}{m}{RESET}")
    line = f"{fg(MUTED)}Mode{RESET}  " + f" {fg(MUTED)}|{RESET} ".join(parts)
    return panel("mode bar", [line], width, color=SOIL, title_color=ACCENT)


def farm_scene(width: int, db_path: str) -> List[str]:
    """Farm Console 的终端主视觉：木屋、任务板、记忆水晶的文字压缩版。"""
    from .config import EMBEDDING_PROVIDER
    from .llm.client import LLM
    from .llm.embeddings import EMBEDDINGS

    llm_on = getattr(LLM, "available", False)
    emb_on = getattr(EMBEDDINGS, "available", False)
    dot = lambda ok: (fg(OK_C) + "●" + RESET) if ok else (fg(ERR_C) + "○" + RESET)  # noqa: E731
    db_short = db_path if len(db_path) <= 36 else "…" + db_path[-35:]
    body = [
        f"{fg(SKY)}┌────────────── night study farm ──────────────┐{RESET}",
        f"{fg(SKY)}│{RESET} {fg(ACCENT)}▣ desk lamp{RESET}   {fg(LEAF)}▥ knowledge field{RESET}   "
        f"{fg(TITLE)}◇ memory crystal{RESET}   {fg(SKY)}│{RESET}",
        f"{fg(SKY)}│{RESET} {fg(WOOD)}▤ quest board{RESET}: ask · diagnose · plan · mock · review   {fg(SKY)}│{RESET}",
        f"{fg(SKY)}└──────────────────────────────────────────────┘{RESET}",
        f"{dot(llm_on)} LLM {fg(MUTED)}{'online' if llm_on else 'offline stub'}{RESET}   "
        f"{dot(emb_on)} embed {fg(MUTED)}{EMBEDDING_PROVIDER}{RESET}   "
        f"{fg(MUTED)}db {db_short}{RESET}",
    ]
    return panel("Farm Console", body, width, color=BORDER, title_color=TITLE)


def help_panel(width: int) -> None:
    rows = [
        f"{fg(ACCENT)}直接输入{RESET}            Farm Console 问答，自动 PLAN→EXECUTE→AGGREGATE",
        f"{fg(ACCENT)}/mock {RESET}{fg(MUTED)}<topic>{RESET}       进入面试小屋；答题 / {fg(TITLE)}/pause /switch <t> /end /back{RESET}",
        f"{fg(ACCENT)}/diagnose{RESET}           生成弱点田块报告（只读诊断）",
        f"{fg(ACCENT)}/plan {RESET}{fg(MUTED)}<goal>{RESET}        在任务公告板上生成学习路径",
        f"{fg(ACCENT)}/note {RESET}{fg(MUTED)}<topic>{RESET}       生成美观学习笔记（先走现有主图，后续接 artifact 工具）",
        f"{fg(ACCENT)}/memory{RESET}             查看记忆水晶：来源、索引、本轮写入",
        f"{fg(ACCENT)}/review  /practice{RESET}  查看复习/练习入口占位",
        f"{fg(ACCENT)}/help  /clear  /quit{RESET}",
        f"{fg(MUTED)}例：{RESET}快面试了帮我准备一下   ·   生成一份 Redis 美观学习笔记   ·   /mock 并发",
    ]
    print_lines(panel("commands", rows, width))


def placeholder_panel(name: str, width: int) -> None:
    if name == "review":
        body = [
            f"{fg(ACCENT)}Daily Review{RESET} 会聚合 mastery、mock weakness、QA uncertain 和 daily memory。",
            f"{fg(MUTED)}CLI MVP 建议展示：今日任务、薄弱田块、三日复习路线、完成状态。{RESET}",
            f"现在可先用 {fg(TITLE)}/diagnose{RESET} 生成诊断，再用 {fg(TITLE)}/plan{RESET} 调整路径。",
        ]
    else:
        body = [
            f"{fg(ACCENT)}Practice{RESET} 预留给本地刷题和未来 LeetCode/MCP 接入。",
            f"{fg(MUTED)}建议默认本地运行 sample test，用户确认后再打开外部提交。{RESET}",
            f"练习结果后续应写入 {fg(TITLE)}interaction_events{RESET}，由 Manager 更新 mastery。",
        ]
    print_lines(panel(f"{name} · coming soon", body, width, color=SOIL, title_color=ACCENT))


# ----------------------------------------------------------------- 结果渲染
def _result_lines(agent: str, resp_result: dict, width: int) -> List[str]:
    """把单个 worker 的 ResponsePayload.result 渲染为若干行。"""
    inner = width - 6
    L: List[str] = []
    if agent == "qa":
        answer = (resp_result.get("answer") or "").strip() or "（无内容）"
        verdict = resp_result.get("verdict", "?")
        L.append(answer)  # 纯文本，交 panel 按显示宽度换行
        cites = resp_result.get("citations") or []
        L.append(f"{fg(MUTED)}verdict={verdict} · citations={len(cites)}{RESET}")
        for c in cites[:3]:
            prefix = f"  └ [{c.get('source_type','?')}] "
            snip = _clip(c.get("snippet") or "", max(8, inner - vis_len(prefix)))
            L.append(f"{fg(MUTED)}{prefix}{snip}{RESET}")
    elif agent == "diagnosis":
        clusters = resp_result.get("clusters") or []
        if clusters:
            L.append(f"{fg(ACCENT)}弱点话题簇{RESET}（按 severity）：")
            for c in clusters[:5]:
                L.append(f"  {fg(ERR_C)}▸{RESET} {c['topic']:<14} {conf_bar(c['severity'], 10)}")
        recs = resp_result.get("recommendations") or []
        for r in recs[:3]:
            L.append(f"{fg(OK_C)}✎{RESET} {_clip(r, inner - 2)}")
        if not clusters and not recs:
            L.append(f"{fg(MUTED)}（数据不足，未见明显弱点）{RESET}")
    elif agent == "planning":
        if resp_result.get("skipped"):
            L.append(f"{fg(WARN_C)}⤼ 跳过改计划{RESET}：{resp_result.get('reason','')}")
        else:
            diff = resp_result.get("diff") or {}
            add, rm, re_ = diff.get("add", []), diff.get("remove", []), diff.get("reorder", [])
            L.append(f"PathDiff：{fg(OK_C)}+{len(add)}{RESET} / {fg(ERR_C)}-{len(rm)}{RESET}"
                     f" / ↻{len(re_)}   {fg(MUTED)}{_clip(diff.get('rationale',''), max(0, inner-30))}{RESET}")
    else:
        txt = str(resp_result)[:inner]
        L.append(f"{fg(MUTED)}{txt}{RESET}")
    return L


def _activity_kind(agent: str, result: dict) -> str:
    if agent == "qa":
        return "RAG" if result.get("citations") else "QA"
    if agent == "diagnosis":
        return "Diagnosis"
    if agent == "planning":
        return "Path"
    if agent == "mock":
        return "Mock"
    return "Trace"


def _activity_lines(plan: List[dict], responses: List, agg: dict,
                    replan: int, width: int) -> List[str]:
    """右侧 Activity Log：压缩展示 agent 编排、记忆读写、降级/跳过等事件。"""
    from .memory.log import MEMORY_LOG

    now = datetime.now().strftime("%H:%M:%S")
    body: List[str] = [
        f"{fg(MUTED)}{now}{RESET} {fg(TITLE)}[Trace]{RESET} {agg.get('trace_id','-')}",
    ]
    chain = " -> ".join(t.get("agent", "?") for t in plan) or "(empty)"
    body.append(f"{fg(MUTED)}{now}{RESET} {fg(ACCENT)}[Plan]{RESET} {chain}")
    if replan:
        body.append(f"{fg(MUTED)}{now}{RESET} {fg(WARN_C)}[Replan]{RESET} x{replan}")

    for t, resp in zip(plan, responses):
        agent = t.get("agent", "?")
        result = resp.result if hasattr(resp, "result") else resp.get("result", {})
        st = resp.status.value if hasattr(resp.status, "value") else (
            resp.get("status") if isinstance(resp, dict) else "ok")
        conf = resp.confidence if hasattr(resp, "confidence") else resp.get("confidence", 0.0)
        kind = _activity_kind(agent, result)
        detail = "ok"
        if agent == "qa":
            detail = f"citations={len(result.get('citations') or [])}"
        elif agent == "diagnosis":
            detail = f"clusters={len(result.get('clusters') or [])}"
        elif agent == "planning":
            diff = result.get("diff") or {}
            if result.get("skipped"):
                detail = "modify skipped"
            else:
                detail = f"+{len(diff.get('add') or [])}/-{len(diff.get('remove') or [])}"
        body.append(
            f"{fg(MUTED)}{now}{RESET} {fg(TITLE)}[{kind}]{RESET} "
            f"{agent} {st} c={conf:.2f} {fg(MUTED)}{detail}{RESET}"
        )

    for e in MEMORY_LOG.events[-6:]:
        tag = {"read": "Memory", "inject": "Inject", "write": "Write",
               "maintain": "Maintain"}.get(e.category, "Memory")
        body.append(
            f"{fg(MUTED)}{e.ts}{RESET} {fg(LEAF)}[{tag}]{RESET} "
            f"{_clip(e.action + ' ' + e.result, max(12, width - 18))}"
        )
    return body or [f"{fg(MUTED)}(no activity){RESET}"]


def _friendly_reply_from_workers(plan: List[dict], responses: List) -> str:
    if not plan or not responses:
        return ""
    t = plan[-1]
    resp = responses[-1]
    agent = t.get("agent")
    result = resp.result if hasattr(resp, "result") else resp.get("result", {})
    if agent == "diagnosis":
        clusters = result.get("clusters") or []
        recs = result.get("recommendations") or []
        if clusters:
            tops = "、".join(c.get("topic", "") for c in clusters[:3])
            return f"诊断完成：目前最需要照看的薄弱田块是 {tops}。"
        if recs:
            return recs[0]
        return "诊断完成：目前没有足够信号判断弱点，建议先做一次问答或模拟面试采集数据。"
    if agent == "planning":
        if result.get("skipped"):
            return result.get("reason", "计划调整已跳过。")
        diff = result.get("diff") or {}
        return diff.get("rationale") or "学习路径已生成或调整。"
    if agent == "qa":
        return result.get("answer", "")
    if agent == "mock":
        return result.get("summary", "") or "模拟面试状态已更新。"
    return ""


def render_exchange(plan: List[dict], responses: List, agg: dict,
                    replan: int, width: int) -> None:
    # PLAN：DAG 箭头
    chain = (f" {fg(MUTED)}→{RESET} ").join(
        f"{fg(TITLE)}{t['agent']}{RESET}" for t in plan
    ) or f"{fg(MUTED)}(空){RESET}"
    plan_line = f"{fg(MUTED)}PLAN{RESET}  {chain}"
    if replan:
        plan_line += f"   {fg(WARN_C)}↻ replan×{replan}{RESET}"
    print_lines([""] + panel("manager · plan", [plan_line], width, color=BORDER))

    # EXECUTE：逐 worker 结果
    for t, resp in zip(plan, responses):
        agent = t["agent"]
        rd = resp.result if hasattr(resp, "result") else resp.get("result", {})
        st = resp.status.value if hasattr(resp.status, "value") else (
            resp.get("status") if isinstance(resp, dict) else "ok")
        conf = resp.confidence if hasattr(resp, "confidence") else resp.get("confidence", 0.0)
        title = f"worker · {agent}"
        body = [f"{badge(st)}  {conf_bar(conf)}"] + _result_lines(agent, rd, width)
        print_lines(panel(title, body, width, color=ACCENT, title_color=ACCENT))

    # AGGREGATE：左侧聚合回复 + 右侧 Activity Log（窄屏自动上下排列）。
    reply = (agg.get("reply_text") or "").strip()
    body = [f"{badge(agg.get('status','ok'))}"]
    if reply.startswith("[stub aggregate]"):
        reply = _friendly_reply_from_workers(plan, responses)
    if reply:
        body += ["", reply]  # 纯文本，panel 按显示宽度换行
    for a in agg.get("next_actions") or []:
        body.append(f"{fg(OK_C)}↳{RESET} {_clip(a, width - 8)}")
    body.append(f"{fg(MUTED)}trace_id={agg.get('trace_id','-')}{RESET}")
    if width >= 88:
        left_w = max(42, int(width * 0.58))
        right_w = width - left_w - 2
        activity_body = _activity_lines(plan, responses, agg, replan, right_w)
        left = panel("Farm Console · reply", body, left_w, color=TITLE)
        right = panel("Activity Log", activity_body, right_w, color=SOIL, title_color=ACCENT)
        print_lines(hstack(left, right, gap=2))
    else:
        activity_body = _activity_lines(plan, responses, agg, replan, width)
        print_lines(panel("Farm Console · reply", body, width, color=TITLE))
        print_lines(panel("Activity Log", activity_body, width, color=SOIL, title_color=ACCENT))


# ----------------------------------------------------------------- mock 交互
_RISK_LABELS = {"overclaim": "夸大无证据", "no_evidence": "缺证据链", "vague": "含糊/过短"}


def _score_line(s) -> str:
    ov = s.overall if s.overall is not None else "—"
    d = s.dims
    line = (f"{fg(ACCENT)}评分{RESET} {ov}/5  "
            f"{fg(MUTED)}正确性{d.correctness} 深度{d.depth} 表达{d.clarity}{RESET}")
    risks = getattr(s, "risk_flags", None) or []
    if risks:
        tags = "、".join(_RISK_LABELS.get(r, r) for r in risks)
        line += f"  {fg(WARN_C)}⚠ 风险：{tags}{RESET}"
    return line


def _resolve_mock_frame(text: str, width: int):
    """把 /mock 后的自然语言解析成针对性 MockSlots；缺岗位/简历锚点时主动澄清一轮。"""
    from .intent import IntentResolver

    resolver = IntentResolver()
    frame = resolver.mock_frame(text or "")
    # 最多澄清两轮（够补齐 岗位 / 简历），避免无限追问。
    for _ in range(2):
        if not frame.clarification:
            break
        print_lines(panel("interviewer · 澄清", [frame.clarification], width,
                          color=TITLE, title_color=TITLE))
        try:
            reply = input(_prompt("you")).strip()
        except (EOFError, KeyboardInterrupt):
            print_lines([""])
            break
        if not reply or reply in ("/skip", "跳过"):
            frame.clarification = None
            break
        frame = resolver.resume_pending(frame.model_dump(), reply)
    return frame.mock_slots()


def run_mock(mgr, topic: str, width: int) -> None:
    from .agents.mock.actions import (
        CHANNEL_CONTROL,
        CHANNEL_INTERRUPT,
        Step,
        channel_of,
        run_step,
    )
    from .contracts.agents.mock import MockInput
    from .intent.mock_turn import classify_mock_input

    session_id = f"cli-{uuid.uuid4().hex[:8]}"
    slots = _resolve_mock_frame(topic, width)
    topic = slots.topic or "综合技术面试"
    ictx = slots.to_interview_context()
    role_hint = f" · {slots.target_role}" if slots.target_role else ""
    print_lines(panel(f"mock · {topic}{role_hint}", [
        f"{fg(MUTED)}多轮模拟面试已开始。直接输入作答（也可自然说“换个话题/太难了/暂停”）；{RESET}",
        f"{fg(TITLE)}/pause{RESET} 暂停  {fg(TITLE)}/switch <topic>{RESET} 换题  "
        f"{fg(TITLE)}/end{RESET} 结束复盘  {fg(TITLE)}/back{RESET} 退出",
    ], width, color=ESC_C, title_color=ESC_C))

    out = mgr.mock.run(MockInput(
        topic=topic, session_id=session_id, context=ictx,
        target_difficulty=slots.difficulty or 3, max_turns=slots.max_turns or 10))
    while True:
        if out.status == "active" and out.question:
            print_lines(panel(f"Q{out.turn_index + 1} · interviewer",
                              [out.question], width, color=ACCENT, title_color=ACCENT))
        elif out.status == "paused":
            print_lines(panel("mock · paused",
                              [f"{fg(WARN_C)}已暂停{RESET}，输入任意内容继续，或 /back 退出。"],
                              width, color=WARN_C, title_color=WARN_C))
        elif out.status == "review":
            _render_review(out, width)
            settlement = mgr.settle_mock(session_id)
            _render_settlement(settlement, width)
            return
        elif out.status == "escalate":
            print_lines(panel("mock · escalate → manager", [
                f"{fg(ESC_C)}面试中触发跨能力请求（{out.escalate_action}），交回 Manager。{RESET}",
            ], width, color=ESC_C, title_color=ESC_C))
            return

        try:
            raw = input(_prompt("answer")).strip()
        except (EOFError, KeyboardInterrupt):
            print_lines([""])
            return
        if not raw:
            continue
        if raw in ("/back", "/quit"):
            return
        # 显式 slash 命令直达 interrupt（/switch 带目标，保留其精确措辞）。
        if raw == "/pause":
            out = mgr.mock.run(MockInput(topic=topic, session_id=session_id, user_interrupt="暂停"))
        elif raw == "/end":
            out = mgr.mock.run(MockInput(topic=topic, session_id=session_id, user_interrupt="结束"))
        elif raw.startswith("/switch"):
            tgt = raw[len("/switch"):].strip()
            out = mgr.mock.run(MockInput(topic=topic, session_id=session_id,
                                         user_interrupt=f"换到 {tgt}" if tgt else "换个话题"))
        else:
            # 自然语言：一句话 → 计划（当前单步），经动作执行器按注册表 channel 分发。
            step = Step(classify_mock_input(out.question or "", raw), raw)
            answered = channel_of(step.action) not in (CHANNEL_CONTROL, CHANNEL_INTERRUPT)
            out = run_step(mgr.mock, session_id, step)
            if out.followup:  # 即时控制的提示/答案/点评/跳过说明。
                print_lines(panel("interviewer · 即时", [out.followup], width,
                                  color=TITLE, title_color=TITLE))
            if out.turn_scores and answered:
                print_lines(["  " + _score_line(out.turn_scores[-1])])


def _render_review(out, width: int) -> None:
    r = out.review
    body: List[str] = []
    if r is None:
        body.append(f"{fg(MUTED)}（样本不足，无复盘）{RESET}")
    else:
        if r.summary:
            body += [r.summary, ""]
        for s in r.strengths[:3]:
            body.append(f"{fg(OK_C)}＋{RESET} {_clip(s, width - 8)}")
        for w in r.weaknesses[:4]:
            body.append(f"{fg(ERR_C)}－{RESET} {_clip(w.topic + '：' + w.evidence, width - 8)}")
        for n in r.next_steps[:3]:
            body.append(f"{fg(TITLE)}→{RESET} {_clip(n, width - 8)}")
    avg = [s.overall for s in out.turn_scores if s.overall is not None]
    if avg:
        body.append(f"{fg(MUTED)}{len(avg)} 轮平均 {sum(avg)/len(avg):.1f}/5{RESET}")
    print_lines(panel("◆ coach · 复盘", body, width, color=TITLE))
    _render_answer_cards(r, width)


def _render_answer_cards(report, width: int) -> None:
    """渲染高风险轮的更优回答建议（接入 LLMInternSkill answer-cards）。"""
    cards = getattr(report, "answer_cards", None) if report else None
    if not cards:
        return
    for c in cards[:3]:
        body = [
            f"{fg(MUTED)}{_clip(c.question, width - 8)}{RESET}",
            f"{fg(ERR_C)}风险{RESET} {_clip(c.why_risky, width - 8)}",
            f"{fg(WARN_C)}及格{RESET} {_clip(c.passable, width - 8)}",
            f"{fg(OK_C)}更强{RESET} {_clip(c.strong, width - 8)}",
        ]
        if c.evidence_needed:
            body.append(f"{fg(TITLE)}补证据{RESET} {_clip('、'.join(c.evidence_needed), width - 8)}")
        print_lines(panel("answer card · 更优回答", body, width, color=ACCENT))


def _render_settlement(s: dict, width: int) -> None:
    body = [f"{fg(OK_C)}✓ 掌握度已提交{RESET} {s.get('mastery_committed',0)} 个 atom"
            f"   {fg(MUTED)}（Manager 唯一写者）{RESET}"]
    diag = s.get("diagnosis")
    if diag and (diag.get("clusters")):
        tops = "、".join(c["topic"] for c in diag["clusters"][:3])
        body.append(f"{fg(ACCENT)}post-mock 诊断{RESET}：{tops}  "
                    f"{fg(MUTED)}(${s.get('diagnosis_cost_usd',0):.4f}){RESET}")
    if s.get("diagnosis_image_path"):
        body.append(f"{fg(TITLE)}🖼️ 诊断信息图{RESET}：{s['diagnosis_image_path']}")
    print_lines(panel("settlement · Q5 自动诊断", body, width, color=BORDER))


# ----------------------------------------------------------------- 主循环
def _prompt(label: str = "you") -> str:
    return f"\n{fg(LEAF)}{BOLD}farm{RESET}{fg(MUTED)}:{RESET}{fg(TITLE)}{BOLD}{label} ›{RESET} "


_SESSION_ID = f"cli-{uuid.uuid4().hex[:8]}"  # 单次 CLI 进程内共享会话（短期记忆连续）


def handle_request(mgr, text: str, width: int) -> None:
    """复刻 Manager.handle，但保留 responses 以可视化各 worker（Design §2b）。"""
    trace_id = f"cli-{uuid.uuid4().hex[:6]}"
    # 记忆面板入口：本轮读取稳定 + 会话记忆（清空并开始记录本轮记忆操作）。
    mgr.begin_memory_turn(text, _SESSION_ID)
    # Manager ReAct 编排：每步看子 agent 结果决策，直到 finish/预算（不预拆 DAG、无 replan）。
    responses, meta, plan = mgr.execute_dynamic(text, trace_id=trace_id)
    agg = mgr.aggregate(responses, plan, meta)
    agg["trace_id"] = trace_id
    # 短期记忆：收尾追加本轮原文（超 token 阈值则 compaction），供下一轮载入。
    # 重要结果（带引用答案 / 诊断·规划·结算）→ pin 不压缩。
    mgr.record_turn(_SESSION_ID, text, agg.get("reply_text") or "",
                    important=mgr.turn_is_important(agg.get("citations"), plan))
    render_exchange(plan, responses, agg, 0, width)
    render_memory_panel(width)


def render_memory_overview(db_path: str, width: int) -> None:
    """记忆来源 + 长期记忆分类概览（REQUIREMENTS §2.1 / §2.2）。"""
    from .memory.log import memory_files_overview, memory_index_overview

    files = [f"{fg(TITLE)}▸{RESET} {x}" for x in memory_files_overview(db_path)]
    print_lines(panel("memory · 记忆来源", files, width, color=BORDER, title_color=TITLE))
    idx = [f"{fg(ACCENT)}▸{RESET} {x}" for x in memory_index_overview(db_path)]
    print_lines(panel("memory · 长期记忆分类", idx, width, color=BORDER, title_color=ACCENT))


def render_memory_panel(width: int) -> None:
    """渲染本轮记忆日志面板（REQUIREMENTS §2.3 最近操作 + §4 最小摘要）。"""
    from .memory.log import INJECT, MAINTAIN, READ, MEMORY_LOG

    _cat_color = {READ: TITLE, INJECT: OK_C, "write": ACCENT, MAINTAIN: MUTED}
    _cat_tag = {READ: "读", INJECT: "注", "write": "写", MAINTAIN: "维"}
    body: List[str] = []
    for e in MEMORY_LOG.events:
        c = _cat_color.get(e.category, MUTED)
        tag = _cat_tag.get(e.category, "·")
        line = f"{fg(MUTED)}{e.ts}{RESET} {fg(c)}[{tag}]{RESET} {e.action}：{e.result}"
        if e.reason:
            line += f" {fg(MUTED)}（{e.reason}）{RESET}"
        body.append(line)
    if not body:
        body.append(f"{fg(MUTED)}（本轮无记忆操作）{RESET}")
    body.append(hr(width - 4, MUTED))
    s = MEMORY_LOG.summary()
    yn = lambda b: (fg(OK_C) + "是" + RESET) if b else (fg(MUTED) + "否" + RESET)  # noqa: E731
    body.append(
        f"MEMORY.md {yn(s['loaded_memory_md'])}  会话 {yn(s['loaded_session'])}  "
        f"搜索长期 {yn(s['searched_daily'])}  注入 {fg(ACCENT)}{s['injected_count']}{RESET} 条  "
        f"候选 {yn(s['extracted_candidate'])}"
    )
    if s["saved"]:
        body.append(f"{fg(OK_C)}✓ {s['save_result']}{RESET}")
    elif s["skip_reason"]:
        body.append(f"{fg(WARN_C)}⤼ 未保存：{s['skip_reason']}{RESET}")
    print_lines(panel("memory · 本轮记忆日志", body, width, color=MUTED, title_color=TITLE))


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="LearnForge 交互式 CLI")
    parser.add_argument("--db", default=None, help="SQLite 库路径（默认 config.DB_PATH）")
    args = parser.parse_args(argv)

    from .orchestration.manager import ManagerAgent
    from .config import DB_PATH
    from .storage.db import init_db

    db_path = args.db or DB_PATH
    init_db(db_path)  # 幂等：缺表自动建（schema 用 IF NOT EXISTS）
    mgr = ManagerAgent(db_path=db_path)

    width = term_width()
    sys.stdout.write("\033[2J\033[H")  # 清屏
    header(width, db_path)
    help_panel(width)

    while True:
        try:
            raw = input(_prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            print_lines(["", f"{fg(ACCENT)}再见，明天继续耕耘。{RESET}"])
            return
        if not raw:
            continue

        width = term_width()
        if raw in ("/quit", "/exit", "/q"):
            print_lines([f"{fg(ACCENT)}再见，明天继续耕耘。{RESET}"])
            return
        if raw == "/clear":
            sys.stdout.write("\033[2J\033[H")
            header(width, db_path)
            continue
        if raw == "/help":
            help_panel(width)
            continue
        if raw.startswith("/mock"):
            run_mock(mgr, raw[len("/mock"):].strip() or "通用技术面试", width)
            continue
        if raw == "/memory":
            render_memory_overview(db_path, width)
            continue
        if raw == "/diagnose":
            handle_request(mgr, "诊断我的弱点", width)
            continue
        if raw.startswith("/plan"):
            goal = raw[len("/plan"):].strip() or "制定一份学习计划"
            handle_request(mgr, f"生成学习计划：{goal}", width)
            continue
        if raw.startswith("/note"):
            topic = raw[len("/note"):].strip() or "当前学习主题"
            handle_request(mgr, f"生成一份美观、结构化、可复习的学习笔记：{topic}", width)
            continue
        if raw == "/review":
            placeholder_panel("review", width)
            continue
        if raw == "/practice":
            placeholder_panel("practice", width)
            continue

        handle_request(mgr, raw, width)


if __name__ == "__main__":
    main()
