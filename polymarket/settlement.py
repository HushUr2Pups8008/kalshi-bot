from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    SettlementObservation,
    VoidRefundContract,
    build_settlement_observation,
)
from trading.venue import MarketRef, Venue, normalize_venue


_VOID_OUTCOMES = {"void", "voided", "canceled", "cancelled"}
_CANONICAL_ID_KEYS = ("market_id", "marketId", "id")
_OUTCOME_KEYS = (
    "settlement",
    "resolvedOutcome",
    "resolved_outcome",
    "outcome",
    "result",
)


def normalize_polymarket_settlement(
    market_ref: MarketRef,
    payload: Mapping[str, Any],
    *,
    observed_at: datetime,
    effective_at: datetime,
    rules_version: str,
    source_id: str,
    void_refund: VoidRefundContract | None = None,
    previous_observation: SettlementObservation | None = None,
    supersedes_observation_sha256: str | None = None,
) -> SettlementObservation:
    """Normalize a Polymarket US settlement payload without runtime mutation."""

    if normalize_venue(market_ref.venue) is not Venue.POLYMARKET_US:
        raise SettlementDriftError("Polymarket settlement identity has wrong venue")
    if not isinstance(payload, Mapping):
        raise SettlementDriftError("Polymarket settlement payload must be an object")
    has_canonical_id = _validate_payload_identity(market_ref, payload)

    has_settled_marker = "settled" in payload
    if has_settled_marker and payload["settled"] is not True:
        raise SettlementDriftError(
            "Polymarket settlement settled marker must be boolean true"
        )
    if has_settled_marker and not has_canonical_id:
        raise SettlementDriftError(
            "Polymarket settlement canonical identity missing outside exact live "
            "settlement shape"
        )
    if not has_settled_marker:
        supplied_outcome_keys = {key for key in _OUTCOME_KEYS if key in payload}
        allowed_keys = {"settlement", "slug", *_CANONICAL_ID_KEYS}
        if (
            supplied_outcome_keys != {"settlement"}
            or any(key not in allowed_keys for key in payload)
            or (not has_canonical_id and set(payload) != {"settlement", "slug"})
            or not _is_numeric_settlement(payload.get("settlement"))
        ):
            raise SettlementDriftError(
                "Polymarket settlement without settled marker must match the live "
                "settlement shape"
            )

    outcome, raw_outcome = _normalize_authoritative_outcomes(market_ref, payload)

    return build_settlement_observation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome=raw_outcome,
        authoritative_payload=payload,
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version=rules_version,
        source_id=source_id,
        void_refund=void_refund,
        previous_observation=previous_observation,
        supersedes_observation_sha256=supersedes_observation_sha256,
    )


def _validate_payload_identity(
    market_ref: MarketRef, payload: Mapping[str, Any]
) -> bool:
    has_canonical_id = False
    for key in _CANONICAL_ID_KEYS:
        if key not in payload:
            continue
        has_canonical_id = True
        supplied_id = str(payload[key]).strip()
        if supplied_id != market_ref.venue_market_id:
            raise SettlementDriftError(
                "Polymarket settlement identity mismatch: "
                f"expected {market_ref.venue_market_id!r}, got {supplied_id!r} "
                f"in {key!r}"
            )

    if "slug" in payload:
        slug = str(payload["slug"]).strip()
        if slug != market_ref.alias:
            raise SettlementDriftError(
                "Polymarket settlement alias identity mismatch: "
                f"expected {market_ref.alias!r}, got {slug!r}"
            )
    return has_canonical_id


def _normalize_authoritative_outcomes(
    market_ref: MarketRef, payload: Mapping[str, Any]
) -> tuple[MarketOutcome, Any]:
    normalized: list[tuple[str, MarketOutcome, Any]] = []
    for key in _OUTCOME_KEYS:
        if key not in payload:
            continue
        raw_outcome = payload[key]
        if key == "settlement":
            outcome = _outcome_from_settlement_value(market_ref, raw_outcome)
        else:
            outcome = _outcome_from_text_value(raw_outcome)
        normalized.append((key, outcome, raw_outcome))

    if not normalized:
        raise SettlementDriftError(
            "Polymarket settlement payload missing authoritative outcome"
        )
    if len({outcome for _, outcome, _ in normalized}) != 1:
        raise SettlementDriftError(
            "Polymarket settlement payload has conflicting authoritative outcomes"
        )
    _, outcome, raw_outcome = normalized[0]
    return outcome, raw_outcome


def _outcome_from_text_value(raw_outcome: Any) -> MarketOutcome:
    if not isinstance(raw_outcome, str):
        raise SettlementDriftError(
            "Polymarket settlement payload has malformed authoritative outcome"
        )
    normalized = raw_outcome.strip().lower()
    if normalized == "yes":
        return MarketOutcome.YES
    if normalized == "no":
        return MarketOutcome.NO
    if normalized in _VOID_OUTCOMES:
        return MarketOutcome.VOID
    raise SettlementDriftError(
        "Polymarket settlement payload has unsupported outcome: "
        f"{raw_outcome!r}"
    )


def _outcome_from_settlement_value(
    market_ref: MarketRef, raw_outcome: Any
) -> MarketOutcome:
    if not _is_numeric_settlement(raw_outcome):
        raise SettlementDriftError(
            "Polymarket settlement value must be numeric"
        )
    value = float(raw_outcome)
    if not math.isfinite(value) or value not in {0.0, 1.0}:
        raise SettlementDriftError(
            f"Polymarket settlement for {market_ref.venue_market_id} is nonbinary"
        )
    return MarketOutcome.YES if value == 1.0 else MarketOutcome.NO


def _is_numeric_settlement(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
