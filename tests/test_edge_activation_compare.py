"""Tests for tasks/stats/edge_activation_compare — before/after EV of the
ENABLE_NEWS_EDGE_PRIORITIZATION activation.

The flag was flipped on (operator override of the replay-EV gate) as a paper-mode
monitored experiment. This tool splits paper trades + opportunities at the
activation timestamp and compares the edge cluster's realized EV / opportunity
rate before vs after, so the keep-or-revert verdict is unambiguous once trades
resolve. The cohort math is pure + tested here.
"""

from tasks.stats.edge_activation_compare import (
    cohort_stats,
    split_by_activation,
    compare_verdict,
    opportunities_per_day,
)

ACT = "2026-05-30T03:15:35+00:00"


def test_cohort_stats_basic():
    trades = [
        {"resolved": 1, "pnl_dollars": 2.0, "edge": 0.05},
        {"resolved": 1, "pnl_dollars": -1.0, "edge": 0.03},
        {"resolved": 0, "pnl_dollars": None, "edge": 0.04},  # unresolved → excluded from EV
    ]
    s = cohort_stats(trades)
    assert s["n"] == 3
    assert s["resolved"] == 2
    assert s["wins"] == 1
    assert s["win_rate"] == 0.5
    assert s["total_pnl"] == 1.0
    assert s["mean_pnl_ev"] == 0.5


def test_cohort_stats_empty():
    s = cohort_stats([])
    assert s["n"] == 0 and s["resolved"] == 0
    assert s["win_rate"] is None and s["mean_pnl_ev"] is None


def test_split_by_activation_is_inclusive_of_after():
    trades = [
        {"ts": "2026-05-29T00:00:00+00:00"},          # before
        {"ts": "2026-05-30T04:00:00+00:00"},          # after
        {"ts": ACT},                                   # exactly at activation → after
    ]
    before, after = split_by_activation(trades, ACT)
    assert len(before) == 1
    assert len(after) == 2


def test_compare_verdict_insufficient_after_data():
    before = cohort_stats([{"resolved": 1, "pnl_dollars": 1.0, "edge": 0.05}] * 5)
    after = cohort_stats([])
    assert "insufficient" in compare_verdict(before, after, min_after_n=10).lower()


def test_compare_verdict_degraded_triggers_rollback_hint():
    before = cohort_stats([{"resolved": 1, "pnl_dollars": 1.0, "edge": 0.05}] * 12)
    after = cohort_stats([{"resolved": 1, "pnl_dollars": -1.0, "edge": 0.05}] * 12)
    v = compare_verdict(before, after, min_after_n=10).lower()
    assert "degraded" in v or "rollback" in v


def test_opportunities_per_day_rates():
    # 4 opps over a 2-day before window, 6 over a 3-day after window.
    before = [("KXTRUMPIRAN", "x")] * 4
    after = [("KXTRUMPIRAN", "x")] * 6
    rates = opportunities_per_day(before, after, before_days=2.0, after_days=3.0)
    assert rates["before_per_day"] == 2.0
    assert rates["after_per_day"] == 2.0
