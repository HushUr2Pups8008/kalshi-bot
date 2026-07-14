"""Venue persistence tests for paper-trade storage and portfolio hydration."""

import sqlite3
from unittest.mock import patch

import pytest

from tests.test_paper_trader import (
    _cfg_module,
    _make_mock_analysis,
    _shared_memory_connect,
)
from trading.portfolio import Portfolio


@pytest.fixture()
def venue_trader(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)

    from trading.paper_trader import PaperTrader

    keeper, connect = _shared_memory_connect("venue-trades")

    with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
         patch("trading.paper_trader.SourceCredibility") as MockCred:
        MockCred.return_value.get_multiplier.return_value = 1.0
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            pt = PaperTrader(db_path=":memory:", startup_context="test")
            pt._set_state("notional_bankroll", "500.0")
            yield pt
    keeper.close()


def test_record_trade_defaults_venue_to_kalshi(venue_trader):
    analysis = _make_mock_analysis()

    with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
        venue_trader.record_trade(analysis)

    row = venue_trader._conn.execute("SELECT venue FROM paper_trades LIMIT 1").fetchone()
    assert row["venue"] == "kalshi"


def test_record_trade_uses_analysis_venue_when_present(venue_trader):
    analysis = _make_mock_analysis(ticker="will-example-happen-2026")
    analysis.venue = "polymarket_us"
    analysis.market.venue = "polymarket_us"
    analysis.market.venue_market_id = "8594"

    with patch("dataclasses.asdict", return_value={"series_ticker": ""}):
        venue_trader.record_trade(analysis)

    row = venue_trader._conn.execute(
        "SELECT venue, ticker FROM paper_trades LIMIT 1"
    ).fetchone()
    assert row["venue"] == "polymarket_us"
    assert row["ticker"] == "will-example-happen-2026"

    positions = venue_trader.portfolio.open_positions("will-example-happen-2026")
    assert len(positions) == 1
    assert positions[0].venue == "polymarket_us"


def test_portfolio_hydrates_venue_when_column_exists(venue_trader):
    analysis = _make_mock_analysis(ticker="will-example-happen-2026")
    analysis.venue = "polymarket_us"
    analysis.market.venue = "polymarket_us"
    analysis.market.venue_market_id = "8594"

    with patch("dataclasses.asdict", return_value={"series_ticker": ""}):
        venue_trader.record_trade(analysis)

    portfolio = Portfolio()
    portfolio.load_from_db(venue_trader._conn)

    positions = portfolio.open_positions("will-example-happen-2026")
    assert len(positions) == 1
    assert positions[0].venue == "polymarket_us"


def test_portfolio_defaults_legacy_rows_to_kalshi():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT,
            ticker TEXT,
            side TEXT,
            contracts INTEGER,
            cost_dollars REAL,
            price_cents INTEGER,
            estimated_prob REAL,
            entry_price_cents REAL,
            ts TEXT,
            resolved INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO paper_trades
        (trade_id, ticker, side, contracts, cost_dollars, price_cents,
         estimated_prob, entry_price_cents, ts, resolved)
        VALUES ('legacy', 'KXLEG', 'yes', 5, 10.0, 50, 0.6, 50.0,
                '2026-01-01T00:00:00+00:00', 0)
        """
    )
    conn.commit()

    portfolio = Portfolio()
    portfolio.load_from_db(conn)

    positions = portfolio.open_positions("KXLEG")
    assert len(positions) == 1
    assert positions[0].venue == "kalshi"
