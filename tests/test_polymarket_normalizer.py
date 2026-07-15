import pytest
from datetime import datetime, timezone
from decimal import Decimal

from polymarket.normalizer import normalize_polymarket_market
from trading.venue import Venue


def test_normalizes_binary_market_payload():
    snapshot_at = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    payload = {
        "id": 8594,
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
            {
                "name": "Yes",
                "tokenId": "yes-token-1",
                "bestBid": {
                    "value": "0.41",
                    "quantity": "120",
                    "currency": "USD",
                },
                "bestAsk": {"value": "0.42", "currency": "USD"},
            },
            {
                "name": "No",
                "tokenId": "no-token-1",
                "bestBid": {
                    "value": "0.58",
                    "quantity": "80",
                    "currency": "USD",
                },
                "bestAsk": {"value": "0.59", "currency": "USD"},
            },
        ],
        "volume": {"value": "1234.50", "currency": "USD"},
        "openInterest": {"value": "99", "currency": "USD"},
        "closeTime": "2026-12-31T23:59:59Z",
        "feeCoefficient": "0.06",
        "feeEffectiveAt": "2026-07-01T04:00:00Z",
        "quantityStep": "1",
        "priceTick": "0.01",
        "fillRole": "taker",
    }

    market = normalize_polymarket_market(payload, snapshot_at=snapshot_at)

    assert market.venue == Venue.POLYMARKET_US
    assert market.market_id == "will-example-happen-2026"
    assert market.venue_market_id == "8594"
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
    assert market.yes_bid_cents == 41
    assert market.no_bid_cents == 58
    assert market.yes_bid_size == Decimal("120")
    assert market.no_bid_size == Decimal("80")
    assert market.yes_token_id == "yes-token-1"
    assert market.no_token_id == "no-token-1"
    assert market.fee_coefficient == Decimal("0.06")
    assert market.fee_effective_at == datetime(
        2026, 7, 1, 4, tzinfo=timezone.utc
    )
    assert market.quantity_step == Decimal("1")
    assert market.price_tick == Decimal("0.01")
    assert market.fill_role == "taker"
    assert len(market.source_payload_hash) == 64
    assert market.snapshot_at == snapshot_at
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
    assert market.price_source == "polymarket_public"
    assert market.price_method == "pm_named_outcomes_v1"


def test_missing_polymarket_fee_and_fill_provenance_remains_explicit():
    market = normalize_polymarket_market(_long_book_payload())

    assert market.fee_coefficient is None
    assert market.fee_effective_at is None
    assert market.quantity_step is None
    assert market.price_tick is None
    assert market.fill_role is None
    assert market.yes_token_id is None
    assert market.no_token_id is None
    assert len(market.source_payload_hash) == 64
    assert market.snapshot_at is not None


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

    assert market.yes_bid_cents == 12
    assert market.yes_ask_cents == 13
    assert market.no_bid_cents == 87
    assert market.no_ask_cents == 88
    assert market.price_source == "polymarket_public"
    assert market.price_method == "pm_long_book_v1"


@pytest.mark.parametrize("raw_id", [8594, "8594"])
def test_preserves_numeric_canonical_id_separately_from_slug_alias(raw_id):
    payload = _long_book_payload()
    payload["id"] = raw_id

    market = normalize_polymarket_market(payload)

    assert market.venue_market_id == "8594"
    assert market.market_id == "ewc-usse-ga-2026-11-03-rep"
    assert market.ticker == "ewc-usse-ga-2026-11-03-rep"
    assert market.tradeable_id == "ewc-usse-ga-2026-11-03-rep"


@pytest.mark.parametrize("raw_id", [None, "", "   "])
def test_missing_canonical_id_remains_explicit_without_changing_alias_behavior(raw_id):
    payload = _long_book_payload()
    payload["id"] = raw_id

    market = normalize_polymarket_market(payload)

    assert market.venue_market_id is None
    assert market.market_id == "ewc-usse-ga-2026-11-03-rep"
    assert market.ticker == market.market_id
    assert market.tradeable_id == market.market_id
    assert market.is_tradeable()


def test_absent_canonical_id_remains_explicit_none():
    payload = _long_book_payload()
    payload.pop("id")

    market = normalize_polymarket_market(payload)

    assert market.venue_market_id is None
    assert market.market_id == "ewc-usse-ga-2026-11-03-rep"


@pytest.mark.parametrize("raw_id", ["market-123", "8594.0", 8594.0, True])
def test_rejects_nonnumeric_canonical_id(raw_id):
    payload = _long_book_payload()
    payload["id"] = raw_id

    with pytest.raises(ValueError, match="canonical id"):
        normalize_polymarket_market(payload)


def test_market_side_quotes_are_oriented_fallback_without_top_level_book():
    payload = _long_book_payload()
    payload.pop("bestBidQuote")
    payload.pop("bestAskQuote")

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents == 13
    assert market.no_ask_cents == 88
    assert market.price_source == "polymarket_public"
    assert market.price_method == "pm_named_sides_v1"


def test_present_null_top_level_book_is_unpriced():
    payload = _long_book_payload()
    payload["bestBidQuote"] = None
    payload["bestAskQuote"] = None

    market = normalize_polymarket_market(payload)

    assert market.yes_ask_cents is None
    assert market.no_ask_cents is None
    assert market.price_method == ""


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


@pytest.mark.parametrize("surface", ["book", "sides", "outcomes"])
def test_oversized_probability_values_are_unpriced(surface):
    huge = 10**10000
    if surface == "outcomes":
        payload = {
            "slug": "huge-outcome",
            "title": "Huge outcome?",
            "status": "open",
            "outcomes": [
                {"name": "Yes", "bestAsk": {"value": huge}},
                {"name": "No", "bestAsk": {"value": 0.5}},
            ],
        }
    else:
        payload = _long_book_payload()
        if surface == "book":
            payload["bestBidQuote"] = {"value": huge}
        else:
            payload.pop("bestBidQuote")
            payload.pop("bestAskQuote")
            payload["marketSides"][0]["quote"] = {"value": huge}

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
        "id": "123",
        "question": "Fallback title?",
        "status": "ACTIVE",
        "outcomes": [
            {"name": "no", "bestAsk": {"value": "0.49"}},
            {"name": "yes", "bestAsk": {"value": "0.51"}},
        ],
        "close_time": "2026-12-31T23:59:59Z",
    }

    market = normalize_polymarket_market(payload)

    assert market.market_id == "123"
    assert market.venue_market_id == "123"
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
    assert market.no_ask_cents is None
    assert market.price_source == ""
    assert market.price_method == ""
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
