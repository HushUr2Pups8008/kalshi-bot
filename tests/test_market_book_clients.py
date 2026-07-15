from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from kalshi.rest_client import KalshiRestClient
from polymarket.public_client import PolymarketPublicClient
from trading.orderbook import BinaryMarketBook, BookLevel
from trading.venue import Venue


def test_kalshi_market_book_normalizes_and_sorts_bid_depth():
    client = KalshiRestClient()
    client._request = MagicMock(
        return_value={
            "orderbook_fp": {
                "yes_dollars": [["0.3900", "5.00"], ["0.4000", "6.00"]],
                "no_dollars": [["0.5700", "8.00"], ["0.5500", "7.00"]],
            }
        }
    )

    book = client.get_market_orderbook("KXREPORT-1", depth=100)

    assert book.venue is Venue.KALSHI
    assert book.venue_market_id == "KXREPORT-1"
    assert [(level.price, level.quantity) for level in book.yes_bids] == [
        (Decimal("0.4000"), Decimal("6.00")),
        (Decimal("0.3900"), Decimal("5.00")),
    ]
    assert [(level.price, level.quantity) for level in book.no_bids] == [
        (Decimal("0.5700"), Decimal("8.00")),
        (Decimal("0.5500"), Decimal("7.00")),
    ]
    assert len(book.raw_payload_hash) == 64
    client._request.assert_called_once_with(
        "GET", "/markets/KXREPORT-1/orderbook", params={"depth": 100}
    )


def test_kalshi_event_lookup_returns_exact_series_identity():
    client = KalshiRestClient()
    client._request = MagicMock(
        return_value={
            "event": {
                "event_ticker": "KXREPORT-26",
                "series_ticker": "KXREPORT",
            }
        }
    )

    assert client.get_event_series_ticker("KXREPORT-26") == "KXREPORT"
    client._request.assert_called_once_with("GET", "/events/KXREPORT-26")


def test_kalshi_event_lookup_rejects_identity_mismatch():
    client = KalshiRestClient()
    client._request = MagicMock(
        return_value={
            "event": {
                "event_ticker": "OTHER-26",
                "series_ticker": "KXREPORT",
            }
        }
    )

    with pytest.raises(ValueError, match="event ticker mismatch"):
        client.get_event_series_ticker("KXREPORT-26")


def test_kalshi_event_fee_terms_apply_latest_scheduled_override_across_pages():
    client = KalshiRestClient()
    client._request = MagicMock(
        side_effect=[
            {
                "event": {
                    "event_ticker": "KXREPORT-26",
                    "series_ticker": "KXREPORT",
                }
            },
            {
                "event_fee_changes": [
                    {
                        "id": "future",
                        "event_ticker": "KXREPORT-26",
                        "series_ticker": "KXREPORT",
                        "fee_type_override": "flat",
                        "fee_multiplier_override": 9,
                        "scheduled_ts": "2026-07-15T00:00:00Z",
                    },
                    {
                        "id": "current",
                        "event_ticker": "KXREPORT-26",
                        "series_ticker": "KXREPORT",
                        "fee_type_override": "quadratic_with_maker_fees",
                        "fee_multiplier_override": "0.5",
                        "scheduled_ts": "2026-07-14T10:00:00Z",
                    },
                ],
                "cursor": "next-page",
            },
            {
                "event_fee_changes": [
                    {
                        "id": "older",
                        "event_ticker": "KXREPORT-26",
                        "series_ticker": "KXREPORT",
                        "fee_type_override": None,
                        "fee_multiplier_override": None,
                        "scheduled_ts": "2026-07-01T00:00:00Z",
                    }
                ],
                "cursor": "",
            },
        ]
    )

    terms = client.get_event_fee_terms(
        "KXREPORT-26",
        as_of=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
    )

    assert terms.series_ticker == "KXREPORT"
    assert terms.fee_type_override == "quadratic_with_maker_fees"
    assert terms.fee_multiplier_override == Decimal("0.5")
    assert terms.effective_at == datetime(2026, 7, 14, 10, tzinfo=timezone.utc)
    assert len(terms.raw_payload_hash) == 64
    assert client._request.call_args_list == [
        (("GET", "/events/KXREPORT-26"),),
        (
            ("GET", "/events/fee_changes"),
            {"params": {"event_ticker": "KXREPORT-26", "limit": 100}},
        ),
        (
            ("GET", "/events/fee_changes"),
            {
                "params": {
                    "event_ticker": "KXREPORT-26",
                    "limit": 100,
                    "cursor": "next-page",
                }
            },
        ),
    ]


