from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from feeds import NewsItem
from polymarket.models import PolymarketMarket
from polymarket.paper_runtime import (
    PolymarketPaperRuntime,
    match_polymarket_markets,
    polymarket_paper_runtime_disabled_reason,
)
from trading.venue import Venue


def _news(headline: str = "Example event gets more likely") -> NewsItem:
    return NewsItem(
        headline=headline,
        url="https://example.test/story",
        source="Example Wire",
        published=datetime.now(timezone.utc),
        body="",
    )


def _market(**overrides) -> PolymarketMarket:
    values = {
        "venue": Venue.POLYMARKET_US,
        "market_id": "will-example-event-happen-2026",
        "title": "Will example event happen in 2026?",
        "status": "open",
        "yes_ask_cents": 42,
        "no_ask_cents": 59,
        "volume_dollars": 1000.0,
        "open_interest_dollars": 100.0,
        "close_time": "2026-12-31T23:59:59Z",
        "is_binary": True,
    }
    values.update(overrides)
    return PolymarketMarket(**values)


class _FakeClient:
    def __init__(self, markets):
        self.markets = markets
        self.calls = 0

    def get_markets(self, *, limit: int):
        self.calls += 1
        return self.markets[:limit], None


@pytest.mark.asyncio
async def test_process_news_routes_matched_polymarket_analysis_through_blend():
    routed = []

    async def route_analysis(analysis, **kwargs):
        routed.append((analysis, kwargs))
        return SimpleNamespace(enqueued=True)

    async def estimate_probability(news, market, *, keyword_stats=None, match_meta=None):
        assert market.ticker == "will-example-event-happen-2026"
        assert market.yes_prob == pytest.approx(0.42)
        assert keyword_stats == "keyword-stats"
        assert match_meta["venue"] == "polymarket_us"
        return 0.65, 0.8, ["example"], "reason", "yes", "moderate", 0.8

    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market()]),
        route_analysis=route_analysis,
        keyword_stats="keyword-stats",
        estimate_probability_fn=estimate_probability,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    routed_count = await runtime.process_news(_news())

    assert routed_count == 1
    assert len(routed) == 1
    analysis, kwargs = routed[0]
    assert kwargs == {"accumulate": True, "watch": False}
    assert analysis.venue == "polymarket_us"
    assert analysis.market.ticker == "will-example-event-happen-2026"
    assert analysis.market.venue == Venue.POLYMARKET_US
    assert analysis.executed_price_cents == 42
    assert analysis.edge == pytest.approx(0.23)
    assert analysis.signal_meta["venue"] == "polymarket_us"
    assert analysis.signal_meta["polymarket_match_score"] > 0


@pytest.mark.asyncio
async def test_process_news_skips_when_no_polymarket_market_matches():
    routed = []

    async def route_analysis(analysis, **kwargs):
        routed.append((analysis, kwargs))

    async def estimate_probability(news, market, *, keyword_stats=None, match_meta=None):
        raise AssertionError("estimator should not run without a market match")

    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market(title="Will unrelated bill pass?")]),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
    )

    routed_count = await runtime.process_news(_news("Example event gets more likely"))

    assert routed_count == 0
    assert routed == []


@pytest.mark.asyncio
async def test_process_news_fail_closed_when_public_market_fetch_fails(caplog):
    class FailingClient:
        def get_markets(self, *, limit: int):
            raise RuntimeError("public gateway unavailable")

    async def route_analysis(analysis, **kwargs):
        raise AssertionError("route should not run after fetch failure")

    async def estimate_probability(news, market, *, keyword_stats=None, match_meta=None):
        raise AssertionError("estimator should not run after fetch failure")

    runtime = PolymarketPaperRuntime(
        client=FailingClient(),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
    )

    with caplog.at_level("WARNING", logger="polymarket.paper_runtime"):
        routed_count = await runtime.process_news(_news())

    assert routed_count == 0
    assert "public_market_fetch_failed" in caplog.text


def test_match_polymarket_markets_filters_non_tradeable_markets():
    matches = match_polymarket_markets(
        _news(),
        [
            _market(yes_ask_cents=None),
            _market(market_id="will-example-event-happen-alt", title="Will example event happen?"),
        ],
        max_results=5,
        min_score=0.01,
    )

    assert [market.market_id for market, _score, _meta in matches] == [
        "will-example-event-happen-alt"
    ]


def test_polymarket_paper_runtime_disabled_reason():
    active_cfg = SimpleNamespace(
        polymarket_us_enabled=True,
        is_paper_trading=True,
        polymarket_us_live_trading_enabled=False,
    )
    assert polymarket_paper_runtime_disabled_reason(active_cfg) is None

    assert (
        polymarket_paper_runtime_disabled_reason(
            SimpleNamespace(
                polymarket_us_enabled=False,
                is_paper_trading=True,
                polymarket_us_live_trading_enabled=False,
            )
        )
        == "polymarket_us_enabled=false"
    )
    assert (
        polymarket_paper_runtime_disabled_reason(
            SimpleNamespace(
                polymarket_us_enabled=True,
                is_paper_trading=False,
                polymarket_us_live_trading_enabled=False,
            )
        )
        == "bot_not_in_paper_mode"
    )
    assert (
        polymarket_paper_runtime_disabled_reason(
            SimpleNamespace(
                polymarket_us_enabled=True,
                is_paper_trading=True,
                polymarket_us_live_trading_enabled=True,
            )
        )
        == "polymarket_live_trading_enabled"
    )
