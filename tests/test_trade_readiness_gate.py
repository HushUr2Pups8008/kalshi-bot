from types import SimpleNamespace

import pytest

from tasks.trade_readiness_gate import (
    G1_CONFIDENCE_THRESHOLD,
    G3_OVERRIDE_MULTIPLIER,
    G4_REGIME_CONFIDENCE_THRESHOLD,
    ReadinessInputError,
    evaluate_readiness,
)


def _dossier_candidate(**overrides):
    candidate = {
        "source_lane": "blend",
        "blended_confidence": 0.80,
        "disagreement_score": 0.10,
        "default_min_edge": 0.04,
        "evidence_source_classes": ["news", "official"],
        "drift_suspect": False,
        "in_recovery": False,
        "recency_score": 0.80,
    }
    candidate.update(overrides)
    return candidate


def _fast_candidate(**overrides):
    candidate = {
        "source_lane": "fast",
        "blended_confidence": 0.80,
        "disagreement_score": 0.10,
        "default_min_edge": 0.04,
    }
    candidate.update(overrides)
    return candidate


def test_passing_dossier_candidate_satisfies_all_conditions():
    decision = evaluate_readiness(_dossier_candidate(), regime_confidence=0.80)

    assert decision.passed is True
    assert decision.failure_reasons == ()
    assert decision.trade_blocked_reason is None
    assert decision.readiness_gate_min_edge_override is None
    assert decision.scaled_confidence == pytest.approx(0.64)
    assert decision.applied_conditions == ("G1", "G3", "G4", "G2", "G5", "G6")


def test_g1_scaled_confidence_is_enforced():
    decision = evaluate_readiness(
        _dossier_candidate(blended_confidence=G1_CONFIDENCE_THRESHOLD),
        regime_confidence=0.99,
    )

    assert decision.passed is False
    assert "G1_blended_confidence" in decision.failure_reasons
    assert decision.trade_blocked_reason == "G1_blended_confidence"


