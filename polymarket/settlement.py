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
    supersedes_payload_sha256: str | None = None,
) -> SettlementObservation:
    """Normalize a Polymarket US settlement payload without runtime mutation."""

    if normalize_venue(market_ref.venue) is not Venue.POLYMARKET_US:
        raise SettlementDriftError("Polymarket settlement identity has wrong venue")
    if not isinstance(payload, Mapping):
        raise SettlementDriftError("Polymarket settlement payload must be an object")
    _validate_payload_identity(market_ref, payload)

    if payload.get("settled", True) is False:
        raise SettlementDriftError(
            f"Polymarket settlement for {market_ref.venue_market_id} is nonterminal"
        )

    if "settlement" in payload:
        raw_outcome = payload.get("settlement")
        outcome = _outcome_from_settlement_value(market_ref, raw_outcome)
    else:
        raw_outcome = _authoritative_outcome_value(payload)
        if not isinstance(raw_outcome, str):
            raise SettlementDriftError(
                "Polymarket settlement payload missing authoritative outcome"
            )
        normalized = raw_outcome.strip().lower()
        if normalized == "yes":
            outcome = MarketOutcome.YES
        elif normalized == "no":
            outcome = MarketOutcome.NO
        elif normalized in _VOID_OUTCOMES:
            outcome = MarketOutcome.VOID
        else:
            raise SettlementDriftError(
                "Polymarket settlement payload has unsupported outcome: "
                f"{raw_outcome!r}"
            )

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
        supersedes_payload_sha256=supersedes_payload_sha256,
    )


def _validate_payload_identity(
    market_ref: MarketRef, payload: Mapping[str, Any]
) -> None:
    supplied_ids = {
        str(payload[key]).strip()
        for key in _CANONICAL_ID_KEYS
        if payload.get(key) is not None and str(payload[key]).strip()
    }
    if supplied_ids and supplied_ids != {market_ref.venue_market_id}:
        raise SettlementDriftError(
            "Polymarket settlement identity mismatch: "
            f"expected {market_ref.venue_market_id!r}, got {sorted(supplied_ids)!r}"
        )

    if payload.get("slug") is not None:
        slug = str(payload.get("slug")).strip()
        if not slug or slug not in {market_ref.alias, market_ref.venue_market_id}:
            raise SettlementDriftError(
                "Polymarket settlement alias identity mismatch: "
                f"expected {market_ref.alias!r}, got {slug!r}"
            )


def _outcome_from_settlement_value(
    market_ref: MarketRef, raw_outcome: Any
) -> MarketOutcome:
    if isinstance(raw_outcome, bool):
        raise SettlementDriftError("Polymarket settlement value cannot be boolean")
    try:
        value = float(raw_outcome)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SettlementDriftError(
            "Polymarket settlement value must be numeric"
        ) from exc
    if not math.isfinite(value) or value not in {0.0, 1.0}:
        raise SettlementDriftError(
            f"Polymarket settlement for {market_ref.venue_market_id} is nonbinary"
        )
    return MarketOutcome.YES if value == 1.0 else MarketOutcome.NO


def _authoritative_outcome_value(payload: Mapping[str, Any]) -> Any:
    for key in ("resolvedOutcome", "resolved_outcome", "outcome", "result"):
        if key in payload:
            return payload.get(key)
    return None
