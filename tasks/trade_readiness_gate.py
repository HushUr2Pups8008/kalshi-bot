"""Stateless Trade Readiness Gate from contract Section 5.

This module is intentionally narrow: it evaluates G1-G6 readiness predicates
and returns an auditable decision object. It does not blend signals, compute
structural priors, size trades, or submit anything to execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Any, Mapping

G1_CONFIDENCE_THRESHOLD = 0.35
G1_FAILSAFE_CONFIDENCE_THRESHOLD = 0.50
G2_MIN_SOURCE_CLASSES = 2
G3_DISAGREEMENT_THRESHOLD = 0.20
G3_FAILSAFE_DISAGREEMENT_THRESHOLD = 0.15
G3_OVERRIDE_BAND_START = 0.15
G3_OVERRIDE_MULTIPLIER = 1.5
G4_REGIME_CONFIDENCE_THRESHOLD = 0.40
G6_RECENCY_THRESHOLD = 0.30

FAST_SOURCE_LANE = "fast"


class ReadinessInputError(ValueError):
    """Raised when required gate inputs are missing or malformed."""


@dataclass(frozen=True)
class ReadinessDecision:
    passed: bool
    failure_reasons: tuple[str, ...]
    trade_blocked_reason: str | None
    readiness_gate_min_edge_override: float | None
    scaled_confidence: float
    regime_confidence: float
    fail_safe_active: bool
    applied_conditions: tuple[str, ...]


def evaluate_readiness(blend_result: Any, regime_confidence: float) -> ReadinessDecision:
    """Evaluate the complete Section 5 readiness gate.

    Required blend_result fields:
    - all candidates: source_lane, blended_confidence, disagreement_score
    - disagreement override candidates: default_min_edge
    - dossier-sourced candidates: evidence_source_classes, drift_suspect,
      in_recovery, recency_score

    Fast-lane candidates are exempt only from G2, G5, and G6.
    """
    validated_regime_confidence = _require_probability_value(
        "regime_confidence",
        regime_confidence,
    )
    source_lane = _require_text(blend_result, "source_lane")
    is_fast_lane = source_lane == FAST_SOURCE_LANE
    blended_confidence = _require_probability_field(blend_result, "blended_confidence")
    disagreement_score = _require_non_negative_field(blend_result, "disagreement_score")

    fail_safe_active = validated_regime_confidence < G4_REGIME_CONFIDENCE_THRESHOLD
    g1_threshold = (
        G1_FAILSAFE_CONFIDENCE_THRESHOLD
        if fail_safe_active
        else G1_CONFIDENCE_THRESHOLD
    )
    g3_threshold = (
        G3_FAILSAFE_DISAGREEMENT_THRESHOLD
        if fail_safe_active
        else G3_DISAGREEMENT_THRESHOLD
    )

    scaled_confidence = blended_confidence * validated_regime_confidence
    failure_reasons: list[str] = []
    applied_conditions = ["G1", "G3", "G4"]

    if scaled_confidence < g1_threshold:
        failure_reasons.append("G1_blended_confidence")

    readiness_gate_min_edge_override = _readiness_edge_override(
        blend_result,
        disagreement_score,
        g3_threshold,
    )
    if disagreement_score > g3_threshold:
        failure_reasons.append("G3_disagreement_score")

    if validated_regime_confidence < G4_REGIME_CONFIDENCE_THRESHOLD:
        failure_reasons.append("G4_regime_confidence")

    if not is_fast_lane:
        applied_conditions.extend(["G2", "G5", "G6"])
        source_classes = _require_sequence(blend_result, "evidence_source_classes")
        if len(set(source_classes)) < G2_MIN_SOURCE_CLASSES:
            failure_reasons.append("G2_evidence_source_class_diversity")

        drift_suspect = _require_bool(blend_result, "drift_suspect")
        in_recovery = _require_bool(blend_result, "in_recovery")
        if drift_suspect and not in_recovery:
            failure_reasons.append("G5_dossier_drift_suspect")

        recency_score = _require_probability_field(blend_result, "recency_score")
        if recency_score < G6_RECENCY_THRESHOLD:
            failure_reasons.append("G6_recency_score")

    return ReadinessDecision(
        passed=not failure_reasons,
        failure_reasons=tuple(failure_reasons),
        trade_blocked_reason=failure_reasons[0] if failure_reasons else None,
        readiness_gate_min_edge_override=(
            None if failure_reasons else readiness_gate_min_edge_override
        ),
        scaled_confidence=round(scaled_confidence, 4),
        regime_confidence=validated_regime_confidence,
        fail_safe_active=fail_safe_active,
        applied_conditions=tuple(applied_conditions),
    )


def _readiness_edge_override(
    blend_result: Any,
    disagreement_score: float,
    g3_threshold: float,
) -> float | None:
    if G3_OVERRIDE_BAND_START <= disagreement_score <= g3_threshold:
        default_min_edge = _require_non_negative_field(blend_result, "default_min_edge")
        return round(default_min_edge * G3_OVERRIDE_MULTIPLIER, 4)
    return None


def _field(blend_result: Any, name: str) -> Any:
    if isinstance(blend_result, Mapping):
        if name in blend_result:
            return blend_result[name]
    elif hasattr(blend_result, name):
        return getattr(blend_result, name)
    raise ReadinessInputError(f"blend_result missing required field: {name}")


def _require_text(blend_result: Any, name: str) -> str:
    value = _field(blend_result, name)
    if not isinstance(value, str) or not value.strip():
        raise ReadinessInputError(f"{name} must be a non-empty string")
    return value.strip()


def _require_bool(blend_result: Any, name: str) -> bool:
    value = _field(blend_result, name)
    if not isinstance(value, bool):
        raise ReadinessInputError(f"{name} must be a bool")
    return value


def _require_sequence(blend_result: Any, name: str) -> tuple[str, ...]:
    value = _field(blend_result, name)
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set)):
        raise ReadinessInputError(f"{name} must be a sequence of source classes")
    result = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ReadinessInputError(f"{name} must contain non-empty strings")
    return tuple(item.strip() for item in result)


def _require_probability_field(blend_result: Any, name: str) -> float:
    return _require_probability_value(name, _field(blend_result, name))


def _require_probability_value(name: str, value: Any) -> float:
    numeric = _require_real(name, value)
    if numeric < 0.0 or numeric > 1.0:
        raise ReadinessInputError(f"{name} must be between 0 and 1")
    return numeric


def _require_non_negative_field(blend_result: Any, name: str) -> float:
    numeric = _require_real(name, _field(blend_result, name))
    if numeric < 0.0:
        raise ReadinessInputError(f"{name} must be non-negative")
    return numeric


def _require_real(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ReadinessInputError(f"{name} must be numeric")
    return float(value)
