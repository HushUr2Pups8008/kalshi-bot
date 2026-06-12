"""Stateless Trade Readiness Gate from contract Section 5.

This module is intentionally narrow: it evaluates G1-G6 readiness predicates
and returns an auditable decision object. It does not blend signals, compute
structural priors, size trades, or submit anything to execution.
"""

from __future__ import annotations

import os  # noqa: F401  (used by G2_MIN_SOURCE_CLASSES env override)

from dataclasses import dataclass
from numbers import Real
from typing import Any, Mapping

# G1 — scaled-confidence floor.
#
# scaled_confidence = blended_confidence × regime_confidence.
#
# Threshold history:
#   0.35 base / 0.50 failsafe (initial, commit 33385d9, "stage 2 accumulation
#                              guardrails")
#   0.05 base / 0.10 failsafe (PROFIT-EDGE-003, v0.29.58) — calibration fix.
#
# Why 0.05: G1 = 0.35 was set on the same day as G4 = 0.40 with the same
# unreachable assumptions. The blender's `_effective_confidences()`
# attenuates each lane's confidence by `lane_conf × (rw_lane × rc + (1-rc)/3)`
# and `blended_confidence` was, at the time, the *mean* of those effective
# confidences (divided by lane COUNT). PROFIT-EDGE-014 (2026-06-12, operator
# option b) changed blended_confidence to the interp-weight-weighted MEAN of
# lane confidences — a confident fast lane is no longer diluted by the lane
# count when low-confidence lanes are present; expect higher
# scaled_confidence for the same inputs than the historical examples below
# (kept for threshold-history context). For fast-lane-only LLM signal at
# conf=0.85 on the new categorical priors (pre-EDGE-014 math):
#
#   Sports prior (rc=0.528):           scaled_conf ≈ 0.27
#   KXTRUMPIRAN prior (rc=0.27):       scaled_conf ≈ 0.10
#   KXSBUDGETRES prior (rc=0.28):      scaled_conf ≈ 0.06
#   Polling prior (rc=0.32):           scaled_conf ≈ 0.07
#
# G1 = 0.35 would require regime_confidence ≥ ~0.875 with realistic
# blended_confidence values — only the very-near-close (≤6h) sports markets
# can achieve that, and we filter sports out of the trading universe by
# design. So G1 = 0.35 was *deterministically* zero-trade for the bot's
# actual target market mix.
#
# Empirical confirmation — 154 production BLEND_DECISIONs over the 9-day
# window 2026-04-17 → 2026-04-26 (paper mode, pre-line-688-fix, all-0.5
# lane probabilities):
#
#   percentile  scaled_confidence
#   median      0.026  (pure noise — uncategorized markets at near-uniform rc)
#   P75         0.035
#   P90         0.119  (natural cliff — strong-signal cluster)
#   P95         0.119
#   max         0.304  (KXVANCEPAKISTAN-26APR21-APR24 — best in 9 days)
#
# Zero of 154 cleared G1 = 0.35 — the threshold is 1.15× the best signal the
# bot has ever produced in production. Lowering G1 to 0.05 captures the
# top ~14% of historical signals (the 22 records above 0.05 cluster around
# the P90 inflection at 0.12) while still rejecting median noise (0.026).
# Multi-lane high-quality signals at 0.30 clear with 6× headroom.
#
# Failsafe (G1 = 0.10) is a 2× base, mirroring the original 0.50/0.35 = 1.4×
# but pushed further on the conservative side. With fail-safe coupled to
# G4 in the same threshold (rc < 0.20), failsafe markets had already been
# rejected by G4 — so the failsafe G1 value is academic. It moves only for
# consistency in the [0.05, 0.20) decoupling that may follow.
#
# This calibration is reversible — change is two constants. The post-deploy
# audit log will show whether 14% trade rate is correct or whether the gate
# needs further tightening (e.g. moving G1 to 0.10 = top-10%) once paper
# trades resolve.
#
# See PROFIT-EDGE-003 in docs/profit_path_debt_log.md for the full diagnosis.
G1_CONFIDENCE_THRESHOLD = 0.05
G1_FAILSAFE_CONFIDENCE_THRESHOLD = 0.10
# PROFIT-SOURCE-001 (2026-06-11, operator decision): allow single-source trades.
# Default lowered 2 -> 1 so a single evidence source class clears G2; every trade
# still records its source-class count (signal_meta.evidence_source_class_count)
# so single- vs multi-source performance can be compared. Env-overridable to
# dial back to 2 instantly if single-source trades underperform. Fast-lane
# candidates were already G2-exempt.
G2_MIN_SOURCE_CLASSES = int(os.getenv("G2_MIN_SOURCE_CLASSES", "1"))
G3_DISAGREEMENT_THRESHOLD = 0.20
G3_FAILSAFE_DISAGREEMENT_THRESHOLD = 0.15
G3_OVERRIDE_BAND_START = 0.15
G3_OVERRIDE_MULTIPLIER = 1.5

