import pytest

from polymarket.normalizer import normalize_polymarket_market
from trading.venue import Venue


def test_normalizes_binary_market_payload():
    payload = {
        "slug": "will-example-happen-2026",
        "title": "Will example happen in 2026?",
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
    assert market.status == "open"
    assert market.yes_ask_cents == 42
    assert market.no_ask_cents == 59
    assert market.volume_dollars == 1234.50
    assert market.open_interest_dollars == 99.0
    assert market.close_time == "2026-12-31T23:59:59Z"
    assert market.is_binary is True
    assert market.is_tradeable()


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
