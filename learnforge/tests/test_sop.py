"""Step A 回归：结构化 SOP（Active Skill 稳定内容）可加载、可渲染、进入 prompt、且与动态上下文分离。

全离线、确定性。守护：
  - 每个 Agent 能加载自己的 SOP（render 进 load_instructions → 即 prompt）。
  - SOP 是纯函数渲染（不依赖运行期状态），可独立测试。
  - SOP 不混入动态上下文（不含 Conversation State / Evidence 标记）。
  - 兼容：未迁移 SOP 的 skill 仍能渲染（回退 system_prompt），不报错。
"""

from __future__ import annotations

import pytest

from learnforge.skills.base import Skill
from learnforge.skills.definitions import _ALL
from learnforge.skills.sop import SOP, render_sop
from learnforge.skills.sops import SOPS

# 本批应携带结构化 SOP 的 skill（用户点名的 Manager/QA/Planning/Diagnosis/Mock + 简历）。
_EXPECTED_SOP_SKILLS = {
    "manager.v1", "qa.shell.v1", "qa.synthesizer.v1", "planning.v1",
    "diagnosis.v1", "diagnosis.resume.v1", "mock.shell.v1",
    "mock.interviewer.v1", "mock.coach.v1",
}


def _spec(name):
    return next(s for s in _ALL if s.name == name)


# ----------------------------- 渲染：纯函数、确定性、含小标题 -----------------------------

def test_render_sop_is_deterministic_and_pure():
    sop = SOP(role="r", responsibility=["a"], steps=["x", "y"], not_responsible=["no"])
    assert render_sop(sop) == render_sop(sop)            # 同输入同输出
    out = render_sop(sop)
    assert out.startswith("r")
    assert "【职责】" in out and "【执行步骤】" in out and "【不负责】" in out
    assert "1. x" in out and "2. y" in out               # steps 自动编号


def test_render_sop_skips_empty_sections():
    out = render_sop(SOP(role="只一句"))
    assert out == "只一句"                                # 空字段不渲染空标题


# ----------------------------- 每个目标 Agent 都有 SOP 且进入 prompt -----------------------------

@pytest.mark.parametrize("name", sorted(_EXPECTED_SOP_SKILLS))
def test_each_named_agent_has_sop_in_prompt(name):
    spec = _spec(name)
    assert spec.sop is not None, f"{name} 应携带结构化 SOP"
    skill = Skill(spec)
    instructions = skill.load_instructions()             # = 进 constitution → 进 prompt 的文本
    # SOP 的 role 一句话应出现在加载到 prompt 的说明里。
    assert spec.sop.role.split("：")[0][:6] in instructions
    assert skill.sop_text() != ""


def test_sops_registry_matches_attached_specs():
    for name in _EXPECTED_SOP_SKILLS:
        assert name in SOPS
        assert _spec(name).sop is SOPS[name]             # 登记表与 skill 上挂的是同一对象


# ----------------------------- SOP 与动态上下文分离（核心收敛原则）-----------------------------

@pytest.mark.parametrize("name", sorted(_EXPECTED_SOP_SKILLS))
def test_sop_has_no_dynamic_context_markers(name):
    """SOP 是稳定能力说明：不得含会话历史/附件内容/检索结果等动态注入标记。"""
    text = render_sop(_spec(name).sop)
    for banned in ("【最近对话】", "【可引用附件】", "【最近调用】", "【早期会话摘要】", "【重要结果·固定】"):
        assert banned not in text, f"{name} SOP 不应混入动态 Conversation State 段：{banned}"


# ----------------------------- 兼容：未迁移 SOP 的 skill 仍可渲染 -----------------------------

def test_skill_without_sop_still_renders():
    router = _spec("qa.router.v1")                       # 本批未迁移 SOP
    assert router.sop is None
    skill = Skill(router)
    assert skill.sop_text() == ""
    assert isinstance(skill.load_instructions(), str)    # 不报错，回退既有 sections


def test_all_specs_constructable_and_loadable():
    """全部 skill（含未迁移）都能构造并渲染说明——Step A 不破坏任何 agent。"""
    for spec in _ALL:
        assert isinstance(Skill(spec).load_instructions(), str)
