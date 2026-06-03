"""澄清门控：缺关键槽位时给出**一句**澄清问题（Claude Code 风格）。

铁律：**绝不卡住泛化请求**。只有当用户措辞已暗示一个**针对性**意图、却缺了让它针对的
锚点时才问。例如"拿我简历拷打我"但库里没简历 → 问；"考考我" → 不问，默认综合面试直接开。
每次至多一个问题，`pending_slot` 告诉 resolver 下一轮该把用户回复填进哪个槽位。
"""

from __future__ import annotations

from typing import Optional, Tuple

from ..contracts.intent import Capability, IntentFrame, MockAction

# (pending_slot, 问题) —— 每个缺口对应一句话。
_Q_TARGET_ROLE = ("target_role", "目标岗位/方向是什么？（如「RAG 算法实习」「后端开发」），我好按它出题。")
_Q_RESUME = ("resume_claims", "把你简历的几条要点，或目标 JD 贴一下？我按它针对性追问拷打。")
_Q_PLAN_GOAL = ("goal", "想达成什么目标、到什么时候？（如「两周内补齐 RAG 八股」）")


def needs_clarification(frame: IntentFrame, has_profile_material: bool = False) -> IntentFrame:
    """按 capability/action 判断是否要澄清；要则在 frame 上写入 clarification/pending_slot。

    `has_profile_material`：用户档案里是否已有简历/项目材料（有则拷打类无需再索要）。
    返回同一个 frame（就地补字段），便于链式调用。
    """
    q: Optional[Tuple[str, str]] = None

    if frame.capability == Capability.MOCK:
        slots = frame.mock_slots()
        action = frame.action
        # 角色式开场：暗示要按岗位，却没抽到 target_role / role_type → 问岗位。
        if action == MockAction.START.value:
            wants_role = "role_intent" in frame.signals
            wants_grill = "resume_grill" in frame.signals
            if wants_role and not (slots.target_role or slots.role_type or slots.jd_text):
                q = _Q_TARGET_ROLE
            elif wants_grill and not (slots.resume_claims or slots.projects) \
                    and not has_profile_material:
                q = _Q_RESUME

    elif frame.capability == Capability.PLANNING:
        slots = frame.plan_slots()
        if not slots.goal and not slots.deadline:
            q = _Q_PLAN_GOAL

    if q is not None:
        frame.pending_slot, frame.clarification = q
        if frame.pending_slot not in frame.missing_required:
            frame.missing_required.append(frame.pending_slot)
    return frame
