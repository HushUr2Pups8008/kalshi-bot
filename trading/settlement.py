from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from trading.venue import MarketRef, Venue


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SettlementValidationError(ValueError):
    """Raised when a settlement contract is internally invalid."""


class SettlementDriftError(RuntimeError):
    """Raised when authoritative settlement data cannot be trusted."""


class UnsupportedVoidError(SettlementDriftError):
    """Raised when a void outcome lacks an explicit refund contract."""


class MarketOutcome(StrEnum):
    YES = "yes"
    NO = "no"
    VOID = "void"


@dataclass(frozen=True)
class VoidRefundContract:
    """Exact cash treatment for one voided contract, expressed in cents."""

    refund_cents_per_contract: Decimal
    refunds_entry_fee: bool

    def __post_init__(self) -> None:
        value = self.refund_cents_per_contract
        if not isinstance(value, Decimal) or not value.is_finite():
            raise SettlementValidationError(
                "refund_cents_per_contract must be a finite Decimal"
            )
        if value < 0 or value > 100:
            raise SettlementValidationError(
                "refund_cents_per_contract must be between 0 and 100"
            )
        if not isinstance(self.refunds_entry_fee, bool):
            raise SettlementValidationError("refunds_entry_fee must be boolean")


@dataclass(frozen=True)
class SettlementObservation:
    market_ref: MarketRef
    outcome: MarketOutcome
    authoritative_outcome_json: str
    canonical_payload_json: str
    observed_at: datetime
    effective_at: datetime
    rules_version: str
    source_id: str
    payload_sha256: str
    observation_sha256: str
    void_refund: VoidRefundContract | None = None
    supersedes_observation_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.market_ref, MarketRef):
            raise SettlementValidationError("market_ref must be a MarketRef")
        if not isinstance(self.market_ref.venue, Venue):
            raise SettlementValidationError("market_ref.venue must be a Venue")
        if not self.market_ref.venue_market_id.strip():
            raise SettlementValidationError("market_ref.venue_market_id is required")
        if not self.market_ref.alias.strip():
            raise SettlementValidationError("market_ref.alias is required")
        if not isinstance(self.outcome, MarketOutcome):
            raise SettlementValidationError("outcome must be a MarketOutcome")

        _require_canonical_json(
            self.authoritative_outcome_json, "authoritative_outcome_json"
        )
        _require_canonical_json(
            self.canonical_payload_json, "canonical_payload_json"
        )
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.effective_at, "effective_at")
        if self.effective_at > self.observed_at:
            raise SettlementValidationError(
                "effective_at must not be later than observed_at"
            )
        _require_nonempty(self.rules_version, "rules_version")
        _require_nonempty(self.source_id, "source_id")
        _require_sha256(self.payload_sha256, "payload_sha256")

        expected_sha256 = hashlib.sha256(
            self.canonical_payload_json.encode("utf-8")
        ).hexdigest()
        if self.payload_sha256 != expected_sha256:
            raise SettlementValidationError(
                "payload_sha256 does not match canonical_payload_json"
            )

        if self.outcome is MarketOutcome.VOID and self.void_refund is None:
            raise SettlementValidationError("void_refund is required for void outcome")
        if self.outcome is not MarketOutcome.VOID and self.void_refund is not None:
            raise SettlementValidationError(
                "void_refund is only valid for void outcome"
            )

        _require_sha256(self.observation_sha256, "observation_sha256")
        expected_observation_sha256 = _compute_observation_sha256(
            market_ref=self.market_ref,
            outcome=self.outcome,
            authoritative_outcome_json=self.authoritative_outcome_json,
            payload_sha256=self.payload_sha256,
            effective_at=self.effective_at,
            rules_version=self.rules_version,
            source_id=self.source_id,
            void_refund=self.void_refund,
        )
        if self.observation_sha256 != expected_observation_sha256:
            raise SettlementValidationError(
                "observation_sha256 does not match settlement semantics"
            )

        if self.supersedes_observation_sha256 is not None:
            _require_sha256(
                self.supersedes_observation_sha256,
                "supersedes_observation_sha256",
            )
            if self.supersedes_observation_sha256 == self.observation_sha256:
                raise SettlementValidationError(
                    "supersedes_observation_sha256 must reference an earlier observation"
                )


def canonical_payload_json(payload: object) -> str:
    """Return deterministic JSON or fail closed on unsupported values."""

    normalized = _normalize_json_value(payload, seen=set())
    try:
        return json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise SettlementDriftError(
            "authoritative payload cannot be represented as canonical JSON"
        ) from exc


