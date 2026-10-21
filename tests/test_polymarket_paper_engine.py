"""Polymarket paper uses the same queue, fee, and report path. No order call."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from types import SimpleNamespace

import pytest

from polymarket.paper_views import book_asks, load_polymarket_paper_markets, paper_view
from polymarket.read_only import PolymarketOrderBlocked
from scripts.grok_paper_sidecar import _run_scan
from scripts.pnl_report import _render
from trading.orderbook import BinaryMarketBook, BookLevel
from trading.venue import Venue
from utils.event_news_grok_edge import (
    PAPER_QUEUE_CONTRACTS,
    _fee_net,
    cross_venue_report,
    paper_queue_decision,
    run_paper_edge_scan,
    write_cross_venue,
)
from utils.event_news_grok_veto import XAI_API_KEY_ENV

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
_HASH = "a" * 64


def _book(yes_bid: str, no_bid: str) -> BinaryMarketBook:
    return BinaryMarketBook(
        venue=Venue.POLYMARKET_US,
        venue_market_id="trmpapprv-yes",
        yes_bids=(BookLevel(price=Decimal(yes_bid), quantity=Decimal("10")),),
        no_bids=(BookLevel(price=Decimal(no_bid), quantity=Decimal("10")),),
        as_of=NOW,
        raw_payload_hash=_HASH,
    )


def test_book_asks_use_the_opposing_bid() -> None:
    yes_ask, no_ask = book_asks(_book("0.55", "0.40"))
    assert yes_ask == 60
    assert no_ask == 45


def test_polymarket_queue_keeps_the_shared_guards_and_its_own_fee() -> None:
    """35c, 54c YES, 91c, and 9 contracts skip. One contract at 50c can disagree."""
    assert paper_queue_decision(
        probability=Decimal("0.90"), ask_cents=35, side="no", contracts=1, venue="polymarket_us"
    )[0] == "ask_band"
    assert paper_queue_decision(
        probability=Decimal("0.90"), ask_cents=54, side="yes", contracts=1, venue="polymarket_us"
    )[0] == "yes_floor"
    assert paper_queue_decision(
        probability=Decimal("0.99"), ask_cents=91, side="no", contracts=1, venue="polymarket_us"
    ) == ("near_certain_ask", None)
    assert paper_queue_decision(
        probability=Decimal("0.99"), ask_cents=91, side="yes", contracts=9, venue="polymarket_us"
    ) == ("size_cap", None)
    kalshi_91 = paper_queue_decision(
        probability=Decimal("0.99"), ask_cents=91, side="no", contracts=1, edge_min=Decimal("0.04")
    )[0]
    assert kalshi_91 != "near_certain_ask"
    minimum = Decimal("0.04")
    kalshi = paper_queue_decision(
        probability=Decimal("0.558"),
        ask_cents=50,
        side="no",
        contracts=1,
        edge_min=minimum,
    )
    polymarket = paper_queue_decision(
        probability=Decimal("0.558"),
        ask_cents=50,
        side="no",
        contracts=1,
        edge_min=minimum,
        venue="polymarket_us",
    )
    assert kalshi[0] == "queue"
    assert polymarket[0] == "edge"


def _quote(series: str, ticker: str, yes_ask: int, no_ask: int, venue: str = "kalshi") -> dict:
    return {
        "ticker": ticker,
        "series": series,
        "status": "active",
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "title": series,
        "rules": "rules",
        "close_time": "2026-10-20T00:00:00Z",
        "venue": venue,
    }


def test_cross_venue_logs_prices_and_the_fee_net_gap(tmp_path) -> None:
    """YES 40 plus the other venue's NO 40 clears 0.04 after both taker fees."""
    markets = [
        _quote("KXTRUMPAPPROVE", "KXTRUMPAPPROVE-A", 40, 70),
        _quote("PMTRUMPAPPROVE", "trmpapprv-yes", 70, 40, "polymarket_us"),
    ]
    rows = write_cross_venue(markets, path=tmp_path / "cross.jsonl", now=NOW)
    trump = next(row for row in rows if row["event"] == "PMTRUMPAPPROVE")
    assert trump["kalshi_yes_cents"] == 40
    assert trump["polymarket_yes_cents"] == 70
    assert trump["pair_clears"] is True
    assert Decimal(trump["fee_net_gap"]) >= Decimal("0.04")
    missing = cross_venue_report([markets[0]], now=NOW)
    lone = next(row for row in missing if row["event"] == "PMTRUMPAPPROVE")
    assert lone["kalshi_yes_cents"] == 40
    assert lone["polymarket_yes_cents"] is None
    assert lone["pair_clears"] is False
    logged = json.loads((tmp_path / "cross.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert logged["event"] == rows[0]["event"]


def test_load_reads_books_and_lets_an_order_block_escape() -> None:
    book = _book("0.60", "0.30")
    calls: list[str] = []

    class Client:
        def list_event_markets(self, slug: str):
            calls.append("list_event_markets")
            assert slug == "trmpapprv-wk-2026-10-23"
            return (
                {
                    "slug": "trmpapprv-yes",
                    "status": "MARKET_STATUS_OPEN",
                    "title": "Approve",
                    "description": "rules text",
                    "closeTime": "2026-10-25T00:00:00Z",
                    "volume": "12",
                },
            )

        def get_market_book(self, slug: str):
            calls.append("get_market_book")
            assert slug == "trmpapprv-yes"
            return book

        def place_limit_order(self, *_args, **_kwargs):
            raise AssertionError("order")

    entry = SimpleNamespace(series="PMTRUMPAPPROVE", venue_market="trmpapprv-wk-2026-10-23")
    loaded = load_polymarket_paper_markets(Client(), [entry])
    assert calls == ["list_event_markets", "get_market_book"]
    assert loaded[0]["venue"] == "polymarket_us"
    assert loaded[0]["status"] == "active"
    assert loaded[0]["ticker"] == "trmpapprv-yes"
    assert loaded[0]["yes_ask"] == 70
    assert loaded[0]["no_ask"] == 40

    class Blocked:
        def list_event_markets(self, _slug: str):
            raise PolymarketOrderBlocked("blocked")

    with pytest.raises(PolymarketOrderBlocked):
        load_polymarket_paper_markets(Blocked(), [entry])
    closed = paper_view({"status": "MARKET_STATUS_CLOSED", "slug": "trmpapprv-yes"}, "PMTRUMPAPPROVE", book)
    assert closed is None


def test_two_cached_scans_queue_a_polymarket_row_without_a_paid_call(tmp_path) -> None:
    market = _quote("PMTRUMPAPPROVE", "trmpapprv-yes", 64, 40, "polymarket_us")
    cache = {
        "tickers": {
            "trmpapprv-yes": {
                "ts": "2026-10-17T11:00:00Z",
                "prob": "0.80",
                "confidence": "0.80",
                "sources": ["https://example.test/cached"],
                "yes_ask": 64,
                "no_ask": 40,
                "post_id": "post-1",
                "series": "PMTRUMPAPPROVE",
                "summary": "cached",
                "scans": [
                    {"prob": "0.80", "post_id": "post-1"},
                    {"prob": "0.80", "post_id": "post-1"},
                ],
            }
        }
    }
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    def post(*_args, **_kwargs):
        raise AssertionError("paid call")

    counts = run_paper_edge_scan(
        [market],
        live=False,
        allowlist=("PMTRUMPAPPROVE",),
        environ={XAI_API_KEY_ENV: "test-key"},
        post=post,
        now=NOW,
        post_lookup=lambda: "post-1",
        call_log_path=tmp_path / "calls.jsonl",
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=cache_path,
        screen_log_path=tmp_path / "screen.jsonl",
        db_path=tmp_path / "grok_paper.db",
        raw_dir=tmp_path / "raw",
    )
    assert counts["queued"] == 1
    assert counts["cache_hits"] == 1
    assert not (tmp_path / "calls.jsonl").exists()
    row = json.loads((tmp_path / "candidates.jsonl").read_text(encoding="utf-8"))
    assert row["venue"] == "polymarket_us"
    assert row["queued"] is True
    poly = _fee_net(Decimal("0.80"), 64, PAPER_QUEUE_CONTRACTS, "polymarket_us")
    kalshi = _fee_net(Decimal("0.80"), 64, PAPER_QUEUE_CONTRACTS, "kalshi")
    assert poly is not None and kalshi is not None
    assert poly[1] != kalshi[1]
    assert row["fee_cents"] == int((poly[1] * Decimal(100)).to_integral_value(rounding=ROUND_CEILING))
    stored = sqlite3.connect(tmp_path / "grok_paper.db").execute(
        "SELECT venue, fee_cents FROM paper_candidates"
    ).fetchone()
    assert stored == ("polymarket_us", row["fee_cents"])


def test_sidecar_scan_does_not_ask_kalshi_for_polymarket_names(monkeypatch, tmp_path) -> None:
    seen: list[str] = []
    scans: list[tuple] = []

    def scan(markets, **kwargs):
        tickers = []
        for market in markets:
            if isinstance(market, dict):
                tickers.append(market.get("ticker"))
            else:
                tickers.append(getattr(market, "ticker", None))
        scans.append((tickers, tuple(kwargs["allowlist"]), kwargs["live"], kwargs["max_markets"]))
        return {
            "examined": 0,
            "queued": 0,
            "skipped": 0,
            "screen_skipped": 0,
            "cache_hits": 0,
            "grounded_calls": 0,
        }

    monkeypatch.setattr("utils.event_news_grok_edge.run_paper_edge_scan", scan)

    class Rest:
        def get_markets(self, status="open", series_ticker="", limit=20):
            assert status == "open"
            assert limit == 20
            seen.append(series_ticker)
            return (
                [
                    _quote(series_ticker, f"{series_ticker}-A", 40 if series_ticker == "KXTRUMPAPPROVE" else 55, 70)
                ],
                None,
            )

    class Client:
        def __init__(self) -> None:
            self.slugs: list[str] = []

        def list_event_markets(self, slug: str):
            self.slugs.append(slug)
            market_slug = "trmpapprv-yes" if slug.startswith("trmp") else "hormuz-yes"
            return (
                {
                    "slug": market_slug,
                    "status": "MARKET_STATUS_OPEN",
                    "title": slug,
                    "description": "rules",
                    "closeTime": "2026-10-25T00:00:00Z",
                    "volume": "20",
                },
            )

        def get_market_book(self, _slug: str):
            return _book("0.60", "0.30")

        def place_limit_order(self, *_args, **_kwargs):
            raise AssertionError("order")

    client = Client()
    _run_scan(
        Rest(),
        positions=[],
        now=NOW,
        polymarket_client=client,
        cross_venue_path=tmp_path / "cross.jsonl",
    )
    assert "PMTRUMPAPPROVE" not in seen
    assert "PMHORMUZ" not in seen
    assert seen
    assert all(name == name.upper() for name in seen)
    assert client.slugs == ["trmpapprv-wk-2026-10-23", "hormuz-normal"]
    assert len(scans) == 2
    assert "PMTRUMPAPPROVE" not in scans[0][1]
    assert scans[1][1] == ("PMTRUMPAPPROVE", "PMHORMUZ")
    assert scans[0][2] is False and scans[1][2] is False
    logged = [
        json.loads(line)
        for line in (tmp_path / "cross.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    trump = next(row for row in logged if row["event"] == "PMTRUMPAPPROVE")
    assert trump["kalshi_yes_cents"] == 40
    assert trump["polymarket_yes_cents"] == 70


def test_pnl_venue_column_does_not_change_the_live_total() -> None:
    text = _render(
        stamp="20261021",
        trades=[("KXTRUMPSAY-1", "yes", "1", "64", "0.01", "1", "0.35")],
        series_totals={"KXTRUMPSAY": Decimal("2.7509")},
        running=Decimal("2.7509"),
        mismatches=[],
        paper_rows=[
            {
                "ticker": "trmpapprv-yes",
                "side": "yes",
                "contracts": 1,
                "price_cents": 40,
                "resolved": 0,
                "pnl_dollars": "",
                "series_ticker": "PMTRUMPAPPROVE",
                "signal_type": "grok",
                "venue": "polymarket_us",
            }
        ],
        paper_note=None,
        backfilled=[],
    )
    assert "running_total_dollars: 2.7509" in text
    assert "| kalshi | KXTRUMPSAY | 2.7509 |" in text
    assert "| polymarket_us | trmpapprv-yes |" in text
    assert "Not included in the live fee-net total." in text
    assert "| venue | series | fee_net_dollars |" in text
