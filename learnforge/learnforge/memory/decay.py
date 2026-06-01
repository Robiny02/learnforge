"""daily memory 召回的时间衰减（镜像 mastery.effective_mastery 的 exp(-λ·days)）。

越旧的 daily note 召回权重越低，但**不删除**；MEMORY.md 不经此（它不进索引）。
普通每日记录衰减快（半衰期≈7天），反复出现的薄弱点/mock 反馈衰减慢（≈30天）。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from ..config import (
    MEMORY_DECAY_FAST,
    MEMORY_DECAY_SLOW,
    MEMORY_IMPORTANCE,
    MEMORY_IMPORTANCE_DEFAULT,
    MEMORY_STICKY_KINDS,
)


def decay_rate_for(kind: Optional[str]) -> float:
    """note 类型 → 衰减率 λ。weak/mock/progress/decision 慢衰减，其余快衰减。"""
    return MEMORY_DECAY_SLOW if (kind or "") in MEMORY_STICKY_KINDS else MEMORY_DECAY_FAST


def importance_for(kind: Optional[str]) -> float:
    """note 类型 → 基础重要性 ∈[0,1]（REQUIREMENTS R4.2/R7.3）。

    召回排序的次要因子：相关性(score)主导、时间(decay)与重要性(importance)作修正，
    不与衰减黏性重复计权——importance 只做温和加权，避免压过相关性。
    """
    return MEMORY_IMPORTANCE.get(kind or "", MEMORY_IMPORTANCE_DEFAULT)


def _parse_dt(value) -> Optional[datetime]:
    """容忍 ISO8601 字符串 / datetime / 仅日期；解析失败 → None（视为不衰减）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def decay_weight(
    date, kind: Optional[str] = None, now: Optional[datetime] = None
) -> float:
    """返回 ∈(0,1] 的衰减权重 = exp(-λ_kind · days_since(date))。

    无法解析日期（如无日期的 mock_turns/qa 命中）→ 1.0（不衰减）。
    """
    dt = _parse_dt(date)
    if dt is None:
        return 1.0
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - dt).total_seconds() / 86400.0)
    return math.exp(-decay_rate_for(kind) * days)