def build_settlement_observation(
    *,
    market_ref: MarketRef,
    outcome: MarketOutcome,
    authoritative_outcome: object,
    authoritative_payload: object,
    observed_at: datetime,
    effective_at: datetime,
    rules_version: str,
    source_id: str,
    void_refund: VoidRefundContract | None = None,
    previous_observation: SettlementObservation | None = None,
    supersedes_observation_sha256: str | None = None,
) -> SettlementObservation:
    if outcome is MarketOutcome.VOID and void_refund is None:
        raise UnsupportedVoidError(
            f"void settlement for {market_ref.venue_market_id} has no refund contract"
        )

    payload_json = canonical_payload_json(authoritative_payload)
    authoritative_outcome_json = canonical_payload_json(authoritative_outcome)
    payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    observation_sha256 = _compute_observation_sha256(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome_json=authoritative_outcome_json,
        payload_sha256=payload_sha256,
        effective_at=effective_at,
        rules_version=rules_version,
        source_id=source_id,
        void_refund=void_refund,
    )
    observation = SettlementObservation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome_json=authoritative_outcome_json,
        canonical_payload_json=payload_json,
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version=rules_version,
        source_id=source_id,
        payload_sha256=payload_sha256,
        observation_sha256=observation_sha256,
        void_refund=void_refund,
        supersedes_observation_sha256=supersedes_observation_sha256,
    )
    validate_observation_transition(previous_observation, observation)
    return observation


def validate_observation_transition(
    previous: SettlementObservation | None,
    current: SettlementObservation,
) -> None:
    """Require explicit, hash-linked supersession for changed observations."""

    if previous is None:
        if current.supersedes_observation_sha256 is not None:
            raise SettlementDriftError(
                "settlement supersession cannot be validated without prior observation"
            )
        return

    if previous.market_ref != current.market_ref:
        raise SettlementDriftError("settlement supersession identity mismatch")
    if current.observed_at < previous.observed_at:
        raise SettlementDriftError("settlement observed_at cannot move backward")
    if current.effective_at < previous.effective_at:
        raise SettlementDriftError("settlement effective_at cannot move backward")

    if previous.observation_sha256 == current.observation_sha256:
        if current.supersedes_observation_sha256 is not None:
            raise SettlementDriftError(
                "unchanged settlement observation cannot declare supersession"
            )
        return

    if current.supersedes_observation_sha256 != previous.observation_sha256:
        raise SettlementDriftError(
            "changed settlement observation requires valid supersession"
        )


def _compute_observation_sha256(
    *,
    market_ref: MarketRef,
    outcome: MarketOutcome,
    authoritative_outcome_json: str,
    payload_sha256: str,
    effective_at: datetime,
    rules_version: str,
    source_id: str,
    void_refund: VoidRefundContract | None,
) -> str:
    _require_aware(effective_at, "effective_at")
    refund_fields = None
    if void_refund is not None:
        refund_fields = {
            "refund_cents_per_contract": _canonical_decimal(
                void_refund.refund_cents_per_contract
            ),
            "refunds_entry_fee": void_refund.refunds_entry_fee,
        }
    semantic_json = canonical_payload_json(
        {
            "authoritative_outcome_json": authoritative_outcome_json,
            "effective_at_utc": effective_at.astimezone(timezone.utc).isoformat(
                timespec="microseconds"
            ),
            "market_ref": {
                "alias": market_ref.alias,
                "venue": market_ref.venue.value,
                "venue_market_id": market_ref.venue_market_id,
            },
            "outcome": outcome.value,
            "payload_sha256": payload_sha256,
            "rules_version": rules_version,
            "source_id": source_id,
            "void_refund": refund_fields,
        }
    )
    return hashlib.sha256(semantic_json.encode("utf-8")).hexdigest()


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _normalize_json_value(value: object, *, seen: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SettlementDriftError(
                "authoritative payload numbers must be finite"
            )
        return value
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            raise SettlementDriftError("cyclic authoritative payload is unsupported")
        seen.add(marker)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise SettlementDriftError(
                        "authoritative payload object keys must be strings"
                    )
                normalized[key] = _normalize_json_value(item, seen=seen)
            return normalized
        finally:
            seen.remove(marker)
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        marker = id(value)
        if marker in seen:
            raise SettlementDriftError("cyclic authoritative payload is unsupported")
        seen.add(marker)
        try:
            return [_normalize_json_value(item, seen=seen) for item in value]
        finally:
            seen.remove(marker)
    raise SettlementDriftError(
        f"unsupported authoritative payload value: {type(value).__name__}"
    )


def _require_nonempty(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SettlementValidationError(f"{field_name} is required")


def _require_aware(value: object, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise SettlementValidationError(f"{field_name} must be timezone-aware")


def _require_sha256(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise SettlementValidationError(
            f"{field_name} must be a lowercase SHA-256 hex digest"
        )


def _require_canonical_json(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise SettlementValidationError(f"{field_name} is required")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SettlementValidationError(f"{field_name} must be valid JSON") from exc
    if canonical_payload_json(parsed) != value:
        raise SettlementValidationError(f"{field_name} must be canonical JSON")
