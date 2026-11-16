"""Maker-versus-taker paper log. The live order payload stays the same."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from analysis import SignalAnalysis
from kalshi import KalshiMarket
from tasks.kalshi_execution_liquidity import fetch_kalshi_execution_liquidity
from tests.test_event_news_grok_flags import _analysis, _executor, _quiet_live_side_effects
from trading.execution_terms import FinalExecutionTerms
from trading.maker_taker_paper import (
    PUBLIC_TRADES_URL,
    fetch_public_trades,
    record_veto_allowed_attempt,
    score_candidate_file,
    score_rows,
)
from trading.orderbook import BinaryMarketBook, BookLevel
from trading.venue import Venue


class _Reader:
    def __init__(self, book: BinaryMarketBook) -> None:
        self.book = book
        self.calls: list[tuple[str, int]] = []

    def get_market_orderbook(self, ticker: str, *, depth: int = 100) -> BinaryMarketBook:
        self.calls.append((ticker, depth))
        return self.book


def _market(ticker: str, series: str) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title="paper",
        yes_bid=40,
        yes_ask=60,
        yes_price=50,
        volume=10,
        open_interest=10,
        close_time="2026-11-21T00:00:00Z",
        status="active",
        series_ticker=series,
        yes_bid_cents=40,
        yes_ask_cents=60,
        no_bid_cents=40,
        no_ask_cents=60,
        price_available=True,
    )


def _signal(market: KalshiMarket) -> SignalAnalysis:
    return SignalAnalysis(
        news_item=None,
        market=market,
        estimated_probability=0.8,
        executed_price_cents=60,
        edge=0.1,
        side="yes",
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=4.8,
        reasoning="test",
        confidence=0.9,
        match_score=0.8,
    )


def _book(ticker: str) -> BinaryMarketBook:
    return BinaryMarketBook(
        venue=Venue.KALSHI,
        venue_market_id=ticker,
        yes_bids=(
            BookLevel(Decimal("0.59"), Decimal("15")),
            BookLevel(Decimal("0.58"), Decimal("40")),
        ),
        no_bids=(BookLevel(Decimal("0.40"), Decimal("20")),),
        as_of=datetime(2026, 11, 15, 15, tzinfo=UTC),
        raw_payload_hash="a" * 64,
    )


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts": "2026-11-15T15:00:00.000000Z",
        "ticker": "KXFEDMENTION-26NOV21-T1",
        "series": "KXFEDMENTION",
        "side": "no",
        "best_ask_cents": 40,
        "ask_minus_1c": 39,
        "resting_size_at_ask_minus_1c": "4",
        "queue_ahead": "4",
        "contracts": 8,
        "fee_type": "quadratic",
        "fee_multiplier": "1",
    }
    row.update(overrides)
    return row


def _trade(ts: str, *, yes: str, count: str, ticker: str, no: str | None = None) -> dict[str, str]:
    return {
        "ticker": ticker,
        "created_time": ts,
        "yes_price_dollars": yes,
        "no_price_dollars": no if no is not None else format(Decimal("1") - Decimal(yes), "f"),
        "count_fp": count,
    }


@pytest.mark.asyncio
async def test_order_payload_is_identical_with_the_paper_log_on_off_or_raising(
    monkeypatch, tmp_path
):
    payloads = []
    for mode in ("on", "off", "raise"):
        ids = iter(range(1, 6))
        monkeypatch.setattr(
            "trading.executor.uuid.uuid4",
            lambda: SimpleNamespace(hex=f"{next(ids):032x}"),
        )
        monkeypatch.delenv("EVENT_NEWS_MAKER_FIRST", raising=False)
        executor = _executor(monkeypatch, tmp_path / mode)
        analysis = _analysis()
        analysis.executed_price_cents = 89
        if mode == "on":
            monkeypatch.setattr(
                "trading.maker_taker_paper.MAKER_TAKER_PAPER_PATH",
                tmp_path / mode / "maker_taker_paper.jsonl",
            )
            analysis._maker_taker_orderbook = _book("KXTRUMPSAY-26OCT19-GOLF")
        elif mode == "off":
            monkeypatch.setattr(
                "trading.maker_taker_paper.record_veto_allowed_attempt",
                lambda *args, **kwargs: None,
            )
        else:
            def _boom(*args, **kwargs):
                raise RuntimeError("paper log failed")

            monkeypatch.setattr(
                "trading.maker_taker_paper.record_veto_allowed_attempt",
                _boom,
            )
        executor._rest.place_limit_order.return_value = SimpleNamespace(
            error=None, order_id="ord-1", status="executed", filled=8, http_status=201
        )
        journal, fill_log = _quiet_live_side_effects()
        with (
            patch(
                "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
                return_value=True,
            ),
            journal,
            fill_log,
        ):
            order_id = await executor._execute_live(
                analysis,
                execution_plan=FinalExecutionTerms(89, 8, 7.12),
            )
        assert order_id == "ord-1"
        payloads.append(executor._rest.place_limit_order.call_args.kwargs)
        executor._rest.place_limit_order.assert_called_once()
    assert payloads[0] == payloads[1] == payloads[2]
    assert payloads[0] == {
        "ticker": "KXTRUMPSAY-26OCT19-GOLF",
        "side": "yes",
        "count": 8,
        "limit_price": 89,
        "client_order_id": f"{1:032x}",
    }
    logged = json.loads((tmp_path / "on" / "maker_taker_paper.jsonl").read_text(encoding="utf-8"))
    assert logged["ticker"] == "KXTRUMPSAY-26OCT19-GOLF"
    assert logged["best_ask_cents"] == 60
    assert logged["ask_minus_1c"] == 59
    assert logged["queue_ahead"] == "15"


def test_a_logging_exception_does_not_propagate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "trading.maker_taker_paper._append_jsonl",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    market = _market("KXTRUTHSOCIAL-26NOV21-T40", "KXTRUTHSOCIAL")
    record_veto_allowed_attempt(_signal(market), contracts=8, path=tmp_path / "row.jsonl")
    assert not (tmp_path / "row.jsonl").exists()


@pytest.mark.asyncio
async def test_queue_comes_from_the_orderbook_already_fetched(tmp_path):
    ticker = "KXTRUTHSOCIAL-26NOV21-T40"
    reader = _Reader(_book(ticker))
    analysis = _signal(_market(ticker, "KXTRUTHSOCIAL"))
    await fetch_kalshi_execution_liquidity(reader, analysis)
    record_veto_allowed_attempt(analysis, contracts=8, path=tmp_path / "row.jsonl", now=datetime(2026, 11, 15, 15, tzinfo=UTC))
    assert reader.calls == [(ticker, 100)]
    row = json.loads((tmp_path / "row.jsonl").read_text(encoding="utf-8"))
    assert row["best_ask_cents"] == 60
    assert row["ask_minus_1c"] == 59
    assert row["resting_size_at_ask_minus_1c"] == "15"
    assert row["queue_ahead"] == "15"
    assert row["contracts"] == 8
    assert row["fee_type"] == "quadratic"
    assert Decimal(row["taker_fee"]) > 0
    assert Decimal(row["maker_fee"]) == 0

    priced = _signal(_market("KXFED-26NOV01-T1", "KXFED"))
    priced.market.ticker = "KXFED-26NOV01-T1"
    object.__setattr__(priced, "_maker_taker_orderbook", _book("KXFED-26NOV01-T1"))
    record_veto_allowed_attempt(priced, contracts=8, path=tmp_path / "fed.jsonl", now=datetime(2026, 11, 15, 15, tzinfo=UTC))
    fed = json.loads((tmp_path / "fed.jsonl").read_text(encoding="utf-8"))
    assert fed["fee_type"] == "quadratic_with_maker_fees"
    assert Decimal(fed["maker_fee"]) > 0

    unknown = _signal(_market("KXNOTASERIES-1", "KXNOTASERIES"))
    object.__setattr__(unknown, "_maker_taker_orderbook", _book("KXNOTASERIES-1"))
    record_veto_allowed_attempt(
        unknown, contracts=8, path=tmp_path / "unknown.jsonl", now=datetime(2026, 11, 15, 15, tzinfo=UTC)
    )
    blank = json.loads((tmp_path / "unknown.jsonl").read_text(encoding="utf-8"))
    assert blank["taker_fee"] is None
    assert blank["maker_fee"] is None


def test_public_window_includes_the_180th_second_and_excludes_the_next(tmp_path):
    ticker = "KXSECPRESSMENTION-26NOV21-T1"
    start = "2026-11-15T15:00:00.000000Z"
    row = _row(
        ticker=ticker,
        series="KXSECPRESSMENTION",
        side="yes",
        ts=start,
        best_ask_cents=60,
        ask_minus_1c=59,
        queue_ahead="0",
        resting_size_at_ask_minus_1c="0",
    )
    trades = [
        _trade(start, yes="0.59", count="100", ticker=ticker),
        _trade("2026-11-15T15:03:00.000000Z", yes="0.59", count="5", ticker=ticker),
        _trade("2026-11-15T15:03:00.000001Z", yes="0.59", count="100", ticker=ticker),
    ]
    scored = score_rows([row], trades, window_s=180)[0]
    assert scored["traded_at_or_through"] is True
    assert scored["volume"] == "5"
    assert scored["queue_ahead"] == "0"
    assert Decimal(scored["volume_minus_queue"]) == Decimal("5")
    assert scored["maker_filled"] is True
    assert scored["filled_contracts"] == "5"
    assert Decimal(scored["fee_net_delta"]) > 0

    short = score_rows(
        [_row(queue_ahead="5", resting_size_at_ask_minus_1c="5")],
        [_trade("2026-11-15T15:01:00.000000Z", yes="0.61", count="5", ticker=_row()["ticker"])],
    )[0]
    assert short["traded_at_or_through"] is True
    assert short["maker_filled"] is False
    assert short["fee_net_delta"] is None

    cleared = score_rows(
        [_row(side="yes", best_ask_cents=60, ask_minus_1c=59, queue_ahead="100")],
        [_trade("2026-11-15T15:01:00.000000Z", yes="0.58", count="1", ticker=_row()["ticker"])],
    )[0]
    assert cleared["maker_filled"] is True
    assert cleared["filled_contracts"] == "8"


def test_scout_no_bid_ignores_a_print_at_the_bid(tmp_path):
    path = tmp_path / "candidates.jsonl"
    mention = "KXFEDMENTION-26NOV21-RATE"
    press = "KXSECPRESSMENTION-26NOV21-POWELL"
    rows = [
        _row(ticker=mention, series="KXFEDMENTION"),
        _row(ticker=press, series="KXSECPRESSMENTION", queue_ahead="20", resting_size_at_ask_minus_1c="20"),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    trades = [
        _trade("2026-11-15T15:01:00Z", yes="0.61", count="50", ticker=mention, no="0.39"),
        _trade("2026-11-15T15:01:00Z", yes="0.62", count="50", ticker=press, no="0.38"),
    ]
    at_bid, through = score_candidate_file(path, trades, scout_no_bid=True)
    assert at_bid["fill_rule"] == "scout_no_bid"
    assert at_bid["traded_at_or_through"] is False
    assert at_bid["maker_filled"] is False
    assert at_bid["volume"] == "0"
    assert through["maker_filled"] is True
    assert through["series"] == "KXSECPRESSMENTION"
    assert Decimal(through["fee_net_delta"]) > 0

    counted = score_candidate_file(path, trades, scout_no_bid=False)[0]
    assert counted["fill_rule"] == "at_or_through"
    assert counted["maker_filled"] is True


def test_public_trades_url_is_the_unauthenticated_tape():
    seen: list[str] = []

    def _get(url: str) -> dict[str, object]:
        seen.append(url)
        assert url.startswith(PUBLIC_TRADES_URL + "?")
        assert "Authorization" not in url
        assert "/portfolio/" not in url
        return {"trades": [], "cursor": ""}

    start = datetime(2026, 11, 15, 15, 0, tzinfo=UTC)
    assert fetch_public_trades("KXTRUTHSOCIAL-26NOV21-T40", start, start, get=_get) == []
    assert seen
