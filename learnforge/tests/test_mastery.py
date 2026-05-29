"""掌握度算法单测（Design §4d）。"""

from datetime import datetime, timedelta, timezone

from learnforge.mastery import effective_mastery, to_tier, update_mastery


def test_update_weights():
    # mock α=0.5 / qa α=0.3 / self α=0.15
    assert abs(update_mastery(0.2, 0.9, "mock") - 0.55) < 1e-9
    assert abs(update_mastery(0.5, 1.0, "qa") - (0.5 * 0.7 + 1.0 * 0.3)) < 1e-9
    assert abs(update_mastery(0.4, 0.8, "self") - (0.4 * 0.85 + 0.8 * 0.15)) < 1e-9


def test_update_clamps():
    assert update_mastery(1.0, 5.0, "mock") <= 1.0
    assert update_mastery(0.0, -1.0, "mock") >= 0.0


def test_tier_boundaries():
    assert to_tier(0.0).value == "unknown"
    assert to_tier(0.19).value == "unknown"
    assert to_tier(0.2).value == "learning"
    assert to_tier(0.4).value == "familiar"
    assert to_tier(0.6).value == "proficient"
    assert to_tier(0.85).value == "mastered"
    assert to_tier(1.0).value == "mastered"


def test_effective_decay():
    old = datetime.now(timezone.utc) - timedelta(days=30)
    assert effective_mastery(1.0, 0.05, old) < 1.0          # 衰减发生
    assert effective_mastery(1.0, 0.05, None) == 1.0        # 未复习不衰减
    # 衰减随时间单调下降
    d10 = datetime.now(timezone.utc) - timedelta(days=10)
    d40 = datetime.now(timezone.utc) - timedelta(days=40)
    assert effective_mastery(1.0, 0.05, d10) > effective_mastery(1.0, 0.05, d40)
