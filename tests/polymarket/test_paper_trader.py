from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

import config as config_module
from analysis import SignalAnalysis
from feeds import NewsItem
from polymarket.models import PolymarketMarket
from polymarket.paper_trader import PolymarketPaperTrader
from trading.paper_trader import PaperTrader
from trading.venue import Venue


@pytest.fixture()
def paper_trader(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(config_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(config_module.cfg, "max_ticker_exposure_pct", 0.25)
    monkeypatch.setattr(config_module.cfg, "kelly_fraction", 0.5)
    return PaperTrader(tmp_path / "paper_trades.db", startup_context="test")


def _analysis(*, executed_price_cents: int | None = 42) -> SignalAnalysis:
    market = PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="will-example-happen-2026",
        venue_market_id="8594",
        title="Will example happen in 2026?",
        status="open",
        yes_ask_cents=42,
        no_ask_cents=59,
        volume_dollars=1000.0,
        open_interest_dollars=100.0,
        close_time="2026-12-31T23:59:59Z",
    )
    news = NewsItem(
        headline="Example event gets more likely",
        url="https://example.test/news",
        source="Example Wire",
        published=datetime.now(timezone.utc),
    )
    return SignalAnalysis(
        news_item=news,
        market=market,
        estimated_probability=0.62,
        executed_price_cents=executed_price_cents,
        edge=0.20,
        side="yes",
        kelly_fraction=0.5,
        kelly_dollars=10.0,
        capped_dollars=10.0,
        keywords_matched=["example"],
        reasoning="synthetic Polymarket paper test",
        confidence=0.8,
        match_score=0.3,
    )


def test_record_trade_persists_polymarket_venue(paper_trader):
    trader = PolymarketPaperTrader(paper_trader)

    trade_id = trader.record_trade(_analysis())

    row = paper_trader._conn.execute(
        """
        SELECT trade_id, ticker, venue, side, price_cents, entry_price_cents
        FROM paper_trades
        WHERE trade_id = ?
        """,
        (trade_id,),
    ).fetchone()
    assert row is not None
    assert row["ticker"] == "will-example-happen-2026"
    assert row["venue"] == "polymarket_us"
    assert row["side"] == "yes"
    assert row["price_cents"] == 42
    assert row["entry_price_cents"] == 42.0


def test_record_trade_fails_closed_without_executed_price(paper_trader, caplog):
    trader = PolymarketPaperTrader(paper_trader)

    with caplog.at_level(logging.WARNING, logger="polymarket.paper_trader"):
        trade_id = trader.record_trade(_analysis(executed_price_cents=None))

    assert trade_id == ""
    assert "executed_price_unavailable" in caplog.text
    row_count = paper_trader._conn.execute("SELECT count(*) FROM paper_trades").fetchone()[0]
    assert row_count == 0
