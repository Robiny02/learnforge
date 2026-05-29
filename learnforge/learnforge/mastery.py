"""掌握度算法（Design §4d，Q1：连续分 + 时间遗忘 + 5 档映射）。

纯函数，无副作用、可单测。被两处复用：
- DiagnosisAgent（只读）：读取时按 effective_mastery 计算"有效掌握"做弱点聚合。
- Manager 会话结算（唯一写者，Design §2a）：mock S7 / QA 返回后 update_mastery 落库。

权重 α（信号来源）：mock=0.5 > qa=0.3 > self=0.15（Design §4d）。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from .contracts.enums import MasteryTier

# 信号来源权重 α（Design §4d）
SIGNAL_ALPHA = {"mock": 0.5, "qa": 0.3, "self": 0.15}

# 5 档边界（Design §4d）：[0,0.2) [0.2,0.4) [0.4,0.6) [0.6,0.85) [0.85,1]
_TIER_BOUNDS = [
    (0.85, MasteryTier.MASTERED),
    (0.60, MasteryTier.PROFICIENT),
    (0.40, MasteryTier.FAMILIAR),
    (0.20, MasteryTier.LEARNING),
    (0.0, MasteryTier.UNKNOWN),
]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def alpha_for(source: str) -> float:
    """信号来源 → 更新权重 α；未知来源退化为 self 权重（保守）。"""
    return SIGNAL_ALPHA.get(source, SIGNAL_ALPHA["self"])


def update_mastery(old_score: float, signal: float, source: str = "qa") -> float:
    """指数滑动更新：new = clamp(old*(1-α) + signal*α, 0, 1)（Design §4d）。

    - signal ∈ [0,1]：本次表现归一化分（如 Judge overall/5）。
    - source：mock|qa|self，决定 α。
    """
    a = alpha_for(source)
    return _clamp(old_score * (1 - a) + _clamp(signal) * a)


def effective_mastery(
    score: float,
    decay_rate: float = 0.05,
    last_reviewed_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> float:
    """时间遗忘：effective = score * exp(-decay_rate * days_since_review)（Design §4d）。

    从未复习（last_reviewed_at 为 None）时不衰减，直接返回 score。
    """
    if last_reviewed_at is None:
        return _clamp(score)
    now = now or datetime.now(timezone.utc)
    # 容忍 naive datetime（按 UTC 解释），避免 aware/naive 相减报错。
    if last_reviewed_at.tzinfo is None:
        last_reviewed_at = last_reviewed_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - last_reviewed_at).total_seconds() / 86400.0)
    return _clamp(score * math.exp(-decay_rate * days))


def to_tier(score: float) -> MasteryTier:
    """连续分 → 离散档（Design §4d）。"""
    s = _clamp(score)
    for lo, tier in _TIER_BOUNDS:
        if s >= lo:
            return tier
    return MasteryTier.UNKNOWN
