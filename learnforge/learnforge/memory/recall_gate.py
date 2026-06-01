"""记忆召回相似度闸门（REQUIREMENTS 测试 §10 防编造 + §7 过召回控制）。

对 **cosine 相似度** 排序的召回结果做"采纳 / 弃用 / 不确定"判定：
- top1 < MIN_SIM → ABSTAIN：库中没有足够相关的记忆 → 回"没有找到明确记忆"（不编造）。
- top1-top2 margin < MARGIN → UNCERTAIN：候选过近（如同主题干扰对），不直接采纳，
  交 rerank / 保守处理（避免在 m1 vs m9 这类细粒度上误采）。
- 否则 ACCEPT。

适用范围：仅当分数是真实 cosine 相似度（vector / hybrid 模式）时有意义；
FTS 的 RRF 位置分不是相似度，**不要**对其套用本闸门。阈值来源见 config（小规模 eval 经验值）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..config import (
    MEMORY_RECALL_MARGIN,
    MEMORY_RECALL_MIN_SIM,
    MEMORY_RECALL_THRESHOLD_SOURCE,
)

ACCEPT = "accept"
ABSTAIN = "abstain"
UNCERTAIN = "uncertain"

ABSTAIN_MESSAGE = "没有找到明确记忆"


@dataclass
class GateDecision:
    decision: str          # accept | abstain | uncertain
    reason: str
    top_sim: float
    margin: float
    threshold_source: str = MEMORY_RECALL_THRESHOLD_SOURCE


def gate_recall(
    sims: List[float],
    min_sim: Optional[float] = None,
    margin_thr: Optional[float] = None,
) -> GateDecision:
    """sims：按相关性降序的 cosine 相似度列表。返回采纳决策。

    阈值默认取 config（可经 env 覆盖，不写死）；调用方可显式传入做实验。
    """
    min_sim = MEMORY_RECALL_MIN_SIM if min_sim is None else min_sim
    margin_thr = MEMORY_RECALL_MARGIN if margin_thr is None else margin_thr
    if not sims:
        return GateDecision(ABSTAIN, "无候选", 0.0, 0.0)
    top1 = sims[0]
    top2 = sims[1] if len(sims) > 1 else 0.0
    margin = top1 - top2
    if top1 < min_sim:
        return GateDecision(ABSTAIN,
                            f"top1 {top1:.3f} < 阈值 {min_sim:.3f}：{ABSTAIN_MESSAGE}", top1, margin)
    if margin < margin_thr:
        return GateDecision(UNCERTAIN,
                            f"top1-top2 margin {margin:.3f} < {margin_thr:.3f}："
                            "候选过近，需 rerank / 保守处理", top1, margin)
    return GateDecision(ACCEPT, "采纳 top1", top1, margin)
