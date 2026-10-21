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


def _listed(slug: str, yes: str = "0.55", no: str = "0.46") -> dict:
    """List payload whose quotes sit inside the 36c-90c prefilter."""
    return {
        "slug": slug,
        "status": "MARKET_STATUS_OPEN",
        "title": "Approve",
        "description": "rules text",
        "closeTime": "2026-10-25T00:00:00Z",
        "volume": "12",
        "marketSides": [
            {"long": True, "quote": {"value": yes}},
            {"long": False, "quote": {"value": no}},
        ],
    }


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


def _quote(
    series: str,
    ticker: str,
    yes_ask: int,
    no_ask: int | None,
    venue: str = "kalshi",
    *,
    title: str | None = None,
    rules: str = "rules",
    close_time: str = "2026-10-20T00:00:00Z",
) -> dict:
    return {
        "ticker": ticker,
        "series": series,
        "status": "active",
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "title": title if title is not None else series,
        "rules": rules,
        "close_time": close_time,
        "venue": venue,
    }


def test_cross_venue_matches_the_same_outcome_and_ignores_a_one_cent_tail(tmp_path) -> None:
    """Same close, outcome, and source pair. A different outcome is not the pair."""
    close = "2026-11-06T22:00:00Z"
    markets = [
        _quote(
            "KXTRUMPAPPROVE",
            "KXTRUMPAPPROVE-E38.5",
            40,
            70,
            title="exactly 38.5",
            rules="RCP average",
            close_time=close,
        ),
        _quote(
            "KXTRUMPAPPROVE",
            "KXTRUMPAPPROVE-TAIL",
            1,
            99,
            title="exactly 39.2",
            rules="RCP average",
            close_time=close,
        ),
        _quote(
            "PMTRUMPAPPROVE",
            "parc-38pt5",
            70,
            40,
            "polymarket_us",
            title="exactly 38.5",
            rules="RCP average",
            close_time=close,
        ),
    ]
    rows = write_cross_venue(markets, path=tmp_path / "cross.jsonl", now=NOW)
    trump_rows = [row for row in rows if row["event"] == "PMTRUMPAPPROVE"]
    assert len(trump_rows) == 1
    trump = trump_rows[0]
    assert trump["match"] == "matched"
    assert trump["kalshi_ticker"] == "KXTRUMPAPPROVE-E38.5"
    assert trump["polymarket_ticker"] == "parc-38pt5"
    assert trump["kalshi_yes_cents"] == 40
    assert trump["polymarket_yes_cents"] == 70
    assert trump["kalshi_no_cents"] == 70
    assert trump["polymarket_no_cents"] == 40
    assert trump["pair_clears"] is True
    assert Decimal(trump["fee_net_gap"]) >= Decimal("0.04")
    assert trump["kalshi_ticker"] != "KXTRUMPAPPROVE-TAIL"
    missing = cross_venue_report([markets[0], markets[1]], now=NOW)
    lone = next(row for row in missing if row["event"] == "PMTRUMPAPPROVE")
    assert lone["match"] == "no match"
    assert lone["kalshi_yes_cents"] is None
    assert lone["polymarket_yes_cents"] is None
    assert lone["kalshi_ticker"] == ""
    assert lone["polymarket_ticker"] == ""
    assert lone["fee_net_gap"] is None
    assert lone["pair_clears"] is False
    logged = json.loads((tmp_path / "cross.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert logged["kalshi_ticker"] == "KXTRUMPAPPROVE-E38.5"
    assert "KXTRUMPAPPROVE-TAIL" not in (tmp_path / "cross.jsonl").read_text(encoding="utf-8")


def test_cross_venue_rejects_a_different_close_source_or_two_matches() -> None:
    """A 1c ask is not a match when the contract identity differs or is ambiguous."""
    close = "2026-11-06T22:00:00Z"
    other = "2026-10-22T16:59:00Z"
    kalshi = _quote(
        "KXTRUMPAPPROVE",
        "KXTRUMPAPPROVE-E38.5",
        1,
        99,
        title="exactly 38.5",
        rules="RCP average",
        close_time=other,
    )
    poly = _quote(
        "PMTRUMPAPPROVE",
        "parc-38pt5",
        70,
        40,
        "polymarket_us",
        title="exactly 38.5",
        rules="RCP average",
        close_time=close,
    )
    mismatched = cross_venue_report([kalshi, poly], now=NOW)
    row = next(item for item in mismatched if item["event"] == "PMTRUMPAPPROVE")
    assert row["match"] == "no match"
    assert row["kalshi_yes_cents"] is None
    assert row["fee_net_gap"] is None
    poly["close_time"] = other
    poly["rules"] = "a different print"
    sourced = cross_venue_report([kalshi, poly], now=NOW)
    source_row = next(item for item in sourced if item["event"] == "PMTRUMPAPPROVE")
    assert source_row["match"] == "no match"
    assert source_row["kalshi_yes_cents"] is None
    twin = dict(poly)
    twin["rules"] = "RCP average"
    twin["ticker"] = "parc-38pt5-b"
    ambiguous = cross_venue_report([kalshi, poly | {"rules": "RCP average"}, twin], now=NOW)
    ambiguous_row = next(item for item in ambiguous if item["event"] == "PMTRUMPAPPROVE")
    assert ambiguous_row["match"] == "no match"
    assert ambiguous_row["kalshi_yes_cents"] is None
    assert ambiguous_row["polymarket_ticker"] == ""


def test_cross_venue_derives_a_missing_no_ask_from_the_yes_bid() -> None:
    """The report reads 100 minus the YES bid. The paper gate still requires a printed ask."""
    close = "2026-11-06T22:00:00Z"
    kalshi = _quote(
        "KXTRUMPAPPROVE",
        "KXTRUMPAPPROVE-E38.5",
        40,
        None,
        title="exactly 38.5",
        rules="RCP average",
        close_time=close,
    )
    kalshi["yes_bid"] = 65
    poly = _quote(
        "PMTRUMPAPPROVE",
        "parc-38pt5",
        70,
        40,
        "polymarket_us",
        title="exactly 38.5",
        rules="RCP average",
        close_time=close,
    )
    rows = cross_venue_report([kalshi, poly], now=NOW)
    trump = next(row for row in rows if row["event"] == "PMTRUMPAPPROVE")
    assert trump["match"] == "matched"
    assert trump["kalshi_yes_cents"] == 40
    assert trump["kalshi_no_cents"] == 35
    assert trump["polymarket_no_cents"] == 40


def test_load_reads_books_and_lets_an_order_block_escape() -> None:
    book = _book("0.60", "0.30")
    calls: list[str] = []

    class Client:
        def list_event_markets(self, slug: str):
            calls.append("list_event_markets")
            assert slug == "trmpapprv-wk-2026-10-23"
            return (_listed("trmpapprv-yes"),)

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


class _BookError(Exception):
    def __init__(self, status: int | None) -> None:
        super().__init__("book")
        self.response = None if status is None else SimpleNamespace(status_code=status)


def test_book_failure_logs_status_and_does_not_score_a_missing_book(caplog) -> None:
    """404 is not a rate-limit skip. A 429 after the shared retry is book_rate_limited."""
    entry = SimpleNamespace(series="PMTRUMPAPPROVE", venue_market="trmpapprv-wk-2026-10-23")
    book = _book("0.60", "0.30")

    class Missing:
        def list_event_markets(self, _slug: str):
            return (_listed("parc-missing?token=secret"),)

        def get_market_book(self, _slug: str):
            raise _BookError(404)

    with caplog.at_level("WARNING", logger="polymarket_paper"):
        assert load_polymarket_paper_markets(Missing(), [entry]) == []
    text = "\n".join(caplog.messages)
    assert "status=404" in text
    assert "endpoint=/v1/markets/parc-missing/book" in text
    assert "token" not in text
    assert "secret" not in text
    assert "book_rate_limited" not in text
    caplog.clear()

    class Limited:
        def __init__(self) -> None:
            self.calls = 0

        def list_event_markets(self, _slug: str):
            return (_listed("parc-open"),)

        def get_market_book(self, _slug: str):
            self.calls += 1
            raise _BookError(429)

    limited = Limited()
    with caplog.at_level("INFO", logger="polymarket_paper"):
        assert load_polymarket_paper_markets(limited, [entry]) == []
    assert limited.calls == 1
    assert "status=429" in "\n".join(caplog.messages)
    assert "reason=book_rate_limited" in "\n".join(caplog.messages)
    assert "series=PMTRUMPAPPROVE ok=0 fail=1" in "\n".join(caplog.messages)

    class Down:
        def list_event_markets(self, _slug: str):
            return (_listed("parc-down"), _listed("parc-up"))

        def get_market_book(self, slug: str):
            if slug == "parc-down":
                raise _BookError(503)
            return book

    caplog.clear()
    with caplog.at_level("INFO", logger="polymarket_paper"):
        loaded = load_polymarket_paper_markets(Down(), [entry])
    assert [row["ticker"] for row in loaded] == ["parc-up"]
    assert "series=PMTRUMPAPPROVE ok=1 fail=1" in "\n".join(caplog.messages)
    assert caplog.messages.count("polymarket paper book failed series=PMTRUMPAPPROVE status=503 endpoint=/v1/markets/parc-down/book error_type=_BookError") == 1
    assert "reason=book_rate_limited" not in "\n".join(caplog.messages)

    class Order:
        def list_event_markets(self, _slug: str):
            return (_listed("parc-open"),)

        def get_market_book(self, _slug: str):
            raise PolymarketOrderBlocked("blocked")

    with pytest.raises(PolymarketOrderBlocked):
        load_polymarket_paper_markets(Order(), [entry])


def test_prefilter_reads_books_only_inside_the_ask_band(caplog) -> None:
    """36c-90c passes. Both sides outside the band, a sports prefix, or no list ask does not."""
    calls: list[str] = []

    class Client:
        def list_event_markets(self, _slug: str):
            return (
                _listed("parc-wide", yes="0.94", no="0.07"),
                _listed("parc-open"),
                {"slug": "parc-bare", "status": "MARKET_STATUS_OPEN"},
                {
                    "slug": "parc-one-sided",
                    "status": "MARKET_STATUS_OPEN",
                    "bestBidQuote": None,
                    "bestAskQuote": {"value": "0.50"},
                    "marketSides": [
                        {"long": True, "quote": {"value": "0.50"}},
                        {"long": False, "quote": None},
                    ],
                },
            )

        def get_market_book(self, slug: str):
            calls.append(slug)
            return _book("0.60", "0.30")

    entry = SimpleNamespace(series="PMTRUMPAPPROVE", venue_market="trmpapprv-wk-2026-10-23")
    with caplog.at_level("INFO", logger="polymarket_paper"):
        loaded = load_polymarket_paper_markets(Client(), [entry])
    assert calls == ["parc-open", "parc-one-sided"]
    assert [row["ticker"] for row in loaded] == ["parc-open", "parc-one-sided"]
    text = "\n".join(caplog.messages)
    assert "reason=near_certain_ask" in text
    assert "reason=no_list_ask" in text
    caplog.clear()

    class Sports:
        def list_event_markets(self, _slug: str):
            return (_listed("kxnfl-game"),)

        def get_market_book(self, slug: str):
            raise AssertionError(slug)

    with caplog.at_level("INFO", logger="polymarket_paper"):
        assert load_polymarket_paper_markets(
            Sports(),
            [SimpleNamespace(series="KXNFL", venue_market="kxnfl-week")],
        ) == []
    assert "reason=blocklist" in "\n".join(caplog.messages)


def test_partial_book_is_not_scored(caplog) -> None:
    empty = BinaryMarketBook(
        venue=Venue.POLYMARKET_US,
        venue_market_id="parc-empty",
        yes_bids=(),
        no_bids=(),
        as_of=NOW,
        raw_payload_hash=_HASH,
    )

    class Client:
        def list_event_markets(self, _slug: str):
            return (_listed("parc-empty"),)

        def get_market_book(self, _slug: str):
            return empty

    entry = SimpleNamespace(series="PMTRUMPAPPROVE", venue_market="trmpapprv-wk-2026-10-23")
    with caplog.at_level("INFO", logger="polymarket_paper"):
        assert load_polymarket_paper_markets(Client(), [entry]) == []
    assert "reason=partial_book" in "\n".join(caplog.messages)


def test_shared_limiter_honors_retry_after_and_does_not_retry_404(monkeypatch) -> None:
    """https://docs.polymarket.us/api-reference/rate-limits.md is 25/s. Retry-After is capped at 5s."""
    import polymarket.rate_limit as rl

    sleeps: list[float] = []
    clock = {"t": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(rl.time, "sleep", lambda seconds: sleeps.append(seconds))

    class Resp:
        def __init__(self, status: int, retry: str | None = None) -> None:
            self.status_code = status
            self.headers = {} if retry is None else {"Retry-After": retry}

    rl._next = 0.0
    calls: list[int] = []

    def send() -> Resp:
        calls.append(1)
        if len(calls) == 1:
            return Resp(429, "2")
        return Resp(200)

    assert rl.call_once(send).status_code == 200
    assert sleeps[0] == 2.0
    assert len(sleeps) == 2
    assert len(calls) == 2
    assert rl._INTERVAL == 1.0 / 25

    rl._next = 0.0
    sleeps.clear()
    once: list[int] = []

    def missing() -> Resp:
        once.append(1)
        return Resp(404)

    assert rl.call_once(missing).status_code == 404
    assert once == [1]
    assert sleeps == []

    rl._next = 0.0
    sleeps.clear()
    seen = {"n": 0}

    def hostile() -> Resp:
        seen["n"] += 1
        if seen["n"] == 1:
            return Resp(429, "90")
        return Resp(200)

    assert rl.call_once(hostile).status_code == 200
    assert sleeps[0] == 5.0

    rl._next = 0.0
    sleeps.clear()
    seen["n"] = 0

    def bare() -> Resp:
        seen["n"] += 1
        if seen["n"] == 1:
            return Resp(503)
        return Resp(200)

    assert rl.call_once(bare).status_code == 200
    assert sleeps[0] == 1.0


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
            return (_listed(market_slug),)

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
    assert trump["match"] == "no match"
    assert trump["kalshi_yes_cents"] is None
    assert trump["polymarket_yes_cents"] is None
    assert trump["kalshi_no_cents"] is None
    assert trump["polymarket_no_cents"] is None
    assert trump["fee_net_gap"] is None


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
