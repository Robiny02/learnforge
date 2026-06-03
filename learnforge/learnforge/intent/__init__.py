"""统一意图层（生产主路径的单一入口）：自然语言 → 结构化 IntentFrame。

把历史上碎在三处的意图识别（orchestrator/router 关键词路由、server 暴力剥词、
mock/intent 五分类）收敛成一个三层兜底的 resolver，并为四个能力做槽位填充、面试最深，
缺关键槽位时主动澄清一轮。"链路永远通"：无 ANTHROPIC_API_KEY 时全程确定性。

公开入口：
    from learnforge.intent import IntentResolver
    from learnforge.intent.mock_turn import classify_mock_input, classify_exit_confirmation
"""

from __future__ import annotations

from .resolver import IntentResolver

__all__ = ["IntentResolver"]
