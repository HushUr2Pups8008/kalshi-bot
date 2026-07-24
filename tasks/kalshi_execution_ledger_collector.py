"""Explicit-order, GET-only collection of authenticated Kalshi execution receipts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from trading.kalshi_execution_ledger import (
    HISTORICAL_CUTOFF_UNKNOWN,
    UNATTRIBUTED_MANUAL_SOURCE,
    KalshiExecutionLedger,
)


_MAX_FILL_PAGES_PER_ORDER = 10_000


class CollectorProtocolError(ValueError):
    """Raised when a receipt source cannot safely be reconciled."""


class KalshiExecutionReceiptClient(Protocol):
    """Narrow GET-only protocol; no submit, cancel, or hold operations exist here."""

    def get_order_receipt(self, order_id: str) -> dict[str, Any]: ...

    def get_fills_page(
        self,
        *,
        order_id: str,
        min_ts: int | None = None,
        max_ts: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        subaccount: int | None = None,
    ) -> tuple[list[object], str | None]: ...


@dataclass(frozen=True)
class CollectionResult:
    order_id: str
    pages: int
    fill_statuses: tuple[str, ...]
    complete_coverage: bool
    coverage_state: str
    source_kind: str
    integrity_ok: bool


class KalshiExecutionLedgerCollector:
    """Collects exact order IDs only; it intentionally cannot infer or trade."""

    def __init__(
        self,
        *,
        client: KalshiExecutionReceiptClient,
        ledger: KalshiExecutionLedger,
        now: Callable[[], str],
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._now = now

    def collect_order(
        self,
        order_id: str,
        *,
        source_kind: str = UNATTRIBUTED_MANUAL_SOURCE,
    ) -> CollectionResult:
        requested_order_id = _official_order_id(order_id)
        if source_kind != UNATTRIBUTED_MANUAL_SOURCE:
            raise CollectorProtocolError("unsupported order attribution source")
        order = self._client.get_order_receipt(requested_order_id)
        if not isinstance(order, Mapping):
            raise CollectorProtocolError("order receipt must be an object")
        if _official_order_id(order.get("order_id")) != requested_order_id:
            raise CollectorProtocolError("order receipt order ID mismatch")

        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages = 0
        fill_statuses: list[str] = []
        while True:
            if pages >= _MAX_FILL_PAGES_PER_ORDER:
                raise CollectorProtocolError("fill page limit exceeded")
            fills, next_cursor = self._client.get_fills_page(
                order_id=requested_order_id,
                cursor=cursor,
            )
            if not isinstance(fills, list):
                raise CollectorProtocolError("fill page must be a list")
            if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
                raise CollectorProtocolError("fill cursor must be a non-empty string or None")

            result = self._ledger.apply_page(
                order,
                fills,
                collected_at=_collected_at(self._now()),
                source_kind=source_kind,
            )
            if result.order_status in {"quarantined", "conflict"}:
                raise CollectorProtocolError(f"order receipt {result.order_status}")
            pages += 1
            fill_statuses.extend(result.fill_statuses)

            if next_cursor is None:
                break
            if next_cursor in seen_cursors:
                raise CollectorProtocolError("repeated fill cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        # The recent fills endpoint has a historical cutoff. No exact-order run
        # can claim complete fill or fee coverage until that cutoff is handled.
        return CollectionResult(
            order_id=requested_order_id,
            pages=pages,
            fill_statuses=tuple(fill_statuses),
            complete_coverage=False,
            coverage_state=HISTORICAL_CUTOFF_UNKNOWN,
            source_kind=source_kind,
            integrity_ok=all(status in {"inserted", "identical"} for status in fill_statuses),
        )


def _official_order_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CollectorProtocolError("an explicit official order ID is required")
    return value.strip()


def _collected_at(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CollectorProtocolError("collector clock must return a timestamp string")
    return value.strip()
