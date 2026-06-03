"""面试内动作层的**单一来源**：动作注册表 + List[Step] 执行器（解耦铺路）。

历史痛点：每加一个面试内动作（如 skip/hint），要散改 5 处——MockAction 枚举、mock_turn 触发词、
子图 S_CONTROL 的 if 链、server 分发、cli 分发。本模块把"一个动作 *是什么 channel*、*控制时怎么回应*"
收敛到一张表 `REGISTRY`，让子图/server/cli 改成查表，不再各自硬编码。新增动作 = 往表里加一行。

`Step` + `run_plan`：把"一句话 → 一个动作"升级成"一句话 → 一个有序小计划"。当前 `plan_from_message`
只产长度 1 的计划（单意图，**零行为变化**）；将来接 LLM 拆解多意图时，只改这一个产 plan 的函数，
执行器与注册表不动——这就是和能力层 handoffs/ReAct 同一套 plan-based 思想下沉到动作层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import control as CTRL

# --- channel：一个动作落到 MockInput 的哪个字段 / 走哪条编排路径 ---
CHANNEL_ANSWER = "answer"        # → user_answer（默认）
CHANNEL_INTERRUPT = "interrupt"  # → user_interrupt（经 Strategist：换题/调难度/暂停/结束）
CHANNEL_CONTROL = "control"      # → control_action（子图 S_CONTROL，不消耗轮次）
CHANNEL_SIDE = "side"            # 插问：离开面试走常规问答，保留进行态（编排特例，由入口处理）
CHANNEL_ESCALATE = "escalate"    # 跨能力：结束面试带交接摘要回 Manager（编排特例，由入口处理）


@dataclass(frozen=True)
class ControlCtx:
    """S_CONTROL 生成回应所需的当前上下文（题面/要点/上一轮/可回退的上一题）。"""

    question: str = ""
    expected_points: List[str] = field(default_factory=list)
    last_turn: Optional[dict] = None
    prev_question: Optional[str] = None


@dataclass(frozen=True)
class ControlSpec:
    """即时控制动作的行为：回应文案 build + 去向 goto（await=回原题 / next=出新题 / redo=回退一轮）。"""

    goto: str
    build: Callable[[ControlCtx], str]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    channel: str
    control: Optional[ControlSpec] = None


# 即时控制子表（goto 语义 + 确定性文案，全部复用 control.py）。
_CONTROLS: Dict[str, ControlSpec] = {
    "skip": ControlSpec("next", lambda c: CTRL.build_skip_note()),
    "hint": ControlSpec("await", lambda c: CTRL.build_hint(c.question, c.expected_points)),
    "repeat": ControlSpec("await", lambda c: CTRL.build_repeat(c.question)),
    "reveal": ControlSpec("await", lambda c: CTRL.build_reveal(c.question, c.expected_points)),
    "feedback": ControlSpec("await", lambda c: CTRL.build_feedback(c.last_turn)),
    "redo": ControlSpec("redo", lambda c: CTRL.build_redo_note(c.prev_question or "")),
}

# 动作注册表（单一来源）。顺序仅作可读性；分发按 name 查。
REGISTRY: Dict[str, ActionSpec] = {
    "answer": ActionSpec("answer", CHANNEL_ANSWER),
    "side": ActionSpec("side", CHANNEL_SIDE),
    "escalate": ActionSpec("escalate", CHANNEL_ESCALATE),
    "pause": ActionSpec("pause", CHANNEL_INTERRUPT),
    "exit": ActionSpec("exit", CHANNEL_INTERRUPT),
    "stop": ActionSpec("stop", CHANNEL_INTERRUPT),          # 显式结束按钮（= exit 文案）
    "switch_topic": ActionSpec("switch_topic", CHANNEL_INTERRUPT),
    "set_difficulty": ActionSpec("set_difficulty", CHANNEL_INTERRUPT),
    **{name: ActionSpec(name, CHANNEL_CONTROL, spec) for name, spec in _CONTROLS.items()},
}

# 即时控制动作名（供 mock_turn / 子图作单一来源，避免重复硬编码）。
CONTROL_ACTION_NAMES = tuple(_CONTROLS.keys())


def control_spec(action: str) -> Optional[ControlSpec]:
    spec = REGISTRY.get(action)
    return spec.control if spec else None


def channel_of(action: str) -> str:
    spec = REGISTRY.get(action)
    return spec.channel if spec else CHANNEL_ANSWER


# ----------------------------------------------------------------- Step + 执行器
@dataclass(frozen=True)
class Step:
    """计划里的一步：一个动作 + 触发它的原始文本（作答内容/中断措辞/插问问题）。"""

    action: str
    text: str = ""


def plan_from_message(question: str, message: str) -> List[Step]:
    """一句话 → 有序计划。当前单意图（长度 1）；未来 LLM 多意图拆解只改这里。"""
    from ...intent.mock_turn import classify_mock_input  # 延迟导入避免环

    return [Step(action=classify_mock_input(question or "", message), text=message)]


def run_step(mock_agent, session_id: str, step: Step):
    """执行一步面试内动作：按 channel 落成 MockInput 字段并跑子图。

    `mock_agent` = 提供 `.run(MockInput)` 的 MockInterviewAgent（生产里传 `mgr.mock`）。
    side/escalate 是编排特例（要离开面试），由入口自行处理——**不**经此执行器；
    此处把它们与未知动作一律按 answer 兜底（与 cli 历史行为一致，不吞输入）。
    """
    from ...contracts.agents.mock import MockInput
    from ...intent.mock_turn import interrupt_for

    ch = channel_of(step.action)
    if ch == CHANNEL_CONTROL:
        mi = MockInput(topic="", session_id=session_id, control_action=step.action)
    elif ch == CHANNEL_INTERRUPT:
        name = "exit" if step.action == "stop" else step.action
        mi = MockInput(topic="", session_id=session_id,
                       user_interrupt=interrupt_for(name, step.text))
    else:  # answer / side / escalate / 未知 → 当作答（编排特例已在入口拦截）
        mi = MockInput(topic="", session_id=session_id, user_answer=step.text)
    return mock_agent.run(mi)


def run_plan(mock_agent, session_id: str, steps: List[Step]):
    """串行执行计划，返回最后一步的 MockOutput。

    串行（非并行）保证步间依赖正确：如「跳过→提示」，提示作用在跳过后的新题上。
    单步计划即退化为单意图，行为与历史逐字一致。
    """
    out = None
    for step in steps:
        out = run_step(mock_agent, session_id, step)
    return out
