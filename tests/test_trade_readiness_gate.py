from types import SimpleNamespace

import pytest

from tasks.trade_readiness_gate import (
    G1_CONFIDENCE_THRESHOLD,
    G3_OVERRIDE_MULTIPLIER,
    G4_REGIME_CONFIDENCE_THRESHOLD,
    G6_RECENCY_THRESHOLD,
    ReadinessInputError,
    evaluate_readiness,
)


def _dossier_candidate(**overrides):
    candidate = {
        "source_lane": "blend",
        "blended_confidence": 0.80,
        "disagreement_score": 0.10,
        "default_min_edge": 0.04,
        "evidence_source_classes": ["news", "regional"],
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
    assert decision.applied_conditions == ("G1", "G3", "G4", "G7", "G2", "G5", "G6")


def test_capital_drawdown_blocks_otherwise_passing_candidate():
    decision = evaluate_readiness(
        _dossier_candidate(open_exposure_drawdown_pct=0.201),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G7_open_exposure_drawdown",)
    assert decision.trade_blocked_reason == "G7_open_exposure_drawdown"
    assert "G7" in decision.applied_conditions


def test_zero_liquidity_blocks_otherwise_passing_candidate():
    decision = evaluate_readiness(
        _dossier_candidate(market_liquidity_dollars=0.0),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G7_zero_liquidity",)
    assert decision.trade_blocked_reason == "G7_zero_liquidity"


@pytest.mark.parametrize(
    ("side", "momentum"),
    [
        ("yes", -1.0),
        ("no", 1.0),
    ],
)
def test_adverse_price_momentum_blocks_news_chase_entries(side, momentum):
    decision = evaluate_readiness(
        _dossier_candidate(
            intended_side=side,
            market_price_momentum_cents=momentum,
        ),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G7_adverse_price_momentum",)


def test_g1_scaled_confidence_is_enforced():
    decision = evaluate_readiness(
        _dossier_candidate(blended_confidence=G1_CONFIDENCE_THRESHOLD),
        regime_confidence=0.99,
    )

    assert decision.passed is False
    assert "G1_blended_confidence" in decision.failure_reasons
    assert decision.trade_blocked_reason == "G1_blended_confidence"


def test_g2_source_class_diversity_is_enforced_for_dossiers(monkeypatch):
    # PROFIT-SOURCE-001: G2 default is now 1 (single-source allowed). Pin it to 2
    # to test the diversity-enforcement MECHANISM. Relaxed-policy behaviour is
    # covered by test_blend_task.py::test_single_source_allowed_and_count_tracked.
    monkeypatch.setattr("tasks.trade_readiness_gate.G2_MIN_SOURCE_CLASSES", 2)
    decision = evaluate_readiness(
        _dossier_candidate(evidence_source_classes=["news", "news"]),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G2_evidence_source_class_diversity",)
    assert decision.source_class_count == 1


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
    # PROFIT-EDGE-003 (v0.29.58): G1 base 0.35→0.05, failsafe 0.50→0.10.
    # blended_confidence chosen so scaled (= bc × rc) sits below the failsafe
    # G1 threshold (0.10) when rc is just below the new G4 (0.20):
    #   bc=0.50, rc=0.19  →  scaled = 0.095 < 0.10  →  G1 fails
    decision = evaluate_readiness(
        _dossier_candidate(blended_confidence=0.50, disagreement_score=0.16),
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


def test_g1_threshold_is_calibrated_to_pass_top_decile_production_signals():
    """PROFIT-EDGE-003 calibration test (analogue of the G4 calibration test).

    Production data over the 9-day window 2026-04-17 → 2026-04-26 produced
    154 BLEND_DECISIONs with the following scaled_confidence distribution:

        median  P75    P90    P95    max
        0.026   0.035  0.119  0.119  0.304

    G1 must sit below the P90 cluster (0.119) to actually allow strong
    signals through, but above the median (0.026) to reject noise. The
    chosen value is 0.05; this test pins the contract so that future
    bumps back above ~0.10 fail until accompanied by explicit
    recalibration of either the regime priors, the blender, or the
    blended-confidence model.

    A representative top-decile production scaled_conf is 0.119 — that
    must clear G1 with non-trivial headroom.
    """
    p90_production_scaled_conf = 0.119
    assert p90_production_scaled_conf > G1_CONFIDENCE_THRESHOLD * 1.5, (
        f"G1={G1_CONFIDENCE_THRESHOLD} leaves <50% headroom over the P90 "
        f"production scaled_conf of {p90_production_scaled_conf}; either "
        f"lower G1 or recalibrate the regime classifier"
    )

    median_production_scaled_conf = 0.026
    assert median_production_scaled_conf < G1_CONFIDENCE_THRESHOLD, (
        f"G1={G1_CONFIDENCE_THRESHOLD} is at or below the median noise "
        f"floor of {median_production_scaled_conf}; the gate is no longer "
        f"discriminating between signal and noise"
    )


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
    assert decision.recency_score == pytest.approx(0.2999)
    assert decision.recency_threshold == pytest.approx(G6_RECENCY_THRESHOLD)
    assert decision.recency_distance == pytest.approx(-0.0001)


def test_passing_dossier_records_g6_recency_margin():
    decision = evaluate_readiness(
        _dossier_candidate(recency_score=0.80),
        regime_confidence=0.80,
    )

    assert decision.passed is True
    assert decision.recency_score == pytest.approx(0.80)
    assert decision.recency_threshold == pytest.approx(G6_RECENCY_THRESHOLD)
    assert decision.recency_distance == pytest.approx(0.80 - G6_RECENCY_THRESHOLD)


@pytest.mark.parametrize(
    ("overrides", "expected_threshold"),
    [
        ({}, 0.30),
        ({"evidence_source_classes": ["other"]}, 0.35),
        ({"time_to_close_seconds": 6 * 60 * 60}, 0.38),
        ({"time_to_close_seconds": 2 * 86_400}, 0.34),
        ({"time_to_close_seconds": 21 * 86_400}, 0.27),
        ({"settlement_source_relevant": True}, 0.26),
    ],
)
def test_g6_recency_threshold_component_adjustments_are_pinned(
    overrides,
    expected_threshold,
):
    decision = evaluate_readiness(
        _dossier_candidate(recency_score=0.99, **overrides),
        regime_confidence=0.80,
    )

    assert decision.passed is True
    assert decision.recency_threshold == pytest.approx(expected_threshold)


def test_g6_replays_observed_weak_source_long_horizon_skip_shape():
    decision = evaluate_readiness(
        _dossier_candidate(
            evidence_source_classes=["other"],
            recency_score=0.0682,
            time_to_close_seconds=129 * 86_400,
        ),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G6_recency_score",)
    assert decision.recency_threshold == pytest.approx(0.32)
    assert decision.recency_distance == pytest.approx(-0.2518)


def test_g6_recency_threshold_relaxes_for_relevant_official_long_horizon_evidence():
    decision = evaluate_readiness(
        _dossier_candidate(
            evidence_source_classes=["official", "wire"],
            recency_score=0.205,
            time_to_close_seconds=21 * 86_400,
            settlement_source_relevant=True,
        ),
        regime_confidence=0.80,
    )

    assert decision.passed is True
    assert decision.recency_threshold == pytest.approx(0.20)
    assert decision.recency_distance == pytest.approx(0.005)


def test_g6_recency_threshold_tightens_for_near_close_other_source_evidence():
    decision = evaluate_readiness(
        _dossier_candidate(
            evidence_source_classes=["news", "other"],
            recency_score=0.33,
            time_to_close_seconds=6 * 60 * 60,
            settlement_source_relevant=False,
        ),
        regime_confidence=0.80,
    )

    assert decision.passed is False
    assert decision.failure_reasons == ("G6_recency_score",)
    assert decision.recency_threshold == pytest.approx(0.43)
    assert decision.recency_distance == pytest.approx(-0.10)


def test_fast_lane_has_no_g6_recency_telemetry():
    decision = evaluate_readiness(_fast_candidate(), regime_confidence=0.80)

    assert decision.passed is True
    assert decision.recency_score is None
    assert decision.recency_threshold is None
    assert decision.recency_distance is None


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
    assert decision.applied_conditions == ("G1", "G3", "G4", "G7")


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