# G4 — regime-confidence floor.
#
# regime_confidence = 1 - entropy(regime_weights) / log(N_lanes)
#
# Threshold history:
#   0.40 (initial, commit 33385d9, "stage 2 accumulation guardrails")
#   0.20 (PROFIT-EDGE-002, v0.29.57) — calibration fix.
#
# Why 0.20: at the original 0.40, only regime distributions with the dominant
# lane > 0.80 cleared the gate, which excluded almost every categorical prior
# defined in analysis/regime_classifier.py:_SERIES_PRIORS. Concretely, on
# 9 days of production (2026-04-17 → 2026-04-26), bot trade flow stalled at
# zero — every blend on a non-sport categorical-priored market failed G4.
# Existing categorical priors with their regime_confidence values:
#
#   Sports (KXNFL/KXNBA/...)        rc = 0.528  passes 0.40
#   ≤6h time fallback               rc = 0.528  passes 0.40
#   Polling (KXAPRPOTUS)            rc = 0.321  fails 0.40 — DESIGNED tradeable
#   Central bank (KXCBDECISION)     rc = 0.280  fails 0.40 — DESIGNED tradeable
#   Crypto (KXCRYPTO)               rc = 0.251  fails 0.40 — DESIGNED tradeable
#   Weather (KXWEATHER)             rc = 0.220  fails 0.40 — DESIGNED tradeable
#   Trump-say (KXTRUMPSAY)          rc = 0.157  fails 0.40
#   Entertainment (KXENTERTAIN)     rc = 0.080  fails 0.40
#
# 0.20 is the cleanest separator that:
#   (a) PASSES the categorical priors marked above as designed-tradeable
#       (Polling, Central bank, Crypto, Weather);
#   (b) FAILS the time-fallback uncategorized buckets (1-3d rc=0.080,
#       3-7d rc=0.063, 7-14d rc=0.136) and weakly-categorical KXTRUMPSAY /
#       KXENTERTAIN, which are correctly subjected to fail-safe.
#
# Note: lowering this threshold also broadens the fail-safe activation band.
# fail_safe_active = (rc < G4_REGIME_CONFIDENCE_THRESHOLD), so markets with
# rc in [0.20, 0.40) move from fail-safe to normal mode (G1=0.35, G3=0.20)
# instead of fail-safe (G1=0.50, G3=0.15). This is the intended effect: a
# categorical prior IS our knowledge about the market regime, so demanding
# additional caution beyond the standard gate is over-tight.
#
# See PROFIT-EDGE-002 in docs/profit_path_debt_log.md for the full diagnosis.
G4_REGIME_CONFIDENCE_THRESHOLD = 0.20

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
    # PROFIT-SOURCE-001: distinct evidence source-class count for this candidate
    # (non-fast-lane). None for fast-lane (G2-exempt). Carried into signal_meta
    # so single- vs multi-source trade performance can be tracked.
    source_class_count: int | None = None


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

    source_class_count: int | None = None
    if not is_fast_lane:
        applied_conditions.extend(["G2", "G5", "G6"])
        source_classes = _require_sequence(blend_result, "evidence_source_classes")
        source_class_count = len(set(source_classes))
        if source_class_count < G2_MIN_SOURCE_CLASSES:
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
        source_class_count=source_class_count,
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
