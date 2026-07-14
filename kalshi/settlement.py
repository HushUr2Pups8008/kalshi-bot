from __future__ import annotations

from datetime import datetime

from kalshi import KalshiMarket
from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    SettlementObservation,
    VoidRefundContract,
    build_settlement_observation,
)
from trading.venue import MarketRef, Venue, normalize_venue


_VOID_OUTCOMES = {"void", "voided", "canceled", "cancelled"}
_TERMINAL_STATUSES = {"finalized", "settled"}


def normalize_kalshi_settlement(
    market_ref: MarketRef,
    market: KalshiMarket,
    *,
    observed_at: datetime,
    effective_at: datetime,
    rules_version: str,
    source_id: str,
    void_refund: VoidRefundContract | None = None,
    previous_observation: SettlementObservation | None = None,
    supersedes_payload_sha256: str | None = None,
) -> SettlementObservation:
    """Normalize an already-parsed Kalshi market into a pure observation."""

    if normalize_venue(market_ref.venue) is not Venue.KALSHI:
        raise SettlementDriftError("Kalshi settlement identity has wrong venue")
    market_id = str(market.ticker).strip()
    if market_id != market_ref.venue_market_id:
        raise SettlementDriftError(
            "Kalshi settlement identity mismatch: "
            f"expected {market_ref.venue_market_id!r}, got {market_id!r}"
        )
    status = str(market.status).strip().lower()
    if status not in _TERMINAL_STATUSES:
        raise SettlementDriftError(
            f"Kalshi settlement for {market_id} is nonterminal: {market.status!r}"
        )

    raw_outcome = str(market.result).strip()
    normalized_outcome = raw_outcome.lower()
    if normalized_outcome == "yes":
        outcome = MarketOutcome.YES
    elif normalized_outcome == "no":
        outcome = MarketOutcome.NO
    elif normalized_outcome in _VOID_OUTCOMES:
        outcome = MarketOutcome.VOID
    else:
        raise SettlementDriftError(
            f"Kalshi settlement for {market_id} has unsupported outcome: "
            f"{market.result!r}"
        )

    authoritative_payload = {
        "expiration_time": market.expiration_time,
        "raw_payload_hash": market.raw_payload_hash,
        "result": market.result,
        "status": market.status,
        "ticker": market.ticker,
        "updated_time": (
            market.updated_time.isoformat()
            if market.updated_time is not None
            else None
        ),
    }
    return build_settlement_observation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome=market.result,
        authoritative_payload=authoritative_payload,
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version=rules_version,
        source_id=source_id,
        void_refund=void_refund,
        previous_observation=previous_observation,
        supersedes_payload_sha256=supersedes_payload_sha256,
    )
