from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from trading.venue import Venue
from utils.logger import get_logger

log = get_logger("polymarket_settlement")


class SettlementNotFound(Exception):
    """Raised by a settlement source when the market has not settled yet."""


class SettlementDriftError(RuntimeError):
    """Raised when Polymarket settlement payload shape violates expectations."""


class SettlementSource(Protocol):
    def get_settlement(self, market_id: str) -> Mapping[str, Any] | None:
        """Return a settlement payload or raise SettlementNotFound."""


class SettlementResolver(Protocol):
    _conn: sqlite3.Connection

    def _resolve_market_sync(
        self, ticker: str, resolved_yes: bool
    ) -> list[tuple[str, str, float]]:
        """Resolve open paper trades using PaperTrader atomicity semantics."""


class PolymarketPublicSettlementSource:
    def __init__(self, client: Any | None = None):
        if client is None:
            from polymarket.public_client import PolymarketPublicClient

            client = PolymarketPublicClient()
        self._client = client

    def get_settlement(self, market_id: str) -> Mapping[str, Any] | None:
        payload = self._client.get_market_payload(market_id)
        if not _payload_is_settled(payload):
            raise SettlementNotFound(f"Polymarket market {market_id} is not settled")
        return payload


@dataclass(frozen=True)
class SettlementReconcileResult:
    checked: int = 0
    resolved: int = 0
    not_found: int = 0
    lane_events: tuple[tuple[str, bool, str, str, float], ...] = ()


class SettlementReconciler:
    def __init__(self, *, source: SettlementSource, resolver: SettlementResolver):
        self._source = source
        self._resolver = resolver

    def reconcile(self, *, limit: int | None = None) -> SettlementReconcileResult:
        tickers = self._open_polymarket_tickers(limit=limit)
        checked = 0
        resolved = 0
        not_found = 0
        lane_events: list[tuple[str, bool, str, str, float]] = []

        for ticker in tickers:
            checked += 1
            try:
                payload = self._source.get_settlement(ticker)
            except SettlementNotFound:
                not_found += 1
                continue

            resolved_yes = _resolved_yes_from_payload(ticker, payload)
            resolved_lane_events = self._resolver._resolve_market_sync(
                ticker, resolved_yes
            )
            for trade_id, lane_name, lane_estimate in resolved_lane_events:
                lane_events.append(
                    (ticker, resolved_yes, trade_id, lane_name, lane_estimate)
                )
            resolved += 1

        return SettlementReconcileResult(
            checked=checked,
            resolved=resolved,
            not_found=not_found,
            lane_events=tuple(lane_events),
        )

    def _open_polymarket_tickers(self, *, limit: int | None) -> list[str]:
        sql = (
            "SELECT DISTINCT ticker FROM paper_trades "
            "WHERE venue = ? AND resolved = 0 "
            "ORDER BY ts ASC"
        )
        params: list[Any] = [Venue.POLYMARKET_US.value]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._resolver._conn.execute(sql, params).fetchall()
        return [str(row["ticker"]) for row in rows]


def _payload_is_settled(payload: Mapping[str, Any]) -> bool:
    status = str(payload.get("status") or "").strip().lower()
    return (
        payload.get("settled") is True
        or payload.get("closed") is True
        or status in {"closed", "finalized", "resolved", "settled"}
    )


def _resolved_yes_from_payload(
    market_id: str, payload: Mapping[str, Any] | None
) -> bool:
    if payload is None:
        raise SettlementDriftError(
            f"settlement payload missing for polymarket market {market_id}"
        )

    settled = payload.get("settled", True)
    if settled is False:
        raise SettlementDriftError(
            f"settlement payload for {market_id} was returned before settlement"
        )

    raw_outcome = (
        payload.get("resolvedOutcome")
        or payload.get("resolved_outcome")
        or payload.get("outcome")
        or payload.get("result")
    )
    if not isinstance(raw_outcome, str):
        raise SettlementDriftError(
            f"settlement payload for {market_id} missing resolvedOutcome"
        )

    outcome = raw_outcome.strip().lower()
    if outcome == "yes":
        return True
    if outcome == "no":
        return False

    raise SettlementDriftError(
        f"settlement payload for {market_id} has unsupported resolvedOutcome: "
        f"{raw_outcome!r}"
    )
