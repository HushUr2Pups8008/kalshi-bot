"""Tests for tasks/stats/market_horizon_audit (rot surfaces #11/#12).

MARKET_CACHE_TTL_SECONDS, MAX_MARKET_DAYS_TO_EXPIRY (30) and _DAYS_TO_CLOSE_CAP
(90) encode static assumptions about Kalshi's market-time distribution. If the
platform shifts toward longer-dated markets these silently misalign — the
universe filter excludes too much, or the proximity sub-score saturates. This
surfaces the actual close-time distribution + drift flags from live markets.
The distribution math is a pure, testable function.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tasks.stats.market_horizon_audit import summarize_market_horizon

NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


def _m(days, oi=10):
    return SimpleNamespace(close_time=(NOW + timedelta(days=days)).isoformat(), open_interest=oi)


def test_counts_dated_and_unparseable():
    markets = [_m(5), _m(40), SimpleNamespace(close_time="garbage", open_interest=1)]
    r = summarize_market_horizon(markets, max_days_expiry=30, days_close_cap=90, now=NOW)
    assert r["count"] == 3
    assert r["count_dated"] == 2
    assert r["count_unparseable"] == 1


def test_pct_beyond_thresholds():
    markets = [_m(5), _m(40), _m(100), _m(200)]
    r = summarize_market_horizon(markets, max_days_expiry=30, days_close_cap=90, now=NOW)
    assert abs(r["pct_beyond_max_days_expiry"] - 0.75) < 1e-9  # 40,100,200
    assert abs(r["pct_beyond_days_close_cap"] - 0.50) < 1e-9   # 100,200


def test_flag_when_p75_exceeds_days_close_cap():
    markets = [_m(100), _m(110), _m(120), _m(130)]
    r = summarize_market_horizon(markets, max_days_expiry=30, days_close_cap=90, now=NOW)
    assert any("days_close_cap" in f.lower() or "proximity" in f.lower() for f in r["flags"])


def test_flag_when_most_markets_excluded_by_expiry_filter():
    markets = [_m(5), _m(40), _m(50), _m(60)]  # 3/4 beyond 30
    r = summarize_market_horizon(markets, max_days_expiry=30, days_close_cap=90, now=NOW)
    assert any("max_days_expiry" in f.lower() or "excluded" in f.lower() for f in r["flags"])


def test_no_flags_when_within_assumptions():
    markets = [_m(2), _m(5), _m(10), _m(20)]
    r = summarize_market_horizon(markets, max_days_expiry=30, days_close_cap=90, now=NOW)
    assert r["flags"] == []
