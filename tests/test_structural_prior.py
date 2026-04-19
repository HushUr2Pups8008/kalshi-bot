"""Tests for analysis/structural_prior.py (S3.1).

Covers:
- PriorEstimate type (fields, immutability)
- Estimate synthesis (LLM authority, market price fallback, clamping)
- Confidence model (per-component contributions, cap, fail-safe boundary)
- Context key handling (missing keys, None values, empty records)
- Output invariants (market_ticker, computed_ts, probability bounds)
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest

from analysis.evidence_types import PriorEstimate
from analysis.structural_prior import (
    _CONFIDENCE_CAP,
    _CONFIDENCE_LLM_BONUS,
    _CONFIDENCE_REGIME_WEIGHT,
    _CONFIDENCE_EVIDENCE_WEIGHT,
    _ESTIMATE_CEILING,
    _ESTIMATE_FLOOR,
    _EVIDENCE_SATURATION,
    compute_structural_prior,
)
from kalshi import KalshiMarket

# ── Helpers ───────────────────────────────────────────────────────────────────

_TICKER = "KXTEST-STRUCT-26DEC31"
_NOW_TS = "2026-04-19T12:00:00+00:00"


def _market(
    *,
    yes_price: float = 55.0,
    structural_weight: float = 0.0,
) -> KalshiMarket:
    regime = {"fast": 0.0, "interpretation": 0.0, "structural": structural_weight}
    total = sum(regime.values())
    if total < 1.0:
        regime["fast"] += 1.0 - total
    return KalshiMarket(
        ticker=_TICKER,
        title="Test structural market",
        yes_bid=54.0,
        yes_ask=56.0,
        yes_price=yes_price,
        volume=1000,
        open_interest=500,
        close_time="2026-12-31T23:59:59Z",
        status="open",
        regime_weights=regime,
    )


def _context(
    *,
    llm_estimate: float | None = None,
    llm_called: bool = False,
    evidence_count: int = 0,
    now_ts: str | None = _NOW_TS,
) -> dict:
    records = [object() for _ in range(evidence_count)]
    ctx: dict = {
        "llm_called": llm_called,
        "evidence_records": records,
    }
    if llm_estimate is not None:
        ctx["llm_estimate"] = llm_estimate
    if now_ts is not None:
        ctx["now_ts"] = now_ts
    return ctx


# ── PriorEstimate type ────────────────────────────────────────────────────────

class TestPriorEstimateType:
    def test_fields_accessible(self):
        pe = PriorEstimate(
            market_ticker=_TICKER,
            estimate=0.60,
            confidence=0.75,
            input_source_count=5,
            llm_called=True,
            computed_ts=_NOW_TS,
        )
        assert pe.market_ticker == _TICKER
        assert pe.estimate == pytest.approx(0.60)
        assert pe.confidence == pytest.approx(0.75)
        assert pe.input_source_count == 5
        assert pe.llm_called is True
        assert pe.computed_ts == _NOW_TS

    def test_is_immutable(self):
        pe = PriorEstimate(
            market_ticker=_TICKER,
            estimate=0.50,
            confidence=0.40,
            input_source_count=0,
            llm_called=False,
            computed_ts=_NOW_TS,
        )
        with pytest.raises(FrozenInstanceError):
            pe.estimate = 0.99  # type: ignore[misc]


# ── Estimate synthesis ────────────────────────────────────────────────────────

class TestEstimateSynthesis:
    def test_llm_estimate_is_authoritative(self):
        market = _market(yes_price=55.0)
        result = compute_structural_prior(market, _context(llm_estimate=0.72, llm_called=True))
        assert result.estimate == pytest.approx(0.72)

    def test_market_price_used_when_no_llm_estimate(self):
        market = _market(yes_price=63.0)
        result = compute_structural_prior(market, _context())
        assert result.estimate == pytest.approx(0.63)

    def test_llm_estimate_overrides_market_price(self):
        market = _market(yes_price=30.0)
        result = compute_structural_prior(market, _context(llm_estimate=0.80, llm_called=True))
        assert result.estimate == pytest.approx(0.80)
        assert result.estimate != pytest.approx(0.30)

    def test_llm_estimate_below_floor_clamped(self):
        result = compute_structural_prior(_market(), _context(llm_estimate=0.0, llm_called=True))
        assert result.estimate == pytest.approx(_ESTIMATE_FLOOR)

    def test_llm_estimate_above_ceiling_clamped(self):
        result = compute_structural_prior(_market(), _context(llm_estimate=1.0, llm_called=True))
        assert result.estimate == pytest.approx(_ESTIMATE_CEILING)

    def test_market_price_zero_clamped_to_floor(self):
        market = _market(yes_price=0.0)
        result = compute_structural_prior(market, _context())
        assert result.estimate == pytest.approx(_ESTIMATE_FLOOR)

    def test_market_price_100_clamped_to_ceiling(self):
        market = _market(yes_price=100.0)
        result = compute_structural_prior(market, _context())
        assert result.estimate == pytest.approx(_ESTIMATE_CEILING)

    def test_none_llm_estimate_falls_back_to_market(self):
        market = _market(yes_price=45.0)
        ctx = _context()
        ctx["llm_estimate"] = None
        result = compute_structural_prior(market, ctx)
        assert result.estimate == pytest.approx(0.45)


# ── Confidence model ──────────────────────────────────────────────────────────

class TestConfidenceModel:
    def test_zero_confidence_with_no_inputs(self):
        market = _market(structural_weight=0.0)
        result = compute_structural_prior(market, _context(llm_called=False, evidence_count=0))
        assert result.confidence == pytest.approx(0.0)

    def test_llm_bonus_applied(self):
        market = _market(structural_weight=0.0)
        result = compute_structural_prior(market, _context(llm_called=True, evidence_count=0))
        assert result.confidence == pytest.approx(_CONFIDENCE_LLM_BONUS)

    def test_regime_weight_contribution(self):
        market = _market(structural_weight=1.0)
        result = compute_structural_prior(market, _context(llm_called=False, evidence_count=0))
        assert result.confidence == pytest.approx(_CONFIDENCE_REGIME_WEIGHT)

    def test_evidence_saturates_at_saturation_count(self):
        market = _market(structural_weight=0.0)
        at_saturation = compute_structural_prior(
            market, _context(llm_called=False, evidence_count=_EVIDENCE_SATURATION)
        )
        above_saturation = compute_structural_prior(
            market, _context(llm_called=False, evidence_count=_EVIDENCE_SATURATION * 2)
        )
        assert at_saturation.confidence == pytest.approx(above_saturation.confidence)
        assert at_saturation.confidence == pytest.approx(_CONFIDENCE_EVIDENCE_WEIGHT)

    def test_max_confidence_without_llm_below_failsafe_threshold(self):
        """Without LLM, structural fail-safe (DER-3/DER-4, threshold 0.70) must never activate."""
        market = _market(structural_weight=1.0)
        result = compute_structural_prior(
            market,
            _context(llm_called=False, evidence_count=_EVIDENCE_SATURATION * 10),
        )
        max_without_llm = _CONFIDENCE_REGIME_WEIGHT + _CONFIDENCE_EVIDENCE_WEIGHT
        assert result.confidence == pytest.approx(max_without_llm)
        assert result.confidence < 0.70

    def test_max_confidence_with_llm_can_exceed_failsafe_threshold(self):
        """With LLM + max regime weight + full evidence, confidence must exceed 0.70."""
        market = _market(structural_weight=1.0)
        result = compute_structural_prior(
            market,
            _context(llm_called=True, evidence_count=_EVIDENCE_SATURATION),
        )
        assert result.confidence > 0.70

    def test_confidence_capped_at_095(self):
        market = _market(structural_weight=1.0)
        result = compute_structural_prior(
            market,
            _context(llm_called=True, evidence_count=_EVIDENCE_SATURATION * 10),
        )
        assert result.confidence == pytest.approx(_CONFIDENCE_CAP)

    def test_partial_regime_weight_proportional(self):
        market = _market(structural_weight=0.5)
        result = compute_structural_prior(market, _context(llm_called=False, evidence_count=0))
        assert result.confidence == pytest.approx(0.5 * _CONFIDENCE_REGIME_WEIGHT)

    def test_partial_evidence_depth_proportional(self):
        market = _market(structural_weight=0.0)
        half = _EVIDENCE_SATURATION // 2
        result = compute_structural_prior(market, _context(llm_called=False, evidence_count=half))
        assert result.confidence == pytest.approx(0.5 * _CONFIDENCE_EVIDENCE_WEIGHT)

    def test_all_components_additive(self):
        market = _market(structural_weight=1.0)
        result = compute_structural_prior(
            market,
            _context(llm_called=True, evidence_count=_EVIDENCE_SATURATION),
        )
        expected = min(
            _CONFIDENCE_REGIME_WEIGHT + _CONFIDENCE_EVIDENCE_WEIGHT + _CONFIDENCE_LLM_BONUS,
            _CONFIDENCE_CAP,
        )
        assert result.confidence == pytest.approx(expected)


# ── Context key handling ──────────────────────────────────────────────────────

class TestContextKeyHandling:
    def test_empty_context_uses_defaults(self):
        market = _market(yes_price=50.0)
        result = compute_structural_prior(market, {})
        assert result.estimate == pytest.approx(0.50)
        assert result.confidence == pytest.approx(0.0)
        assert result.llm_called is False
        assert result.input_source_count == 0

    def test_missing_now_ts_produces_valid_iso_timestamp(self):
        result = compute_structural_prior(_market(), {})
        ts = result.computed_ts
        assert "T" in ts
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)

    def test_now_ts_none_falls_back_to_utcnow(self):
        ctx = _context(now_ts=None)
        result = compute_structural_prior(_market(), ctx)
        assert result.computed_ts is not None
        assert "T" in result.computed_ts

    def test_none_evidence_records_treated_as_empty(self):
        ctx = {"evidence_records": None, "llm_called": False, "now_ts": _NOW_TS}
        result = compute_structural_prior(_market(), ctx)
        assert result.input_source_count == 0

    def test_llm_called_absent_defaults_false(self):
        ctx = {"now_ts": _NOW_TS}
        result = compute_structural_prior(_market(), ctx)
        assert result.llm_called is False

    def test_market_without_regime_weights_uses_zero_structural(self):
        market = KalshiMarket(
            ticker=_TICKER,
            title="No regime",
            yes_bid=49.0,
            yes_ask=51.0,
            yes_price=50.0,
            volume=100,
            open_interest=50,
            close_time="2026-12-31T23:59:59Z",
            status="open",
        )
        result = compute_structural_prior(market, _context())
        assert result.confidence == pytest.approx(0.0)


# ── Output invariants ─────────────────────────────────────────────────────────

class TestOutputInvariants:
    def test_market_ticker_preserved(self):
        result = compute_structural_prior(_market(), _context())
        assert result.market_ticker == _TICKER

    def test_now_ts_preserved_when_provided(self):
        result = compute_structural_prior(_market(), _context(now_ts=_NOW_TS))
        assert result.computed_ts == _NOW_TS

    def test_estimate_always_in_01_range(self):
        for yes_price in [0.0, 0.5, 50.0, 99.5, 100.0]:
            market = _market(yes_price=yes_price)
            result = compute_structural_prior(market, _context())
            assert 0.0 <= result.estimate <= 1.0

    def test_confidence_always_nonnegative(self):
        result = compute_structural_prior(_market(), _context())
        assert result.confidence >= 0.0

    def test_confidence_never_exceeds_cap(self):
        market = _market(structural_weight=1.0)
        result = compute_structural_prior(
            market,
            _context(llm_called=True, evidence_count=100),
        )
        assert result.confidence <= _CONFIDENCE_CAP

    def test_input_source_count_matches_evidence_records_length(self):
        result = compute_structural_prior(_market(), _context(evidence_count=7))
        assert result.input_source_count == 7

    def test_llm_called_flag_propagated(self):
        r_false = compute_structural_prior(_market(), _context(llm_called=False))
        r_true = compute_structural_prior(_market(), _context(llm_called=True))
        assert r_false.llm_called is False
        assert r_true.llm_called is True
