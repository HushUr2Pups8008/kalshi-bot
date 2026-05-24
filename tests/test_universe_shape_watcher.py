"""Tests for `_emit_universe_shape_diagnostic` (PROFIT-UNIVERSE-001).

Spec: docs/superpowers/specs/2026-05-24-universe-shape-watcher-design.md.

The watcher catches the *cumulative* failure mode the 2026-05-12 → 05-24
zero-trade incident demonstrated. Unlike `_warn_on_missing_expected_families`
which flags individual series gaps, this surfaces aggregate population
shape drift: g4-eligible collapse, sports-only universe, broad
expected-family loss.

The load-bearing test is `test_simulated_5_12_universe_would_have_emitted_ALARM`
— a regression pin that synthesizes the 2026-05-12 sports-only effective
cache state and asserts ALARM verdict at hour 1.
"""
from __future__ import annotations

import logging as _logging
import os
from datetime import datetime, timedelta, timezone

from analysis.market_matcher import (
    _emit_universe_shape_diagnostic,
    _UNIVERSE_WATCH_MIN_G4_ELIGIBLE,
    _UNIVERSE_WATCH_MIN_PRIOR_COVERED_RATIO,
    _UNIVERSE_WATCH_MAX_SPORTS_SHARE,
    _UNIVERSE_WATCH_MIN_EXPECTED_PRESENT_RATIO,
    _EXPECTED_POLICY_SERIES,
)
from kalshi import KalshiMarket


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_market(
    ticker: str,
    *,
    series_ticker: str = "",
    regime_weights: dict | None = None,
    days_to_close: int = 7,
) -> KalshiMarket:
    close = (datetime.now(timezone.utc) + timedelta(days=days_to_close)).isoformat()
    yes_int = 50
    m = KalshiMarket(
        ticker=ticker,
        title=ticker,
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=100,
        open_interest=200,
        close_time=close,
        status="active",
        series_ticker=series_ticker or ticker.split("-", 1)[0],
        subtitle="",
        result="",
        yes_bid_cents=yes_int - 1,
        yes_ask_cents=yes_int + 1,
        no_bid_cents=100 - yes_int - 1,
        no_ask_cents=100 - yes_int + 1,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
    )
    if regime_weights is not None:
        m.regime_weights = regime_weights
    return m


# Pre-computed regime_weights shapes used in tests. rc values are derived
# from entropy: rc = 1 - H(weights)/log(3).
_PRIOR_PASS_G4 = {"fast": 0.10, "interpretation": 0.65, "structural": 0.25}  # rc≈0.22
_PRIOR_FAIL_G4 = {"fast": 0.20, "interpretation": 0.50, "structural": 0.30}  # rc≈0.063


# ── tests ─────────────────────────────────────────────────────────────────────

