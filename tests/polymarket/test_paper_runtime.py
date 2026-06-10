from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import PAPER_MAX_CANDIDATES
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
        "question": "Will example event happen in 2026?",
        "subtitle": "",
        "category": "politics",
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


class _FakeSourceStats:
    def __init__(self):
        self.signals = []

    def increment_signals(self, source: str) -> None:
        self.signals.append(source)


@pytest.mark.asyncio
async def test_warm_cache_populates_cached_markets_for_shared_getters():
    market = _market()
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([market]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    assert runtime.cached_markets() == []

    warmed = await runtime.warm_cache()

    assert warmed == 1
    assert runtime.cached_markets() == [market]


@pytest.mark.asyncio
async def test_cached_candidate_markets_excludes_suppressed_polymarket_categories():
    politics = _market(market_id="will-us-iran-deal-happen", category="politics")
    sports = _market(market_id="will-nba-finals-game-seven-happen", category="sports")
    culture = _market(market_id="will-tommy-lee-jones-attend", category="culture")
    macro = _market(market_id="will-fed-cut-rates", category="macro")
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([sports, culture, macro, politics]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    await runtime.warm_cache()

    assert runtime.cached_candidate_markets() == [politics]


@pytest.mark.asyncio
async def test_process_news_routes_matched_polymarket_analysis_through_blend(caplog):
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

    with caplog.at_level("INFO", logger="polymarket.paper_runtime"):
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
    stats = runtime.stats()
    assert stats.market_count == 1
    assert stats.news_processed == 1
    assert stats.routed_count == 1
    assert stats.no_match_count == 0
    assert stats.last_match_count == 1
    assert "[POLYMARKET_MATCH] candidate ticker=will-example-event-happen-2026" in caplog.text
    assert "[POLYMARKET_ANALYSIS] candidate ticker=will-example-event-happen-2026" in caplog.text
    assert "[POLYMARKET_PAPER] heartbeat markets=1" in caplog.text


@pytest.mark.asyncio
async def test_process_news_defaults_to_shared_paper_candidate_count():
    routed = []
    markets = [
        _market(market_id=f"will-example-event-happen-{idx}")
        for idx in range(PAPER_MAX_CANDIDATES)
    ]

    async def route_analysis(analysis, **kwargs):
        routed.append((analysis, kwargs))
        return SimpleNamespace(enqueued=True)

    async def estimate_probability(news, market, *, keyword_stats=None, match_meta=None):
        return 0.65, 0.8, ["example"], "reason", "yes", "moderate", 0.8

    runtime = PolymarketPaperRuntime(
        client=_FakeClient(markets),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    routed_count = await runtime.process_news(_news())

    assert routed_count == PAPER_MAX_CANDIDATES
    assert [analysis.market.ticker for analysis, _kwargs in routed] == [
        market.market_id for market in markets
    ]


@pytest.mark.asyncio
async def test_process_news_supplies_shared_match_meta_and_signal_stats():
    routed = []
    source_stats = _FakeSourceStats()

    async def route_analysis(analysis, **kwargs):
        routed.append((analysis, kwargs))
        return SimpleNamespace(enqueued=True)

    async def estimate_probability(news, market, *, keyword_stats=None, match_meta=None):
        assert match_meta["venue"] == "polymarket_us"
        assert match_meta["matched_tokens"] == ["election", "governor", "kansas"]
        assert match_meta["pre_llm_quality_pass"] is True
        assert match_meta["pre_llm_semantic_overlap_count"] == 3
        assert match_meta["pre_llm_semantic_overlap_ratio"] > 0.25
        assert match_meta["pre_llm_gate_reason"] is None
        return 0.65, 0.8, [], "reason", "yes", "moderate", 0.8

    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [
                _market(
                    market_id="ewc-usgub-ks-2026-11-03-dem",
                    title="Democratic Party",
                    question="Kansas Governor Election Winner",
                    subtitle="2026 race",
                )
            ]
        ),
        route_analysis=route_analysis,
        keyword_stats=None,
        source_stats=source_stats,
        estimate_probability_fn=estimate_probability,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    routed_count = await runtime.process_news(
        _news("Kansas governor election tightens after new polling")
    )

    assert routed_count == 1
    assert source_stats.signals == ["Example Wire"]
    assert routed[0][0].keywords_matched == ["election", "governor", "kansas"]
    assert routed[0][0].signal_meta["pre_llm_quality_pass"] is True


@pytest.mark.asyncio
async def test_process_news_skips_when_no_polymarket_market_matches(caplog):
    routed = []

    async def route_analysis(analysis, **kwargs):
        routed.append((analysis, kwargs))

    async def estimate_probability(news, market, *, keyword_stats=None, match_meta=None):
        raise AssertionError("estimator should not run without a market match")

    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [
                _market(
                    title="Will unrelated bill pass?",
                    question="Will unrelated bill pass?",
                )
            ]
        ),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
    )

    with caplog.at_level("INFO", logger="polymarket.paper_runtime"):
        routed_count = await runtime.process_news(_news("Example event gets more likely"))

    assert routed_count == 0
    assert routed == []
    stats = runtime.stats()
    assert stats.market_count == 1
    assert stats.news_processed == 1
    assert stats.routed_count == 0
    assert stats.no_match_count == 1
    assert stats.last_match_count == 0
    assert "[POLYMARKET_MATCH] no_match markets=1" in caplog.text
    assert "[POLYMARKET_PAPER] heartbeat markets=1" in caplog.text


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
    stats = runtime.stats()
    assert stats.market_count == 0
    assert stats.news_processed == 1
    assert stats.last_error == "public_market_fetch_failed"


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


