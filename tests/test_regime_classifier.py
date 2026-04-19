"""Tests for analysis/regime_classifier.py"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analysis.regime_classifier import (
    FAST,
    INTERPRETATION,
    STRUCTURAL,
    _apply_title_nudge,
    _normalize,
    _series_prior,
    _time_prior,
    compute_regime_weights,
)
from kalshi import KalshiMarket


# ── Helpers ───────────────────────────────────────────────────────────────────

def _market(
    ticker: str = "KXTEST-1",
    series_ticker: str = "",
    title: str = "Will this resolve yes?",
    subtitle: str = "",
    days: float = 5.0,
) -> KalshiMarket:
    close = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    return KalshiMarket(
        ticker=ticker,
        title=title,
        yes_bid=45,
        yes_ask=55,
        yes_price=50,
        volume=1000,
        open_interest=500,
        close_time=close,
        status="open",
        series_ticker=series_ticker,
        subtitle=subtitle,
    )


def _weights_valid(w: dict) -> bool:
    """Return True if weights are non-negative and sum to ~1.0."""
    return (
        all(v >= 0 for v in w.values())
        and abs(sum(w.values()) - 1.0) < 1e-6
        and set(w.keys()) == {FAST, INTERPRETATION, STRUCTURAL}
    )


# ── Invariant: weights always sum to 1.0 ─────────────────────────────────────

class TestWeightInvariant:
    def test_sports_series_sums_to_one(self):
        w = compute_regime_weights(_market(series_ticker="KXNFL"))
        assert _weights_valid(w)

    def test_structural_series_sums_to_one(self):
        w = compute_regime_weights(_market(series_ticker="KXCBDECISION"))
        assert _weights_valid(w)

    def test_time_fallback_sums_to_one(self):
        for days in [0.1, 0.5, 2.0, 5.0, 10.0, 20.0]:
            w = compute_regime_weights(_market(days=days))
            assert _weights_valid(w), f"Failed for days={days}: {w}"

    def test_no_close_time_sums_to_one(self):
        m = _market()
        m.close_time = ""
        w = compute_regime_weights(m)
        assert _weights_valid(w)

    def test_unparseable_close_time_sums_to_one(self):
        m = _market()
        m.close_time = "not-a-date"
        w = compute_regime_weights(m)
        assert _weights_valid(w)

    def test_all_weights_positive(self):
        # No lane should ever be zeroed out — all weights must be positive.
        for series in ["KXNFL", "KXCBDECISION", "KXWEATHER", "KXCRYPTO"]:
            w = compute_regime_weights(_market(series_ticker=series))
            assert all(v > 0 for v in w.values()), f"Zero weight for {series}: {w}"


# ── Series-ticker classification ──────────────────────────────────────────────

class TestSeriesClassification:
    def test_sports_is_fast_dominant(self):
        for prefix in ["KXNFL", "KXNBA", "KXMLB", "KXNHL", "KXNCAA", "KXTENNIS", "KXBOXING"]:
            w = compute_regime_weights(_market(series_ticker=prefix))
            assert w[FAST] > 0.70, f"{prefix}: expected fast > 0.70, got {w}"
            assert w[FAST] > w[INTERPRETATION] > w[STRUCTURAL]

    def test_central_bank_is_structural_dominant(self):
        w = compute_regime_weights(_market(series_ticker="KXCBDECISION"))
        assert w[STRUCTURAL] > 0.55
        assert w[STRUCTURAL] > w[INTERPRETATION] > w[FAST]

    def test_approval_polls_are_structural_dominant(self):
        for prefix in ["KXAPRPOTUS", "KXPOLLPOTUS"]:
            w = compute_regime_weights(_market(series_ticker=prefix))
            assert w[STRUCTURAL] > 0.60, f"{prefix}: {w}"

    def test_weather_is_structural_dominant(self):
        w = compute_regime_weights(_market(series_ticker="KXWEATHER"))
        assert w[STRUCTURAL] > 0.55

    def test_crypto_is_fast_dominant(self):
        for prefix in ["KXCRYPTO", "KXBTC", "KXETH", "KXSOL"]:
            w = compute_regime_weights(_market(series_ticker=prefix))
            assert w[FAST] > 0.55, f"{prefix}: {w}"

    def test_entertainment_is_interpretation_heavy(self):
        w = compute_regime_weights(_market(series_ticker="KXENTERTAIN"))
        assert w[INTERPRETATION] >= 0.40

    def test_series_ticker_prefix_matching_not_exact(self):
        # KXNFL-2025-SUPERB should still match KXNFL prefix
        w = compute_regime_weights(_market(series_ticker="KXNFL-2025-SUPERB"))
        assert w[FAST] > 0.70

    def test_market_ticker_fallback_when_series_empty(self):
        # series_ticker is empty but market ticker has known prefix
        w_with_series = compute_regime_weights(_market(series_ticker="KXNFL"))
        w_ticker_only = compute_regime_weights(_market(ticker="KXNFL-GAME-1", series_ticker=""))
        assert w_with_series[FAST] == w_ticker_only[FAST]

    def test_unknown_series_falls_through_to_time(self):
        w_unknown = compute_regime_weights(_market(series_ticker="KXUNKNOWN", days=5.0))
        w_time = compute_regime_weights(_market(series_ticker="", days=5.0))
        # Both should resolve via time-based prior (same days)
        assert w_unknown == w_time


# ── Time-based classification ─────────────────────────────────────────────────

class TestTimePrior:
    def test_very_short_horizon_is_fast(self):
        f, i, s = _time_prior(0.1)
        assert f > 0.80
        assert f > i > s

    def test_intraday_is_fast_dominant(self):
        f, i, s = _time_prior(0.5)
        assert f > 0.60

    def test_one_to_three_days_is_mixed(self):
        f, i, s = _time_prior(2.0)
        assert f > 0.35
        assert i > 0.35

    def test_three_to_seven_days_is_interpretation_dominant(self):
        f, i, s = _time_prior(5.0)
        assert i > 0.40
        assert i > f

    def test_one_to_two_weeks_is_mixed_interpretation_structural(self):
        f, i, s = _time_prior(10.0)
        assert i >= s
        assert f < 0.20

    def test_long_horizon_is_structural_dominant(self):
        f, i, s = _time_prior(20.0)
        assert s > 0.55
        assert s > i > f

    def test_monotone_structural_increase_with_days(self):
        days_seq = [0.2, 1.0, 3.0, 7.0, 14.0, 21.0]
        structural_seq = [_time_prior(d)[2] for d in days_seq]
        assert structural_seq == sorted(structural_seq), "Structural weight should increase with days"

    def test_monotone_fast_decrease_with_days(self):
        days_seq = [0.2, 1.0, 3.0, 7.0, 14.0, 21.0]
        fast_seq = [_time_prior(d)[0] for d in days_seq]
        assert fast_seq == sorted(fast_seq, reverse=True), "Fast weight should decrease with days"


# ── Title keyword nudges ──────────────────────────────────────────────────────

class TestTitleNudge:
    _BASE = (0.33, 0.34, 0.33)

    def test_structural_keyword_increases_structural(self):
        nudged = _apply_title_nudge(self._BASE, "Will the federal reserve cut rates?")
        assert nudged[2] > self._BASE[2]
        assert nudged[0] < self._BASE[0]

    def test_fast_keyword_increases_fast(self):
        nudged = _apply_title_nudge(self._BASE, "Breaking: ceasefire announced")
        assert nudged[0] > self._BASE[0]
        assert nudged[2] < self._BASE[2]

    def test_no_match_returns_unchanged(self):
        nudged = _apply_title_nudge(self._BASE, "Will Team X win the championship?")
        assert nudged == self._BASE

    def test_conflicting_signals_return_unchanged(self):
        # Both fast and structural keywords present — should cancel
        nudged = _apply_title_nudge(self._BASE, "Breaking: federal reserve rate decision")
        assert nudged == self._BASE

    def test_nudge_preserves_sum_to_one_after_normalize(self):
        raw = _apply_title_nudge(self._BASE, "Will the fed cut interest rate?")
        w = _normalize(raw)
        assert _weights_valid(w)


# ── Normalize ─────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_valid_weights_sum_to_one(self):
        w = _normalize((0.4, 0.35, 0.25))
        assert abs(sum(w.values()) - 1.0) < 1e-6

    def test_negative_weights_clamped(self):
        # Clamp prevents zero/negative; post-normalization value is small but positive.
        w = _normalize((-0.1, 0.6, 0.5))
        assert all(v > 0 for v in w.values())
        assert abs(sum(w.values()) - 1.0) < 1e-6

    def test_zero_weights_clamped_to_minimum(self):
        # Zero input is clamped to a small positive value before normalization.
        w = _normalize((0.0, 0.5, 0.5))
        assert w[FAST] > 0

    def test_output_keys_correct(self):
        w = _normalize((0.33, 0.33, 0.34))
        assert set(w.keys()) == {FAST, INTERPRETATION, STRUCTURAL}


# ── Series prior helper ───────────────────────────────────────────────────────

class TestSeriesPrior:
    def test_known_prefix_returns_tuple(self):
        m = _market(series_ticker="KXNFL")
        result = _series_prior(m)
        assert result is not None
        assert len(result) == 3
        assert abs(sum(result) - 1.0) < 1e-6

    def test_unknown_prefix_returns_none(self):
        m = _market(series_ticker="KXUNKNOWN", ticker="KXUNKNOWN-1")
        assert _series_prior(m) is None

    def test_empty_series_ticker_falls_back_to_market_ticker(self):
        m = _market(series_ticker="", ticker="KXNBA-2025-FINALS")
        result = _series_prior(m)
        assert result is not None
        assert result[0] > 0.70  # fast-dominant


# ── Integration: full compute_regime_weights ─────────────────────────────────

class TestComputeRegimeWeights:
    def test_geo_political_short_horizon_fast_dominant(self):
        # No series match → time fallback; 2 days → mixed but fast-leaning
        w = compute_regime_weights(_market(
            ticker="KXIRAN-DEAL-1",
            series_ticker="",
            title="Will Iran sign a nuclear deal this week?",
            days=2.0,
        ))
        assert _weights_valid(w)
        assert w[FAST] > 0.35

    def test_geo_political_long_horizon_structural_dominant(self):
        w = compute_regime_weights(_market(
            ticker="KXIRANLONG-1",
            series_ticker="",
            title="Will Iran sign a deal by end of year?",
            days=60.0,
        ))
        assert _weights_valid(w)
        assert w[STRUCTURAL] > 0.55

    def test_fed_rate_decision_market(self):
        w = compute_regime_weights(_market(
            series_ticker="KXCBDECISION",
            title="Will the Federal Reserve cut interest rates in June?",
            days=45.0,
        ))
        assert _weights_valid(w)
        assert w[STRUCTURAL] > 0.55
        # Title keyword nudge shifts structural further
        assert w[STRUCTURAL] > 0.60

    def test_nfl_game_tonight(self):
        w = compute_regime_weights(_market(
            series_ticker="KXNFL",
            title="Will the Chiefs win tonight?",
            days=0.3,
        ))
        assert _weights_valid(w)
        assert w[FAST] > 0.75
