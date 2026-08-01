"""Typed boundary for default-off Kalshi FIX settlement ingress capture."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from trading.kalshi_fix_settlement_ingress import (
    KalshiFixSettlementIngressAppendResult,
    KalshiFixSettlementIngressStore,
    VerifiedKalshiFixUMSEnvelope,
)


@runtime_checkable
class VerifiedKalshiFixUMSSource(Protocol):
    def receive_verified_ums(self) -> VerifiedKalshiFixUMSEnvelope: ...


def _require_aware_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _reject_file_shaped_input(value: object) -> None:
    if isinstance(value, (Mapping, str, bytes, bytearray, memoryview, Path)):
        raise TypeError("verified UMS source must be a typed source, not file-shaped input")
    if hasattr(value, "read"):
        raise TypeError("verified UMS source must not expose file-like receipt input")


def capture_verified_ums(
    source: VerifiedKalshiFixUMSSource,
    ledger: KalshiFixSettlementIngressStore,
    now: datetime,
) -> KalshiFixSettlementIngressAppendResult:
    """Capture one already-verified UMS envelope without creating runtime wiring."""
    _reject_file_shaped_input(source)
    if not isinstance(source, VerifiedKalshiFixUMSSource):
        raise TypeError("source must implement VerifiedKalshiFixUMSSource")
    if not isinstance(ledger, KalshiFixSettlementIngressStore):
        raise TypeError("ledger must be KalshiFixSettlementIngressStore")
    _require_aware_utc(now, "now")

    envelope = source.receive_verified_ums()
    if not isinstance(envelope, VerifiedKalshiFixUMSEnvelope):
        raise TypeError("source must return VerifiedKalshiFixUMSEnvelope")
    return ledger.append_verified_envelope(envelope)
