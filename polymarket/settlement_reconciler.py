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


@dataclass(frozen=True)
class SettlementReconcileResult:
    checked: int = 0
    resolved: int = 0
    not_found: int = 0


class SettlementReconciler:
    def __init__(self, *, source: SettlementSource, resolver: SettlementResolver):
        self._source = source
        self._resolver = resolver

    def reconcile(self, *, limit: int | None = None) -> SettlementReconcileResult:
        tickers = self._open_polymarket_tickers(limit=limit)
        checked = 0
        resolved = 0
        not_found = 0

        for ticker in tickers:
            checked += 1
            try:
                payload = self._source.get_settlement(ticker)
            except SettlementNotFound:
                not_found += 1
                continue

            resolved_yes = _resolved_yes_from_payload(ticker, payload)
            self._resolver._resolve_market_sync(ticker, resolved_yes)
            resolved += 1

        return SettlementReconcileResult(
            checked=checked,
            resolved=resolved,
            not_found=not_found,
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
