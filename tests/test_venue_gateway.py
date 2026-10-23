"""Venue field and the Kalshi gateway. No orders and no Kalshi formula change."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    INITIAL_ORDER_FEE_ACCUMULATOR,
    FeeContext,
    FeeRole,
    fee_coefficient_for,
    fee_schedule_at,
    quote_fee,
)
from trading.venue import Venue
from trading.venue_gateway import KalshiGateway, PolymarketGateway, gateway_for
from utils.series_universe import get_universe, reset_series_universe


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.pages = [( ["m1"], "next"), (["m2"], None)]
        self.market = type("M", (), {"rules_primary": "rules", "result": "yes"})()

    def get_markets(self, **kwargs):
        self.calls.append(("markets", kwargs))
        return self.pages.pop(0)

    def get_market_orderbook(self, ticker, **kwargs):
        self.calls.append(("book", {"ticker": ticker, **kwargs}))
        return {"ticker": ticker}

    def get_market(self, ticker):
        self.calls.append(("market", {"ticker": ticker}))
        return self.market

    def get_market_exact(self, ticker):
        self.calls.append(("exact", {"ticker": ticker}))
        return self.market


def test_shipped_rows_are_kalshi_and_a_missing_venue_stays_kalshi(tmp_path) -> None:
    reset_series_universe()
    try:
        entries = get_universe().entries
        assert len(entries) == 30
        assert {entry.venue for entry in entries if entry.series.startswith("KX")} == {
            "kalshi"
        }
        polymarket = {
            entry.series: entry.venue_market
            for entry in entries
            if entry.venue == "polymarket_us"
        }
        assert polymarket == {
            "PMTRUMPAPPROVE": "trmpapprv-wk-2026-10-23",
            "PMUFCPETYAN": "ufc-petyan-merdva-2026-10-24",
            "PMUFCKODSTE": "ufc-kodste-noagug-2026-10-24",
            "PMLOLLOST1": "lol-los-t1-2026-10-23",
            "PMLOLALKC": "lol-al-kc-2026-10-23",
            "PMNBADALIND": "nba-dal-ind-2026-10-23",
            "PMUFCIASLUC": "ufc-iasluc-niccal-2026-10-24",
        }
        assert all(
            entry.mode == "paper"
            for entry in entries
            if entry.venue == "polymarket_us"
        )
    finally:
        reset_series_universe()

    path = tmp_path / "universe.yaml"
    path.write_text(
        "- series: KXTRUTHSOCIAL\n"
        "  mode: live\n"
        "  favorite_lo_cents: 36\n"
        "  favorite_hi_cents: 90\n"
        "  notes: default venue\n",
        encoding="utf-8",
    )
    reset_series_universe(path)
    try:
        loaded = get_universe()
        assert loaded.failed is False
        assert loaded.entries[0].venue == "kalshi"
    finally:
        reset_series_universe()


def test_kalshi_gateway_uses_the_existing_open_book_and_fee() -> None:
    client = _Client()
    moment = datetime(2026, 10, 21, 16, tzinfo=timezone.utc)
    gateway = KalshiGateway(client, now=lambda: moment)
    assert gateway.list_markets("KXTRUTHSOCIAL") == ("m1", "m2")
    assert client.calls[0] == (
        "markets",
        {"status": "open", "series_ticker": "KXTRUTHSOCIAL"},
    )
    assert client.calls[1][1]["cursor"] == "next"
    assert "active" not in str(client.calls)
    assert gateway.order_book("KXTRUTHSOCIAL-26OCT26-GOLF") == {
        "ticker": "KXTRUTHSOCIAL-26OCT26-GOLF"
    }
    assert gateway.rules_text("KXTRUTHSOCIAL-26OCT26-GOLF") == "rules"
    assert gateway.settlement("KXTRUTHSOCIAL-26OCT26-GOLF") == "yes"

    schedule = fee_schedule_at(venue=Venue.KALSHI, timestamp=moment)
    expected = quote_fee(
        FeeContext(
            schedule_id=schedule,
            role=FeeRole.TAKER,
            quantity=Decimal("8"),
            price=Decimal("0.55"),
            signed_revenue=-(Decimal("8") * Decimal("0.55")),
            order_id="venue-fee",
            accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
            multiplier=Decimal("1"),
            coefficient=fee_coefficient_for(schedule, FeeRole.TAKER),
            account_precision=DIRECT_ACCOUNT_PRECISION,
            timestamp=moment,
        )
    ).net_fee
    assert gateway.fee(Decimal("0.55"), Decimal("8")) == expected
    assert expected > 0


def test_missing_market_and_other_venues_fail_closed() -> None:
    client = _Client()
    client.market = None
    gateway = KalshiGateway(client)
    with pytest.raises(ValueError, match="market_missing"):
        gateway.rules_text("KXTEST")
    with pytest.raises(ValueError, match="market_missing"):
        gateway.settlement("KXTEST")
    with pytest.raises(ValueError, match="unsupported venue"):
        gateway_for("binance", client)
    assert gateway_for("kalshi", client).__class__ is KalshiGateway
    assert gateway_for(Venue.POLYMARKET_US, client).__class__ is PolymarketGateway


class _Polymarket:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def list_event_markets(self, slug: str):
        self.calls.append(("event", slug))
        return ({"slug": "stsc-hormuz-normal-2026-12-31"},)

    def get_market_book(self, slug: str):
        self.calls.append(("book", slug))
        return {"slug": slug}

    def get_market(self, slug: str):
        self.calls.append(("market", slug))
        return type("M", (), {"description": "Hormuz rules"})()

    def get_market_settlement(self, slug: str):
        self.calls.append(("settlement", slug))
        return {"slug": slug, "settlement": 1}


def test_polymarket_gateway_reads_and_quotes_the_october_fee() -> None:
    reset_series_universe()
    try:
        client = _Polymarket()
        moment = datetime(2026, 10, 21, 16, tzinfo=timezone.utc)
        assert gateway_for(Venue.POLYMARKET_US, client).__class__ is PolymarketGateway
        gateway = PolymarketGateway(client, now=lambda: moment)
        assert isinstance(gateway, PolymarketGateway)
        names = {
            name for name in dir(PolymarketGateway) if not name.startswith("_")
        }
        assert names == {
            "fee",
            "list_markets",
            "order_book",
            "rules_text",
            "settlement",
        }
        assert gateway.list_markets("PMHORMUZ") == (
            {"slug": "stsc-hormuz-normal-2026-12-31"},
        )
        assert client.calls[0] == ("event", "hormuz-normal")
        assert gateway.order_book("stsc-hormuz-normal-2026-12-31")["slug"] == (
            "stsc-hormuz-normal-2026-12-31"
        )
        assert gateway.rules_text("stsc-hormuz-normal-2026-12-31") == "Hormuz rules"
        assert gateway.settlement("stsc-hormuz-normal-2026-12-31") == "1"
        schedule = fee_schedule_at(venue=Venue.POLYMARKET_US, timestamp=moment)
        expected = quote_fee(
            FeeContext(
                schedule_id=schedule,
                role=FeeRole.TAKER,
                quantity=Decimal("1000"),
                price=Decimal("0.50"),
                signed_revenue=-(Decimal("1000") * Decimal("0.50")),
                order_id="venue-fee",
                accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
                multiplier=Decimal("1"),
                coefficient=fee_coefficient_for(schedule, FeeRole.TAKER),
                account_precision=None,
                timestamp=moment,
            )
        ).net_fee
        assert gateway.fee(Decimal("0.50"), Decimal("1000")) == expected
        assert expected == Decimal("17.38")
        with pytest.raises(ValueError, match="series_not_polymarket"):
            gateway.list_markets("KXTRUTHSOCIAL")
        with pytest.raises(ValueError, match="rules_missing"):
            client.get_market = lambda slug: type("M", (), {"description": ""})()
            gateway.rules_text("stsc-hormuz-normal-2026-12-31")
    finally:
        reset_series_universe()
