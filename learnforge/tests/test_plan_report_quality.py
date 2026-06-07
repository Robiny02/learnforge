"""学习计划/报告生成优化的测试（report 嵌图 + rationale + 每日焦点；planning SOP）。

对应改动：
- report.generate 渲染模型 rationale 成 Plan Design 段；有信息图(URL/路径)则嵌入报告顶部，
  无图给占位提示（参考 gpt-image house style，runtime/skills/gpt-image-2-style-library）。
- 每日 Practice/Acceptance 关联当天焦点知识点，而非千篇一律模板。
- PLANNING_SKILL 注入 progressive `sop`（编号步骤 + few-shot rationale 颗粒度）。

全部离线、确定性（不触网、不出真图）。
"""

from __future__ import annotations

import pathlib

from learnforge.contracts.enums import AgentId
from learnforge.integrations.report import report_generate_handler
from learnforge.skills.bootstrap import ensure_skills_registered
from learnforge.skills.registry import SKILL_REGISTRY


def _render(args: dict) -> str:
    out = report_generate_handler(args)
    assert out["ok"], out
    path = pathlib.Path(out["path"])
    try:
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)  # 不在 docs/reports 留测试产物


# --------------------------------------------------------------------------- #
# 报告：嵌入信息图 + 渲染 rationale + 每日焦点
# --------------------------------------------------------------------------- #
def test_report_embeds_image_and_rationale():
    txt = _render({
        "title": "LearnForge 学习计划",
        "summary": "弱点优先",
        "days": {"0": ["[缓存] 缓存击穿", "[缓存] 缓存雪崩"], "1": ["[并发] 线程池"]},
        "rationale": "缓存掌握度最低且高频，放 Day1 先打通；Day2 做一场 mock 检验能否抗追问。",
        "image": "/assets/learning-plan-x-20260606.png",
        "tips": ["每两个学习日做一次短 mock"],
    })
    # 信息图嵌入报告顶部
    assert "![LearnForge 学习计划 — learning roadmap](/assets/learning-plan-x-20260606.png)" in txt
    # 模型 rationale 渲染成 Plan Design 段（不再被丢弃）
    assert "## Plan Design" in txt
    assert "Day1 先打通" in txt
    # 每日焦点进 Learning Targets 与 Practice/Acceptance
    assert "## Day 1: 缓存击穿 / 缓存雪崩" in txt
    assert "**缓存击穿 / 缓存雪崩**" in txt  # 60s 口述任务带当天焦点
    assert "## Day 2: 线程池" in txt


def test_report_absolute_local_path_is_rewritten_to_assets_url():
    txt = _render({
        "title": "T", "summary": "s", "days": {"0": ["x"]},
        "image": "/Users/foo/learnforge/docs/assets/learning-plan-y.png",
    })
    # 本地绝对路径 → /assets/<file> 前端可取 URL
    assert "](/assets/learning-plan-y.png)" in txt


def test_report_placeholder_when_no_image():
    txt = _render({"title": "T", "summary": "s", "days": {"0": ["x"]}})
    assert "![" not in txt  # 无图不嵌空图片
    assert "LF_GPT_IMAGE_AUTO" in txt  # 给出如何生成信息图的占位提示


def test_report_backward_compatible_without_new_args():
    # 不传 rationale/image 仍生成合法报告（向后兼容）。
    txt = _render({"title": "T", "summary": "s", "days": {"0": ["[db] B+树索引"]}})
    assert "# T" in txt
    assert "## Day 1: B+树索引" in txt
    assert "## Plan Design" not in txt  # 无 rationale 则不渲染该段


def test_report_empty_day_falls_back_gracefully():
    txt = _render({"title": "T", "summary": "s", "days": {"0": []}})
    assert "Review and consolidate" in txt  # 空日不报错，给复盘占位


# --------------------------------------------------------------------------- #
# 计划：PLANNING_SKILL 注入 SOP + few-shot（经默认加载链激活）
# --------------------------------------------------------------------------- #
def test_planning_skill_loads_sop_and_fewshot():
    ensure_skills_registered()
    sk = SKILL_REGISTRY.primary(AgentId.PLANNING)
    instr = sk.load_instructions()
    assert "排程 SOP" in instr
    assert "few-shot" in instr
    # few-shot 给出好/烂 rationale 对照，压住「已生成计划」式空话
    assert "烂 rationale" in instr and "好 rationale" in instr
