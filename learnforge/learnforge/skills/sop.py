"""SOP —— 单个 Agent 的标准操作流程（Active Skill 的稳定内容）。

设计目标（架构收敛 Step A）：把每个 Agent 的稳定执行规则收敛成**可读、可拆分、可测试、
可逐步演进**的结构，而不是一大段硬编码 prompt 字符串。

边界（务必分清，不要混在一起）：
  - **SOP**：只描述"这个 Agent 应该怎么做事"——职责/输入/步骤/工具使用策略/输出要求/降级/不负责。
    **稳定能力说明**，绝不含动态上下文（会话历史、附件内容、检索结果、最近工具调用都不进 SOP）。
  - **Tool Registry**：它"能用什么工具"——是 `SkillSpec.allowed_tools` / `tool_descriptions`，不在 SOP 里。
    SOP 的 `tool_use` 只写"**何时**用工具"的策略，不重复罗列工具名。
  - **Conversation State / Evidence**：另外两层（最近发生了什么 / 这轮基于什么材料），与 SOP 无关。

维护方式：每个字段可单独增删改；调整某个 Agent 的步骤/输出/降级**不需要动 prompt assembler 主链路**，
也不影响其它 Agent。每个 SOP 是一个独立常量（见 `skills/sops/<area>.py`），可独立 review。

渲染：`render_sop()` 是确定性纯函数（不读会话/不调 LLM/无副作用），输出注入 Active Skill 文本。
Python 3.9 兼容：用 typing.List/Optional，不用 `X | None`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class SOP:
    """一个 Agent 的标准操作流程。各字段可独立编辑/review/测试。

    `role` 渲染为开头一句身份；其余字段渲染为带小标题的条目，空字段自动跳过。
    `steps` 渲染为有序步骤（无需在文本里手写编号，便于增删/重排）。
    `examples` 渲染为 few-shot 示例块（可含多行，用于压住模型漂移）。
    """

    role: str = ""                                              # 一句话身份/定位
    responsibility: List[str] = field(default_factory=list)     # 负责什么
    inputs: List[str] = field(default_factory=list)             # 输入来源有哪些
    steps: List[str] = field(default_factory=list)              # 执行步骤（有序）
    tool_use: List[str] = field(default_factory=list)           # 何时使用工具（策略，非工具清单）
    output: List[str] = field(default_factory=list)             # 输出结构/质量要求
    degradation: List[str] = field(default_factory=list)        # 信息不足/无 key 时如何降级
    not_responsible: List[str] = field(default_factory=list)    # 不负责什么
    examples: List[str] = field(default_factory=list)           # few-shot（可选）
    version: str = "v1"

    def render(self) -> str:
        return render_sop(self)


# (attr, 小标题)。顺序即渲染顺序；steps/examples 特殊渲染，其余统一 bullet。
_SECTIONS = [
    ("responsibility", "【职责】"),
    ("inputs", "【输入来源】"),
    ("steps", "【执行步骤】"),
    ("tool_use", "【工具使用】"),
    ("output", "【输出要求】"),
    ("degradation", "【降级策略】"),
    ("not_responsible", "【不负责】"),
    ("examples", "【示例】"),
]


def _clean(items: List[str]) -> List[str]:
    return [str(x).strip() for x in items if str(x).strip()]


def render_sop(sop: SOP) -> str:
    """确定性渲染 SOP 为注入文本。纯函数：同一 SOP 多次渲染结果一致，不依赖任何运行期状态。"""
    parts: List[str] = []
    if sop.role.strip():
        parts.append(sop.role.strip())
    for attr, header in _SECTIONS:
        items = _clean(getattr(sop, attr))
        if not items:
            continue
        if attr == "steps":
            body = "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1))
        elif attr == "examples":
            body = "\n\n".join(items)
        else:
            body = "\n".join(f"- {x}" for x in items)
        parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)
