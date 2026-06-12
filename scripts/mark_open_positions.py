#!/usr/bin/env python3
"""Mark open paper positions to current market price (PROFIT-DRAWDOWN-001a).

The paper bankroll model deducts an open trade's full entry cost at placement,
so notional_bankroll understates true paper equity whenever open positions are
in the money. This tool fetches the CURRENT price for each unresolved position
(Kalshi via signed REST GET, Polymarket via the public client), values the held
side at its current market price, and reports true paper equity =
notional_bankroll + sum(current value of open positions).

READ-ONLY: issues only GET /markets/<ticker> (Kalshi) and the Polymarket public
get_market. Places no orders, writes nothing, mutates no state.

Usage:  python scripts/mark_open_positions.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Shared constant so the go-live gate's MTM marks and the report's trades can
# never silently read different DBs (PROFIT-DRAWDOWN-001c review note).
from utils.output_paths import PAPER_TRADES_DB as DB_PATH  # noqa: E402


def _kalshi_held_price_cents(market, side: str):
    """Current price (cents) of the held side. Mid when both bid/ask exist,
    else bid, else ask, else last (last is YES-referenced).

    The ``last`` fallback only counts when strictly inside (0, 100): an
    untraded/settling book reports last=0, and a held NO would otherwise be
    valued at 100-0 = full contract value — overstating equity exactly when
    the order book is emptiest. Out-of-band last -> unpriced ($0 to the
    go-live gate's MTM equity; PROFIT-DRAWDOWN-001c review finding)."""
    try:
        if side == "yes":
            bid, ask = market.yes_bid_cents, market.yes_ask_cents
        else:
            bid, ask = market.no_bid_cents, market.no_ask_cents
    except Exception:
        bid = ask = None
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    if bid is not None:
        return float(bid)
    if ask is not None:
        return float(ask)
    last = getattr(market, "last_price_cents", None)
    if last is not None and 0 < last < 100:
        return float(last) if side == "yes" else float(100 - last)
    return None


def _poly_held_price_cents(market, side: str):
    """Conservative mark for the held side (Polymarket exposes asks only).

    The held side's own ask is what a BUYER pays — liquidation value is
    bid-side. In a binary market the bid-equivalent is ``100 - opposite ask``
    (selling your side == buying the opposite at its ask). Marking at the held
    ask overstates equity by up to the full spread, which fed the go-live
    gate's MTM drawdown optimistically (PROFIT-DRAWDOWN-001c review finding).
    Use min(held ask, 100 - opposite ask) when both quotes exist; else
    whichever exists."""
    held = market.yes_ask_cents if side == "yes" else market.no_ask_cents
    opposite = market.no_ask_cents if side == "yes" else market.yes_ask_cents
    bid_equiv = (100 - opposite) if opposite is not None else None
    if held is not None and bid_equiv is not None:
        return min(float(held), float(bid_equiv))
    if bid_equiv is not None:
        return float(bid_equiv)
    return float(held) if held is not None else None


def compute_open_position_marks(db_path: Path = DB_PATH) -> dict | None:
    """Price every unresolved position at current market and return a summary.

    Importable core of this tool (PROFIT-DRAWDOWN-001c): the daily performance
    report's go-live section consumes this so its drawdown criterion reflects
    TRUE paper equity (bankroll + current value of open positions) instead of
    the notional bankroll, which deducts each open trade's full entry cost at
    placement and therefore overstates drawdown whenever positions retain value.

    Read-only (GETs only; writes nothing). Returns None when the DB is missing.
    Per-position fetch failures are non-fatal: the position lands in
    ``unknown_cost`` / ``unpriced_count`` and is worth $0 in ``marked_value`` —
    callers that gate on equity stay conservative (unpriced == worthless).

    Returns dict: bankroll, total_cost, marked_value, unknown_cost,
    priced_count, unpriced_count, rows (per-position render dicts).
    """
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        state = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM bot_state")}
    except sqlite3.OperationalError:
        state = {}
    try:
        bankroll = float(state.get("notional_bankroll", 0) or 0)
    except (TypeError, ValueError):
        bankroll = 0.0
    rows = conn.execute(
        "SELECT ticker, venue, side, contracts, cost_dollars FROM paper_trades "
        "WHERE resolved = 0 ORDER BY venue, ticker"
    ).fetchall()
    conn.close()

    kalshi = poly = None
    marked_cv = 0.0
    unknown_cost = 0.0
    total_cost = 0.0
    priced_count = 0
    unpriced_count = 0
    out_rows: list[dict] = []

    for r in rows:
        ticker = r["ticker"]
        venue = (r["venue"] or "").lower()
        side = (r["side"] or "").lower()
        contracts = r["contracts"] or 0
        cost = float(r["cost_dollars"] or 0)
        total_cost += cost
        cents = None
        note = ""
        try:
            if venue.startswith("kalshi") or ticker.startswith("KX"):
                if kalshi is None:
                    from kalshi.rest_client import KalshiRestClient
                    kalshi = KalshiRestClient()
                m = kalshi.get_market(ticker)
                cents = _kalshi_held_price_cents(m, side) if m else None
                if m is None:
                    note = "no market (closed/settled?)"
            else:
                if poly is None:
                    from polymarket.public_client import PolymarketPublicClient
                    poly = PolymarketPublicClient()
                m = poly.get_market(ticker)
                cents = _poly_held_price_cents(m, side) if m else None
        except Exception as exc:  # pragma: no cover - live API surface
            note = "fetch error: %s" % str(exc)[:50]

        value = None
        if cents is None:
            unknown_cost += cost
            unpriced_count += 1
        else:
            value = contracts * cents / 100.0
            marked_cv += value
            priced_count += 1
        out_rows.append({
            "ticker": ticker,
            "venue": venue,
            "side": side,
            "contracts": contracts,
            "cost": cost,
            "cents": cents,
            "value": value,
            "note": note,
        })

    return {
        "bankroll": bankroll,
        "total_cost": total_cost,
        "marked_value": marked_cv,
        "unknown_cost": unknown_cost,
        "priced_count": priced_count,
        "unpriced_count": unpriced_count,
        "rows": out_rows,
    }


def main() -> int:
    marks = compute_open_position_marks()
    if marks is None:
        print("No database at %s" % DB_PATH)
        return 1
    bankroll = marks["bankroll"]
    total_cost = marks["total_cost"]
    marked_cv = marks["marked_value"]
    unknown_cost = marks["unknown_cost"]

    lines = ["Open-position mark-to-market (read-only)", ""]
    lines.append("%-38s %-11s %-4s %4s %8s %8s %9s"
                 % ("Ticker", "Venue", "Side", "Ct", "Cost$", "Px(c)", "Value$"))
    lines.append("-" * 92)
    for row in marks["rows"]:
        px_s = "--" if row["cents"] is None else "%.1f" % row["cents"]
        cv_s = "??" if row["value"] is None else "%.2f" % row["value"]
        note = row["note"]
        lines.append("%-38s %-11s %-4s %4d %8.2f %8s %9s%s"
                     % (row["ticker"][:38], row["venue"][:11], row["side"],
                        row["contracts"], row["cost"], px_s, cv_s,
                        ("  " + note) if note else ""))

    lines.append("")
    lines.append("Notional bankroll (open cost already deducted): $%.2f" % bankroll)
    lines.append("Open positions total entry cost              : $%.2f" % total_cost)
    lines.append("Marked current value (priced positions)       : $%.2f" % marked_cv)
    if unknown_cost > 0:
        lo = bankroll + marked_cv
        hi = bankroll + marked_cv + unknown_cost
        lines.append("Unpriced open cost (value unknown)            : $%.2f" % unknown_cost)
        lines.append("")
        lines.append("TRUE PAPER EQUITY (range): $%.2f .. $%.2f" % (lo, hi))
        lines.append("  (low = unpriced positions worthless; high = unpriced worth entry cost)")
    else:
        lines.append("")
        lines.append("TRUE PAPER EQUITY: $%.2f" % (bankroll + marked_cv))
    lines.append("")
    lines.append("vs $50.00 start -> true drawdown: %.1f%% .. %.1f%%"
                 % ((1 - (bankroll + marked_cv + unknown_cost) / 50.0) * 100,
                    (1 - (bankroll + marked_cv) / 50.0) * 100))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
