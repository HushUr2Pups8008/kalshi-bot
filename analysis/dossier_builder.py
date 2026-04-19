"""Dossier belief update engine — S2.4.

Pure function layer (INV-4). No I/O, no DB access, no LLM calls.

Implements BSR-1 through BSR-7 from the implementation contract:
  BSR-1: State update vs confidence update classification.
  BSR-2: Per-update displacement cap (0.10 normal; 0.05 recovery).
  BSR-3: Drift detection, freeze, half-life escape, and recovery mode.
  BSR-4: Decay is applied at read time; original_weight is stored undecayed.
  BSR-5: Same-class diminishing returns (baked into EvidenceScore.original_weight).
  BSR-6: Confidence evolution: cross-class growth, same-class growth, contradiction drop.
  BSR-7: Independence approximation (resolved in evidence_scorer; carried as is_independent).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from analysis.evidence_types import Dossier, EvidenceScore

# ── Constants ─────────────────────────────────────────────────────────────────

_CONFIDENCE_CAP: float = 0.95
_CONFIDENCE_FLOOR: float = 0.05
_CONFIDENCE_GROWTH_RATE: float = 0.30       # BSR-6
_CONTRADICTION_DROP: float = 0.20           # BSR-6
_CONTRADICTION_QUALITY_THRESHOLD: float = 0.60  # BSR-6
_NORMAL_CAP: float = 0.10                   # BSR-2
_RECOVERY_CAP: float = 0.05                 # BSR-2, BSR-3
_DRIFT_THRESHOLD: float = 0.25             # BSR-3

# ── Internal helpers ──────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _is_contradiction(
    implied_prob: float,
    current_estimate: float,
    quality: float,
) -> bool:
    """BSR-6: opposite-direction cross-class signal with quality ≥ 0.6."""
    if quality < _CONTRADICTION_QUALITY_THRESHOLD:
        return False
    estimate_favors_yes = current_estimate >= 0.5
    evidence_favors_yes = implied_prob >= 0.5
    # Require meaningful disagreement, not just crossing 0.5 by a hair.
    return (estimate_favors_yes != evidence_favors_yes) and abs(implied_prob - current_estimate) > 0.10


def _half_life_elapsed(freeze_ts: str, now: datetime, half_life_days: float) -> bool:
    elapsed_s = (now - _parse_ts(freeze_ts)).total_seconds()
    return elapsed_s >= half_life_days * 86_400


def _should_escape_drift(
    dossier: Dossier,
    now: datetime,
    half_life_days: float,
    is_cross_class: bool,
) -> bool:
    """BSR-3 escape: one full half-life elapsed OR cross-class signal meets BSR-7."""
    if is_cross_class:
        return True
    if dossier.freeze_started_ts is not None:
        return _half_life_elapsed(dossier.freeze_started_ts, now, half_life_days)
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def classify_update(dossier: Dossier, score: EvidenceScore) -> str:
    """Return 'state' or 'confidence' per BSR-1.

    Note: this does NOT model the drift escape path (BSR-3). When drift_suspect
    is True and a cross-class signal arrives, update_dossier overrides the type
    to 'state' after transitioning out of freeze. Callers should always pass the
    output of this function to update_dossier and let it handle the escape.
    """
    if not score.is_independent:
        return "confidence"
    if dossier.drift_suspect and not dossier.in_recovery:
        return "confidence"
    return "state"


def update_dossier(
    dossier: Dossier,
    evidence_score: EvidenceScore,
    update_type: str,
    *,
    now: datetime | None = None,
    market_half_life_days: float = 7.0,
) -> Dossier:
    """Apply one scored evidence item to the dossier, returning a new Dossier.

    ``update_type`` should be the output of classify_update(). update_dossier
    may override it to 'state' if a cross-class signal triggers drift escape
    (BSR-3). The caller is not responsible for handling the escape path.

    BSR-4 note: decay is NOT applied here. Callers that need effective weight
    for blending or recency scoring must apply the decay formula at read time.
    """
    if now is None:
        now = _utc_now()
    now_iso = now.isoformat()

    # ── BSR-3: Check drift escape before applying update ──────────────────────
    escaping_drift = False
    if dossier.drift_suspect:
        escaping_drift = _should_escape_drift(
            dossier, now, market_half_life_days, evidence_score.is_independent
        )
        if escaping_drift and evidence_score.is_independent:
            # Cross-class signal meets BSR-7 criteria → override to state update.
            update_type = "state"

    # ── Determine displacement cap (BSR-2) ────────────────────────────────────
    entering_recovery = escaping_drift  # about to become in_recovery
    use_recovery_cap = dossier.in_recovery or entering_recovery
    cap = _RECOVERY_CAP if use_recovery_cap else _NORMAL_CAP

    # ── Working copies of mutable dossier fields ──────────────────────────────
    current_estimate = dossier.current_estimate
    confidence = dossier.confidence
    prior_estimate = dossier.prior_estimate
    old_prior = dossier.prior_estimate  # preserved for drift check (BSR-3)
    last_cross_class_ts = dossier.last_cross_class_state_update_ts
    drift_suspect = dossier.drift_suspect
    in_recovery = dossier.in_recovery
    freeze_started_ts = dossier.freeze_started_ts
    recovery_started_ts = dossier.recovery_started_ts
    recovery_until_ts = dossier.recovery_until_ts

    # ── Contradiction check (BSR-6) ───────────────────────────────────────────
    is_contra = (
        evidence_score.is_independent
        and current_estimate is not None
        and _is_contradiction(
            evidence_score.implied_probability,
            current_estimate,
            evidence_score.quality_score,
        )
    )

    # ── Apply state or confidence update ──────────────────────────────────────
    if update_type == "state":
        if current_estimate is None:
            # First state update: seed the estimate directly (BSR-2 cap not applicable).
            new_estimate = float(evidence_score.implied_probability)
        else:
            raw_delta = evidence_score.implied_probability - current_estimate
            weighted_delta = raw_delta * evidence_score.original_weight
            capped_delta = max(-cap, min(cap, weighted_delta))
            new_estimate = max(0.0, min(1.0, current_estimate + capped_delta))

        # BSR-6: cross-class state update increases confidence.
        confidence = min(
            _CONFIDENCE_CAP,
            confidence + evidence_score.quality_score * (1.0 - confidence) * _CONFIDENCE_GROWTH_RATE,
        )

        current_estimate = new_estimate

    else:  # confidence-only update (BSR-6)
        # original_weight already has BSR-5 diminishing returns baked in.
        confidence = min(
            _CONFIDENCE_CAP,
            confidence + evidence_score.original_weight * (1.0 - confidence) * _CONFIDENCE_GROWTH_RATE,
        )

    # ── BSR-6: contradiction penalty ─────────────────────────────────────────
    if is_contra:
        confidence = max(_CONFIDENCE_FLOOR, confidence - _CONTRADICTION_DROP)

    # ── BSR-3: Drift state machine ────────────────────────────────────────────
    if escaping_drift:
        # Transition: freeze → recovery.
        drift_suspect = False
        freeze_started_ts = None
        in_recovery = True
        recovery_started_ts = now_iso
        recovery_until_ts = (now + timedelta(days=market_half_life_days)).isoformat()

    elif not drift_suspect and current_estimate is not None and old_prior is not None:
        # Check drift against the pre-update anchor (BSR-3).
        # old_prior is the anchor BEFORE this update's prior_estimate reset.
        if abs(current_estimate - old_prior) > _DRIFT_THRESHOLD:
            drift_suspect = True
            freeze_started_ts = now_iso

    # ── BSR-3: snapshot prior_estimate on cross-class state update (after drift check) ──
    if update_type == "state" and evidence_score.is_independent and not drift_suspect:
        prior_estimate = current_estimate
        last_cross_class_ts = now_iso
    elif update_type == "state" and evidence_score.is_independent and drift_suspect:
        # Drift just detected — do NOT advance the anchor; preserve old_prior as the anchor.
        last_cross_class_ts = now_iso

    # ── BSR-3: Recovery expiry check ─────────────────────────────────────────
    if in_recovery and recovery_until_ts is not None:
        if now >= _parse_ts(recovery_until_ts):
            in_recovery = False
            recovery_started_ts = None
            recovery_until_ts = None

    return replace(
        dossier,
        dossier_version=dossier.dossier_version + 1,
        current_estimate=current_estimate,
        confidence=confidence,
        prior_estimate=prior_estimate,
        drift_suspect=drift_suspect,
        in_recovery=in_recovery,
        freeze_started_ts=freeze_started_ts,
        recovery_started_ts=recovery_started_ts,
        recovery_until_ts=recovery_until_ts,
        last_cross_class_state_update_ts=last_cross_class_ts,
        updated_ts=now_iso,
    )
