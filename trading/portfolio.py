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
from typing import Optional, overload

from trading.venue import MarketRef, normalize_venue


@dataclass
class Position:
    """A single open paper or live trade.

    ``entry_price_cents`` is the EXECUTED ENTRY price in cents for the
    chosen side (NOT the legacy YES midpoint). ``venue`` defaults to
    ``"kalshi"`` for historical rows that predate venue namespacing. The
    ``price_source`` and ``price_method`` fields carry provenance so callers
    can distinguish executed-side semantics from legacy rows. Defaulted to
    ``"unavailable"`` / ``"none"`` for hydration of pre-P0 DB rows that
    lack the columns.

    P1-B renamed the underlying DB column from ``market_yes_price`` to
    ``entry_price_cents``. The hydration layer still tolerates the legacy
    column name for direct legacy-fixture loads, but PaperTrader migrates the
    live DB before calling this method.
    """
    trade_id:          str
    ticker:            str
    side:              str    # "yes" | "no"
    contracts:         int
    cost_dollars:      float
    price_cents:       int
    estimated_prob:    float
    entry_price_cents: float
    ts:                str    # ISO8601 UTC — used for cooldown seeding on restart
    venue:            str = "kalshi"
    price_source:     str = "unavailable"
    price_method:     str = "none"


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

        DT-1b: hydrates post-P0 provenance columns (``price_source``,
        ``price_method``) when present in the DB schema; legacy rows that
        predate the columns default to ``"unavailable"`` / ``"none"``.
        """
        # Detect post-P0 provenance columns without forcing a schema
        # migration (P-6 owns that). Use PRAGMA introspection so legacy
        # schemas continue to load.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_trades)").fetchall()}
        has_provenance = "price_source" in cols and "price_method" in cols
        venue_expr = "venue" if "venue" in cols else "'kalshi' AS venue"
        entry_price_col = "entry_price_cents" if "entry_price_cents" in cols else "market_yes_price"
        if has_provenance:
            select_sql = (
                "SELECT trade_id, ticker, side, contracts, cost_dollars, price_cents, "
                f"       estimated_prob, {entry_price_col} AS entry_price_cents, ts, "
                f"       {venue_expr}, price_source, price_method "
                "FROM paper_trades WHERE resolved = 0 ORDER BY ts ASC"
            )
        else:
            select_sql = (
                "SELECT trade_id, ticker, side, contracts, cost_dollars, price_cents, "
                f"       estimated_prob, {entry_price_col} AS entry_price_cents, ts, "
                f"       {venue_expr} "
                "FROM paper_trades WHERE resolved = 0 ORDER BY ts ASC"
            )
        rows = conn.execute(select_sql).fetchall()
        self._positions.clear()
        for row in rows:
            keys = row.keys() if hasattr(row, "keys") else None

            def _get(name: str, default):
                if keys is not None and name in keys:
                    value = row[name]
                    return value if value is not None else default
                return default

            pos = Position(
                trade_id=row["trade_id"],
                ticker=row["ticker"],
                side=row["side"],
                contracts=row["contracts"],
                cost_dollars=row["cost_dollars"],
                price_cents=row["price_cents"],
                estimated_prob=row["estimated_prob"],
                entry_price_cents=row["entry_price_cents"],
                ts=row["ts"],
                venue=_get("venue", "kalshi"),
                price_source=_get("price_source", "unavailable"),
                price_method=_get("price_method", "none"),
            )
            self._positions.setdefault(pos.ticker, []).append(pos)

    # ── Mutations (called by PaperTrader only) ────────────────────────────────

    def add(self, pos: Position) -> None:
        """Record a newly opened position."""
        self._positions.setdefault(pos.ticker, []).append(pos)

    @overload
    def resolve(self, market_ref: MarketRef) -> list[Position]: ...

    @overload
    def resolve(self, market_ref: str) -> None: ...

    def resolve(self, market_ref: MarketRef | str) -> list[Position] | None:
        """Remove positions by exact market identity or legacy ticker."""
        if isinstance(market_ref, str):
            self._positions.pop(market_ref, None)
            return None

        ticker = market_ref.venue_market_id
        positions = self._positions.get(ticker, [])
        venue = normalize_venue(market_ref.venue)
        closed = [pos for pos in positions if normalize_venue(pos.venue) == venue]
        remaining = [pos for pos in positions if normalize_venue(pos.venue) != venue]

        if remaining:
            self._positions[ticker] = remaining
        else:
            self._positions.pop(ticker, None)
        return closed

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

    def open_positions_by_prefix(self, market_prefix: str) -> list[Position]:
        """PROFIT-ALIGN-004 (2026-05-25): all open positions whose ticker
        starts with the given market_prefix.

        The matcher can produce multiple outcome-contract matches in the same
        series (e.g. KXTXRUNOFFENDORSE-DJT-BOTH / -KPAX / -JCOR). Same-signal
        guard works per-ticker; this method enables a per-prefix concentration
        cap so one news event can't trigger N concurrent bets on the same
        underlying topic.
        """
        if not market_prefix:
            return []
        # Match `<prefix>` exact OR `<prefix>-...` (avoid KXFOO matching KXFOOBAR)
        return [
            pos
            for ticker, bucket in self._positions.items()
            if ticker == market_prefix or ticker.startswith(f"{market_prefix}-")
            for pos in bucket
        ]

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