def test_match_polymarket_markets_filters_non_politics_categories():
    matches = match_polymarket_markets(
        _news("Georgia governor election polling tightens"),
        [
            _market(
                market_id="will-georgia-governor-election-happen",
                title="Georgia Governor Election Winner",
                question="Georgia Governor Election Winner",
                category="politics",
            ),
            _market(
                market_id="will-georgia-reality-show-winner-happen",
                title="Georgia reality show winner",
                question="Georgia reality show winner",
                category="culture",
            ),
            _market(
                market_id="will-georgia-game-total-happen",
                title="Georgia game total",
                question="Georgia game total",
                category="sports",
            ),
        ],
        max_results=5,
        min_score=0.01,
    )

    assert [market.market_id for market, _score, _meta in matches] == [
        "will-georgia-governor-election-happen"
    ]


def test_match_polymarket_markets_uses_headline_score_when_body_dilutes_match():
    news = _news("Alaska Senate candidate under investigation over alleged voter confusion scheme")
    news.body = " ".join(f"irrelevanttoken{idx}" for idx in range(80))

    matches = match_polymarket_markets(
        news,
        [
            _market(
                market_id="ewc-usse-ak-2026-11-03-rep",
                title="Republican Party",
                question="Alaska Senate Election Winner",
                category="politics",
            ),
            _market(
                market_id="ewc-usse-ak-2026-11-03-dem",
                title="Democratic Party",
                question="Alaska Senate Election Winner",
                category="politics",
            ),
        ],
        max_results=5,
        min_score=0.08,
    )

    assert [market.market_id for market, _score, _meta in matches] == [
        "ewc-usse-ak-2026-11-03-rep",
        "ewc-usse-ak-2026-11-03-dem",
    ]


def test_match_polymarket_markets_uses_question_and_subtitle_text():
    matches = match_polymarket_markets(
        _news("Kansas governor election tightens after new polling"),
        [
            _market(
                market_id="ewc-usgub-ks-2026-11-03-dem",
                title="Democratic Party",
                question="Kansas Governor Election Winner",
                subtitle="2026 race",
                category="politics",
            ),
        ],
        max_results=5,
        min_score=0.01,
    )

    assert [market.market_id for market, _score, _meta in matches] == [
        "ewc-usgub-ks-2026-11-03-dem"
    ]
    assert matches[0][2]["polymarket_matched_tokens"] == [
        "election",
        "governor",
        "kansas",
    ]


def test_match_polymarket_markets_suppresses_sports_false_positives():
    matches = match_polymarket_markets(
        _news(
            "The Memo: Spencer Pratt comes up short in Los Angeles, drawing "
            "hollow claims of voter fraud"
        ),
        [
            _market(
                market_id="tec-mlb-champ-2026-09-27-lad",
                title="Los Angeles Dodgers",
                question="World Series Champion",
                category="sports",
            ),
            _market(
                market_id="ewc-usse-ca-2026-11-03-dem",
                title="Democratic Party",
                question="California Senate Election Winner",
                category="politics",
            ),
        ],
        max_results=5,
        min_score=0.01,
    )

    assert [market.market_id for market, _score, _meta in matches] == []


def test_polymarket_paper_runtime_default_market_limit_covers_broader_universe():
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([]),
        route_analysis=lambda analysis, **kwargs: None,
        keyword_stats=None,
    )

    assert runtime._market_limit >= 500


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
