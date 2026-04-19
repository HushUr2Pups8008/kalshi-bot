"""Structural prior synthesis — S3.1.

Pure function layer (INV-4). No I/O, no DB access, no network calls.

Computes a PriorEstimate from market metadata and a context dict populated
by structural_task.py (S3.2). Any LLM call is made in the task layer and its
result is passed in via context["llm_estimate"].

Context dict keys (all optional; documented defaults apply when absent):
    evidence_records  list[Any]    Evidence records consumed in synthesis.
                                   Used for input_source_count and confidence.
                                   The function only inspects len(); it does not
                                   read individual record fields, so any sequence
                                   type is accepted (EvidenceRecord, dict, etc.).
    llm_estimate      float|None   LLM-synthesized probability, 0..1.
                                   When present, used as the structural estimate.
                                   Absent or None: market midprice is used instead.
    llm_called        bool         Whether an LLM call was made. Default False.
    now_ts            str|None     ISO 8601 UTC timestamp for PriorEstimate.computed_ts.
                                   Falls back to UTC now if absent or None.

Confidence model — maximum contribution per component:
    structural regime weight   0.25   (market.regime_weights["structural"] * 0.25)
    evidence depth             0.25   (saturates at _EVIDENCE_SATURATION records)
    LLM synthesis bonus        0.45   (awarded when llm_called is True)
    cap                        0.95

Without LLM synthesis the maximum confidence is 0.50, keeping the structural
lane below the DER-3/DER-4 fail-safe activation threshold (0.70). LLM synthesis
is the only path to high structural confidence and fail-safe eligibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from analysis.evidence_types import PriorEstimate
from kalshi import KalshiMarket

# ── Confidence contribution caps ──────────────────────────────────────────────
_CONFIDENCE_REGIME_WEIGHT = 0.25
_CONFIDENCE_EVIDENCE_WEIGHT = 0.25
_CONFIDENCE_LLM_BONUS = 0.45
_EVIDENCE_SATURATION = 8   # evidence count at which depth contribution plateaus
_CONFIDENCE_CAP = 0.95

# ── Estimate bounds ───────────────────────────────────────────────────────────
_ESTIMATE_FLOOR = 0.01
_ESTIMATE_CEILING = 0.99


def compute_structural_prior(
    market: KalshiMarket,
    context: dict[str, Any],
) -> PriorEstimate:
    """Synthesize a structural prior for a market.

    Estimate authority order: LLM synthesis > market midprice.
    Returns low-confidence priors when no synthesis data is available so that
    the structural lane contributes minimal blend weight until evidence accumulates.
    """
    now_ts: str = context.get("now_ts") or _utcnow_iso()
    llm_called: bool = bool(context.get("llm_called", False))
    llm_estimate: float | None = context.get("llm_estimate")
    evidence_records: list[Any] = context.get("evidence_records") or []
    evidence_count = len(evidence_records)

    estimate = _synthesize_estimate(market, llm_estimate)
    structural_weight = market.regime_weights.get("structural", 0.0)
    confidence = _structural_confidence(structural_weight, evidence_count, llm_called)

    return PriorEstimate(
        market_ticker=market.ticker,
        estimate=estimate,
        confidence=confidence,
        input_source_count=evidence_count,
        llm_called=llm_called,
        computed_ts=now_ts,
    )


def _synthesize_estimate(market: KalshiMarket, llm_estimate: float | None) -> float:
    if llm_estimate is not None:
        return _clamp(llm_estimate)
    return _clamp(market.yes_prob)


def _structural_confidence(
    structural_regime_weight: float,
    evidence_count: int,
    llm_called: bool,
) -> float:
    regime = structural_regime_weight * _CONFIDENCE_REGIME_WEIGHT
    depth = (min(evidence_count, _EVIDENCE_SATURATION) / _EVIDENCE_SATURATION) * _CONFIDENCE_EVIDENCE_WEIGHT
    llm = _CONFIDENCE_LLM_BONUS if llm_called else 0.0
    return min(regime + depth + llm, _CONFIDENCE_CAP)


def _clamp(p: float) -> float:
    return max(_ESTIMATE_FLOOR, min(_ESTIMATE_CEILING, float(p)))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
