"""Default-off, decision-time capture for diagnostic G7 skip evidence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable, Mapping

from analysis import SignalAnalysis
from tasks.trade_readiness_gate import (
    G7_MAX_OPEN_EXPOSURE_DRAWDOWN_PCT,
    G7_MIN_MARKET_LIQUIDITY_DOLLARS,
    ReadinessDecision,
)
from trading.g7_skip_evidence import G7SkipEvidenceStore, G7SkipEvidenceRecord


class G7SkipEvidenceCaptureError(RuntimeError):
    """Raised when an append-only G7 receipt conflicts with an existing one."""


@dataclass(frozen=True)
class G7SkipEvidenceCaptureEnvelope:
    analysis: SignalAnalysis
    readiness_decision: ReadinessDecision
    readiness_input: Mapping[str, Any]
    trade_blocked_reason: str
    venue: str
    market_family: str | None
    lifecycle_id: str | None
    decision_at: datetime


@dataclass(frozen=True)
class G7SkipEvidenceCaptureResult:
    status: str


class G7SkipEvidenceCaptureSink:
    """Persist diagnostic receipts without blocking the event loop on SQLite."""

    def __init__(
        self,
        store: G7SkipEvidenceStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def capture(
        self,
        envelope: G7SkipEvidenceCaptureEnvelope,
    ) -> G7SkipEvidenceCaptureResult:
        return await asyncio.to_thread(self._capture_sync, envelope)

    def _capture_sync(
        self,
        envelope: G7SkipEvidenceCaptureEnvelope,
    ) -> G7SkipEvidenceCaptureResult:
        record = _record(envelope, captured_at=self._now())
        result = self._store.append_record(record)
        if result.status == "conflict":
            raise G7SkipEvidenceCaptureError("G7 skip evidence receipt conflict")
        return G7SkipEvidenceCaptureResult(status=result.status)


def _record(
    envelope: G7SkipEvidenceCaptureEnvelope,
    *,
    captured_at: datetime,
) -> G7SkipEvidenceRecord:
    readiness = envelope.readiness_decision
    if readiness.trade_blocked_reason != envelope.trade_blocked_reason:
        raise ValueError("trade_blocked_reason must match final readiness decision")

    ordered_failures = tuple(readiness.failure_reasons)
    g7_failures = tuple(reason for reason in ordered_failures if reason.startswith("G7_"))
    non_drawdown_g7_failures = tuple(
        reason for reason in g7_failures if reason != "G7_open_exposure_drawdown"
    )
    if not non_drawdown_g7_failures:
        raise ValueError("G7 receipt requires a non-drawdown final G7 failure")

    decision_at = _require_utc("decision_at", envelope.decision_at)
    captured_at = _require_utc("captured_at", captured_at)
    if captured_at < decision_at:
        captured_at = decision_at

    market_ticker = str(envelope.analysis.market.ticker).strip()
    if not market_ticker:
        raise ValueError("analysis market ticker must be non-empty")
    lifecycle_id = _normalized_lifecycle_id(
        envelope.lifecycle_id,
        market_ticker=market_ticker,
        decision_at=decision_at,
    )
    intended_side = _optional_side(envelope.readiness_input.get("intended_side"))
    execution_liquidity, evidence_status = _execution_liquidity_receipt(envelope.analysis)

    return G7SkipEvidenceRecord(
        decision_key=f"g7-skip:{lifecycle_id}",
        lifecycle_id=lifecycle_id,
        decision_at=decision_at,
        captured_at=captured_at,
        venue=str(envelope.venue).strip(),
        market_ticker=market_ticker,
        intended_side=intended_side,
        market_family=_optional_text(envelope.market_family),
        ordered_failures=ordered_failures,
        g7_failures=g7_failures,
        trade_blocked_reason=envelope.trade_blocked_reason,
        g7_inputs={
            "minimum_market_liquidity_dollars": float(
                G7_MIN_MARKET_LIQUIDITY_DOLLARS
            ),
            "maximum_open_exposure_drawdown_pct": float(
                G7_MAX_OPEN_EXPOSURE_DRAWDOWN_PCT
            ),
            "market_liquidity_dollars": _optional_number(
                envelope.readiness_input.get("market_liquidity_dollars")
            ),
            "market_price_momentum_cents": _optional_number(
                envelope.readiness_input.get("market_price_momentum_cents")
            ),
            "intended_side": intended_side,
            "open_exposure_drawdown_pct": _optional_number(
                envelope.readiness_input.get("open_exposure_drawdown_pct")
            ),
        },
        g7_results={
            "ordered_failures": list(ordered_failures),
            "g7_failures": list(g7_failures),
            "non_drawdown_g7_failures": list(non_drawdown_g7_failures),
            "trade_blocked_reason": envelope.trade_blocked_reason,
        },
        liquidity_evidence_status=evidence_status,
        execution_liquidity=execution_liquidity,
    )


def decision_time_at_or_after_execution_observation(
    decision_at: datetime,
    analysis: SignalAnalysis,
) -> datetime:
    """Keep a receipt's final decision timestamp no earlier than its book read."""
    decision_at = _require_utc("decision_at", decision_at)
    signal_meta = analysis.signal_meta if isinstance(analysis.signal_meta, dict) else {}
    metadata = signal_meta.get("g7_execution_liquidity")
    if not isinstance(metadata, Mapping):
        return decision_at
    observed_at = _optional_utc_timestamp(metadata.get("as_of"))
    if observed_at is None:
        return decision_at
    return max(decision_at, observed_at)


def _execution_liquidity_receipt(
    analysis: SignalAnalysis,
) -> tuple[dict[str, Any], str]:
    signal_meta = analysis.signal_meta or {}
    metadata = signal_meta.get("g7_execution_liquidity")
    if metadata is None:
        return (
            {
                "status": "not_queried",
                "reason": "execution_liquidity_not_queried",
            },
            "not_queried",
        )
    if not isinstance(metadata, Mapping):
        return (
            {
                "source": "unknown",
                "status": "unavailable",
                "reason": "invalid_execution_liquidity_metadata",
            },
            "unavailable",
        )

    receipt = dict(metadata)
    if receipt.get("status") == "unavailable":
        return receipt, "unavailable"
    return receipt, "observed"


def _normalized_lifecycle_id(
    lifecycle_id: str | None,
    *,
    market_ticker: str,
    decision_at: datetime,
) -> str:
    if isinstance(lifecycle_id, str) and lifecycle_id.strip():
        return lifecycle_id.strip()
    return f"unattributed:{market_ticker}:{decision_at.isoformat()}"


def _optional_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        return None
    return parsed.astimezone(UTC)


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_side(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in {"yes", "no"} else None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _require_utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be UTC")
    return value