def test_g2_source_class_diversity_is_enforced_for_dossiers():
    decision = evaluate_readiness(
        _dossier_candidate(evidence_source_classes=["news", "news"]),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G2_evidence_source_class_diversity",)


def test_g3_blocks_above_disagreement_threshold():
    decision = evaluate_readiness(
        _dossier_candidate(disagreement_score=0.2001),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G3_disagreement_score",)


def test_g3_mid_band_passes_with_min_edge_override():
    decision = evaluate_readiness(
        _dossier_candidate(disagreement_score=0.15, default_min_edge=0.04),
        regime_confidence=0.80,
    )

    assert decision.passed is True
    assert decision.readiness_gate_min_edge_override == pytest.approx(
        0.04 * G3_OVERRIDE_MULTIPLIER
    )


def test_g4_regime_confidence_is_enforced_and_tightens_thresholds():
    # PROFIT-EDGE-002 (v0.29.57): G4 threshold lowered from 0.40 to 0.20.
    # Use a regime_confidence below the new threshold to exercise the
    # fail-safe activation path; structure of the assertions is unchanged.
    decision = evaluate_readiness(
        _dossier_candidate(blended_confidence=1.0, disagreement_score=0.16),
        regime_confidence=G4_REGIME_CONFIDENCE_THRESHOLD - 0.01,
    )

    assert decision.passed is False
    assert decision.fail_safe_active is True
    assert decision.failure_reasons == (
        "G1_blended_confidence",
        "G3_disagreement_score",
        "G4_regime_confidence",
    )


def test_g4_threshold_is_calibrated_to_pass_existing_categorical_priors():
    """PROFIT-EDGE-002 calibration test.

    The G4 threshold MUST be at or below the regime_confidence values produced
    by the polling, central-bank, crypto, and weather categorical priors in
    analysis/regime_classifier.py — those priors were intentionally designed
    to be tradeable. The pre-fix threshold (0.40) excluded all four. Pinning
    this expectation here prevents a future bump back above 0.20 without
    explicit recalibration of the prior table.
    """
    import math

    def _rc(weights: tuple[float, float, float]) -> float:
        ent = -sum(w * math.log(w) for w in weights if w > 0)
        return 1.0 - ent / math.log(3)

    designed_tradeable_priors = {
        "Polling (KXAPRPOTUS)":      (0.05, 0.25, 0.70),
        "Central bank (KXCBDECISION)": (0.05, 0.30, 0.65),
        "Crypto (KXCRYPTO)":         (0.65, 0.28, 0.07),
        "Weather (KXWEATHER)":       (0.10, 0.25, 0.65),
    }
    for name, w in designed_tradeable_priors.items():
        rc = _rc(w)
        assert rc >= G4_REGIME_CONFIDENCE_THRESHOLD, (
            f"{name} prior rc={rc:.3f} fails G4={G4_REGIME_CONFIDENCE_THRESHOLD}; "
            "either lower G4 or concentrate the prior weights"
        )


def test_g4_passes_at_threshold_boundary_in_normal_mode():
    """Markets exactly at G4 should pass G4 and run in normal (non-failsafe) mode."""
    decision = evaluate_readiness(
        _dossier_candidate(),
        regime_confidence=G4_REGIME_CONFIDENCE_THRESHOLD,
    )

    assert "G4_regime_confidence" not in decision.failure_reasons
    assert decision.fail_safe_active is False


def test_g5_blocks_drift_suspect_dossier():
    decision = evaluate_readiness(
        _dossier_candidate(drift_suspect=True),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G5_dossier_drift_suspect",)


def test_g5_allows_recovery_mode_dossier():
    decision = evaluate_readiness(
        _dossier_candidate(drift_suspect=True, in_recovery=True),
        regime_confidence=0.80,
    )

    assert decision.passed is True


def test_g6_recency_score_is_enforced_for_dossiers():
    decision = evaluate_readiness(
        _dossier_candidate(recency_score=0.2999),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G6_recency_score",)


def test_fast_lane_exempts_dossier_only_conditions():
    decision = evaluate_readiness(
        _fast_candidate(
            evidence_source_classes=[],
            drift_suspect=True,
            in_recovery=False,
            recency_score=0.0,
        ),
        regime_confidence=0.80,
    )

    assert decision.passed is True
    assert decision.applied_conditions == ("G1", "G3", "G4")


def test_fast_lane_does_not_exempt_common_conditions():
    decision = evaluate_readiness(
        _fast_candidate(blended_confidence=0.10, disagreement_score=0.21),
        regime_confidence=G4_REGIME_CONFIDENCE_THRESHOLD - 0.01,
    )

    assert decision.passed is False
    assert decision.failure_reasons == (
        "G1_blended_confidence",
        "G3_disagreement_score",
        "G4_regime_confidence",
    )


def test_missing_dossier_required_field_fails_clearly():
    candidate = _dossier_candidate()
    candidate.pop("recency_score")

    with pytest.raises(ReadinessInputError, match="recency_score"):
        evaluate_readiness(candidate, regime_confidence=0.80)


def test_missing_default_min_edge_only_required_for_override_band():
    candidate = _dossier_candidate(disagreement_score=0.10)
    candidate.pop("default_min_edge")

    decision = evaluate_readiness(candidate, regime_confidence=0.80)

    assert decision.passed is True

    with pytest.raises(ReadinessInputError, match="default_min_edge"):
        evaluate_readiness(
            {**candidate, "disagreement_score": 0.15},
            regime_confidence=0.80,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("blended_confidence", 1.1),
        ("blended_confidence", -0.1),
        ("disagreement_score", -0.1),
        ("source_lane", ""),
    ],
)
def test_malformed_inputs_fail_clearly(field, value):
    with pytest.raises(ReadinessInputError, match=field):
        evaluate_readiness(_dossier_candidate(**{field: value}), regime_confidence=0.80)


def test_invalid_regime_confidence_fails_clearly():
    with pytest.raises(ReadinessInputError, match="regime_confidence"):
        evaluate_readiness(_dossier_candidate(), regime_confidence=1.1)


def test_attribute_style_blend_result_is_supported():
    decision = evaluate_readiness(
        SimpleNamespace(**_dossier_candidate()),
        regime_confidence=0.80,
    )

    assert decision.passed is True


def test_evaluation_is_stateless_and_deterministic():
    candidate = _dossier_candidate(disagreement_score=0.15)

    first = evaluate_readiness(candidate, regime_confidence=0.80)
    second = evaluate_readiness(candidate, regime_confidence=0.80)

    assert first == second


def test_no_public_partial_gate_helpers_are_exposed():
    import tasks.trade_readiness_gate as gate

    public_callables = {
        name
        for name, value in gate.__dict__.items()
        if callable(value) and not name.startswith("_")
    }

    assert "evaluate_readiness" in public_callables
    assert not any("G1" in name or "partial" in name for name in public_callables)
