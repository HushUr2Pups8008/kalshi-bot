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

DB_PATH = REPO_ROOT / "data" / "paper_trades.db"


def _kalshi_held_price_cents(market, side: str):
    """Current price (cents) of the held side. Mid when both bid/ask exist,
    else bid, else ask, else last (last is YES-referenced)."""
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
    if last is not None:
        return float(last) if side == "yes" else float(100 - last)
    return None


def _poly_held_price_cents(market, side: str):
    """Polymarket model exposes ask cents only; use the held side's ask."""
    return market.yes_ask_cents if side == "yes" else market.no_ask_cents


def main() -> int:
    if not DB_PATH.exists():
        print("No database at %s" % DB_PATH)
        return 1
    conn = sqlite3.connect(str(DB_PATH))
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
    lines = ["Open-position mark-to-market (read-only)", ""]
    lines.append("%-38s %-11s %-4s %4s %8s %8s %9s"
                 % ("Ticker", "Venue", "Side", "Ct", "Cost$", "Px(c)", "Value$"))
    lines.append("-" * 92)

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

        if cents is None:
            unknown_cost += cost
            cv_s = "??"
            px_s = "--"
        else:
            cv = contracts * cents / 100.0
            marked_cv += cv
            cv_s = "%.2f" % cv
            px_s = "%.1f" % cents
        lines.append("%-38s %-11s %-4s %4d %8.2f %8s %9s%s"
                     % (ticker[:38], venue[:11], side, contracts, cost, px_s, cv_s,
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
