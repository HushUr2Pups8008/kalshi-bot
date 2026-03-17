"""
Portfolio — single source of truth for all open positions.

Loaded from DB on startup, updated in-memory on every trade and resolution.
PaperTrader owns this object and keeps it in sync after every DB write.
TradeExecutor reads from it for risk checks — zero extra DB queries at decision time.

Key guarantees:
  - Always consistent with DB state (updated atomically alongside DB writes)
  - Survives restarts (reloaded from DB on PaperTrader.__init__)
  - Thread-safe for single-async-consumer access (asyncio single-threaded model)
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    """A single open paper or live trade."""
    trade_id:         str
    ticker:           str
    side:             str    # "yes" | "no"
    contracts:        int
    cost_dollars:     float
    price_cents:      int
    estimated_prob:   float
    market_yes_price: float
    ts:               str    # ISO8601 UTC — used for cooldown seeding on restart


class Portfolio:
    """
    In-memory book of all open positions.

    PaperTrader is the only writer. TradeExecutor is the primary reader.
    No DB access after initial load — all state lives in self._positions.
    """

    def __init__(self) -> None:
        # ticker -> list of open Position objects (insertion order preserved)
        self._positions: dict[str, list[Position]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def load_from_db(self, conn: sqlite3.Connection) -> None:
        """
        Populate from all unresolved trades in the DB.
        Called once by PaperTrader.__init__ after schema migration.
        """
        rows = conn.execute(
            "SELECT trade_id, ticker, side, contracts, cost_dollars, price_cents, "
            "       estimated_prob, market_yes_price, ts "
            "FROM paper_trades WHERE resolved = 0 ORDER BY ts ASC"
        ).fetchall()
        self._positions.clear()
        for row in rows:
            pos = Position(
                trade_id=row["trade_id"],
                ticker=row["ticker"],
                side=row["side"],
                contracts=row["contracts"],
                cost_dollars=row["cost_dollars"],
                price_cents=row["price_cents"],
                estimated_prob=row["estimated_prob"],
                market_yes_price=row["market_yes_price"],
                ts=row["ts"],
            )
            self._positions.setdefault(pos.ticker, []).append(pos)

    # ── Mutations (called by PaperTrader only) ────────────────────────────────

    def add(self, pos: Position) -> None:
        """Record a newly opened position."""
        self._positions.setdefault(pos.ticker, []).append(pos)

    def resolve(self, ticker: str) -> None:
        """Remove all open positions for ticker after market resolution."""
        self._positions.pop(ticker, None)

    # ── Queries ───────────────────────────────────────────────────────────────

    def open_positions(self, ticker: Optional[str] = None) -> list[Position]:
        """
        All open positions, optionally filtered to a single ticker.
        Returns a copy — callers cannot mutate internal state.
        """
        if ticker is not None:
            return list(self._positions.get(ticker, []))
        return [pos for bucket in self._positions.values() for pos in bucket]

    def tickers(self) -> set[str]:
        """All tickers with at least one open position."""
        return set(self._positions.keys())

    def exposure(self, ticker: str) -> float:
        """Total dollars deployed across all open positions for a ticker."""
        return sum(p.cost_dollars for p in self._positions.get(ticker, []))

    def total_deployed(self) -> float:
        """Total dollars deployed across all open positions."""
        return sum(
            p.cost_dollars
            for bucket in self._positions.values()
            for p in bucket
        )

    def position_count(self) -> int:
        """Total number of open positions across all tickers."""
        return sum(len(v) for v in self._positions.values())

    def latest_ts(self, ticker: str) -> Optional[str]:
        """
        ISO timestamp of the most recent open position for ticker, or None.
        Used by TradeExecutor to seed per-ticker cooldowns from portfolio on restart
        instead of re-querying the DB.
        """
        positions = self._positions.get(ticker, [])
        if not positions:
            return None
        return max(p.ts for p in positions)

    def is_concentration_ok(
        self,
        ticker: str,
        additional_dollars: float,
        max_ticker_pct: float,
        bankroll: float,
    ) -> bool:
        """
        Return True if adding `additional_dollars` to `ticker` stays within the
        per-ticker concentration limit (max_ticker_pct * bankroll).

        Example: max_ticker_pct=0.25, bankroll=$500 → cap per ticker = $125.
        """
        if bankroll <= 0 or max_ticker_pct <= 0:
            return True
        cap = max_ticker_pct * bankroll
        new_exposure = self.exposure(ticker) + additional_dollars
        return new_exposure <= cap

    # ── Reporting ─────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """
        Snapshot dict suitable for logging or status endpoints.
        Shows open tickers, total deployed, and per-ticker breakdown.
        """
        return {
            "open_tickers":   sorted(self.tickers()),
            "position_count": self.position_count(),
            "total_deployed": round(self.total_deployed(), 2),
            "by_ticker": {
                ticker: {
                    "count":    len(positions),
                    "exposure": round(sum(p.cost_dollars for p in positions), 2),
                    "sides":    sorted({p.side for p in positions}),
                }
                for ticker, positions in self._positions.items()
            },
        }
