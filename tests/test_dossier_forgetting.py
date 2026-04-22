"""Tests for S2.6 forgetting mechanisms in analysis/dossier_builder.py."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from analysis.dossier_builder import (
    _DEFAULT_HALF_LIFE,
    _HALF_LIFE_BY_REGIME,
    clear_on_resolution,
    decay_weight,
    half_life_for_regime,
    identify_superseded,
    recency_score,
)
from analysis.evidence_types import Dossier, Evidence


# ── Fixtures ──────────────────────────────────────────────────────────────────

_TS = "2026-04-19T12:00:00+00:00"
_HL = 7.0  # default half-life in days for most tests


def _now() -> datetime:
    return datetime.fromisoformat(_TS)


def _ev(
    evidence_id: str = "ev-1",
    source_class: str = "news",
    headline: str = "Market moves on new data release",
    ingested_ts: str = _TS,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        market_ticker="KXTEST-1",
        source="reuters",
        source_class=source_class,
        headline=headline,
        ingested_ts=ingested_ts,
        implied_probability=0.60,
        content_hash=evidence_id,
    )


def _dossier(
    current_estimate: float | None = 0.65,
    confidence: float = 0.40,
    prior_estimate: float | None = 0.65,
    drift_suspect: bool = False,
    in_recovery: bool = False,
    version: int = 3,
) -> Dossier:
    return Dossier(
        market_ticker="KXTEST-1",
        dossier_version=version,
        current_estimate=current_estimate,
        confidence=confidence,
        prior_estimate=prior_estimate,
        drift_suspect=drift_suspect,
        in_recovery=in_recovery,
        freeze_started_ts="2026-04-18T12:00:00+00:00" if drift_suspect else None,
        recovery_started_ts="2026-04-18T12:00:00+00:00" if in_recovery else None,
        recovery_until_ts="2026-04-26T12:00:00+00:00" if in_recovery else None,
        last_cross_class_state_update_ts=_TS,
        created_ts=_TS,
        updated_ts=_TS,
    )


# ── decay_weight ──────────────────────────────────────────────────────────────

class TestDecayWeight:
    def test_zero_age_no_decay(self):
        assert decay_weight(0.70, 0.0, _HL) == pytest.approx(0.70)

    def test_one_half_life_halves_weight(self):
        age_s = _HL * 86_400
        result = decay_weight(1.0, age_s, _HL)
        assert result == pytest.approx(0.5, rel=1e-6)

    def test_two_half_lives_quarter_weight(self):
        age_s = 2 * _HL * 86_400
        result = decay_weight(1.0, age_s, _HL)
        assert result == pytest.approx(0.25, rel=1e-6)

    def test_weight_decreases_monotonically(self):
        w0 = decay_weight(1.0, 0.0, _HL)
        w1 = decay_weight(1.0, 86_400.0, _HL)
        w7 = decay_weight(1.0, 7 * 86_400.0, _HL)
        assert w0 > w1 > w7

    def test_weight_always_positive(self):
        assert decay_weight(1.0, 365 * 86_400.0, _HL) > 0.0

    def test_proportional_to_original_weight(self):
        # decay_weight is linear in original_weight.
        age_s = 3 * 86_400.0
        r1 = decay_weight(1.0, age_s, _HL)
        r2 = decay_weight(2.0, age_s, _HL)
        assert r2 == pytest.approx(2 * r1)


# ── recency_score ─────────────────────────────────────────────────────────────

class TestRecencyScore:
    def test_empty_items_returns_zero(self):
        assert recency_score([], _now(), _HL) == pytest.approx(0.0)

    def test_all_fresh_items_score_one(self):
        # Items ingested at exactly now — age=0, no decay.
        items = [(0.70, _TS), (0.85, _TS)]
        assert recency_score(items, _now(), _HL) == pytest.approx(1.0)

    def test_single_item_one_half_life_old_scores_half(self):
        old_ts = (_now() - timedelta(days=_HL)).isoformat()
        items = [(1.0, old_ts)]
        assert recency_score(items, _now(), _HL) == pytest.approx(0.5, rel=1e-5)

    def test_mixed_items_score_between_zero_and_one(self):
        old_ts = (_now() - timedelta(days=_HL)).isoformat()
        items = [(1.0, _TS), (1.0, old_ts)]
        score = recency_score(items, _now(), _HL)
        assert 0.0 < score < 1.0

    def test_very_old_items_approach_zero(self):
        ancient_ts = (_now() - timedelta(days=100 * _HL)).isoformat()
        items = [(1.0, ancient_ts)]
        assert recency_score(items, _now(), _HL) < 0.01

    def test_score_bounded_zero_to_one(self):
        items = [(0.70, _TS), (0.55, _TS)]
        score = recency_score(items, _now(), _HL)
        assert 0.0 <= score <= 1.0


# ── half_life_for_regime ──────────────────────────────────────────────────────

class TestHalfLifeForRegime:
    def test_fast_shorter_than_structural(self):
        assert half_life_for_regime("fast") < half_life_for_regime("structural")

    def test_interpretation_between_fast_and_structural(self):
        assert half_life_for_regime("fast") < half_life_for_regime("interpretation") < half_life_for_regime("structural")

    def test_unknown_regime_returns_default(self):
        assert half_life_for_regime("unknown_xyz") == pytest.approx(_DEFAULT_HALF_LIFE)

    def test_all_known_regimes_positive(self):
        for regime in _HALF_LIFE_BY_REGIME:
            assert half_life_for_regime(regime) > 0.0


# ── identify_superseded ───────────────────────────────────────────────────────

class TestIdentifySuperseded:
    _LATER = "2026-04-19T13:00:00+00:00"

    def test_empty_list_returns_empty(self):
        assert identify_superseded([]) == frozenset()

    def test_single_item_returns_empty(self):
        assert identify_superseded([_ev()]) == frozenset()

    def test_newer_same_class_high_overlap_supersedes_older(self):
        headline = "Federal Reserve cuts interest rates by fifty basis points today"
        older = _ev(evidence_id="ev-old", headline=headline, ingested_ts=_TS)
        newer = _ev(evidence_id="ev-new", headline=headline, ingested_ts=self._LATER)
        result = identify_superseded([older, newer])
        assert "ev-old" in result
        assert "ev-new" not in result

    def test_older_does_not_supersede_newer(self):
        headline = "Federal Reserve cuts interest rates by fifty basis points today"
        older = _ev(evidence_id="ev-old", headline=headline, ingested_ts=_TS)
        newer = _ev(evidence_id="ev-new", headline=headline, ingested_ts=self._LATER)
        result = identify_superseded([older, newer])
        assert "ev-new" not in result

    def test_different_class_high_overlap_not_superseded(self):
        headline = "Federal Reserve cuts interest rates by fifty basis points today"
        older = _ev(evidence_id="ev-old", source_class="news", headline=headline, ingested_ts=_TS)
        newer = _ev(evidence_id="ev-new", source_class="official", headline=headline, ingested_ts=self._LATER)
        result = identify_superseded([older, newer])
        assert "ev-old" not in result

    def test_same_class_low_overlap_not_superseded(self):
        older = _ev(evidence_id="ev-old", headline="Apple stock rises on earnings report", ingested_ts=_TS)
        newer = _ev(evidence_id="ev-new", headline="Unemployment rate falls to new low", ingested_ts=self._LATER)
        result = identify_superseded([older, newer])
        assert "ev-old" not in result

    def test_chain_middle_superseded_by_newest(self):
        headline = "Federal Reserve cuts interest rates by fifty basis points today"
        t1 = "2026-04-19T10:00:00+00:00"
        t2 = "2026-04-19T11:00:00+00:00"
        t3 = "2026-04-19T12:00:00+00:00"
        ev1 = _ev(evidence_id="ev-1", headline=headline, ingested_ts=t1)
        ev2 = _ev(evidence_id="ev-2", headline=headline, ingested_ts=t2)
        ev3 = _ev(evidence_id="ev-3", headline=headline, ingested_ts=t3)
        result = identify_superseded([ev1, ev2, ev3])
        assert "ev-1" in result
        assert "ev-2" in result
        assert "ev-3" not in result

    def test_unrelated_items_not_superseded(self):
        ev_a = _ev(evidence_id="ev-a", headline="Apple stock rises on earnings beat", ingested_ts=_TS)
        ev_b = _ev(evidence_id="ev-b", headline="Federal Reserve holds interest rates steady", ingested_ts=self._LATER)
        result = identify_superseded([ev_a, ev_b])
        assert result == frozenset()


# ── clear_on_resolution ───────────────────────────────────────────────────────

class TestClearOnResolution:
    _CLEAR_TS = "2026-04-20T00:00:00+00:00"

    def test_current_estimate_cleared(self):
        result = clear_on_resolution(_dossier(current_estimate=0.72), self._CLEAR_TS)
        assert result.current_estimate is None

    def test_prior_estimate_cleared(self):
        result = clear_on_resolution(_dossier(prior_estimate=0.65), self._CLEAR_TS)
        assert result.prior_estimate is None

    def test_confidence_reset_to_zero(self):
        result = clear_on_resolution(_dossier(confidence=0.80), self._CLEAR_TS)
        assert result.confidence == pytest.approx(0.0)

    def test_drift_suspect_cleared(self):
        result = clear_on_resolution(_dossier(drift_suspect=True), self._CLEAR_TS)
        assert result.drift_suspect is False

    def test_in_recovery_cleared(self):
        result = clear_on_resolution(_dossier(in_recovery=True), self._CLEAR_TS)
        assert result.in_recovery is False

    def test_all_optional_timestamps_cleared(self):
        d = _dossier(drift_suspect=True)
        result = clear_on_resolution(d, self._CLEAR_TS)
        assert result.freeze_started_ts is None
        assert result.recovery_started_ts is None
        assert result.recovery_until_ts is None
        assert result.last_cross_class_state_update_ts is None

    def test_market_ticker_preserved(self):
        result = clear_on_resolution(_dossier(), self._CLEAR_TS)
        assert result.market_ticker == "KXTEST-1"

    def test_created_ts_preserved(self):
        result = clear_on_resolution(_dossier(), self._CLEAR_TS)
        assert result.created_ts == _TS

    def test_version_incremented(self):
        d = _dossier(version=5)
        result = clear_on_resolution(d, self._CLEAR_TS)
        assert result.dossier_version == 6

    def test_updated_ts_set_to_cleared_ts(self):
        result = clear_on_resolution(_dossier(), self._CLEAR_TS)
        assert result.updated_ts == self._CLEAR_TS

    def test_original_dossier_unchanged(self):
        d = _dossier(current_estimate=0.65, confidence=0.40)
        _ = clear_on_resolution(d, self._CLEAR_TS)
        assert d.current_estimate == pytest.approx(0.65)
        assert d.confidence == pytest.approx(0.40)