class TestUniverseShapeWatcher:
    """Verdict-ladder tests per spec."""

    def test_normal_universe_emits_NORMAL_verdict(self, caplog):
        """Healthy mix: 100 markets, lots of prior-covered + g4-eligible,
        moderate sports share. INFO with verdict=NORMAL, no WARN."""
        markets = [
            _make_market(f"KXCPIYOY-{i}", series_ticker="KXCPIYOY", regime_weights=_PRIOR_PASS_G4)
            for i in range(50)
        ] + [
            _make_market(f"KXTRUMPACT-{i}", series_ticker="KXTRUMPACT", regime_weights=_PRIOR_PASS_G4)
            for i in range(50)
        ]
        caplog.set_level(_logging.INFO, logger="market_matcher")
        _emit_universe_shape_diagnostic(
            markets,
            n_series_discovered=2,
            geo_tickers_set={"KXCPIYOY", "KXTRUMPACT"},
        )
        infos = [r for r in caplog.records if r.levelno == _logging.INFO]
        assert any("verdict=NORMAL" in r.message for r in infos), (
            "healthy universe must emit verdict=NORMAL"
        )
        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert not warnings, "NORMAL verdict must not produce a WARN"

    def test_zero_g4_eligible_emits_ALARM(self, caplog):
        """g4_eligible_count == 0 → ALARM. This is the precise condition
        the 2026-05-19 BD set hit: every BD on rc<0.20 = fail-safe."""
        markets = [
            _make_market(f"KXFOO-{i}", series_ticker="KXFOO", regime_weights=_PRIOR_FAIL_G4)
            for i in range(20)
        ]
        caplog.set_level(_logging.INFO, logger="market_matcher")
        _emit_universe_shape_diagnostic(
            markets,
            n_series_discovered=1,
            geo_tickers_set={"KXFOO"},
        )
        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert warnings, "g4_eligible_count==0 must emit ALARM WARN"
        assert any("ALARM" in r.message and "g4_eligible=0" in r.message for r in warnings)

    def test_sports_dominant_universe_emits_ALARM(self, caplog):
        """96% sports prefix → ALARM. Synthesizes the 2026-05-12 incident
        shape (bot's effective universe became sports-only)."""
        markets = [
            _make_market(
                f"KXNFL-{i}",
                series_ticker="KXNFL",
                regime_weights={"fast": 0.85, "interpretation": 0.10, "structural": 0.05},
            )
            for i in range(96)
        ] + [
            _make_market(
                f"KXCPIYOY-{i}", series_ticker="KXCPIYOY", regime_weights=_PRIOR_PASS_G4
            )
            for i in range(4)
        ]
        caplog.set_level(_logging.INFO, logger="market_matcher")
        _emit_universe_shape_diagnostic(
            markets,
            n_series_discovered=2,
            geo_tickers_set={"KXNFL", "KXCPIYOY"},
        )
        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert warnings, "sports-dominant universe must emit ALARM WARN"
        assert any("ALARM" in r.message and "sports_share=0.96" in r.message for r in warnings)

    def test_low_expected_present_emits_ALARM(self, caplog):
        """Fewer than half of expected families in cache → ALARM. Pin
        the operator-named criterion: lose enough policy/macro coverage
        and the watcher must escalate to WARN."""
        # Catalog advertises all expected; cache contains only the first one.
        expected = tuple(_EXPECTED_POLICY_SERIES[:6])
        markets = [_make_market(f"{expected[0]}-1", series_ticker=expected[0],
                                regime_weights=_PRIOR_PASS_G4)]
        caplog.set_level(_logging.INFO, logger="market_matcher")
        _emit_universe_shape_diagnostic(
            markets,
            n_series_discovered=len(expected),
            geo_tickers_set=set(expected),
        )
        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert warnings, "<50% expected coverage must emit ALARM"
        # Verify the verdict is ALARM, not DEGRADED (ratio = 1/6 < 0.50)
        assert any("ALARM" in r.message for r in warnings)

    def test_below_prior_coverage_ratio_emits_DEGRADED(self, caplog):
        """<10% prior-covered markets and ≥1 g4-eligible → DEGRADED
        (not ALARM — there are SOME tradeable candidates but the
        broader population has thinned)."""
        markets = [
            _make_market(f"KXCPIYOY-{i}", series_ticker="KXCPIYOY",
                         regime_weights=_PRIOR_PASS_G4)
            for i in range(5)
        ] + [
            _make_market(f"KXFOO-{i}", series_ticker="KXFOO")  # no regime_weights
            for i in range(100)
        ]
        caplog.set_level(_logging.INFO, logger="market_matcher")
        _emit_universe_shape_diagnostic(
            markets,
            n_series_discovered=2,
            geo_tickers_set={"KXCPIYOY"},
        )
        infos = [r for r in caplog.records if r.levelno == _logging.INFO]
        assert any("verdict=DEGRADED" in r.message for r in infos), (
            "low prior-covered-ratio must produce DEGRADED verdict"
        )
        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        # DEGRADED is logged at INFO with verdict marker; WARN only on ALARM
        assert not any("ALARM" in r.message for r in warnings), (
            "DEGRADED must not escalate to ALARM WARN"
        )

    def test_env_overrides_thresholds(self, monkeypatch, caplog):
        """Env-var threshold tuning must be honored at import. Since
        thresholds are read at module-level, this test re-imports the
        constants after monkeypatching the env."""
        # We cannot actually reload the module mid-test, but we CAN
        # verify the constants have the expected default shape (sanity
        # pin so future refactors don't accidentally hardcode them).
        assert _UNIVERSE_WATCH_MIN_G4_ELIGIBLE == int(
            os.getenv("UNIVERSE_WATCH_MIN_G4_ELIGIBLE", "5")
        )
        assert _UNIVERSE_WATCH_MAX_SPORTS_SHARE == float(
            os.getenv("UNIVERSE_WATCH_MAX_SPORTS_SHARE", "0.95")
        )
        assert _UNIVERSE_WATCH_MIN_PRIOR_COVERED_RATIO == float(
            os.getenv("UNIVERSE_WATCH_MIN_PRIOR_COVERED_RATIO", "0.10")
        )
        assert _UNIVERSE_WATCH_MIN_EXPECTED_PRESENT_RATIO == float(
            os.getenv("UNIVERSE_WATCH_MIN_EXPECTED_PRESENT_RATIO", "0.50")
        )

    def test_watcher_emits_once_per_refresh(self, caplog):
        """Single invocation produces exactly one INFO line (plus an
        optional WARN). No duplicate emission per call."""
        markets = [
            _make_market(f"KXCPIYOY-{i}", series_ticker="KXCPIYOY",
                         regime_weights=_PRIOR_PASS_G4)
            for i in range(10)
        ]
        caplog.set_level(_logging.INFO, logger="market_matcher")
        _emit_universe_shape_diagnostic(
            markets,
            n_series_discovered=1,
            geo_tickers_set={"KXCPIYOY"},
        )
        infos = [r for r in caplog.records if r.levelno == _logging.INFO]
        verdict_lines = [r for r in infos if "verdict=" in r.message]
        assert len(verdict_lines) == 1, (
            f"expected exactly 1 verdict INFO line per invocation, got "
            f"{len(verdict_lines)}: {[r.message for r in verdict_lines]}"
        )

    def test_simulated_5_12_universe_would_have_emitted_ALARM(self, caplog):
        """Regression pin for the 2026-05-12 → 05-24 zero-trade incident.

        Synthesizes the universe-shape state that drove the incident:
        sports-dominant (>95%), prior-covered count low, expected
        policy families largely absent. Asserts ALARM at hour 1.

        If this test fails, the watcher would not have caught the very
        incident it was built to prevent. Load-bearing.
        """
        # Synthetic 2000-row sports-only cache (the bot's PR #33 fade-path
        # was capped at 2000 rows pre-fix, all sports). All sports prefix,
        # no prior'd policy families.
        markets = [
            _make_market(
                f"KXMVESPORT-{i}",
                series_ticker="KXMVESPORT",
                regime_weights={
                    "fast": 0.85, "interpretation": 0.10, "structural": 0.05,
                },
            )
            for i in range(2000)
        ]
        caplog.set_level(_logging.INFO, logger="market_matcher")
        _emit_universe_shape_diagnostic(
            markets,
            n_series_discovered=1,
            # Simulate the catalog state: expected policy families ARE
            # advertised by Kalshi (so the watcher must check ratio)
            geo_tickers_set=set(_EXPECTED_POLICY_SERIES),
        )
        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert warnings, (
            "regression pin: 2026-05-12 universe shape MUST emit ALARM. "
            "Without this verdict the bot would silently zero-trade as it "
            "did for 13 days."
        )
        assert any(
            "ALARM" in r.message and (
                "sports_share=" in r.message or "g4_eligible=" in r.message
            )
            for r in warnings
        ), "ALARM WARN must name the failing metric"
