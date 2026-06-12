"""Tests for scripts/mark_open_positions.py (PROFIT-DRAWDOWN-001a/c).

The importable core ``compute_open_position_marks`` feeds the go-live gate's
MTM drawdown (section 8 of the performance report), so its accumulation
semantics are load-bearing: a pricing failure must land in unknown_cost /
unpriced_count (worth $0 to equity), never silently inflate marked_value.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.mark_open_positions import compute_open_position_marks


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY, ticker TEXT, venue TEXT, side TEXT,
                contracts INTEGER, cost_dollars REAL, resolved INTEGER
            )
            """
        )
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO bot_state VALUES ('notional_bankroll', '22.65')"
        )
        conn.executemany(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?)",
            [
                ("a", "KXPRICED-1", "kalshi", "yes", 5, 1.50, 0),
                ("b", "KXFAILS-1", "kalshi", "yes", 5, 2.00, 0),
                ("c", "KXDONE-1", "kalshi", "yes", 5, 1.00, 1),  # resolved: excluded
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_marks_accumulate_priced_and_fail_soft_unpriced(tmp_path: Path):
    db = tmp_path / "paper_trades.db"
    _make_db(db)

    class _FakeKalshi:
        def get_market(self, ticker):
            if "FAILS" in ticker:
                raise RuntimeError("api down")
            # held-side mid = (40+44)/2 = 42c -> 5 contracts = $2.10
            return SimpleNamespace(
                yes_bid_cents=40, yes_ask_cents=44,
                no_bid_cents=56, no_ask_cents=60,
                last_price_cents=42,
            )

    with patch("kalshi.rest_client.KalshiRestClient", _FakeKalshi):
        marks = compute_open_position_marks(db)

    assert marks is not None
    assert marks["bankroll"] == 22.65
    # Only the 2 unresolved rows are considered; resolved row excluded.
    assert len(marks["rows"]) == 2
    assert marks["priced_count"] == 1
    assert marks["unpriced_count"] == 1
    assert marks["marked_value"] == 2.10  # 5 * 42c
    # WHY: the failed fetch must land in unknown_cost (worth $0 to MTM equity),
    # never inflate marked_value -- the go-live gate adds only marked_value.
    assert marks["unknown_cost"] == 2.00
    assert marks["total_cost"] == 3.50


def test_missing_db_returns_none(tmp_path: Path):
    assert compute_open_position_marks(tmp_path / "absent.db") is None


def test_kalshi_last_zero_no_side_is_unpriced_not_full_value():
    """Review finding (PROFIT-DRAWDOWN-001c): an empty book reporting last=0
    must NOT value a held NO at 100-0 = full contract value — that overstates
    equity exactly when the market is least liquid, in the go-live gate's
    false-pass direction. Out-of-band last -> unpriced ($0 to MTM equity)."""
    from scripts.mark_open_positions import _kalshi_held_price_cents

    empty_book_last_zero = SimpleNamespace(
        yes_bid_cents=None, yes_ask_cents=None,
        no_bid_cents=None, no_ask_cents=None,
        last_price_cents=0,
    )
    assert _kalshi_held_price_cents(empty_book_last_zero, "no") is None
    assert _kalshi_held_price_cents(empty_book_last_zero, "yes") is None
    # In-band last still prices both sides.
    in_band = SimpleNamespace(
        yes_bid_cents=None, yes_ask_cents=None,
        no_bid_cents=None, no_ask_cents=None,
        last_price_cents=40,
    )
    assert _kalshi_held_price_cents(in_band, "yes") == 40.0
    assert _kalshi_held_price_cents(in_band, "no") == 60.0


def test_poly_mark_is_conservative_bid_equivalent():
    """Review finding (PROFIT-DRAWDOWN-001c): the held side's own ask is what a
    BUYER pays; liquidation value is the bid side (= 100 - opposite ask in a
    binary market). Marking at min(held ask, bid-equivalent) keeps the go-live
    gate's MTM equity from absorbing the full spread as phantom value."""
    from scripts.mark_open_positions import _poly_held_price_cents

    # yes ask 60, no ask 55 -> yes bid-equivalent = 45 < 60 -> mark 45.
    wide = SimpleNamespace(yes_ask_cents=60, no_ask_cents=55)
    assert _poly_held_price_cents(wide, "yes") == 45.0
    # no side: bid-equivalent = 100 - 60 = 40 < 55 -> mark 40.
    assert _poly_held_price_cents(wide, "no") == 40.0
    # Only the held ask available -> fall back to it.
    held_only = SimpleNamespace(yes_ask_cents=60, no_ask_cents=None)
    assert _poly_held_price_cents(held_only, "yes") == 60.0
    # Neither -> unpriced.
    none_q = SimpleNamespace(yes_ask_cents=None, no_ask_cents=None)
    assert _poly_held_price_cents(none_q, "yes") is None
