"""Decision blender — S3.3.

Pure function layer (INV-4). No I/O, no DB access, no LLM calls.

Implements DER-1 through DER-4 from IMPLEMENTATION_CONTRACT.md:

  DER-1  Confidence-weighted blend with RHR-3 regime interpolation.
  DER-2  Dominance rule: single lane with >2× others' total takes full authority.
  DER-3  Structural fail-safe Tier 1: high-confidence structural divergence +
         active fast-lane signal → pass with doubled min-edge override.
  DER-4  Structural fail-safe Tier 2: same conditions but no fast-lane signal
         AND structural stability → veto.

Inputs:
  fast        LaneInput | None — fast lane (always present in normal operation)
  accumulation LaneInput | None — accumulation lane (may be absent early)
  structural   LaneInput | None — structural lane (may be absent early)
  regime_weights  dict[str, float] — per-lane weight from regime_classifier
  regime_confidence float — 0..1 from regime_classifier; drives RHR-3
  fast_signal_active bool — True when a fast-lane signal exists within 2×
                             deduplication window (required for DER-3/DER-4)
  structural_stable  bool — True when structural prior has been stable (no
                             recompute movement > 0.05) for one full recompute
                             cycle (required for DER-4 activation)

Output: BlendResult dataclass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

# ── Constants ─────────────────────────────────────────────────────────────────
_STRUCTURAL_FAILSAFE_CONFIDENCE_THRESHOLD = 0.70
_STRUCTURAL_FAILSAFE_DIVERGENCE_THRESHOLD = 0.30
_DOMINANCE_RATIO = 2.0
_EQUAL_WEIGHT = 1.0 / 3.0  # RHR-3: uniform weight when regime_confidence == 0

BlendMode = Literal[
    "weighted_blend",
    "dominant_lane",
    "structural_tier1_override",
    "structural_tier2_veto",
]


@dataclass(frozen=True)
class LaneInput:
    """Single-lane estimate passed into the blender."""

    p: float          # probability estimate, 0..1
    confidence: float # raw lane confidence, 0..1
    lane_id: str      # "fast" | "accumulation" | "structural"


@dataclass(frozen=True)
class BlendResult:
    """Output of decision_blender.blend()."""

    blended_p: float
    blended_confidence: float
    disagreement_score: float
    blend_mode: BlendMode
    readiness_gate_min_edge_override: float | None
    trade_blocked_reason: str | None
    # lane snapshots (None when lane was absent)
    fast_lane_p: float | None
    fast_lane_confidence: float | None
    accumulation_p: float | None
    accumulation_confidence: float | None
    structural_p: float | None
    structural_confidence: float | None


def blend(
    *,
    fast: LaneInput | None,
    accumulation: LaneInput | None,
    structural: LaneInput | None,
    regime_weights: dict[str, float],
    regime_confidence: float,
    fast_signal_active: bool = False,
    structural_stable: bool = False,
    default_min_edge: float = 0.05,
) -> BlendResult:
    """Produce a blended probability estimate from up to three lane inputs.

    Lanes passed as None are excluded from blending (DER-1 note on absent lanes).
    When only one lane is present it is adopted directly (degenerates gracefully).
    """
    active: list[LaneInput] = [ln for ln in (fast, accumulation, structural) if ln is not None]

    if not active:
        raise ValueError("blend() requires at least one non-None lane input")

    eff_conf = _effective_confidences(active, regime_weights, regime_confidence)

    blend_mode, blended_p, blended_conf, min_edge_override, block_reason = _resolve_blend(
        active=active,
        eff_conf=eff_conf,
        structural=structural,
        fast_signal_active=fast_signal_active,
        structural_stable=structural_stable,
        default_min_edge=default_min_edge,
    )

    disagreement = _disagreement_score(active, eff_conf, blended_p)

    return BlendResult(
        blended_p=blended_p,
        blended_confidence=blended_conf,
        disagreement_score=disagreement,
        blend_mode=blend_mode,
        readiness_gate_min_edge_override=min_edge_override,
        trade_blocked_reason=block_reason,
        fast_lane_p=fast.p if fast is not None else None,
        fast_lane_confidence=fast.confidence if fast is not None else None,
        accumulation_p=accumulation.p if accumulation is not None else None,
        accumulation_confidence=accumulation.confidence if accumulation is not None else None,
        structural_p=structural.p if structural is not None else None,
        structural_confidence=structural.confidence if structural is not None else None,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _effective_confidences(
    active: list[LaneInput],
    regime_weights: dict[str, float],
    regime_confidence: float,
) -> list[float]:
    """RHR-3: interpolate between uniform weight and regime weight based on confidence."""
    result: list[float] = []
    for lane in active:
        rw = float(regime_weights.get(lane.lane_id, 0.0))
        interp_regime = (1.0 - regime_confidence) * _EQUAL_WEIGHT + regime_confidence * rw
        result.append(lane.confidence * interp_regime)
    return result


def _resolve_blend(
    *,
    active: list[LaneInput],
    eff_conf: list[float],
    structural: LaneInput | None,
    fast_signal_active: bool,
    structural_stable: bool,
    default_min_edge: float,
) -> tuple[BlendMode, float, float, float | None, str | None]:
    """Apply DER-1 → DER-2 → DER-3/4 in order. Return (mode, p, conf, override, block)."""
    total_eff = sum(eff_conf)

    if total_eff == 0.0:
        # All effective confidences are zero — equal weight fallback.
        p_blend = sum(ln.p for ln in active) / len(active)
        conf_blend = 0.0
    else:
        p_blend = sum(eff_conf[i] * active[i].p for i in range(len(active))) / total_eff
        conf_blend = total_eff / len(active)  # mean effective confidence as blended confidence

    # DER-2: dominance check
    dominant_idx = _dominant_lane_index(eff_conf)
    if dominant_idx is not None:
        p_blend = active[dominant_idx].p
        conf_blend = eff_conf[dominant_idx]
        return "dominant_lane", p_blend, conf_blend, None, None

    # DER-3 / DER-4: structural fail-safe
    if structural is not None:
        s_conf = structural.confidence
        divergence = abs(structural.p - p_blend)
        if (
            s_conf >= _STRUCTURAL_FAILSAFE_CONFIDENCE_THRESHOLD
            and divergence > _STRUCTURAL_FAILSAFE_DIVERGENCE_THRESHOLD
        ):
            if fast_signal_active:
                # Tier 1: fast-lane escape valve present
                override = 2.0 * default_min_edge
                return "structural_tier1_override", p_blend, conf_blend, override, None
            if structural_stable:
                # Tier 2: stable structural veto
                return (
                    "structural_tier2_veto",
                    p_blend,
                    conf_blend,
                    None,
                    "structural_tier2_veto: high-confidence structural divergence with no fast-lane signal",
                )

    return "weighted_blend", p_blend, conf_blend, None, None


def _dominant_lane_index(eff_conf: list[float]) -> int | None:
    """Return index of dominant lane per DER-2, or None if no lane dominates."""
    for i, ec in enumerate(eff_conf):
        others_total = sum(eff_conf[j] for j in range(len(eff_conf)) if j != i)
        if ec > _DOMINANCE_RATIO * others_total:
            return i
    return None


def _disagreement_score(
    active: list[LaneInput],
    eff_conf: list[float],
    p_blend: float,
) -> float:
    """CL-9: confidence-weighted std-dev of lane_p values."""
    if len(active) <= 1:
        return 0.0
    total = sum(eff_conf)
    if total == 0.0:
        w = [1.0 / len(active)] * len(active)
    else:
        w = [ec / total for ec in eff_conf]
    mean = sum(w[i] * active[i].p for i in range(len(active)))
    variance = sum(w[i] * (active[i].p - mean) ** 2 for i in range(len(active)))
    return math.sqrt(variance)