def test_kalshi_event_fee_terms_reject_ambiguous_same_time_overrides():
    client = KalshiRestClient()
    client._request = MagicMock(
        side_effect=[
            {
                "event": {
                    "event_ticker": "KXREPORT-26",
                    "series_ticker": "KXREPORT",
                }
            },
            {
                "event_fee_changes": [
                    {
                        "id": "a",
                        "event_ticker": "KXREPORT-26",
                        "series_ticker": "KXREPORT",
                        "fee_type_override": "quadratic",
                        "fee_multiplier_override": 1,
                        "scheduled_ts": "2026-07-14T10:00:00Z",
                    },
                    {
                        "id": "b",
                        "event_ticker": "KXREPORT-26",
                        "series_ticker": "KXREPORT",
                        "fee_type_override": "flat",
                        "fee_multiplier_override": 2,
                        "scheduled_ts": "2026-07-14T10:00:00Z",
                    },
                ],
                "cursor": "",
            },
        ]
    )

    with pytest.raises(ValueError, match="ambiguous Kalshi event fee changes"):
        client.get_event_fee_terms(
            "KXREPORT-26",
            as_of=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
        )


def test_polymarket_market_book_derives_no_bids_from_yes_offers():
    client = PolymarketPublicClient()
    client._request = MagicMock(
        return_value={
            "marketData": {
                "marketSlug": "pm-report-1",
                "bids": [
                    {"px": {"value": "0.40", "currency": "USD"}, "qty": "6"},
                    {"px": {"value": "0.39", "currency": "USD"}, "qty": "5"},
                ],
                "offers": [
                    {"px": {"value": "0.50", "currency": "USD"}, "qty": "5"},
                    {"px": {"value": "0.51", "currency": "USD"}, "qty": "10"},
                ],
                "transactTime": "2026-07-14T12:00:00Z",
            }
        }
    )

    book = client.get_market_book("pm-report-1")

    assert book.venue is Venue.POLYMARKET_US
    assert book.venue_market_id == "pm-report-1"
    assert [(level.price, level.quantity) for level in book.yes_bids] == [
        (Decimal("0.40"), Decimal("6")),
        (Decimal("0.39"), Decimal("5")),
    ]
    assert [(level.price, level.quantity) for level in book.no_bids] == [
        (Decimal("0.50"), Decimal("5")),
        (Decimal("0.49"), Decimal("10")),
    ]
    assert book.as_of == datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    assert len(book.raw_payload_hash) == 64
    client._request.assert_called_once_with(
        "GET", "/v1/markets/pm-report-1/book"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"marketData": {"marketSlug": "wrong", "bids": [], "offers": []}},
        {
            "marketData": {
                "marketSlug": "pm-report-1",
                "bids": [{"px": {"value": "0.40"}, "qty": "not-a-number"}],
                "offers": [],
            }
        },
    ],
)
def test_polymarket_market_book_rejects_malformed_or_mismatched_payload(payload):
    client = PolymarketPublicClient()
    client._request = MagicMock(return_value=payload)

    with pytest.raises(ValueError):
        client.get_market_book("pm-report-1")


def test_binary_market_book_rejects_crossed_binary_bids():
    with pytest.raises(ValueError, match="crossed binary book"):
        BinaryMarketBook(
            venue=Venue.POLYMARKET_US,
            venue_market_id="pm-report-1",
            yes_bids=(BookLevel(Decimal("0.40"), Decimal("5")),),
            no_bids=(BookLevel(Decimal("0.70"), Decimal("5")),),
            as_of=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
            raw_payload_hash="a" * 64,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"orderbook_fp": {"yes_dollars": [], "no_dollars": "bad"}},
        {
            "orderbook_fp": {
                "yes_dollars": [["0.40", "-1"]],
                "no_dollars": [],
            }
        },
    ],
)
def test_kalshi_market_book_rejects_malformed_payload(payload):
    client = KalshiRestClient()
    client._request = MagicMock(return_value=payload)

    with pytest.raises(ValueError):
        client.get_market_orderbook("KXREPORT-1")
