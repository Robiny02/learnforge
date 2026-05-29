"""幂等注册默认 skill。

由 BaseAgent 在首次实例化时调用，避免在包 import 期产生导入顺序问题。
"""

from __future__ import annotations

from .registry import SKILL_REGISTRY

_registered = False


def ensure_skills_registered() -> None:
    global _registered
    if _registered:
        return
    from .definitions import register_default_skills

    register_default_skills(SKILL_REGISTRY)
    _registered = True
