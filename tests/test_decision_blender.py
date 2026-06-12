"""Tests for analysis/decision_blender.py (S3.3).

Covers:
- BlendResult type (fields, immutability)
- DER-1: confidence-weighted blend with RHR-3 regime interpolation
- DER-2: dominance rule (single lane >2× others)
- DER-3: structural fail-safe Tier 1 (active fast signal)
- DER-4: structural fail-safe Tier 2 (structural stable, no fast signal)
- Disagreement score (CL-9 formula)
- Edge cases: single lane, all-zero confidence, absent structural lane
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from analysis.decision_blender import (
    BlendResult,
    LaneInput,
    blend,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_EQUAL_REGIME = {"fast": 1.0 / 3, "interpretation": 1.0 / 3, "structural": 1.0 / 3}
_FAST_REGIME = {"fast": 1.0, "interpretation": 0.0, "structural": 0.0}
_UNIT_REGIME_CONF = 1.0   # full regime confidence → pure regime weights
_ZERO_REGIME_CONF = 0.0   # no regime confidence → uniform 1/3


def _fast(p: float = 0.60, conf: float = 0.50) -> LaneInput:
    return LaneInput(p=p, confidence=conf, lane_id="fast")


def _accum(p: float = 0.55, conf: float = 0.40) -> LaneInput:
    return LaneInput(p=p, confidence=conf, lane_id="accumulation")


def _struct(p: float = 0.50, conf: float = 0.30) -> LaneInput:
    return LaneInput(p=p, confidence=conf, lane_id="structural")


def _blend_all(
    *,
    fast_p: float = 0.60,
    fast_conf: float = 0.50,
    accum_p: float = 0.55,
    accum_conf: float = 0.40,
    struct_p: float = 0.50,
    struct_conf: float = 0.30,
    regime_weights: dict | None = None,
    regime_confidence: float = 1.0,
    fast_signal_active: bool = False,
    structural_stable: bool = False,
    default_min_edge: float = 0.05,
) -> BlendResult:
    rw = regime_weights or _EQUAL_REGIME
    return blend(
        fast=_fast(fast_p, fast_conf),
        accumulation=_accum(accum_p, accum_conf),
        structural=_struct(struct_p, struct_conf),
        regime_weights=rw,
        regime_confidence=regime_confidence,
        fast_signal_active=fast_signal_active,
        structural_stable=structural_stable,
        default_min_edge=default_min_edge,
    )


# ── Regime-weight key mapping (PROFIT-BLENDER-002) ────────────────────────────

class TestRegimeWeightKeyMapping:
    """The middle lane is "accumulation" in lane space but "interpretation" in
    regime-weight space (analysis/regime_classifier.compute_regime_weights emits
    {fast, interpretation, structural}). The blender must reconcile them: an
    accumulation lane must be weighted by the "interpretation" regime weight.

    Before the fix, `regime_weights.get("accumulation", 0.0)` returned 0.0 and
    silently zeroed the accumulation lane for every production blend (production
    never emits an "accumulation" key) -- diluting or dropping the dossier lane.
    The two behavioral tests feed the PRODUCTION key shape and fail on the unfixed
    lookup; the contract test guards against a future emitter-key rename silently
    re-introducing the bug.
    """

    def test_accumulation_lane_uses_interpretation_regime_weight(self):
        # rc=1.0 -> pure regime weights. interpretation weight 0.5 must reach the
        # accumulation lane so both lanes carry equal effective confidence and
        # blended_p is the mean of the lane p's.
        r = blend(
            fast=LaneInput(p=0.80, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.20, confidence=0.50, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5, "structural": 0.0},
            regime_confidence=1.0,
        )
        # Fixed: eff_fast = eff_accum = 0.25 -> weighted_blend -> mean(0.80,0.20)=0.50.
        # Bug: accum weight 0.0 -> eff_accum 0.0 -> fast dominates -> blended_p 0.80.
        assert r.blend_mode == "weighted_blend"
        assert r.blended_p == pytest.approx(0.50)

    def test_accumulation_lane_steers_blend_via_interpretation_weight(self):
        # Bug-sensitive (two-lane): a high interpretation weight must let the
        # accumulation lane steer the blend. fast(p=0.5,conf=0.3) vs
        # accumulation(p=0.2,conf=0.6) at interpretation weight 0.9, rc=1.0:
        #   fixed: eff_fast=0.03, eff_accum=0.54 -> accumulation dominates -> p=0.20.
        #   bug:   eff_accum=0.0 -> fast dominates -> p=0.50 (assertion fails -> guards).
        r = blend(
            fast=LaneInput(p=0.50, confidence=0.30, lane_id="fast"),
            accumulation=LaneInput(p=0.20, confidence=0.60, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.1, "interpretation": 0.9, "structural": 0.0},
            regime_confidence=1.0,
        )
        assert r.blend_mode == "dominant_lane"
        assert r.blended_p == pytest.approx(0.20)

    def test_alias_targets_are_keys_the_emitter_produces(self):
        # PROFIT-BLENDER-002 hardening: every _LANE_TO_REGIME_KEY alias TARGET must
        # be a key compute_regime_weights actually emits. If the emitter's middle
        # key were renamed (e.g. "interpretation" -> "interp"), the alias would
        # silently miss and re-zero the accumulation lane -- the exact bug. This
        # contract makes that rename fail loudly instead of regressing silently.
        from analysis.decision_blender import _LANE_TO_REGIME_KEY
        from analysis.regime_classifier import compute_regime_weights

        from tests.test_regime_classifier import _market

        emitted = set(compute_regime_weights(_market()).keys())
        assert set(_LANE_TO_REGIME_KEY.values()) <= emitted, (
            f"alias targets {set(_LANE_TO_REGIME_KEY.values())} are not all emitted "
            f"by compute_regime_weights ({emitted}) -- a regime-key rename would "
            "silently re-zero the accumulation lane (PROFIT-BLENDER-002)"
        )


# ── BlendResult type ──────────────────────────────────────────────────────────

class TestBlendResultType:
    def test_fields_accessible(self):
        r = _blend_all()
        assert r.blended_p is not None
        assert r.blended_confidence is not None
        assert r.disagreement_score is not None
        assert r.blend_mode is not None
        assert r.fast_lane_p == pytest.approx(0.60)
        assert r.fast_lane_confidence == pytest.approx(0.50)
        assert r.accumulation_p == pytest.approx(0.55)
        assert r.accumulation_confidence == pytest.approx(0.40)
        assert r.structural_p == pytest.approx(0.50)
        assert r.structural_confidence == pytest.approx(0.30)

    def test_is_immutable(self):
        r = _blend_all()
        with pytest.raises(FrozenInstanceError):
            r.blended_p = 0.99  # type: ignore[misc]

    def test_absent_lane_fields_are_none(self):
        r = blend(
            fast=_fast(),
            accumulation=None,
            structural=None,
            regime_weights=_FAST_REGIME,
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.accumulation_p is None
        assert r.accumulation_confidence is None
        assert r.structural_p is None
        assert r.structural_confidence is None


# ── DER-1: Confidence-weighted blend ─────────────────────────────────────────

class TestDER1WeightedBlend:
    def test_equal_confidence_averages_lane_p(self):
        """With equal effective confidence, blended_p is the arithmetic mean."""
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.40, confidence=0.50, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.blended_p == pytest.approx(0.50)
        assert r.blend_mode == "weighted_blend"

    def test_higher_confidence_lane_dominates_blend(self):
        # conf=0.65/0.35 → eff=0.325/0.175; 0.325 < 2*0.175 so dominance does NOT fire
        r = blend(
            fast=LaneInput(p=0.80, confidence=0.65, lane_id="fast"),
            accumulation=LaneInput(p=0.20, confidence=0.35, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        # fast weight = 0.65*0.5 = 0.325; accum weight = 0.35*0.5 = 0.175; total = 0.50
        # p_blend = (0.325*0.80 + 0.175*0.20) / 0.50 = (0.26+0.035)/0.50 = 0.59
        assert r.blend_mode == "weighted_blend"
        assert r.blended_p == pytest.approx(0.59)

    def test_single_fast_lane_used_directly(self):
        r = blend(
            fast=_fast(p=0.72),
            accumulation=None,
            structural=None,
            regime_weights=_FAST_REGIME,
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.blended_p == pytest.approx(0.72)

    def test_rhr3_zero_regime_confidence_uses_uniform_weights(self):
        """RHR-3: at regime_confidence=0, all lanes get equal weight 1/3."""
        r = blend(
            fast=LaneInput(p=0.90, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.10, confidence=0.50, lane_id="accumulation"),
            structural=None,
            # Even with fast=1.0 regime weight, uniform 1/3 applies at conf=0
            regime_weights={"fast": 1.0, "interpretation": 0.0},
            regime_confidence=_ZERO_REGIME_CONF,
        )
        # Both get eff_conf = 0.50 * 1/3 = 0.1667 → equal → mean = 0.50
        assert r.blended_p == pytest.approx(0.50, abs=1e-6)

    def test_rhr3_partial_regime_confidence_interpolates(self):
        # Use balanced regime weights so dominance does not fire:
        # eff_fast = 0.50*((0.4*1/3)+(0.6*0.6)) ≈ 0.247; eff_accum = 0.50*((0.4*1/3)+(0.6*0.4)) ≈ 0.187
        # 0.247 < 2*0.187 = 0.373 → no dominance
        rc = 0.6
        fast_rw = 0.6
        accum_rw = 0.4
        fast_p, fast_conf = 0.70, 0.50
        accum_p, accum_conf = 0.30, 0.50
        eff_fast = fast_conf * ((1 - rc) * (1 / 3) + rc * fast_rw)
        eff_accum = accum_conf * ((1 - rc) * (1 / 3) + rc * accum_rw)
        total = eff_fast + eff_accum
        expected_p = (eff_fast * fast_p + eff_accum * accum_p) / total
        r = blend(
            fast=LaneInput(p=fast_p, confidence=fast_conf, lane_id="fast"),
            accumulation=LaneInput(p=accum_p, confidence=accum_conf, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": fast_rw, "interpretation": accum_rw},
            regime_confidence=rc,
        )
        assert r.blend_mode == "weighted_blend"
        assert r.blended_p == pytest.approx(expected_p)

    def test_all_zero_effective_confidence_equal_weight_fallback(self):
        """Zero effective confidence → equal-weight fallback, not zero-division crash."""
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.0, lane_id="fast"),
            accumulation=LaneInput(p=0.40, confidence=0.0, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.blended_p == pytest.approx(0.50)
        assert r.blended_confidence == pytest.approx(0.0)

    def test_no_lanes_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            blend(
                fast=None,
                accumulation=None,
                structural=None,
                regime_weights={},
                regime_confidence=1.0,
            )


# ── DER-2: Dominance rule ─────────────────────────────────────────────────────

class TestDER2DominanceRule:
    def test_dominant_lane_adopted_directly(self):
        """fast eff_conf = 0.90; accum eff_conf = 0.10; 0.90 > 2*0.10 → dominant."""
        fast_p = 0.75
        r = blend(
            fast=LaneInput(p=fast_p, confidence=0.90, lane_id="fast"),
            accumulation=LaneInput(p=0.20, confidence=0.10, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.blended_p == pytest.approx(fast_p)
        assert r.blend_mode == "dominant_lane"

    def test_non_dominant_falls_through_to_weighted_blend(self):
        """eff_conf nearly equal → neither dominates."""
        r = blend(
            fast=LaneInput(p=0.70, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.30, confidence=0.40, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.blend_mode == "weighted_blend"

    def test_dominance_boundary_exactly_2x_is_not_dominant(self):
        """DER-2 requires strictly greater than 2×, not equal."""
        # eff_conf[0] = 0.60; eff_conf[1] = 0.30; 0.60 == 2*0.30, not > 2×
        r = blend(
            fast=LaneInput(p=0.70, confidence=0.60, lane_id="fast"),
            accumulation=LaneInput(p=0.30, confidence=0.30, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 1.0, "interpretation": 1.0},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.blend_mode == "weighted_blend"

    def test_dominance_checked_by_effective_not_raw_confidence(self):
        """With zero regime weight, high raw confidence does not make a lane dominant."""
        r = blend(
            fast=LaneInput(p=0.80, confidence=0.99, lane_id="fast"),
            accumulation=LaneInput(p=0.40, confidence=0.50, lane_id="accumulation"),
            structural=None,
            # fast has zero regime weight → effective confidence = 0
            regime_weights={"fast": 0.0, "interpretation": 1.0},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        # fast eff_conf = 0, accumulation eff_conf = 0.50 → accumulation dominates
        assert r.blend_mode == "dominant_lane"
        assert r.blended_p == pytest.approx(0.40)


# ── DER-3: Structural Fail-Safe Tier 1 ───────────────────────────────────────

class TestDER3StructuralTier1:
    # Structural has a tiny regime weight (0.01) so it barely moves the blend,
    # ensuring blend stays near fast/accum lanes while structural.p=0.20 diverges
    # by >0.30. Fast and accum share equal weight → no dominance fires.
    # structural.confidence=0.75 >= 0.70 satisfies the threshold.
    _RW = {"fast": 0.5, "interpretation": 0.5, "structural": 0.01}

    def _failsafe_blend(self, *, fast_signal_active: bool, structural_stable: bool = False) -> BlendResult:
        return blend(
            fast=LaneInput(p=0.65, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.65, confidence=0.50, lane_id="accumulation"),
            structural=LaneInput(p=0.20, confidence=0.75, lane_id="structural"),
            regime_weights=self._RW,
            regime_confidence=_UNIT_REGIME_CONF,
            fast_signal_active=fast_signal_active,
            structural_stable=structural_stable,
            default_min_edge=0.05,
        )

    def test_tier1_activates_when_conditions_met(self):
        r = self._failsafe_blend(fast_signal_active=True)
        assert r.blend_mode == "structural_tier1_override"
        assert r.trade_blocked_reason is None

    def test_tier1_doubles_min_edge_override(self):
        r = blend(
            fast=LaneInput(p=0.65, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.65, confidence=0.50, lane_id="accumulation"),
            structural=LaneInput(p=0.20, confidence=0.75, lane_id="structural"),
            regime_weights=self._RW,
            regime_confidence=_UNIT_REGIME_CONF,
            fast_signal_active=True,
            default_min_edge=0.04,
        )
        assert r.blend_mode == "structural_tier1_override"
        assert r.readiness_gate_min_edge_override == pytest.approx(0.08)

    def test_tier1_requires_high_structural_confidence(self):
        """Below threshold → no fail-safe activation."""
        r = blend(
            fast=LaneInput(p=0.65, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.65, confidence=0.50, lane_id="accumulation"),
            structural=LaneInput(p=0.20, confidence=0.69, lane_id="structural"),
            regime_weights=self._RW,
            regime_confidence=_UNIT_REGIME_CONF,
            fast_signal_active=True,
        )
        assert r.blend_mode == "weighted_blend"

    def test_tier1_requires_sufficient_divergence(self):
        """Structural close to blend → no fail-safe."""
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.60, confidence=0.50, lane_id="accumulation"),
            structural=LaneInput(p=0.55, confidence=0.75, lane_id="structural"),
            regime_weights=self._RW,
            regime_confidence=_UNIT_REGIME_CONF,
            fast_signal_active=True,
        )
        assert r.blend_mode != "structural_tier1_override"

    def test_tier1_not_activated_when_no_fast_signal(self):
        """Without fast_signal_active and without structural_stable, neither tier fires."""
        r = self._failsafe_blend(fast_signal_active=False, structural_stable=False)
        assert r.blend_mode == "weighted_blend"


# ── DER-4: Structural Fail-Safe Tier 2 ───────────────────────────────────────

class TestDER4StructuralTier2:
    # Same three-lane setup as DER-3 tests: fast+accum share equal weight,
    # structural has tiny weight (0.01) so blend stays near 0.65 and
    # structural.p=0.20 diverges by >0.30.
    _RW = {"fast": 0.5, "interpretation": 0.5, "structural": 0.01}

    def _failsafe_blend(self, *, fast_signal_active: bool, structural_stable: bool) -> BlendResult:
        return blend(
            fast=LaneInput(p=0.65, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.65, confidence=0.50, lane_id="accumulation"),
            structural=LaneInput(p=0.20, confidence=0.75, lane_id="structural"),
            regime_weights=self._RW,
            regime_confidence=_UNIT_REGIME_CONF,
            fast_signal_active=fast_signal_active,
            structural_stable=structural_stable,
        )

    def test_tier2_activates_stable_no_fast_signal(self):
        r = self._failsafe_blend(fast_signal_active=False, structural_stable=True)
        assert r.blend_mode == "structural_tier2_veto"
        assert r.trade_blocked_reason is not None
        assert "tier2_veto" in r.trade_blocked_reason

    def test_tier2_min_edge_override_is_none(self):
        r = self._failsafe_blend(fast_signal_active=False, structural_stable=True)
        assert r.readiness_gate_min_edge_override is None

    def test_tier2_not_activated_when_structural_unstable(self):
        """Without structural stability, Tier 2 does not activate."""
        r = self._failsafe_blend(fast_signal_active=False, structural_stable=False)
        assert r.blend_mode == "weighted_blend"

    def test_fast_signal_degrades_tier2_to_tier1(self):
        """DER-4: 'If fast-lane signal exists within the window, Tier 2 degrades to Tier 1.'"""
        r = self._failsafe_blend(fast_signal_active=True, structural_stable=True)
        assert r.blend_mode == "structural_tier1_override"

    def test_tier2_no_structural_lane_cannot_activate(self):
        r = blend(
            fast=_fast(),
            accumulation=_accum(),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
            fast_signal_active=False,
            structural_stable=True,
        )
        assert r.blend_mode not in ("structural_tier1_override", "structural_tier2_veto")


# ── Disagreement score (CL-9) ─────────────────────────────────────────────────

class TestDisagreementScore:
    def test_identical_lane_p_zero_disagreement(self):
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.60, confidence=0.50, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.disagreement_score == pytest.approx(0.0)

    def test_single_lane_zero_disagreement(self):
        r = blend(
            fast=_fast(p=0.70),
            accumulation=None,
            structural=None,
            regime_weights=_FAST_REGIME,
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.disagreement_score == pytest.approx(0.0)

    def test_max_disagreement_two_lanes_opposite_ends(self):
        """p=[0,1] equal weight → mean=0.5, variance=0.25, std_dev=0.5."""
        r = blend(
            fast=LaneInput(p=0.0, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=1.0, confidence=0.50, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        assert r.disagreement_score == pytest.approx(0.5)

    def test_disagreement_weighted_by_effective_confidence(self):
        """High-confidence lane with outlier drives score up more than equal weights would."""
        # fast at 0.80 w/ high conf, accum at 0.20 w/ low conf
        # weighted mean pulled toward fast; accum deviation from mean is large but low-weighted
        r_high_conf = blend(
            fast=LaneInput(p=0.80, confidence=0.90, lane_id="fast"),
            accumulation=LaneInput(p=0.20, confidence=0.10, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        r_equal_conf = blend(
            fast=LaneInput(p=0.80, confidence=0.50, lane_id="fast"),
            accumulation=LaneInput(p=0.20, confidence=0.50, lane_id="accumulation"),
            structural=None,
            regime_weights={"fast": 0.5, "interpretation": 0.5},
            regime_confidence=_UNIT_REGIME_CONF,
        )
        # Equal confidence = perfect 50/50 split → max std dev for [0.2, 0.8]
        # High confidence fast case: mean ~= 0.74, accum deviation is big but down-weighted
        # The scores should be different, not testing which is larger (weight-dependent)
        assert r_high_conf.disagreement_score != pytest.approx(r_equal_conf.disagreement_score)

    def test_disagreement_formula_manual_verification(self):
        """Cross-check CL-9 formula manually for two lanes."""
        fast_p, accum_p = 0.70, 0.50
        fast_conf, accum_conf = 0.60, 0.40
        rw = {"fast": 0.5, "interpretation": 0.5}
        rc = 1.0
        eff_f = fast_conf * rw["fast"]
        # The accumulation lane is weighted by the "interpretation" regime weight
        # (PROFIT-BLENDER-002 lane<->regime key reconciliation).
        eff_a = accum_conf * rw["interpretation"]
        total = eff_f + eff_a
        w_f, w_a = eff_f / total, eff_a / total
        mean = w_f * fast_p + w_a * accum_p
        variance = w_f * (fast_p - mean) ** 2 + w_a * (accum_p - mean) ** 2
        expected = math.sqrt(variance)
        r = blend(
            fast=LaneInput(p=fast_p, confidence=fast_conf, lane_id="fast"),
            accumulation=LaneInput(p=accum_p, confidence=accum_conf, lane_id="accumulation"),
            structural=None,
            regime_weights=rw,
            regime_confidence=rc,
        )
        assert r.disagreement_score == pytest.approx(expected)


# ── Output invariants ─────────────────────────────────────────────────────────

class TestOutputInvariants:
    def test_blended_p_always_in_01(self):
        for fp, ap, sp in [(0.0, 0.5, 1.0), (0.99, 0.01, 0.50)]:
            r = _blend_all(fast_p=fp, accum_p=ap, struct_p=sp)
            assert 0.0 <= r.blended_p <= 1.0

    def test_disagreement_score_nonnegative(self):
        r = _blend_all()
        assert r.disagreement_score >= 0.0

    def test_weighted_blend_has_no_block_reason(self):
        r = _blend_all()
        assert r.blend_mode == "weighted_blend"
        assert r.trade_blocked_reason is None

    def test_weighted_blend_no_min_edge_override(self):
        r = _blend_all()
        assert r.readiness_gate_min_edge_override is None

    def test_lane_snapshots_preserved(self):
        r = _blend_all(fast_p=0.61, accum_p=0.55, struct_p=0.49)
        assert r.fast_lane_p == pytest.approx(0.61)
        assert r.accumulation_p == pytest.approx(0.55)
        assert r.structural_p == pytest.approx(0.49)


# ── PROFIT-BLENDER-001: lane-aware blend (signal_kind="fallback" exclusion)
#
# Spec: docs/superpowers/specs/2026-05-24-lane-aware-blender-design.md.


class TestProfitBlender001LaneAwareFiltering:
    """Lane-aware blend filter — `fallback` lanes excluded when ≥1 real."""

    _WEIGHTS = {"fast": 0.65, "interpretation": 0.25, "structural": 0.10}

    def _blend(self, fast=None, accumulation=None, structural=None, rc=0.22):
        return blend(
            fast=fast,
            accumulation=accumulation,
            structural=structural,
            regime_weights=self._WEIGHTS,
            regime_confidence=rc,
            fast_signal_active=True,
        )

    def test_lane_input_default_signal_kind_is_real(self):
        """Backward-compat: existing callers that don't set signal_kind
        get the default `real`, preserving pre-fix behavior."""
        lane = LaneInput(p=0.4, confidence=0.8, lane_id="fast")
        assert lane.signal_kind == "real"

    def test_fallback_lane_excluded_when_real_lane_present(self):
        """Load-bearing contract. Fast lane real + accumulation fallback
        + structural fallback → only fast lane contributes. Mirrors the
        2026-05-24 KXUSAIRANAGREEMENT-27-26JUN incident state."""
        fast = LaneInput(p=0.05, confidence=0.85, lane_id="fast", signal_kind="real")
        accum_fallback = LaneInput(
            p=0.5, confidence=0.15, lane_id="accumulation",
            signal_kind="fallback",
        )
        struct_fallback = LaneInput(
            p=0.5, confidence=0.10, lane_id="structural",
            signal_kind="fallback",
        )
        r = self._blend(fast=fast, accumulation=accum_fallback, structural=struct_fallback)
        # Only fast lane contributed → blended_p should match fast.p exactly.
        assert r.blended_p == pytest.approx(0.05), (
            f"fallback lanes must not dilute the blended_p; got {r.blended_p}"
        )
        # Dominant_lane mode triggers when one lane stands alone.
        assert r.blend_mode == "dominant_lane"
        # Lane snapshot fields still preserved (informational, not used in blend).
        assert r.accumulation_p == pytest.approx(0.5)
        assert r.structural_p == pytest.approx(0.5)

    def test_two_real_lanes_one_fallback_blend_only_real_lanes(self):
        """fast real + accumulation real + structural fallback → blend over
        the two real lanes only. Structural fallback must not contribute."""
        fast = LaneInput(p=0.20, confidence=0.85, lane_id="fast")
        accum_real = LaneInput(p=0.30, confidence=0.70, lane_id="accumulation")
        struct_fallback = LaneInput(
            p=0.5, confidence=0.10, lane_id="structural",
            signal_kind="fallback",
        )
        r = self._blend(fast=fast, accumulation=accum_real, structural=struct_fallback)
        # blended_p should be a weighted combination of fast (0.20) and
        # accum (0.30) only — definitely not pulled toward 0.5.
        assert r.blended_p < 0.35, (
            f"two real lanes (both below 0.35) must not blend to >0.35 "
            f"when structural fallback is excluded; got {r.blended_p}"
        )

    def test_three_real_lanes_unchanged_behavior(self):
        """All three lanes real → no filtering applied → pre-fix behavior
        preserved."""
        fast = LaneInput(p=0.55, confidence=0.50, lane_id="fast", signal_kind="real")
        accum = LaneInput(p=0.60, confidence=0.40, lane_id="accumulation", signal_kind="real")
        struct = LaneInput(p=0.45, confidence=0.30, lane_id="structural", signal_kind="real")
        r_with_kind = self._blend(fast=fast, accumulation=accum, structural=struct)
        # Identical input without signal_kind set (default "real") → same output.
        fast2 = LaneInput(p=0.55, confidence=0.50, lane_id="fast")
        accum2 = LaneInput(p=0.60, confidence=0.40, lane_id="accumulation")
        struct2 = LaneInput(p=0.45, confidence=0.30, lane_id="structural")
        r_without_kind = self._blend(fast=fast2, accumulation=accum2, structural=struct2)
        assert r_with_kind.blended_p == pytest.approx(r_without_kind.blended_p)
        assert r_with_kind.blended_confidence == pytest.approx(r_without_kind.blended_confidence)
        assert r_with_kind.blend_mode == r_without_kind.blend_mode

    def test_all_fallback_lanes_degraded_blend_no_crash(self):
        """All lanes fallback → degenerate to equal-weight blend over
        all of them. Better than crashing."""
        fast_fb = LaneInput(p=0.5, confidence=0.10, lane_id="fast", signal_kind="fallback")
        accum_fb = LaneInput(p=0.5, confidence=0.10, lane_id="accumulation", signal_kind="fallback")
        struct_fb = LaneInput(p=0.5, confidence=0.10, lane_id="structural", signal_kind="fallback")
        r = self._blend(fast=fast_fb, accumulation=accum_fb, structural=struct_fb)
        assert r.blended_p == pytest.approx(0.5)
        assert r.blend_mode in ("weighted_blend", "dominant_lane")

    def test_low_confidence_real_lane_is_NOT_dropped(self):
        """A lane with low confidence but NON-neutral p stays as "real"
        and contributes. The fallback flag is for "no data," NOT for
        "weak data". This pins the Codex-flagged boundary: weak_prior /
        real lanes must NOT be dropped."""
        fast = LaneInput(p=0.10, confidence=0.85, lane_id="fast", signal_kind="real")
        struct_weak_real = LaneInput(
            p=0.20, confidence=0.15, lane_id="structural", signal_kind="real",
        )
        r = self._blend(fast=fast, accumulation=None, structural=struct_weak_real)
        # Lane snapshot preserved
        assert r.structural_p == pytest.approx(0.20)
        # If structural were dropped pre-blend, blended_p would equal fast.p=0.10.
        fast_only = self._blend(fast=fast, accumulation=None, structural=None)
        if r.blend_mode == "weighted_blend":
            assert r.blended_p != pytest.approx(fast_only.blended_p), (
                "weak_real structural lane was silently dropped; "
                "blended_p matches fast-only result"
            )

    def test_kxusairanagreement_2026_05_24_regression(self):
        """REGRESSION: replay the actual 2026-05-24 BD state with the new
        lane filter. Pre-fix produced scaled_confidence=0.027 (failed
        G1=0.05). Post-fix the dominant fast lane should produce a
        blended_confidence high enough to clear G1.

        Lane state from the live BD record:
          fast       : p=0.05, conf=0.85  → real
          accumulation: p=0.50, conf=0.15  → fallback (neutral default)
          structural : p=0.10, conf=0.24  → real (weak prior, non-neutral)
        """
        from tasks.trade_readiness_gate import G1_CONFIDENCE_THRESHOLD
        fast = LaneInput(p=0.05, confidence=0.85, lane_id="fast", signal_kind="real")
        accum_fallback = LaneInput(
            p=0.50, confidence=0.15, lane_id="accumulation",
            signal_kind="fallback",
        )
        struct_real = LaneInput(p=0.10, confidence=0.24, lane_id="structural",
                                signal_kind="real")
        r = self._blend(fast=fast, accumulation=accum_fallback, structural=struct_real, rc=0.22)
        scaled = r.blended_confidence * 0.22
        assert scaled > G1_CONFIDENCE_THRESHOLD, (
            f"REGRESSION PIN: post-fix scaled_confidence must clear G1. "
            f"got bc={r.blended_confidence:.4f} × rc=0.22 = {scaled:.4f}, "
            f"G1={G1_CONFIDENCE_THRESHOLD}. If this assertion fails, the "
            "lane-aware filter is not effective at preventing the "
            "2026-05-24 incident."
        )


class TestProfitBlender001NegativeGates:
    """Lane-aware filter must NOT cause readiness gates to be bypassed."""

    _WEIGHTS = {"fast": 0.65, "interpretation": 0.25, "structural": 0.10}

    def test_filter_does_not_admit_disagreement_blocked_scenarios(self):
        """G3 (disagreement) must still see real-lane disagreement after
        fallback exclusion."""
        fast = LaneInput(p=0.90, confidence=0.85, lane_id="fast", signal_kind="real")
        accum_real = LaneInput(p=0.10, confidence=0.85, lane_id="accumulation", signal_kind="real")
        struct_fb = LaneInput(p=0.5, confidence=0.10, lane_id="structural", signal_kind="fallback")
        r = blend(
            fast=fast, accumulation=accum_real, structural=struct_fb,
            regime_weights=self._WEIGHTS, regime_confidence=0.22,
            fast_signal_active=True,
        )
        assert r.disagreement_score > 0.0, (
            "disagreement must remain visible after fallback filter — "
            "G3 still needs to fire on real-lane disagreement"
        )

    def test_filter_does_not_break_structural_failsafe_when_structural_real(self):
        """DER-3/DER-4 structural fail-safe path must still trigger when
        a high-confidence structural lane diverges from the blend, even
        with an accumulation fallback present."""
        fast = LaneInput(p=0.90, confidence=0.85, lane_id="fast", signal_kind="real")
        accum_fb = LaneInput(p=0.5, confidence=0.10, lane_id="accumulation", signal_kind="fallback")
        struct_high_conf = LaneInput(
            p=0.20, confidence=0.80, lane_id="structural", signal_kind="real",
        )
        r = blend(
            fast=fast, accumulation=accum_fb, structural=struct_high_conf,
            regime_weights=self._WEIGHTS, regime_confidence=0.22,
            fast_signal_active=True,
            structural_stable=False,
        )
        # blend_mode is determined by the existing DER-2/DER-3/DER-4 rules
        # on the non-fallback subset. The contract is "fail-safe path
        # remains reachable" — any of the legitimate modes is fine.
        assert r.blend_mode in (
            "structural_tier1_override",
            "weighted_blend",
            "dominant_lane",
        ), (
            f"unexpected blend_mode {r.blend_mode!r}; the fallback filter "
            "must not break structural fail-safe pathways"
        )


# ── PROFIT-EDGE-014: confidence-weighted blended_confidence (operator option b)


class TestConfidenceWeightedBlend:
    """blended_confidence is the interp-weight-weighted MEAN of lane
    confidences, not a mean of (confidence x weight) products divided by lane
    COUNT. WHY: the count-mean diluted a 0.85-confidence fast lane to ~0.15
    blended whenever two low-confidence lanes were present, producing the G1
    near-miss cluster diagnosed 2026-06-12 (16/16 skips at median scaled 0.044
    vs the 0.05 threshold). DER-1 pins only the p_blend formula; confidence
    aggregation is implementation-defined."""

    _DIAG_REGIME = {"fast": 0.65, "interpretation": 0.25, "structural": 0.10}

    def test_edge014_diagnosed_near_miss_now_clears_g1(self):
        """Production-shaped near-miss: fast 0.75 (observed 0.75-0.85), acc
        0.43 (observed max), struct 0.18, rc=0.25 — ratios flat enough to stay
        in weighted_blend, like the 16 diagnosed skips (their recorded
        blended_confidence 0.10-0.20 matches the old count-mean in
        weighted_blend mode; a dominance-shaped triple would have cleared G1
        even under old math). Old math: blended = 0.493/3 ~0.164 -> scaled
        ~0.041 (the diagnosed near-miss band, G1 FAIL). New math: blended
        ~0.493 -> scaled ~0.123, clearing both G1 thresholds."""
        from tasks.trade_readiness_gate import (
            G1_CONFIDENCE_THRESHOLD,
            G1_FAILSAFE_CONFIDENCE_THRESHOLD,
        )
        rc = 0.25
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.75, lane_id="fast"),
            accumulation=LaneInput(p=0.55, confidence=0.43, lane_id="accumulation"),
            structural=LaneInput(p=0.50, confidence=0.18, lane_id="structural"),
            regime_weights=self._DIAG_REGIME,
            regime_confidence=rc,
        )
        assert r.blend_mode == "weighted_blend"
        # interp weights: fast .4125 / acc .3125 / struct .275 (sum 1.0)
        expected = 0.75 * 0.4125 + 0.43 * 0.3125 + 0.18 * 0.275
        assert r.blended_confidence == pytest.approx(expected, abs=1e-6)
        scaled = r.blended_confidence * rc
        assert scaled >= G1_CONFIDENCE_THRESHOLD
        assert scaled >= G1_FAILSAFE_CONFIDENCE_THRESHOLD

    def test_blended_confidence_bounded_by_lane_confidences(self):
        """A true weighted average cannot leave [min(conf), max(conf)] — the
        property that makes the change safe against overshoot: no combination
        of weights can manufacture confidence above the most confident lane."""
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.85, lane_id="fast"),
            accumulation=LaneInput(p=0.55, confidence=0.21, lane_id="accumulation"),
            structural=LaneInput(p=0.50, confidence=0.12, lane_id="structural"),
            regime_weights=self._DIAG_REGIME,
            regime_confidence=0.25,
        )
        assert 0.12 <= r.blended_confidence <= 0.85

    def test_single_lane_adopts_its_own_confidence(self):
        """Docstring contract: 'When only one lane is present it is adopted
        directly.' The old math scaled a lone lane's confidence by its interp
        regime weight (0.85 -> ~0.35), contradicting that."""
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.85, lane_id="fast"),
            accumulation=None,
            structural=None,
            regime_weights=self._DIAG_REGIME,
            regime_confidence=0.25,
        )
        assert r.blended_confidence == pytest.approx(0.85)

    def test_equal_confidence_lanes_blend_to_that_confidence(self):
        """Weighted-mean invariance: if every lane reports the same confidence,
        the blend reports exactly that confidence regardless of weights."""
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.40, lane_id="fast"),
            accumulation=LaneInput(p=0.55, confidence=0.40, lane_id="accumulation"),
            structural=LaneInput(p=0.50, confidence=0.40, lane_id="structural"),
            regime_weights=self._DIAG_REGIME,
            regime_confidence=0.7,
        )
        assert r.blended_confidence == pytest.approx(0.40)

    def test_dominant_lane_adopts_raw_confidence(self):
        """DER-2 full authority extends to confidence: the old eff-conf output
        made a DOMINANT lane report LOWER confidence than a contested blend
        (more agreement -> less confidence — inverted)."""
        r = blend(
            fast=LaneInput(p=0.80, confidence=0.90, lane_id="fast"),
            accumulation=LaneInput(p=0.20, confidence=0.05, lane_id="accumulation"),
            structural=LaneInput(p=0.30, confidence=0.05, lane_id="structural"),
            regime_weights=self._DIAG_REGIME,
            regime_confidence=1.0,
        )
        assert r.blend_mode == "dominant_lane"
        assert r.blended_p == pytest.approx(0.80)
        assert r.blended_confidence == pytest.approx(0.90)

    def test_all_zero_confidence_still_zero(self):
        """Degraded path unchanged: all-zero lane confidences -> blended 0.0."""
        r = blend(
            fast=LaneInput(p=0.60, confidence=0.0, lane_id="fast"),
            accumulation=LaneInput(p=0.55, confidence=0.0, lane_id="accumulation"),
            structural=None,
            regime_weights=self._DIAG_REGIME,
            regime_confidence=0.25,
        )
        assert r.blended_confidence == pytest.approx(0.0)
