"""Tests for analysis/regime_classifier.py"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


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

    # ── PROFIT-EDGE-002 (v0.29.57) categorical priors ─────────────────────
    # Domestic-policy / geopolitical event series we engage. Each is required
    # to have a categorical prior because the time-fallback (≥7d) drops
    # regime_confidence to ~0.14, below G4=0.20. Weights chosen so the
    # resulting regime_confidence clears the gate.

    def test_legislative_calendar_priors_are_interpretation_dominant(self):
        for prefix in ("KXSBUDGETRES", "KXFISAEXTEND", "KXVOTESAVEAMERICA",
                       "KXEFFTARIFF", "KXMOCTRUMP25"):
            w = compute_regime_weights(_market(series_ticker=prefix))
            assert w[INTERPRETATION] > w[FAST], f"{prefix}: {w}"
            assert w[INTERPRETATION] > w[STRUCTURAL], f"{prefix}: {w}"

    def test_macro_release_priors_are_structural_dominant(self):
        for prefix in ("KXCPIYOY", "KXCPICOREYOY", "KXCPIEU", "KXEZGDPYOYF"):
            w = compute_regime_weights(_market(series_ticker=prefix))
            assert w[STRUCTURAL] > w[INTERPRETATION], f"{prefix}: {w}"
            assert w[STRUCTURAL] > w[FAST], f"{prefix}: {w}"

    def test_event_driven_political_priors_are_fast_dominant(self):
        for prefix in ("KXTRUMPACT", "KXTRUMPENDORSE", "KXTRUMPCHINA",
                       "KXTRUMPCRYPTOCONF", "KXVANCEPAKISTAN",
                       "KXVISITVENEZUELA",
                       # PROFIT-PRIORS-001 (2026-05-24): moved here from
                       # the legislative/calendar interpretation-dominant
                       # cluster. These markets are news-driven via the
                       # LLM fast lane; the interpretation/structural
                       # lanes have no dossier/structural-prior
                       # infrastructure wired for them yet, so
                       # interpretation-dominant priors silently zeroed
                       # the LLM signal during blending (observed live
                       # 2026-05-24: bc≈0.12 on a 90/10-edge market).
                       "KXTXRUNOFFENDORSE", "KXUSAIRANAGREEMENT",
                       "KXNEWTARIFFS"):
            w = compute_regime_weights(_market(series_ticker=prefix))
            assert w[FAST] > w[INTERPRETATION], f"{prefix}: {w}"
            assert w[FAST] > w[STRUCTURAL], f"{prefix}: {w}"

    def test_profit_priors_001_lane_shape_unblocks_g1(self):
        """The three markets re-shaped in PROFIT-PRIORS-001 must produce
        scaled_confidence (bc × rc) high enough to clear G1=0.05 when the
        LLM signal is high-confidence.

        Load-bearing: the 2026-05-24 BD on KXUSAIRANAGREEMENT-27-26JUN
        had LLM fast_lane_confidence=0.85 (strong signal) but
        blended_confidence diluted to 0.1162 because the prior weighted
        the fast lane at only 0.05. scaled_confidence = 0.027 failed G1.
        Post-re-shape (fast=0.65, interp=0.25, structural=0.10), even
        the same LLM confidence produces a much higher blended_confidence
        because the lane carrying the signal now has dominant weight.

        This test pins the contract: for these three series, a high-
        confidence fast-lane signal must produce scaled_confidence well
        above G1=0.05.
        """
        import math
        from tasks.trade_readiness_gate import G1_CONFIDENCE_THRESHOLD
        from analysis.regime_classifier import _SERIES_PRIORS

        for prefix in ("KXUSAIRANAGREEMENT", "KXTXRUNOFFENDORSE",
                       "KXNEWTARIFFS"):
            weights = _SERIES_PRIORS[prefix]
            # Confirm fast-lane has the dominant weight ≥ 0.50 so that
            # any single-lane signal can clear G1 with reasonable confidence.
            assert weights[0] >= 0.50, (
                f"{prefix}: fast-lane weight={weights[0]} must be ≥0.50 "
                "to ensure LLM signals reach blender without dilution"
            )
            # rc derived from the new prior shape
            ent = -sum(w * math.log(w) for w in weights if w > 0)
            rc = 1.0 - ent / math.log(3)
            # Assume realistic high-LLM-confidence scenario: fast_lane_confidence=0.80
            # Conservative model: when only fast lane has data,
            # blended_confidence ≈ fast_lane_confidence × fast_weight
            #                     = 0.80 × weights[0]
            # scaled_confidence = blended_confidence × rc
            #                   = (0.80 × weights[0]) × rc
            bc_proxy = 0.80 * weights[0]
            scaled_proxy = bc_proxy * rc
            assert scaled_proxy > G1_CONFIDENCE_THRESHOLD, (
                f"{prefix}: with LLM conf=0.80, scaled={scaled_proxy:.4f} "
                f"must exceed G1={G1_CONFIDENCE_THRESHOLD}. weights={weights}, "
                f"rc={rc:.4f}, bc_proxy={bc_proxy:.4f}"
            )

    def test_conflict_priors_are_strongly_fast_dominant(self):
        for prefix in ("KXTRUMPIRAN", "KXARMOMINF", "KXELECTIONEMERGENCY"):
            w = compute_regime_weights(_market(series_ticker=prefix))
            assert w[FAST] >= 0.65, f"{prefix}: {w}"

    def test_new_categorical_priors_clear_g4_threshold(self):
        """Every new prior added in PROFIT-EDGE-002 MUST clear G4=0.20.

        Pinning this contract here prevents future weight tweaks from
        reintroducing the no-edge regression.
        """
        import math
        from tasks.trade_readiness_gate import G4_REGIME_CONFIDENCE_THRESHOLD

        new_prefixes = (
            "KXSBUDGETRES", "KXFISAEXTEND", "KXVOTESAVEAMERICA",
            "KXEFFTARIFF", "KXMOCTRUMP25",
            "KXCPIYOY", "KXCPICOREYOY", "KXCPIEU", "KXEZGDPYOYF",
            "KXTRUMPACT", "KXTRUMPENDORSE", "KXTRUMPCHINA",
            "KXTRUMPCRYPTOCONF", "KXVANCEPAKISTAN", "KXVISITVENEZUELA",
            "KXPARDONSTRUMP", "KXLTGOVGANOMR",
            "KXTRUMPIRAN", "KXARMOMINF", "KXELECTIONEMERGENCY",
            # Post-incident additions — 2026-05-12 zero-trade collapse diagnostic
            # surfaced these series reaching BD and failing G4 (rc<0.20) via
            # _time_prior fallback. See docs/CONTRACT_KALSHI_API.md §7 and
            # docs/profit_path_debt_log.md PROFIT-EDGE-005 (TBD).
            "KXTXRUNOFFENDORSE", "KXUSAIRANAGREEMENT", "KXNEWTARIFFS",
        )
        for prefix in new_prefixes:
            w = compute_regime_weights(_market(series_ticker=prefix))
            ent = -sum(v * math.log(v) for v in w.values() if v > 0)
            rc = 1.0 - ent / math.log(3)
            assert rc >= G4_REGIME_CONFIDENCE_THRESHOLD, (
                f"{prefix}: rc={rc:.4f} below G4={G4_REGIME_CONFIDENCE_THRESHOLD}; "
                f"weights={w}"
            )

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
#
# PROFIT-PRIORS-002 (2026-05-24): `_time_prior` is the fallback for series
# with NO `_SERIES_PRIORS` entry. The pre-fix code returned interpretation/
# structural-dominant weights for medium- and long-dated markets on the
# theory that "longer time-to-close means structural priors should matter
# more." That reasoning is sound IF the structural lane has data — but the
# bot's data infrastructure for uninstrumented series is fast-lane only
# (news LLM); interpretation/structural lanes have no dossier or external-
# prior service wired up for series outside `_SERIES_PRIORS`. The pre-fix
# shape silently diluted high-confidence LLM signals on every new Kalshi
# listing, making every uninstrumented series effectively untradeable.
#
# The pinned contract is now: `_time_prior` produces fast-dominant priors
# for ALL ≥1-day buckets, matching where data actually lives.
#
# Series with real structural/interpretation infrastructure (CPI, central
# bank, polling, sports) override via explicit `_SERIES_PRIORS` entries.

class TestTimePrior:
    def test_very_short_horizon_is_fast(self):
        """≤6h: fast-dominant (preserved from pre-fix; news-reactive)."""
        f, i, s = _time_prior(0.1)
        assert f > 0.80
        assert f > i > s

    def test_intraday_is_fast_dominant(self):
        """6h-1d: fast-dominant (preserved from pre-fix)."""
        f, i, s = _time_prior(0.5)
        assert f > 0.60

    def test_all_multiday_buckets_are_fast_dominant(self):
        """PROFIT-PRIORS-002 load-bearing contract: ≥1-day uninstrumented
        markets must be fast-dominant so the LLM signal reaches the
        blender without dilution.

        If a future refactor restores interp/structural-dominant fallbacks
        for these buckets (the pre-fix shape), this test catches it before
        merge. The fix exists because that shape silently broke every new
        Kalshi listing — operators were not catching it manually.
        """
        for days in (2.0, 5.0, 10.0, 20.0, 60.0):
            f, i, s = _time_prior(days)
            assert f >= 0.50, (
                f"days={days}: fast weight {f} must be ≥0.50 — "
                "uninstrumented series have only fast-lane data and "
                "non-fast-dominant priors silently dilute the LLM signal."
            )
            assert f > i, (
                f"days={days}: f={f} not > i={i}; fast lane must dominate"
            )
            assert f > s, (
                f"days={days}: f={f} not > s={s}; fast lane must dominate"
            )

    def test_all_buckets_clear_g4_threshold(self):
        """Every `_time_prior` bucket must produce regime_confidence ≥
        G4=0.20 so unprioritized markets are NEVER trapped in fail-safe
        mode purely because of the time-bucket fallback. Without this
        contract, a new Kalshi series with no explicit prior would be
        unable to clear G4 and would never reach normal-mode G1."""
        import math
        from tasks.trade_readiness_gate import G4_REGIME_CONFIDENCE_THRESHOLD
        for days in (0.1, 0.5, 2.0, 5.0, 10.0, 20.0, 60.0):
            w = _time_prior(days)
            ent = -sum(v * math.log(v) for v in w if v > 0)
            rc = 1.0 - ent / math.log(3)
            assert rc >= G4_REGIME_CONFIDENCE_THRESHOLD, (
                f"days={days}: rc={rc:.4f} below G4={G4_REGIME_CONFIDENCE_THRESHOLD}; "
                f"weights={w}. Uninstrumented series would be trapped in fail-safe."
            )

    def test_fast_weight_does_not_decrease_with_days_for_multiday(self):
        """Sanity: across all ≥1-day buckets the fast weight is at least
        as high as the previous bucket. Pre-fix code had decreasing fast
        weight with days (because it assumed structural lane had data).
        Post-fix all multi-day buckets share the same fast-dominant
        shape, so fast weight is non-decreasing in the multi-day range."""
        days_seq = [1.5, 3.0, 7.0, 14.0, 30.0, 60.0]
        fast_seq = [_time_prior(d)[0] for d in days_seq]
        assert all(
            fast_seq[i] >= fast_seq[i - 1] - 1e-9
            for i in range(1, len(fast_seq))
        ), (
            f"fast weights {fast_seq} must be non-decreasing across ≥1d "
            "buckets post-PROFIT-PRIORS-002. A future refactor that drops "
            "fast weight on longer windows reintroduces the dilution bug."
        )


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

    def test_geo_political_long_horizon_uninstrumented_is_fast_dominant(self):
        """PROFIT-PRIORS-002 (2026-05-24): long-horizon UNINSTRUMENTED
        series (no `_SERIES_PRIORS` entry) now default to fast-dominant
        weights. Pre-fix this test asserted structural-dominant — that
        shape silently broke every new Kalshi listing because the
        structural lane has no data infrastructure outside the
        explicit `_SERIES_PRIORS` entries. Series with real structural
        infrastructure (CPI, central bank) override via explicit
        entries; see `test_fed_rate_decision_market` below for that
        contract.
        """
        w = compute_regime_weights(_market(
            ticker="KXIRANLONG-1",
            series_ticker="",
            title="Will Iran sign a deal by end of year?",
            days=60.0,
        ))
        assert _weights_valid(w)
        assert w[FAST] >= 0.50, (
            f"long-horizon uninstrumented must be fast-dominant: got {w}"
        )
        assert w[FAST] > w[STRUCTURAL]

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
