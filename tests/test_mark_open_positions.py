"""Tests for scripts/mark_open_positions.py (PROFIT-DRAWDOWN-001a/c).

The importable core ``compute_open_position_marks`` feeds the go-live gate's
MTM drawdown (section 8 of the performance report), so its accumulation
semantics are load-bearing: a pricing failure must land in unknown_cost /
unpriced_count (worth $0 to equity), never silently inflate marked_value.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.mark_open_positions import PublicMarketProvider, compute_open_position_marks
from trading.fees import (
    KALSHI_GENERAL_2026_07_07,
    POLYMARKET_US_2026_07_01,
)
from trading.venue import Venue
from trading.orderbook import BinaryMarketBook, BookLevel


def _make_db(
    path: Path,
    *,
    pm_rows: list[tuple] | None = None,
    kalshi_rows: list[tuple] | None = None,
) -> None:
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
                *(
                    kalshi_rows
                    if kalshi_rows is not None
                    else [
                        ("a", "KXPRICED-1", "kalshi", "yes", 5, 1.50, None, 0),
                        ("b", "KXFAILS-1", "kalshi", "yes", 5, 2.00, None, 0),
                    ]
                ),
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


_PM_TEST_IDS = {
    "pm-report-1": "1001",
    "pm-missing-fee": "1002",
}


def _add_canonical_ids(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN venue_market_id TEXT")
        conn.execute(
            "UPDATE paper_trades SET venue_market_id = ticker WHERE resolved = 0"
        )
        for ticker, venue_market_id in _PM_TEST_IDS.items():
            conn.execute(
                "UPDATE paper_trades SET venue_market_id = ? "
                "WHERE resolved = 0 AND venue = 'polymarket_us' AND ticker = ?",
                (venue_market_id, ticker),
            )


class _StaticMarketProvider:
    def __init__(self, markets: dict[tuple[Venue, str], object]):
        self.markets = markets
        self.calls: list[tuple[Venue, str]] = []

    def get_market(self, venue: Venue, ticker: str):
        self.calls.append((venue, ticker))
        key = (venue, ticker)
        if key not in self.markets and venue is Venue.POLYMARKET_US:
            alias = next(
                (
                    market_ticker
                    for market_ticker, venue_market_id in _PM_TEST_IDS.items()
                    if venue_market_id == ticker
                ),
                None,
            )
            if alias is not None:
                key = (venue, alias)
        market = self.markets[key]
        if isinstance(market, Exception):
            raise market
        values = {
            "report_venue": venue.value,
            "report_venue_market_id": ticker,
            **vars(market),
        }
        return SimpleNamespace(**values)


def test_public_provider_enriches_kalshi_fee_terms_from_exact_series_and_caches():
    as_of = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)

    class _FakeKalshi:
        def __init__(self):
            self.event_calls = 0
            self.series_calls = 0

        def get_market(self, ticker):
            return SimpleNamespace(
                ticker=ticker,
                event_ticker="KXREPORT-26",
                fee_multiplier=None,
                fee_type=None,
            )

        def get_event_fee_terms(self, event_ticker, *, as_of):
            self.event_calls += 1
            assert event_ticker == "KXREPORT-26"
            assert as_of == datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
            return SimpleNamespace(
                series_ticker="KXREPORT",
                fee_multiplier_override=Decimal("0.5"),
                fee_type_override=None,
                effective_at=datetime(2026, 7, 14, 10, tzinfo=timezone.utc),
                raw_payload_hash="e" * 64,
            )

        def get_series(self, series_ticker):
            self.series_calls += 1
            assert series_ticker == "KXREPORT"
            return SimpleNamespace(
                fee_multiplier_decimal=Decimal("1"),
                fee_type="quadratic",
                metadata_updated_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
                raw_payload_hash="s" * 64,
            )

        def get_market_orderbook(self, ticker, *, depth):
            assert depth == 100
            return BinaryMarketBook(
                venue=Venue.KALSHI,
                venue_market_id=ticker,
                yes_bids=(BookLevel(Decimal("0.40"), Decimal("10")),),
                no_bids=(BookLevel(Decimal("0.60"), Decimal("10")),),
                as_of=as_of,
                raw_payload_hash="a" * 64,
            )

    client = _FakeKalshi()
    provider = PublicMarketProvider(as_of=as_of)
    provider._kalshi = client

    first = provider.get_market(Venue.KALSHI, "KXREPORT-26-A")
    second = provider.get_market(Venue.KALSHI, "KXREPORT-26-B")

    assert first.fee_multiplier == Decimal("0.5")
    assert first.fee_type == "quadratic"
    assert first.yes_bid_levels == ((Decimal("40.00"), Decimal("10")),)
    assert second.fee_multiplier == Decimal("0.5")
    assert first.fee_effective_at == datetime(
        2026, 7, 14, 10, tzinfo=timezone.utc
    )
    assert len(first.fee_provenance_hash) == 64
    assert client.event_calls == 1
    assert client.series_calls == 1


def test_report_liquidation_uses_held_bids_depth_and_exit_fees_without_g7_cutover(
    tmp_path: Path,
):
    db = tmp_path / "paper_trades.db"
    _make_db(
        db,
        kalshi_rows=[
            ("k", "KXREPORT-1", "kalshi", "yes", 10, 4.00, None, 0),
        ],
        pm_rows=[
            ("p", "pm-report-1", "polymarket_us", "no", 10, 7.00, None, 0),
        ],
    )
    _add_canonical_ids(db)
    as_of = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    provider = _StaticMarketProvider(
        {
            (Venue.KALSHI, "KXREPORT-1"): SimpleNamespace(
                yes_bid_cents=40,
                yes_ask_cents=60,
                no_bid_cents=40,
                no_ask_cents=60,
                yes_bid_size=Decimal("12"),
                no_bid_size=Decimal("12"),
                yes_bid_levels=(
                    (Decimal("40"), Decimal("6")),
                    (Decimal("39"), Decimal("6")),
                ),
                no_bid_levels=((Decimal("40"), Decimal("12")),),
                last_price_cents=50,
                fee_multiplier=Decimal("1"),
                fee_type="quadratic",
                fee_effective_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
                fee_provenance_hash="f" * 64,
            ),
            (Venue.POLYMARKET_US, "pm-report-1"): SimpleNamespace(
                yes_bid_cents=25,
                yes_ask_cents=30,
                no_bid_cents=70,
                no_ask_cents=75,
                yes_bid_size=Decimal("15"),
                no_bid_size=Decimal("15"),
                yes_bid_levels=((Decimal("25"), Decimal("15")),),
                no_bid_levels=(
                    (Decimal("70"), Decimal("5")),
                    (Decimal("69"), Decimal("10")),
                ),
                fee_coefficient=Decimal("0.06"),
            ),
        }
    )

    marks = compute_open_position_marks(db, provider=provider, as_of=as_of)

    assert marks is not None
    # Existing G7 input remains the legacy midpoint/conservative mark exactly.
    assert marks["marked_value"] == pytest.approx(12.0)
    assert marks["gross_bid_value"] == Decimal("10.91")
    assert marks["estimated_exit_fees"] == Decimal("0.2875")
    assert marks["report_net_liquidation_value"] == Decimal("10.6225")
    assert marks["unscorable_cost"] == Decimal("0")
    assert marks["unscorable_reasons"] == {}
    assert marks["as_of"] == "2026-07-14T12:00:00+00:00"
    assert marks["fee_schedule_hashes"]["kalshi"]["artifact_sha256"] == (
        KALSHI_GENERAL_2026_07_07.artifact_sha256
    )
    assert marks["fee_schedule_hashes"]["polymarket_us"]["artifact_sha256"] == (
        POLYMARKET_US_2026_07_01.artifact_sha256
    )
    assert provider.calls == [
        (Venue.KALSHI, "KXREPORT-1"),
        (Venue.POLYMARKET_US, "pm-report-1"),
        (Venue.POLYMARKET_US, "1001"),
    ]
    assert {row["as_of"] for row in marks["rows"]} == {marks["as_of"]}
    assert {row["gross_bid_cents"] for row in marks["rows"]} == {
        Decimal("40"),
        Decimal("70"),
    }
    assert {row["fills"] for row in marks["rows"]} == {2}
    assert {row["liquidation_status"] for row in marks["rows"]} == {"scorable"}


def test_report_liquidation_fails_closed_for_unknowns_without_heuristic_dispatch(
    tmp_path: Path,
):
    db = tmp_path / "paper_trades.db"
    _make_db(
        db,
        kalshi_rows=[
            ("f", "KXFETCH-1", "kalshi", "yes", 2, 1.00, None, 0),
            ("d", "KXDEPTH-1", "kalshi", "yes", 2, 1.00, None, 0),
        ],
        pm_rows=[
            ("m", "pm-missing-fee", "polymarket_us", "yes", 2, 1.00, None, 0),
            ("u", "other-market", "other", "yes", 2, 1.00, None, 0),
        ],
    )
    _add_canonical_ids(db)
    provider = _StaticMarketProvider(
        {
            (Venue.KALSHI, "KXFETCH-1"): RuntimeError("api down"),
            (Venue.KALSHI, "KXDEPTH-1"): SimpleNamespace(
                yes_bid_cents=40,
                yes_ask_cents=45,
                no_bid_cents=55,
                no_ask_cents=60,
                yes_bid_size=None,
                fee_multiplier=Decimal("1"),
                fee_type="quadratic",
            ),
            (Venue.POLYMARKET_US, "pm-missing-fee"): SimpleNamespace(
                yes_bid_cents=40,
                yes_ask_cents=45,
                no_bid_cents=55,
                no_ask_cents=60,
                yes_bid_size=Decimal("5"),
                fee_coefficient=None,
            ),
            # Legacy marked_value still uses its old non-Kalshi branch. The new
            # report dispatcher must nevertheless reject the unsupported venue.
            (Venue.POLYMARKET_US, "other-market"): SimpleNamespace(
                yes_bid_cents=40,
                yes_ask_cents=45,
                no_bid_cents=55,
                no_ask_cents=60,
                yes_bid_size=Decimal("5"),
                fee_coefficient=Decimal("0.06"),
            ),
        }
    )

    marks = compute_open_position_marks(
        db,
        provider=provider,
        as_of=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
    )

    assert marks is not None
    assert marks["report_net_liquidation_value"] == Decimal("0")
    assert marks["unscorable_cost"] == Decimal("4")
    assert marks["unscorable_reasons"] == {
        "fetch_error": 1,
        "missing_bid_depth": 1,
        "missing_fee_provenance": 1,
        "unsupported_venue": 1,
    }
    statuses = {row["ticker"]: row for row in marks["rows"]}
    assert statuses["KXFETCH-1"]["report_net_liquidation_value"] == Decimal("0")
    assert statuses["KXDEPTH-1"]["unscorable_reason"] == "missing_bid_depth"
    assert statuses["pm-missing-fee"]["gross_bid_value"] == Decimal("0.8")
    assert statuses["pm-missing-fee"]["unscorable_reason"] == (
        "missing_fee_provenance"
    )
    assert statuses["other-market"]["unscorable_reason"] == "unsupported_venue"


def test_report_liquidation_rejects_canonical_identity_mismatch_without_changing_legacy_mark(
    tmp_path: Path,
):
    db = tmp_path / "paper_trades.db"
    _make_db(
        db,
        kalshi_rows=[],
        pm_rows=[
            ("p", "pm-report-1", "polymarket_us", "yes", 2, 1.00, None, 0),
        ],
    )
    _add_canonical_ids(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE paper_trades SET venue_market_id = '8594' WHERE ticker = 'pm-report-1'"
        )

    legacy_market = SimpleNamespace(
        yes_bid_cents=40,
        yes_ask_cents=45,
        no_bid_cents=55,
        no_ask_cents=60,
    )
    wrong_canonical_market = SimpleNamespace(
        report_venue=Venue.KALSHI.value,
        report_venue_market_id="KXWRONG",
        yes_bid_cents=40,
        yes_bid_size=Decimal("5"),
        yes_bid_levels=((Decimal("40"), Decimal("5")),),
        fee_coefficient=Decimal("0.06"),
    )
    provider = _StaticMarketProvider(
        {
            (Venue.POLYMARKET_US, "pm-report-1"): legacy_market,
            (Venue.POLYMARKET_US, "8594"): wrong_canonical_market,
        }
    )

    marks = compute_open_position_marks(
        db,
        provider=provider,
        as_of=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
    )

    assert marks is not None
    assert marks["marked_value"] == pytest.approx(0.8)
    assert marks["report_net_liquidation_value"] == Decimal("0")
    assert marks["unscorable_reasons"] == {"identity_mismatch": 1}
    assert provider.calls == [
        (Venue.POLYMARKET_US, "pm-report-1"),
        (Venue.POLYMARKET_US, "8594"),
    ]


@pytest.mark.parametrize(
    ("fee_coefficient", "fee_effective_at"),
    [
        (Decimal("0.05"), None),
        (Decimal("0.06"), datetime(2026, 7, 15, tzinfo=timezone.utc)),
    ],
)
def test_report_liquidation_rejects_pm_fee_terms_outside_pinned_schedule(
    tmp_path: Path,
    fee_coefficient: Decimal,
    fee_effective_at: datetime | None,
):
    db = tmp_path / "paper_trades.db"
    _make_db(
        db,
        kalshi_rows=[],
        pm_rows=[
            ("p", "pm-report-1", "polymarket_us", "yes", 2, 1.00, None, 0),
        ],
    )
    _add_canonical_ids(db)
    provider = _StaticMarketProvider(
        {
            (Venue.POLYMARKET_US, "pm-report-1"): SimpleNamespace(
                yes_bid_cents=40,
                yes_ask_cents=45,
                no_bid_cents=55,
                no_ask_cents=60,
                yes_bid_size=Decimal("5"),
                yes_bid_levels=((Decimal("40"), Decimal("5")),),
                fee_coefficient=fee_coefficient,
                fee_effective_at=fee_effective_at,
            ),
        }
    )

    marks = compute_open_position_marks(
        db,
        provider=provider,
        as_of=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
    )

    assert marks is not None
    assert marks["report_net_liquidation_value"] == Decimal("0")
    assert marks["unscorable_reasons"] == {"fee_schedule_mismatch": 1}


def test_public_provider_clears_unverified_kalshi_fee_fields_on_event_failure():
    as_of = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)

    class _FailingEventKalshi:
        def get_market(self, ticker):
            return SimpleNamespace(
                ticker=ticker,
                event_ticker="KXREPORT-26",
                fee_multiplier=Decimal("1"),
                fee_type="quadratic",
                fee_effective_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
                fee_provenance_hash="m" * 64,
            )

        def get_event_fee_terms(self, event_ticker, *, as_of):
            raise RuntimeError("event fee API unavailable")

        def get_market_orderbook(self, ticker, *, depth):
            return BinaryMarketBook(
                venue=Venue.KALSHI,
                venue_market_id=ticker,
                yes_bids=(BookLevel(Decimal("0.40"), Decimal("5")),),
                no_bids=(BookLevel(Decimal("0.60"), Decimal("5")),),
                as_of=as_of,
                raw_payload_hash="b" * 64,
            )

    provider = PublicMarketProvider(as_of=as_of)
    provider._kalshi = _FailingEventKalshi()

    market = provider.get_market(Venue.KALSHI, "KXREPORT-26-A")

    assert market.fee_multiplier is None
    assert market.fee_type is None
    assert market.fee_effective_at is None
    assert market.fee_provenance_hash is None


def test_report_liquidation_rejects_future_kalshi_fee_provenance(tmp_path: Path):
    db = tmp_path / "paper_trades.db"
    _make_db(
        db,
        kalshi_rows=[
            ("k", "KXREPORT-1", "kalshi", "yes", 2, 1.00, None, 0),
        ],
    )
    _add_canonical_ids(db)
    provider = _StaticMarketProvider(
        {
            (Venue.KALSHI, "KXREPORT-1"): SimpleNamespace(
                yes_bid_cents=40,
                yes_bid_size=Decimal("5"),
                yes_bid_levels=((Decimal("40"), Decimal("5")),),
                fee_multiplier=Decimal("1"),
                fee_type="quadratic",
                fee_effective_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
                fee_provenance_hash="f" * 64,
            ),
        }
    )

    marks = compute_open_position_marks(
        db,
        provider=provider,
        as_of=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
    )

    assert marks is not None
    assert marks["report_net_liquidation_value"] == Decimal("0")
    assert marks["unscorable_reasons"] == {"fee_schedule_mismatch": 1}


def test_report_liquidation_rejects_polymarket_slug_as_canonical_id(tmp_path: Path):
    db = tmp_path / "paper_trades.db"
    _make_db(
        db,
        kalshi_rows=[],
        pm_rows=[
            ("p", "pm-report-1", "polymarket_us", "yes", 2, 1.00, None, 0),
        ],
    )
    _add_canonical_ids(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE paper_trades SET venue_market_id = ticker WHERE ticker = 'pm-report-1'"
        )
    provider = _StaticMarketProvider(
        {
            (Venue.POLYMARKET_US, "pm-report-1"): SimpleNamespace(
                yes_bid_cents=40,
                yes_ask_cents=45,
                no_bid_cents=55,
                no_ask_cents=60,
                yes_bid_size=Decimal("5"),
                yes_bid_levels=((Decimal("40"), Decimal("5")),),
                fee_coefficient=Decimal("0.06"),
            ),
        }
    )

    marks = compute_open_position_marks(
        db,
        provider=provider,
        as_of=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
    )

    assert marks is not None
    assert marks["marked_value"] == pytest.approx(0.8)
    assert marks["report_net_liquidation_value"] == Decimal("0")
    assert marks["unscorable_reasons"] == {"invalid_venue_market_id": 1}
    assert provider.calls == [(Venue.POLYMARKET_US, "pm-report-1")]


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
    *,
    contracts: int = 5,
) -> tuple:
    """A polymarket_us open paper_trades row matching _make_db's column order:
    (trade_id, ticker, venue, side, contracts, cost_dollars,
     market_snapshot, resolved)."""
    snap = json.dumps(snapshot) if snapshot is not None else None
    return (trade_id, ticker, "polymarket_us", side, contracts, cost, snap, 0)


def test_corrected_polymarket_books_produce_audited_portfolio_equity(tmp_path: Path):
    """The corrected long book must expose the real >20% portfolio drawdown."""
    from polymarket.normalizer import normalize_polymarket_market

    # Same 11-position side/contract shape as the audited July 10 exposure.
    positions = (
        ("pm-01", "yes", 4, 32),
        ("pm-02", "yes", 5, 46),
        ("pm-03", "yes", 6, 94),
        ("pm-04", "no", 5, 64),
        ("pm-05", "yes", 5, 59),
        ("pm-06", "no", 5, 49),
        ("pm-ga-senate", "yes", 5, 12),
        ("pm-08", "yes", 5, 64),
        ("pm-09", "yes", 5, 59),
        ("pm-10", "no", 5, 39),
        ("pm-11", "yes", 5, 59),
    )
    db = tmp_path / "paper_trades.db"
    _make_db(
        db,
        pm_rows=[
            _poly_row(
                f"trade-{index}", ticker, side, 1.0, None, contracts=contracts
            )
            for index, (ticker, side, contracts, _mark) in enumerate(positions)
        ],
        kalshi_rows=[
            (f"kalshi-{index}", f"KXAUDIT-{index}", "kalshi", "yes", 1, 0.03, None, 0)
            for index in range(4)
        ],
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE bot_state SET value = '8.76' WHERE key = 'notional_bankroll'"
        )

    books = {}
    for ticker, side, _contracts, mark in positions:
        if side == "yes":
            best_bid, best_ask = mark, mark + 1
        else:
            best_ask, best_bid = 100 - mark, 99 - mark
        books[ticker] = normalize_polymarket_market(
            {
                "slug": ticker,
                "title": ticker,
                "status": "open",
                "bestBidQuote": {"value": best_bid / 100},
                "bestAskQuote": {"value": best_ask / 100},
                "marketSides": [
                    {"long": True, "quote": best_ask / 100},
                    {"long": False, "quote": (100 - best_bid) / 100},
                ],
                # Deliberately reversed positional arrays: the long book wins.
                "outcomes": '["No","Yes"]',
                "outcomePrices": '["0.99","0.01"]',
            }
        )

    class _FakePoly:
        def get_market(self, ticker):
            return books[ticker]

    class _FakeKalshi:
        def get_market(self, ticker):
            return SimpleNamespace(
                yes_bid_cents=2,
                yes_ask_cents=4,
                no_bid_cents=96,
                no_ask_cents=98,
                last_price_cents=3,
            )

    with patch("polymarket.public_client.PolymarketPublicClient", _FakePoly), patch(
        "kalshi.rest_client.KalshiRestClient", _FakeKalshi
    ):
        marks = compute_open_position_marks(db)

    assert marks is not None
    assert marks["priced_count"] == 15
    assert marks["unpriced_count"] == 0
    pm_rows = [row for row in marks["rows"] if row["venue"] == "polymarket_us"]
    kalshi_rows = [row for row in marks["rows"] if row["venue"] == "kalshi"]
    assert len(pm_rows) == 11
    assert len(kalshi_rows) == 4
    assert sum(row["value"] for row in pm_rows) == pytest.approx(29.47)
    assert sum(row["value"] for row in kalshi_rows) == pytest.approx(0.12)
    assert marks["marked_value"] == pytest.approx(29.59)
    equity = marks["bankroll"] + marks["marked_value"]
    assert equity == pytest.approx(38.35)
    assert (50.0 - equity) / 50.0 == pytest.approx(0.233)


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
    snap = {
        "yes_ask_cents": 60,
        "no_ask_cents": 55,
        "price_method": "pm_long_book_v1",
    }
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


def test_unversioned_polymarket_snapshot_is_not_used(tmp_path: Path):
    """Legacy snapshots have unknown side orientation and must fail closed."""
    db = tmp_path / "paper_trades.db"
    snapshot = {"yes_ask_cents": 88, "no_ask_cents": 13}
    _make_db(db, pm_rows=[_poly_row("p", "pm-legacy", "yes", 3.00, snapshot)])

    class _FakePoly:
        def get_market(self, ticker):
            raise RuntimeError("market not found")

    with patch("polymarket.public_client.PolymarketPublicClient", _FakePoly), patch(
        "kalshi.rest_client.KalshiRestClient", _StubKalshiAllFail
    ):
        marks = compute_open_position_marks(db)

    assert marks is not None
    assert marks["snapshot_fallback_count"] == 0
    assert marks["priced_count"] == 0
    assert marks["marked_value"] == 0.0


def test_versioned_polymarket_snapshot_remains_usable(tmp_path: Path):
    """Audited snapshot methods remain available when the live feed is absent."""
    db = tmp_path / "paper_trades.db"
    safe_methods = (
        "pm_long_book_v1",
        "pm_named_sides_v1",
        "pm_named_outcomes_v1",
    )
    rows = [
        _poly_row(
            f"p-{index}",
            f"pm-safe-{index}",
            "yes",
            0.65,
            {
                "yes_ask_cents": 13,
                "no_ask_cents": 88,
                "price_method": method,
            },
        )
        for index, method in enumerate(safe_methods)
    ]
    _make_db(db, pm_rows=rows)

    class _FakePoly:
        def get_market(self, ticker):
            raise RuntimeError("market not found")

    with patch("polymarket.public_client.PolymarketPublicClient", _FakePoly), patch(
        "kalshi.rest_client.KalshiRestClient", _StubKalshiAllFail
    ):
        marks = compute_open_position_marks(db)

    assert marks is not None
    assert marks["snapshot_fallback_count"] == len(safe_methods)
    assert marks["priced_count"] == len(safe_methods)


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
