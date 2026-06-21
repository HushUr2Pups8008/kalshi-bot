"""Tests for scripts/mark_open_positions.py (PROFIT-DRAWDOWN-001a/c).

The importable core ``compute_open_position_marks`` feeds the go-live gate's
MTM drawdown (section 8 of the performance report), so its accumulation
semantics are load-bearing: a pricing failure must land in unknown_cost /
unpriced_count (worth $0 to equity), never silently inflate marked_value.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.mark_open_positions import compute_open_position_marks


def _make_db(path: Path, *, pm_rows: list[tuple] | None = None) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY, ticker TEXT, venue TEXT, side TEXT,
                contracts INTEGER, cost_dollars REAL, market_snapshot TEXT,
                resolved INTEGER
            )
            """
        )
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO bot_state VALUES ('notional_bankroll', '22.65')"
        )
        conn.executemany(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?)",
            [
                ("a", "KXPRICED-1", "kalshi", "yes", 5, 1.50, None, 0),
                ("b", "KXFAILS-1", "kalshi", "yes", 5, 2.00, None, 0),
                # resolved row: excluded from the open-position scan.
                ("c", "KXDONE-1", "kalshi", "yes", 5, 1.00, None, 1),
                *(pm_rows or []),
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


def _poly_row(
    trade_id: str,
    ticker: str,
    side: str,
    cost: float,
    snapshot: dict | None,
) -> tuple:
    """A polymarket_us open paper_trades row matching _make_db's column order:
    (trade_id, ticker, venue, side, contracts, cost_dollars,
     market_snapshot, resolved)."""
    snap = json.dumps(snapshot) if snapshot is not None else None
    return (trade_id, ticker, "polymarket_us", side, 5, cost, snap, 0)


class _StubKalshiAllFail:
    """Kalshi rows in these PM-focused tests are not under test -- stub the
    client so they fail to price WITHOUT a real network call (KXFAILS-1 and
    KXPRICED-1 both land in unknown_cost). Keeps the PM assertions hermetic."""

    def get_market(self, ticker):
        raise RuntimeError("kalshi stubbed out in PM test")


def test_poly_marking_falls_back_to_snapshot_when_live_absent(tmp_path: Path):
    """FIX-2 / WHY: during the ~3-11h 2026-06-17 PM feed drop the held PM
    positions were live-unpriceable, so MTM equity read $0 for each --
    blinding the go-live gate's drawdown criterion. The snapshot already
    stores both asks at trade time, so a transient live miss must fall back to
    that last-known price (clearly labeled stale) instead of $0."""
    db = tmp_path / "paper_trades.db"
    # Snapshot asks: yes=60, no=55 -> _poly_held_price_cents(yes) = min(60, 45)
    # = 45c -> 5 contracts -> $2.25 marked value.
    snap = {"yes_ask_cents": 60, "no_ask_cents": 55}
    _make_db(db, pm_rows=[_poly_row("p", "pm-absent-slug", "yes", 3.00, snap)])

    class _FakePoly:
        def get_market(self, ticker):
            raise RuntimeError("market not found")  # transient feed drop

    with patch("polymarket.public_client.PolymarketPublicClient", _FakePoly), patch(
        "kalshi.rest_client.KalshiRestClient", _StubKalshiAllFail
    ):
        marks = compute_open_position_marks(db)

    assert marks is not None
    pm = next(r for r in marks["rows"] if r["ticker"] == "pm-absent-slug")
    # Priced via the snapshot fallback (was $0/unpriced before FIX-2).
    assert pm["cents"] == 45.0
    assert pm["value"] == 2.25
    assert pm["note"].startswith("stale:")
    # Exactly one position is priced: the snapshot-fallback PM row. Both Kalshi
    # rows are stubbed to fail here (not under test), so they are unpriced.
    assert marks["priced_count"] == 1
    # WHY: the stale fallback must be VISIBLE to callers/reports so an all-stale
    # run is not mistaken for a fully-live-priced one (degraded-equity signal).
    assert marks["snapshot_fallback_count"] == 1
    # The PM row's cost is NOT in unknown_cost (it is priced, just from snapshot);
    # unknown_cost holds only the two stubbed Kalshi rows (1.50 + 2.00).
    assert marks["unknown_cost"] == 3.50


def test_poly_marking_missing_snapshot_stays_unpriced(tmp_path: Path):
    """FIX-2 / WHY: fail-safe -- a transient live miss with NO usable snapshot
    must land in unknown_cost/$0 (the pre-FIX-2 behavior), never raise into the
    read-only report path and never invent a phantom value."""
    db = tmp_path / "paper_trades.db"
    # Snapshot present but malformed for our needs (no ask cents).
    _make_db(
        db,
        pm_rows=[
            _poly_row("p1", "pm-no-snap", "yes", 3.00, None),
            _poly_row("p2", "pm-bad-snap", "yes", 4.00, {"foo": "bar"}),
        ],
    )

    class _FakePoly:
        def get_market(self, ticker):
            return None  # live miss yields cents=None

    with patch("polymarket.public_client.PolymarketPublicClient", _FakePoly), patch(
        "kalshi.rest_client.KalshiRestClient", _StubKalshiAllFail
    ):
        marks = compute_open_position_marks(db)

    assert marks is not None
    for tk in ("pm-no-snap", "pm-bad-snap"):
        row = next(r for r in marks["rows"] if r["ticker"] == tk)
        assert row["cents"] is None
        assert row["value"] is None
    # No position prices: both PM rows lack a usable snapshot and both Kalshi
    # rows are stubbed to fail -> all four entry costs are in unknown_cost
    # (1.50 + 2.00 Kalshi, 3.00 + 4.00 PM).
    assert marks["priced_count"] == 0
    assert marks["unknown_cost"] == 1.50 + 2.00 + 3.00 + 4.00


def test_live_mark_wins_over_snapshot(tmp_path: Path):
    """FIX-2 / WHY: the snapshot is a FALLBACK only. A successful live mark must
    always win so MTM reflects the current market, not a stale entry-time ask --
    and the row carries no 'stale:' note."""
    db = tmp_path / "paper_trades.db"
    # Snapshot would mark yes at min(60, 100-55)=45c. The live market is fresher:
    # yes ask 70, no ask 60 -> yes mark min(70, 100-60)=min(70,40)=40c. The 40c
    # live mark (distinct from the 45c snapshot) must win.
    snap = {"yes_ask_cents": 60, "no_ask_cents": 55}
    _make_db(db, pm_rows=[_poly_row("p", "pm-live-ok", "yes", 3.00, snap)])

    class _FakePoly:
        def get_market(self, ticker):
            return SimpleNamespace(yes_ask_cents=70, no_ask_cents=60)

    with patch("polymarket.public_client.PolymarketPublicClient", _FakePoly), patch(
        "kalshi.rest_client.KalshiRestClient", _StubKalshiAllFail
    ):
        marks = compute_open_position_marks(db)

    assert marks is not None
    pm = next(r for r in marks["rows"] if r["ticker"] == "pm-live-ok")
    assert pm["cents"] == 40.0  # live mark, not the 45c snapshot mark
    assert pm["value"] == 2.00  # 5 contracts * 40c
    assert pm["note"] == ""
