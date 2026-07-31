"""Strict, versioned legacy settlement receipt records.

These records describe an independently collected canonical observation. They
do not apply a settlement or authorize any database mutation by themselves.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from trading.settlement import (
    MarketOutcome,
    SettlementObservation,
    SettlementValidationError,
    VoidRefundContract,
)
from trading.venue import MarketRef, Venue


LEGACY_SETTLEMENT_RECEIPT_SCHEMA_VERSION = 1


class LegacySettlementReceiptError(ValueError):
    """Raised when a legacy settlement receipt cannot be trusted."""


@dataclass(frozen=True)
class LegacySettlementReceipt:
    """One versioned receipt binding a legacy trade to a canonical observation."""

    trade_id: str
    observation: SettlementObservation
    schema_version: int = LEGACY_SETTLEMENT_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != LEGACY_SETTLEMENT_RECEIPT_SCHEMA_VERSION
        ):
            raise LegacySettlementReceiptError("legacy receipt schema version is invalid")
        _require_exact_string(self.trade_id, "trade_id")
        if not isinstance(self.observation, SettlementObservation):
            raise LegacySettlementReceiptError("legacy receipt observation is invalid")

    def to_dict(self) -> dict[str, object]:
        observation = self.observation
        void_refund = observation.void_refund
        return {
            "schema_version": self.schema_version,
            "trade_id": self.trade_id,
            "venue": observation.market_ref.venue.value,
            "venue_market_id": observation.market_ref.venue_market_id,
            "alias": observation.market_ref.alias,
            "outcome": observation.outcome.value,
            "authoritative_outcome_json": observation.authoritative_outcome_json,
            "canonical_payload_json": observation.canonical_payload_json,
            "observed_at": observation.observed_at.isoformat(),
            "effective_at": observation.effective_at.isoformat(),
            "rules_version": observation.rules_version,
            "source_id": observation.source_id,
            "payload_sha256": observation.payload_sha256,
            "observation_sha256": observation.observation_sha256,
            "refund_cents_per_contract": (
                str(void_refund.refund_cents_per_contract)
                if void_refund is not None
                else None
            ),
            "refunds_entry_fee": (
                void_refund.refunds_entry_fee if void_refund is not None else None
            ),
            "supersedes_observation_sha256": (
                observation.supersedes_observation_sha256
            ),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def receipt_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "LegacySettlementReceipt":
        if not isinstance(value, Mapping):
            raise LegacySettlementReceiptError("legacy receipt must be an object")
        expected_fields = {
            "schema_version",
            "trade_id",
            "venue",
            "venue_market_id",
            "alias",
            "outcome",
            "authoritative_outcome_json",
            "canonical_payload_json",
            "observed_at",
            "effective_at",
            "rules_version",
            "source_id",
            "payload_sha256",
            "observation_sha256",
            "refund_cents_per_contract",
            "refunds_entry_fee",
            "supersedes_observation_sha256",
        }
        if set(value) != expected_fields:
            raise LegacySettlementReceiptError("legacy receipt fields are invalid")
        schema_version = value["schema_version"]
        if isinstance(schema_version, bool) or schema_version != (
            LEGACY_SETTLEMENT_RECEIPT_SCHEMA_VERSION
        ):
            raise LegacySettlementReceiptError("legacy receipt schema version is invalid")
        if not isinstance(schema_version, int):
            raise LegacySettlementReceiptError("legacy receipt schema version is invalid")

        trade_id = _require_exact_string(value["trade_id"], "trade_id")
        venue = _parse_enum(Venue, value["venue"], "venue")
        outcome = _parse_enum(MarketOutcome, value["outcome"], "outcome")
        void_refund = _parse_void_refund(
            outcome,
            value["refund_cents_per_contract"],
            value["refunds_entry_fee"],
        )
        supersedes = value["supersedes_observation_sha256"]
        if supersedes is not None:
            supersedes = _require_exact_string(
                supersedes,
                "supersedes_observation_sha256",
            )
        try:
            observation = SettlementObservation(
                market_ref=MarketRef(
                    venue,
                    _require_exact_string(value["venue_market_id"], "venue_market_id"),
                    _require_exact_string(value["alias"], "alias"),
                ),
                outcome=outcome,
                authoritative_outcome_json=_require_exact_string(
                    value["authoritative_outcome_json"],
                    "authoritative_outcome_json",
                ),
                canonical_payload_json=_require_exact_string(
                    value["canonical_payload_json"],
                    "canonical_payload_json",
                ),
                observed_at=_parse_datetime(value["observed_at"], "observed_at"),
                effective_at=_parse_datetime(value["effective_at"], "effective_at"),
                rules_version=_require_exact_string(
                    value["rules_version"],
                    "rules_version",
                ),
                source_id=_require_exact_string(value["source_id"], "source_id"),
                payload_sha256=_require_exact_string(
                    value["payload_sha256"],
                    "payload_sha256",
                ),
                observation_sha256=_require_exact_string(
                    value["observation_sha256"],
                    "observation_sha256",
                ),
                void_refund=void_refund,
                supersedes_observation_sha256=supersedes,
            )
        except (SettlementValidationError, ValueError) as exc:
            raise LegacySettlementReceiptError("legacy receipt observation is invalid") from exc
        return cls(
            trade_id=trade_id,
            observation=observation,
            schema_version=schema_version,
        )


def build_legacy_settlement_receipt(
    trade_id: str,
    observation: SettlementObservation,
) -> LegacySettlementReceipt:
    return LegacySettlementReceipt(trade_id=trade_id, observation=observation)


def _parse_enum(enum_type, value: object, name: str):
    try:
        return enum_type(_require_exact_string(value, name))
    except ValueError as exc:
        raise LegacySettlementReceiptError(f"legacy receipt {name} is invalid") from exc


def _parse_void_refund(
    outcome: MarketOutcome,
    refund_cents_per_contract: object,
    refunds_entry_fee: object,
) -> VoidRefundContract | None:
    if outcome is not MarketOutcome.VOID:
        if refund_cents_per_contract is not None or refunds_entry_fee is not None:
            raise LegacySettlementReceiptError(
                "directional legacy receipt has void refund fields"
            )
        return None
    if not isinstance(refunds_entry_fee, bool):
        raise LegacySettlementReceiptError("void legacy receipt refund flag is invalid")
    refund_text = _require_exact_string(
        refund_cents_per_contract,
        "refund_cents_per_contract",
    )
    try:
        refund_cents = Decimal(refund_text)
    except (InvalidOperation, ValueError) as exc:
        raise LegacySettlementReceiptError("void legacy receipt refund is invalid") from exc
    try:
        return VoidRefundContract(
            refund_cents_per_contract=refund_cents,
            refunds_entry_fee=refunds_entry_fee,
        )
    except SettlementValidationError as exc:
        raise LegacySettlementReceiptError("void legacy receipt refund is invalid") from exc


def _parse_datetime(value: object, name: str) -> datetime:
    text = _require_exact_string(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LegacySettlementReceiptError(f"legacy receipt {name} is invalid") from exc
    if parsed.tzinfo is None:
        raise LegacySettlementReceiptError(f"legacy receipt {name} is invalid")
    return parsed.astimezone(timezone.utc)


def _require_exact_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LegacySettlementReceiptError(f"legacy receipt {name} is invalid")
    return value
