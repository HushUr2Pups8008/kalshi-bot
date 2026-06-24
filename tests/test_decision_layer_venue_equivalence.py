"""Decision-layer venue-equivalence pins (PROFIT-VENUE-PARITY V25 + V13).

The venue-parity audit's central finding: the DECISION core — blend(), kelly_bet(),
evaluate_readiness() (G1-G7) — is venue-blind by construction; every Kalshi and
Polymarket candidate converges on these exact functions. All real venue
divergences live in the feedback/learning + recording layers, NOT here. These
tests are the regression net that protects that invariant: a future refactor that
threads venue/series into the decision core, or that makes a gate read a venue
label, fails here first. There was previously NO automated guard against that.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from analysis.decision_blender import LaneInput, blend
from analysis.kelly import kelly_bet
from tasks.trade_readiness_gate import FAST_SOURCE_LANE, evaluate_readiness

DECISION_FNS = [blend, kelly_bet, evaluate_readiness]


def test_decision_core_has_no_venue_or_series_parameter():
    """Keystone guard: none of blend/kelly_bet/evaluate_readiness may grow a
    'venue' or 'series_ticker' parameter — those would be the first place the two
    venues could diverge in WHICH trades happen / how big they are."""
    for fn in DECISION_FNS:
        params = set(inspect.signature(fn).parameters)
        assert "venue" not in params, f"{fn.__name__} grew a 'venue' param — decision core must stay venue-blind"
        assert "series_ticker" not in params, f"{fn.__name__} grew a 'series_ticker' param — must stay venue/family-blind"


def _readiness_input(venue, *, blended_confidence=0.50, disagreement=0.0):
    """Fast-lane blend-result shape — the shape BOTH venues use for a
    news-triggered candidate. Carries venue/series labels the gate MUST ignore."""
    return SimpleNamespace(
        source_lane=FAST_SOURCE_LANE,
        blended_confidence=blended_confidence,
        disagreement_score=disagreement,
        venue=venue,          # gate must ignore
        series_ticker=venue,  # gate must ignore
    )


def _decision_key(d):
    return (
        d.passed,
        d.failure_reasons,
        d.scaled_confidence,
        d.applied_conditions,
        d.trade_blocked_reason,
        d.fail_safe_active,
    )


def test_readiness_verdict_identical_across_venue_labels():
    """Same numeric blend result, different venue/series label -> identical
    readiness verdict. Proves no venue leakage into G1-G7 (would fail if any
    gate started reading the venue/series field)."""
    rc = 0.30
    kalshi = evaluate_readiness(_readiness_input("kalshi"), rc)
    poly = evaluate_readiness(_readiness_input("polymarket_us"), rc)
    assert _decision_key(kalshi) == _decision_key(poly)
    # And the cross-venue values are equal to each other, NOT to a hardcoded
    # constant (a real cross-venue equality assertion, per the audit recipe).
    assert kalshi.scaled_confidence == poly.scaled_confidence


def test_fast_lane_exempt_from_g2_g5_g6_for_both_venues():
    """V13 pin: a fast-lane candidate (no dossier) is exempt from G2/G5/G6 and
    must not require dossier fields — identically for either venue. An input with
    NO evidence_source_classes/drift/recency must not raise."""
    for venue in ("kalshi", "polymarket_us"):
        d = evaluate_readiness(_readiness_input(venue, blended_confidence=0.9), 0.30)
        assert d.applied_conditions == ("G1", "G3", "G4", "G7")
        for g in ("G2", "G5", "G6"):
            assert g not in d.applied_conditions
        assert d.source_class_count is None


def test_blend_and_kelly_take_no_venue_and_are_deterministic():
    """blend() + kelly_bet() carry no venue; the only thing a venue could vary is
    the numeric input, so identical inputs yield byte-identical outputs. Combined
    with the signature guard this documents the venue-blind contract end-to-end."""
    lane = dict(
        fast=LaneInput(p=0.62, confidence=0.80, lane_id="fast"),
        accumulation=None,
        structural=None,
        regime_weights={"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
        regime_confidence=1.0,
    )
    a, b = blend(**lane), blend(**lane)
    assert (a.blended_p, a.blended_confidence, a.disagreement_score, a.blend_mode) == (
        b.blended_p, b.blended_confidence, b.disagreement_score, b.blend_mode
    )
    kargs = dict(
        estimated_probability=0.62, market_price_cents=50, bankroll=50.0,
        confidence=0.80, days_to_close=14.0,
    )
    assert kelly_bet(**kargs) == kelly_bet(**kargs)
