import pytest

from polymarket.normalizer import normalize_polymarket_market
from trading.venue import Venue


def test_normalizes_binary_market_payload():
    payload = {
        "slug": "will-example-happen-2026",
        "title": "Will example happen in 2026?",
        "question": "Example resolution question?",
        "subtitle": "Example subtitle",
        "category": "politics",
        "description": "Detailed market description for traders.",
        "eventTitle": "Example Event",
        "eventSlug": "example-event",
        "seriesTitle": "Example Series",
        "seriesSlug": "example-series",
        "tags": [{"label": "US Politics"}, {"name": "Iran"}],
        "comments": [{"body": "Public comment context"}, {"text": "Resolution nuance"}],
        "resolutionSource": "https://example.com/resolution-rules",
        "status": "open",
        "outcomes": [
            {"name": "Yes", "bestAsk": {"value": "0.42", "currency": "USD"}},
            {"name": "No", "bestAsk": {"value": "0.59", "currency": "USD"}},
        ],
        "volume": {"value": "1234.50", "currency": "USD"},
        "openInterest": {"value": "99", "currency": "USD"},
        "closeTime": "2026-12-31T23:59:59Z",
    }

    market = normalize_polymarket_market(payload)

    assert market.venue == Venue.POLYMARKET_US
    assert market.market_id == "will-example-happen-2026"
    assert market.title == "Will example happen in 2026?"
    assert market.question == "Example resolution question?"
    assert market.subtitle == "Example subtitle"
    assert market.category == "politics"
    assert market.description == "Detailed market description for traders."
    assert market.event_title == "Example Event"
    assert market.event_slug == "example-event"
    assert market.series_title == "Example Series"
    assert market.series_slug == "example-series"
    assert market.tags == ("US Politics", "Iran")
    assert market.public_comments == ("Public comment context", "Resolution nuance")
    assert market.resolution_source == "https://example.com/resolution-rules"
    assert market.status == "open"
    assert market.yes_ask_cents == 42
    assert market.no_ask_cents == 59
    assert market.volume_dollars == 1234.50
    assert market.open_interest_dollars == 99.0
    assert market.close_time == "2026-12-31T23:59:59Z"
    assert market.is_binary is True
    assert market.is_tradeable()
    assert market.tradeable_id == "will-example-happen-2026"
    assert market.ticker == "will-example-happen-2026"
    # PROFIT-VENUE-PARITY V03: series_ticker is now the per-family pm_domain_key
    # (no ISO date in this slug -> whole stem), NOT the bare venue constant.
    assert market.series_ticker == "polymarket_us:will-example-happen-2026"
    assert market.series_ticker.startswith("polymarket_us")
    assert market.yes_price == 42
    assert market.no_price == 59
    assert market.price_available is True


def _long_book_payload() -> dict:
    return {
        "id": "123",
        "slug": "ewc-usse-ga-2026-11-03-rep",
        "question": "Will the Republican win?",
        "active": True,
        "closed": False,
        "marketSides": [
            {"name": "No", "long": False, "quote": {"value": "0.88"}},
            {"name": "Yes", "long": True, "quote": {"value": "0.13"}},
        ],
        "outcomes": '["No", "Yes"]',
        "outcomePrices": '["0.1300", "0.88"]',
        "bestBidQuote": {"value": "0.1200"},
        "bestAskQuote": {"value": "0.1300"},
        "endDate": "2026-11-03T00:00:00Z",
    }


def test_authoritative_long_book_ignores_reversed_positional_prices():
    payload = _long_book_payload()
    payload["marketSides"][0]["quote"]["value"] = "0.87"
    payload["marketSides"][1]["quote"]["value"] = "0.14"

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents == 13
    assert market.no_ask_cents == 88
    assert market.price_source == "polymarket_public"
    assert market.price_method == "pm_long_book_v1"


def test_market_side_quotes_are_oriented_fallback_without_top_level_book():
    payload = _long_book_payload()
    payload.pop("bestBidQuote")
    payload.pop("bestAskQuote")

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents == 13
    assert market.no_ask_cents == 88
    assert market.price_source == "polymarket_public"
    assert market.price_method == "pm_market_sides_quote_v1"


def test_string_outcomes_without_authoritative_book_are_unpriced():
    payload = _long_book_payload()
    payload.pop("marketSides")
    payload.pop("bestBidQuote")
    payload.pop("bestAskQuote")

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents is None
    assert market.no_ask_cents is None
    assert not market.is_tradeable()


@pytest.mark.parametrize(
    "market_sides",
    [[], [{"long": True}, {"long": True}], [{"long": False}, {"long": False}]],
)
def test_ambiguous_long_side_identity_is_unpriced(market_sides):
    payload = _long_book_payload()
    payload["marketSides"] = market_sides

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents is None
    assert market.no_ask_cents is None


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), -0.01, 1.01])
@pytest.mark.parametrize("quote_key", ["bestBidQuote", "bestAskQuote"])
def test_invalid_top_level_book_values_are_unpriced(quote_key, invalid_value):
    payload = _long_book_payload()
    payload[quote_key] = {"value": invalid_value}

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents is None
    assert market.no_ask_cents is None


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), -0.01, 1.01])
def test_invalid_market_side_quote_values_are_unpriced(invalid_value):
    payload = _long_book_payload()
    payload.pop("bestBidQuote")
    payload.pop("bestAskQuote")
    payload["marketSides"][0]["quote"] = {"value": invalid_value}

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents is None
    assert market.no_ask_cents is None


def test_closed_public_gateway_market_is_not_tradeable():
    payload = {
        "id": "123",
        "question": "Closed?",
        "active": True,
        "closed": True,
        "outcomes": '["No","Yes"]',
        "outcomePrices": '["0.01","1"]',
    }

    market = normalize_polymarket_market(payload)

    assert market.status == "closed"
    assert not market.is_tradeable()


def test_normalizes_id_and_question_fallbacks():
    payload = {
        "id": "market-123",
        "question": "Fallback title?",
        "status": "ACTIVE",
        "outcomes": [
            {"name": "no", "bestAsk": {"value": "0.49"}},
            {"name": "yes", "bestAsk": {"value": "0.51"}},
        ],
        "close_time": "2026-12-31T23:59:59Z",
    }

    market = normalize_polymarket_market(payload)

    assert market.market_id == "market-123"
    assert market.title == "Fallback title?"
    assert market.status == "active"
    assert market.is_tradeable()


def test_missing_best_ask_is_not_tradeable():
    payload = {
        "slug": "missing-ask",
        "title": "Missing ask?",
        "status": "open",
        "outcomes": [
            {"name": "Yes"},
            {"name": "No", "bestAsk": {"value": "0.58"}},
        ],
    }

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents is None
    assert market.no_ask_cents == 58
    assert not market.is_tradeable()


def test_rejects_multi_outcome_payload():
    payload = {
        "slug": "multi",
        "title": "multi",
        "outcomes": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
    }

    with pytest.raises(ValueError, match="binary markets only"):
        normalize_polymarket_market(payload)


def test_rejects_binary_payload_without_yes_no_outcomes():
    payload = {
        "slug": "binary-without-yes-no",
        "title": "binary",
        "outcomes": [{"name": "A"}, {"name": "B"}],
    }

    with pytest.raises(ValueError, match="must contain Yes and No outcomes"):
        normalize_polymarket_market(payload)
