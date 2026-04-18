"""
Tests for main.py candidate processing.

Covers: SignalAnalysis construction and no-signal early return.
"""

import asyncio
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config as _cfg_module
from feeds.subreddit_selector import filter_disabled_subreddits, select_subreddits
from feeds.search_news_monitor import run_search_news_monitor
from feeds.gdelt_monitor import run_gdelt_monitor
from feeds import NewsItem
from kalshi import KalshiMarket
from main import TradingBot


def _make_bot_stub():
    bot = TradingBot.__new__(TradingBot)
    bot.ws = MagicMock()
    bot.ws.get_yes_price.return_value = None
    bot.ws.watch = MagicMock()
    bot.paper = MagicMock()
    bot.paper.credibility.get_multiplier.return_value = 1.0
    bot.paper.get_notional_bankroll.return_value = 500.0
    bot.paper.portfolio = MagicMock()
    bot.paper.portfolio.open_positions.return_value = []
    bot.executor = MagicMock()
    bot.executor.execute = AsyncMock(return_value="trade-123")
    bot.matcher = MagicMock()
    bot.matcher.find_all_candidates = AsyncMock(return_value=[])
    bot.matcher._cache = MagicMock()
    bot.matcher._cache.get_markets = AsyncMock(return_value=[])
    bot.source_stats = MagicMock()
    bot.keyword_stats = MagicMock()
    bot._dedup = MagicMock()
    bot._dedup.is_duplicate.return_value = False
    bot._news_queue = asyncio.PriorityQueue(maxsize=10)
    bot._ws_prev_prices = {}
    bot._ws_velocity = defaultdict(lambda: deque(maxlen=60))
    bot._last_search_triggered = {}
    bot._last_drift_logged = {}
    bot._market_refresh_lock = asyncio.Lock()
    bot._known_market_tickers = set()
    return bot


def _make_market():
    return KalshiMarket(
        ticker="KXTEST-25DEC31",
        title="Will test event happen?",
        yes_bid=49.0,
        yes_ask=51.0,
        yes_price=50.0,
        volume=100,
        open_interest=200,
        close_time="2026-12-31T23:59:59Z",
        status="open",
        series_ticker="KXTEST",
        subtitle="Test criteria",
        result="",
    )


def _make_news():
    return NewsItem(
        headline="Test event headline",
        url="https://example.com/story",
        source="Reuters",
        published=datetime.now(timezone.utc) - timedelta(seconds=30),
        body="test body",
        item_id="id-1",
    )


@pytest.mark.asyncio
async def test_process_candidate_builds_signal_analysis_and_executes(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(_cfg_module.cfg, "min_bet_dollars", 2.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_half_life", 14.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_floor", 0.20)
    monkeypatch.setattr(_cfg_module.cfg, "dynamic_max_bet", lambda bankroll: 75.0)

    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()
    match_meta = {
        "pre_llm_quality_pass": False,
        "pre_llm_semantic_overlap_count": 1,
        "pre_llm_semantic_overlap_ratio": 0.2,
        "pre_llm_gate_reason": "weak_semantic_overlap",
    }

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        0.65, 0.8, ["missile strike"], "test reasoning", "yes", "moderate", 0.8
    ))) as estimate_mock, patch("main.kelly_bet", return_value=(0.12, 15.0, 12.0)), \
         patch("utils.logger.trade_log.log_signal"), \
         patch("utils.logger.trade_log.log_opportunity") as opportunity_mock:
        await bot._process_candidate(news, market, 0.42, match_meta)

    bot.executor.execute.assert_awaited_once()
    estimate_mock.assert_awaited_once_with(
        news,
        market,
        keyword_stats=bot.keyword_stats,
        match_meta=match_meta,
    )
    analysis = bot.executor.execute.await_args.args[0]
    assert analysis.news_item is news
    assert analysis.market.ticker == "KXTEST-25DEC31"
    assert analysis.estimated_probability == pytest.approx(0.65)
    assert analysis.market_yes_price == pytest.approx(50.0)
    assert analysis.edge == pytest.approx(0.15)
    assert analysis.side == "yes"
    assert analysis.kelly_fraction == pytest.approx(0.12)
    assert analysis.kelly_dollars == pytest.approx(15.0)
    assert analysis.capped_dollars == pytest.approx(12.0)
    assert analysis.keywords_matched == ["missile strike"]
    assert analysis.reasoning == "test reasoning"
    assert analysis.confidence == pytest.approx(0.8)
    assert analysis.match_score == pytest.approx(0.42)
    assert analysis.signal_type == "news"
    assert analysis.llm_direction == "yes"
    assert analysis.llm_magnitude == "moderate"
    assert analysis.llm_confidence == pytest.approx(0.8)
    bot.source_stats.increment_signals.assert_called_with("Reuters")
    bot.source_stats.increment_opportunities.assert_called_with("Reuters")
    bot.source_stats.increment_trades.assert_called_with("Reuters")
    bot.ws.watch.assert_called_with(["KXTEST-25DEC31"])
    opportunity_mock.assert_called_once_with(
        ticker=market.ticker,
        market_title=market.title,
        market_yes_price=market.yes_price,
        estimated_probability=0.65,
        edge=pytest.approx(0.15),
        kelly_fraction=0.12,
        kelly_dollars=15.0,
        capped_dollars=12.0,
        side="yes",
        reasoning="test reasoning",
        source=news.source,
        headline=news.headline,
        method="llm",
        llm_direction="yes",
        llm_magnitude="moderate",
    )


@pytest.mark.asyncio
async def test_process_candidate_returns_early_when_no_keywords(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "No relevant keywords found -- no signal.", None, None, None
    ))), patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        await bot._process_candidate(news, market, 0.20)

    bot.executor.execute.assert_not_called()
    bot.source_stats.increment_signals.assert_not_called()
    bot.source_stats.increment_opportunities.assert_not_called()
    bot.source_stats.increment_trades.assert_not_called()
    bot.ws.watch.assert_not_called()
    reject_mock.assert_called_once_with(
        reason="no_keywords",
        ticker=market.ticker,
        source=news.source,
        headline=news.headline,
        match_score=0.20,
    )


@pytest.mark.asyncio
async def test_process_candidate_skips_stale_news_before_estimation(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr("main.MAX_NEWS_AGE_SECONDS", 300)
    bot = _make_bot_stub()
    news = _make_news()
    news.published = datetime.now(timezone.utc) - timedelta(seconds=600)
    market = _make_market()

    with patch("main.estimate_probability", new=AsyncMock()) as estimate_mock, \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        await bot._process_candidate(news, market, 0.20)

    estimate_mock.assert_not_awaited()
    bot.executor.execute.assert_not_called()
    bot.ws.watch.assert_not_called()
    reject_mock.assert_called_once()
    kwargs = reject_mock.call_args.kwargs
    assert kwargs["reason"] == "stale_news"
    assert kwargs["ticker"] == market.ticker
    assert kwargs["source"] == news.source
    assert kwargs["headline"] == news.headline
    assert kwargs["match_score"] == 0.20
    assert kwargs["age_seconds"] == pytest.approx(600.0, abs=5.0)


@pytest.mark.asyncio
async def test_process_candidate_uses_websocket_price_in_handoff(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(_cfg_module.cfg, "min_bet_dollars", 2.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_half_life", 14.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_floor", 0.20)
    monkeypatch.setattr(_cfg_module.cfg, "dynamic_max_bet", lambda bankroll: 75.0)

    bot = _make_bot_stub()
    bot.ws.get_yes_price.return_value = 62.0
    news = _make_news()
    market = _make_market()

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        0.70, 0.7, ["missile strike"], "test reasoning", "yes", "small", 0.7
    ))), patch("main.kelly_bet", return_value=(0.10, 10.0, 8.0)), \
         patch("utils.logger.trade_log.log_signal"), \
         patch("utils.logger.trade_log.log_opportunity"):
        await bot._process_candidate(news, market, 0.33)

    analysis = bot.executor.execute.await_args.args[0]
    assert analysis.market_yes_price == pytest.approx(62.0)
    assert analysis.market.yes_price == pytest.approx(62.0)
    assert analysis.market.yes_bid == pytest.approx(61.0)
    assert analysis.market.yes_ask == pytest.approx(63.0)
    assert analysis.edge == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_process_candidate_uses_paper_placeholder_when_kelly_returns_zero(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(_cfg_module.cfg, "min_bet_dollars", 2.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_half_life", 14.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_floor", 0.20)
    monkeypatch.setattr(_cfg_module.cfg, "dynamic_max_bet", lambda bankroll: 75.0)

    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        0.60, 0.8, ["missile strike"], "test reasoning", "yes", "small", 0.8
    ))), patch("main.kelly_bet", return_value=(0.0, 0.0, 0.0)), \
         patch("utils.logger.trade_log.log_signal"), \
         patch("utils.logger.trade_log.log_opportunity"):
        await bot._process_candidate(news, market, 0.25)

    analysis = bot.executor.execute.await_args.args[0]
    assert analysis.capped_dollars == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_process_candidate_live_mode_returns_early_when_kelly_returns_zero(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(_cfg_module.cfg, "min_bet_dollars", 2.0)
    monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_half_life", 14.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_floor", 0.20)
    monkeypatch.setattr(_cfg_module.cfg, "dynamic_max_bet", lambda bankroll: 75.0)

    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        0.60, 0.8, ["missile strike"], "test reasoning", "yes", "small", 0.8
    ))), patch("main.kelly_bet", return_value=(0.0, 0.0, 0.0)), \
         patch("utils.logger.trade_log.log_signal"), \
         patch("utils.logger.trade_log.log_opportunity"):
        await bot._process_candidate(news, market, 0.25)

    bot.executor.execute.assert_not_called()
    bot.source_stats.increment_trades.assert_not_called()
    bot.ws.watch.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_news_drops_duplicates_before_queueing():
    bot = _make_bot_stub()
    news = _make_news()
    bot._dedup.is_duplicate.return_value = True

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    bot._dedup.is_duplicate.assert_called_once_with(news.headline, source=news.source)
    assert bot._news_queue.empty()
    early_drop_mock.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_news_drops_disabled_source_before_queueing(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", {"BBC News"})
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_SECONDS", 600)
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_BY_SOURCE", {})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    news = _make_news()
    news.source = "BBC News"

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    bot._dedup.is_duplicate.assert_not_called()
    assert bot._news_queue.empty()
    early_drop_mock.assert_called_once_with(
        reason="disabled_source",
        source=news.source,
        headline=news.headline,
    )


@pytest.mark.asyncio
async def test_enqueue_news_drops_disabled_source_case_insensitively(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", {"BBC News"})
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_SECONDS", 600)
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_BY_SOURCE", {})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    news = _make_news()
    news.source = "bbc news"

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    bot._dedup.is_duplicate.assert_not_called()
    assert bot._news_queue.empty()
    early_drop_mock.assert_called_once_with(
        reason="disabled_source",
        source=news.source,
        headline=news.headline,
    )


@pytest.mark.asyncio
async def test_enqueue_news_drops_stale_items_using_default_threshold(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_SECONDS", 600)
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_BY_SOURCE", {})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    news = _make_news()
    news.published = datetime.now(timezone.utc) - timedelta(seconds=601)

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    bot._dedup.is_duplicate.assert_not_called()
    assert bot._news_queue.empty()
    early_drop_mock.assert_called_once()
    kwargs = early_drop_mock.call_args.kwargs
    assert kwargs["reason"] == "stale_by_source_policy"
    assert kwargs["source"] == news.source
    assert kwargs["headline"] == news.headline
    assert kwargs["age_seconds"] == pytest.approx(601.0, abs=5.0)
    assert kwargs["threshold_seconds"] == 600


@pytest.mark.asyncio
async def test_enqueue_news_applies_per_source_override_threshold(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_SECONDS", 600)
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_BY_SOURCE", {"Reuters": 1800})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    news = _make_news()
    news.source = "Reuters"
    news.published = datetime.now(timezone.utc) - timedelta(seconds=1200)

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    early_drop_mock.assert_not_called()
    bot._dedup.is_duplicate.assert_called_once_with(news.headline, source=news.source)
    assert bot._news_queue.qsize() == 1


@pytest.mark.asyncio
async def test_enqueue_news_drops_missing_timestamp_when_policy_enabled(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_SECONDS", 600)
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_BY_SOURCE", {})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    news = _make_news()
    news.published = None

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    bot._dedup.is_duplicate.assert_not_called()
    assert bot._news_queue.empty()
    early_drop_mock.assert_called_once_with(
        reason="missing_timestamp",
        source=news.source,
        headline=news.headline,
    )


@pytest.mark.asyncio
async def test_enqueue_news_drops_invalid_naive_timestamp_when_policy_enabled(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_SECONDS", 600)
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_BY_SOURCE", {})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    news = _make_news()
    news.published = datetime.now()

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    bot._dedup.is_duplicate.assert_not_called()
    assert bot._news_queue.empty()
    early_drop_mock.assert_called_once_with(
        reason="missing_timestamp",
        source=news.source,
        headline=news.headline,
    )


@pytest.mark.asyncio
async def test_enqueue_news_fresh_item_passes_through_unchanged(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_SECONDS", 600)
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_BY_SOURCE", {})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    news = _make_news()
    news.published = datetime.now(timezone.utc) - timedelta(seconds=120)

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock, \
         patch("utils.logger.trade_log.log_early_fresh_pass") as fresh_pass_mock:
        await bot._enqueue_news(news)

    early_drop_mock.assert_not_called()
    fresh_pass_mock.assert_called_once()
    kwargs = fresh_pass_mock.call_args.kwargs
    assert kwargs["source"] == news.source
    assert kwargs["headline"] == news.headline
    assert kwargs["age_seconds"] == pytest.approx(120.0, abs=5.0)
    bot._dedup.is_duplicate.assert_called_once_with(news.headline, source=news.source)
    assert bot._news_queue.qsize() == 1


@pytest.mark.asyncio
async def test_enqueue_news_assigns_source_priority(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    reddit_news = _make_news()
    reddit_news.source = "r/internationalnews"
    wire_news = _make_news()
    wire_news.source = "Reuters"

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock, \
         patch("utils.logger.trade_log.log_early_fresh_pass") as fresh_pass_mock:
        await bot._enqueue_news(reddit_news)
        await bot._enqueue_news(wire_news)

    first_priority, _first_seq, first_news = bot._news_queue.get_nowait()
    second_priority, _second_seq, second_news = bot._news_queue.get_nowait()
    assert (first_priority, first_news.source) == (1, "Reuters")
    assert (second_priority, second_news.source) == (3, "r/internationalnews")
    early_drop_mock.assert_not_called()
    assert fresh_pass_mock.call_count == 2


@pytest.mark.asyncio
async def test_enqueue_news_disabled_source_takes_precedence_over_stale_checks(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", {"BBC News"})
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_SECONDS", 600)
    monkeypatch.setattr("main.EARLY_MAX_NEWS_AGE_BY_SOURCE", {})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    news = _make_news()
    news.source = "BBC News"
    news.published = None

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    bot._dedup.is_duplicate.assert_not_called()
    assert bot._news_queue.empty()
    early_drop_mock.assert_called_once_with(
        reason="disabled_source",
        source=news.source,
        headline=news.headline,
    )


@pytest.mark.asyncio
async def test_enqueue_news_drops_when_queue_is_full(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", set())
    bot = _make_bot_stub()
    bot._news_queue = asyncio.PriorityQueue(maxsize=1)
    first = _make_news()
    second = _make_news()
    second.item_id = "id-2"
    second.headline = "Second headline"

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock, \
         patch("utils.logger.trade_log.log_early_fresh_pass") as fresh_pass_mock:
        await bot._enqueue_news(first)
        await bot._enqueue_news(second)

    assert bot._news_queue.qsize() == 1
    _priority, _seq, queued_news = bot._news_queue.get_nowait()
    assert queued_news.item_id == "id-1"
    early_drop_mock.assert_not_called()
    assert fresh_pass_mock.call_count == 2


@pytest.mark.asyncio
async def test_enqueue_news_drops_bing_family(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", {"bing_news_query", "google_news_query"})
    bot = _make_bot_stub()
    news = _make_news()
    news.source = "donald trump something - BingNews"

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    assert bot._news_queue.empty()
    bot._dedup.is_duplicate.assert_not_called()
    early_drop_mock.assert_called_once_with(
        reason="disabled_source_family",
        source=news.source,
        headline=news.headline,
    )


@pytest.mark.asyncio
async def test_enqueue_news_drops_google_family(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", {"bing_news_query", "google_news_query"})
    bot = _make_bot_stub()
    news = _make_news()
    news.source = '"some query" - Google News'

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    assert bot._news_queue.empty()
    bot._dedup.is_duplicate.assert_not_called()
    early_drop_mock.assert_called_once_with(
        reason="disabled_source_family",
        source=news.source,
        headline=news.headline,
    )


@pytest.mark.asyncio
async def test_enqueue_news_keeps_publisher_rss(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", {"bing_news_query", "google_news_query"})
    bot = _make_bot_stub()
    news = _make_news()
    news.source = "BBC News"

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock, \
         patch("utils.logger.trade_log.log_early_fresh_pass") as fresh_pass_mock:
        await bot._enqueue_news(news)

    early_drop_mock.assert_not_called()
    fresh_pass_mock.assert_called_once()
    bot._dedup.is_duplicate.assert_called_once_with(news.headline, source=news.source)
    assert bot._news_queue.qsize() == 1


@pytest.mark.asyncio
async def test_enqueue_news_family_filter_runs_before_timestamp_logic(monkeypatch):
    monkeypatch.setattr("main.DISABLED_NEWS_SOURCES", set())
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", {"bing_news_query", "google_news_query"})
    monkeypatch.setattr("main.EARLY_DROP_IF_NO_TIMESTAMP", True)
    bot = _make_bot_stub()
    news = _make_news()
    news.source = "donald trump something - BingNews"
    news.published = None

    with patch("utils.logger.trade_log.log_early_stale_drop") as early_drop_mock:
        await bot._enqueue_news(news)

    assert bot._news_queue.empty()
    bot._dedup.is_duplicate.assert_not_called()
    early_drop_mock.assert_called_once_with(
        reason="disabled_source_family",
        source=news.source,
        headline=news.headline,
    )


@pytest.mark.asyncio
async def test_news_consumer_task_processes_and_marks_done():
    bot = _make_bot_stub()
    bot.on_news_item = AsyncMock()
    news = _make_news()
    await bot._news_queue.put((1, 0, news))

    consumer = asyncio.create_task(bot._news_consumer_task())
    await asyncio.wait_for(bot._news_queue.join(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    bot.on_news_item.assert_awaited_once_with(news)
    assert bot._news_queue.qsize() == 0


@pytest.mark.asyncio
async def test_news_consumer_task_continues_after_handler_exception():
    bot = _make_bot_stub()
    first = _make_news()
    second = _make_news()
    second.item_id = "id-2"
    second.headline = "Second headline"
    bot.on_news_item = AsyncMock(side_effect=[RuntimeError("boom"), None])
    await bot._news_queue.put((1, 0, first))
    await bot._news_queue.put((1, 1, second))

    consumer = asyncio.create_task(bot._news_consumer_task())
    await asyncio.wait_for(bot._news_queue.join(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert bot.on_news_item.await_count == 2
    assert bot.on_news_item.await_args_list[0].args == (first,)
    assert bot.on_news_item.await_args_list[1].args == (second,)


@pytest.mark.asyncio
async def test_process_fade_tweet_returns_early_without_pattern():
    bot = _make_bot_stub()
    tweet = _make_news()

    with patch("analysis.fade_signal.detect_fade_pattern", return_value=None):
        await bot._process_fade_tweet(tweet, "Kalshi")

    bot.matcher.find_all_candidates.assert_not_awaited()
    bot.executor.execute.assert_not_called()
    bot.ws.watch.assert_not_called()


@pytest.mark.asyncio
async def test_warm_ws_subscriptions_waits_for_existing_cache_without_refresh():
    bot = _make_bot_stub()
    bot.matcher._cache._markets = []
    market = _make_market()

    async def _populate_cache():
        await asyncio.sleep(0.01)
        bot.matcher._cache._markets = [market]

    populate_task = asyncio.create_task(_populate_cache())
    try:
        await asyncio.wait_for(bot._warm_ws_subscriptions(), timeout=0.5)
    finally:
        await populate_task

    bot.matcher._cache.get_markets.assert_not_called()
    bot.ws.watch.assert_called_once_with([market.ticker])


@pytest.mark.asyncio
async def test_refresh_market_cache_once_logs_startup_warmup_duration(caplog):
    bot = _make_bot_stub()
    market = _make_market()
    bot.matcher.refresh_cache = AsyncMock()
    bot.matcher._cache.get_markets = AsyncMock(return_value=[market])
    bot.matcher._cache._markets = [market]

    with caplog.at_level("INFO", logger="main"):
        await bot._refresh_market_cache_once(initial=True)

    assert "[STARTUP] Market cache warmup started" in caplog.text
    assert "[STARTUP] Market cache ready: 1 markets (" in caplog.text
    bot.matcher.refresh_cache.assert_awaited_once()
    bot.ws.watch.assert_called_once_with([market.ticker])


@pytest.mark.asyncio
async def test_process_fade_tweet_builds_geo_fade_handoff_with_ws_price(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    bot = _make_bot_stub()
    bot.ws.get_yes_price.return_value = 72.0
    tweet = _make_news()
    tweet.headline = "Prediction market is going to the moon"
    market = _make_market()
    bot.matcher.find_all_candidates.return_value = [(market, 0.61)]

    with patch("analysis.fade_signal.detect_fade_pattern", return_value="bullish"):
        await bot._process_fade_tweet(tweet, "Kalshi")

    analysis = bot.executor.execute.await_args.args[0]
    assert analysis.signal_type == "fade_tweet"
    assert analysis.side == "no"
    assert analysis.market_yes_price == pytest.approx(72.0)
    assert analysis.market.yes_bid == pytest.approx(71.0)
    assert analysis.market.yes_ask == pytest.approx(73.0)
    assert analysis.match_score == pytest.approx(0.61)
    assert analysis.confidence == pytest.approx(0.3)
    assert analysis.reasoning.startswith("[FADE/GEO/@Kalshi] bullish:")
    bot.ws.watch.assert_called_with(["KXTEST-25DEC31"])


@pytest.mark.asyncio
async def test_process_fade_tweet_tags_sports_markets():
    bot = _make_bot_stub()
    tweet = _make_news()
    market = _make_market()
    market = replace(
        market,
        ticker="KXNBA-LALBOS-25DEC31",
        series_ticker="KXNBA",
    )
    bot.matcher.find_all_candidates.return_value = [(market, 0.55)]

    with patch("analysis.fade_signal.detect_fade_pattern", return_value="bearish"):
        await bot._process_fade_tweet(tweet, "Sharps")

    analysis = bot.executor.execute.await_args.args[0]
    assert analysis.side == "yes"
    assert analysis.reasoning.startswith("[FADE/SPORTS/@Sharps] bearish:")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crossing", "expected_side"),
    [("high_cross", "no"), ("low_cross", "yes")],
)
async def test_process_price_fade_builds_representative_handoff(crossing, expected_side, monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    bot = _make_bot_stub()
    market = _make_market()
    bot.matcher._cache.get_markets.return_value = [market]

    await bot._process_price_fade(
        ticker=market.ticker,
        crossing=crossing,
        now_mid=86.0 if crossing == "high_cross" else 14.0,
        yes_bid=85.0 if crossing == "high_cross" else 13.0,
        yes_ask=87.0 if crossing == "high_cross" else 15.0,
    )

    analysis = bot.executor.execute.await_args.args[0]
    assert analysis.signal_type == "price_fade"
    assert analysis.side == expected_side
    assert analysis.market.ticker == market.ticker
    assert analysis.market_yes_price == pytest.approx(86.0 if crossing == "high_cross" else 14.0)
    assert analysis.news_item.source == "price_fade"
    assert analysis.news_item.url == f"kalshi://price_fade/{market.ticker}"
    assert crossing in analysis.reasoning


@pytest.mark.asyncio
async def test_process_price_fade_skips_sports_tickers():
    bot = _make_bot_stub()

    await bot._process_price_fade(
        ticker="KXNBA-LALBOS-25DEC31",
        crossing="high_cross",
        now_mid=86.0,
        yes_bid=85.0,
        yes_ask=87.0,
    )

    bot.matcher._cache.get_markets.assert_not_awaited()
    bot.executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_on_price_update_first_tick_initializes_state_only():
    bot = _make_bot_stub()
    bot._process_price_fade = AsyncMock()
    bot._trigger_targeted_search = AsyncMock()

    with patch("main.time.monotonic", return_value=100.0), \
         patch("analysis.fade_signal.detect_price_fade", return_value=None), \
         patch("main.asyncio.create_task") as create_task_mock:
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)

    assert bot._ws_prev_prices["KXTEST-25DEC31"] == pytest.approx(50.0)
    assert list(bot._ws_velocity["KXTEST-25DEC31"]) == [(100.0, 50.0)]
    bot._process_price_fade.assert_not_awaited()
    create_task_mock.assert_not_called()


@pytest.mark.asyncio
async def test_on_price_update_no_trigger_when_price_does_not_cross_thresholds():
    bot = _make_bot_stub()
    bot._process_price_fade = AsyncMock()
    bot._trigger_targeted_search = AsyncMock()

    with patch("main.time.monotonic", side_effect=[100.0, 101.0]), \
         patch("analysis.fade_signal.detect_price_fade", return_value=None), \
         patch("main.asyncio.create_task") as create_task_mock:
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)
        await bot._on_price_update("KXTEST-25DEC31", 49.5, 50.5)

    assert bot._ws_prev_prices["KXTEST-25DEC31"] == pytest.approx(50.0)
    assert list(bot._ws_velocity["KXTEST-25DEC31"]) == [(100.0, 50.0), (101.0, 50.0)]
    bot._process_price_fade.assert_not_awaited()
    create_task_mock.assert_not_called()


@pytest.mark.asyncio
async def test_on_price_update_delegates_to_price_fade_only_on_crossing():
    bot = _make_bot_stub()
    bot._process_price_fade = AsyncMock()
    bot._trigger_targeted_search = AsyncMock()

    with patch("main.time.monotonic", side_effect=[100.0, 101.0]), \
         patch("analysis.fade_signal.detect_price_fade", return_value="high_cross"), \
         patch("main.asyncio.create_task") as create_task_mock:
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)
        await bot._on_price_update("KXTEST-25DEC31", 50.0, 52.0)

    bot._process_price_fade.assert_awaited_once_with(
        "KXTEST-25DEC31", "high_cross", 51.0, 50.0, 52.0
    )
    create_task_mock.assert_not_called()


@pytest.mark.asyncio
async def test_on_price_update_triggers_targeted_search_when_price_move_exceeds_threshold():
    bot = _make_bot_stub()
    bot._process_price_fade = AsyncMock()
    bot._trigger_targeted_search = AsyncMock()

    def _capture_task(coro):
        coro.close()
        return MagicMock()

    with patch("main.time.monotonic", side_effect=[2000.0, 2001.0]), \
         patch("analysis.fade_signal.detect_price_fade", return_value=None), \
         patch("main.asyncio.create_task", side_effect=_capture_task) as create_task_mock:
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)
        await bot._on_price_update("KXTEST-25DEC31", 60.0, 62.0)

    bot._trigger_targeted_search.assert_called_once_with("KXTEST-25DEC31")
    create_task_mock.assert_called_once()
    assert bot._last_search_triggered["KXTEST-25DEC31"] == pytest.approx(2001.0)
    bot._process_price_fade.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_price_update_respects_targeted_search_cooldown():
    bot = _make_bot_stub()
    bot._process_price_fade = AsyncMock()
    bot._trigger_targeted_search = AsyncMock()

    with patch("main.time.monotonic", side_effect=[2000.0, 2001.0]), \
         patch("analysis.fade_signal.detect_price_fade", return_value=None), \
         patch("main.asyncio.create_task") as create_task_mock:
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)
        bot._last_search_triggered["KXTEST-25DEC31"] = 2000.5
        await bot._on_price_update("KXTEST-25DEC31", 60.0, 62.0)

    bot._trigger_targeted_search.assert_not_called()
    create_task_mock.assert_not_called()
    assert bot._last_search_triggered["KXTEST-25DEC31"] == pytest.approx(2000.5)


@pytest.mark.asyncio
async def test_on_price_update_does_not_duplicate_triggers_for_identical_updates():
    bot = _make_bot_stub()
    bot._process_price_fade = AsyncMock()
    bot._trigger_targeted_search = AsyncMock()

    with patch("main.time.monotonic", side_effect=[100.0, 101.0, 102.0]), \
         patch("analysis.fade_signal.detect_price_fade", return_value=None), \
         patch("main.asyncio.create_task") as create_task_mock:
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)

    assert list(bot._ws_velocity["KXTEST-25DEC31"]) == [
        (100.0, 50.0),
        (101.0, 50.0),
        (102.0, 50.0),
    ]
    bot._process_price_fade.assert_not_awaited()
    bot._trigger_targeted_search.assert_not_called()
    create_task_mock.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_targeted_search_exits_when_market_is_missing():
    bot = _make_bot_stub()
    bot.matcher._cache.get_markets.return_value = [_make_market()]

    with patch("feeds.search_news_monitor._markets_to_queries") as queries_mock, \
         patch("feeds.rss_monitor.poll_feed", new=AsyncMock()) as poll_feed_mock:
        await bot._trigger_targeted_search("KXOTHER-25DEC31")

    queries_mock.assert_not_called()
    poll_feed_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_targeted_search_exits_when_no_query_is_available():
    bot = _make_bot_stub()
    market = _make_market()
    bot.matcher._cache.get_markets.return_value = [market]

    with patch("feeds.search_news_monitor._markets_to_queries", return_value=[]), \
         patch("feeds.rss_monitor.poll_feed", new=AsyncMock()) as poll_feed_mock:
        await bot._trigger_targeted_search(market.ticker)

    poll_feed_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_targeted_search_delegates_single_query_into_enqueue_path():
    with patch("config.DISABLED_SOURCE_FAMILIES", set()):
        bot = _make_bot_stub()
        market = _make_market()
        bot.matcher._cache.get_markets.return_value = [market]
        poll_feed_mock = AsyncMock()

        with patch("feeds.search_news_monitor._markets_to_queries", return_value=["query one", "query two"]), \
             patch("feeds.search_news_monitor._gnews_url", side_effect=lambda q: f"gnews:{q}") as gnews_mock, \
             patch("feeds.search_news_monitor._bing_url", side_effect=lambda q: f"bing:{q}") as bing_mock, \
             patch("feeds.rss_monitor.poll_feed", new=poll_feed_mock):
            await bot._trigger_targeted_search(market.ticker)

    gnews_mock.assert_called_once_with("query one")
    bing_mock.assert_called_once_with("query one")
    assert poll_feed_mock.await_count == 2
    first_call = poll_feed_mock.await_args_list[0].args
    second_call = poll_feed_mock.await_args_list[1].args
    assert first_call[0] == "gnews:query one"
    assert second_call[0] == "bing:query one"
    assert first_call[1].__self__ is bot
    assert second_call[1].__self__ is bot
    assert first_call[1].__func__ is bot._enqueue_news.__func__
    assert second_call[1].__func__ is bot._enqueue_news.__func__
    assert first_call[2] is second_call[2]


@pytest.mark.asyncio
async def test_trigger_targeted_search_contains_internal_exceptions():
    bot = _make_bot_stub()

    with patch.object(bot.matcher._cache, "get_markets", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await bot._trigger_targeted_search("KXTEST-25DEC31")


@pytest.mark.asyncio
async def test_trigger_targeted_search_does_not_use_extra_queries():
    with patch("config.DISABLED_SOURCE_FAMILIES", set()):
        bot = _make_bot_stub()
        market = _make_market()
        bot.matcher._cache.get_markets.return_value = [market]
        poll_feed_mock = AsyncMock()

        with patch("feeds.search_news_monitor._markets_to_queries", return_value=["first", "second", "third"]), \
             patch("feeds.search_news_monitor._gnews_url", side_effect=lambda q: f"gnews:{q}") as gnews_mock, \
             patch("feeds.search_news_monitor._bing_url", side_effect=lambda q: f"bing:{q}") as bing_mock, \
             patch("feeds.rss_monitor.poll_feed", new=poll_feed_mock):
            await bot._trigger_targeted_search(market.ticker)

    gnews_mock.assert_called_once_with("first")
    bing_mock.assert_called_once_with("first")
    assert poll_feed_mock.await_count == 2


def test_filter_disabled_subreddits_removes_disabled_reddit_sources(monkeypatch):
    monkeypatch.setattr(
        "feeds.subreddit_selector.DISABLED_NEWS_SOURCES",
        {"r/worldnews", "r/geopolitics"},
    )

    allowed, skipped = filter_disabled_subreddits(
        ["worldnews", "ArmedConflicts", "geopolitics", "worldnews"]
    )

    assert allowed == ["ArmedConflicts"]
    assert skipped == ["worldnews", "geopolitics"]


def test_select_subreddits_filters_disabled_core_and_topic_subreddits(monkeypatch):
    monkeypatch.setattr(
        "feeds.subreddit_selector.DISABLED_NEWS_SOURCES",
        {"r/worldnews", "r/CredibleDefense", "r/WarCollege"},
    )
    market = KalshiMarket(
        ticker="KXTEST-25DEC31",
        title="Will war in Ukraine escalate?",
        yes_bid=49.0,
        yes_ask=51.0,
        yes_price=50.0,
        volume=100,
        open_interest=200,
        close_time="2026-12-31T23:59:59Z",
        status="open",
        series_ticker="KXTEST",
        subtitle="Military conflict",
        result="",
    )

    selected = select_subreddits([market], source_stats=None, db_path=None)

    assert "worldnews" not in selected
    assert "CredibleDefense" not in selected
    assert "WarCollege" not in selected
    assert "ArmedConflicts" in selected


@pytest.mark.asyncio
async def test_search_monitor_exits_when_all_search_families_are_disabled(monkeypatch):
    monkeypatch.setattr(
        "feeds.search_news_monitor.DISABLED_SOURCE_FAMILIES",
        {"google_news_query", "bing_news_query"},
    )
    callback = AsyncMock()

    with patch("feeds.search_news_monitor.poll_feed", new=AsyncMock()) as poll_feed_mock:
        await run_search_news_monitor(callback, get_markets=lambda: [_make_market()])

    poll_feed_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_gdelt_monitor_exits_when_source_is_disabled(monkeypatch):
    monkeypatch.setattr("feeds.gdelt_monitor.DISABLED_NEWS_SOURCES", {"GDELT"})
    callback = AsyncMock()

    await run_gdelt_monitor(callback, get_markets=lambda: [_make_market()])

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_targeted_search_skips_when_search_families_disabled(monkeypatch):
    monkeypatch.setattr("config.DISABLED_SOURCE_FAMILIES", {"google_news_query", "bing_news_query"})
    bot = _make_bot_stub()
    market = _make_market()
    bot.matcher._cache.get_markets.return_value = [market]

    with patch("feeds.search_news_monitor._markets_to_queries", return_value=["query one"]), \
         patch("feeds.rss_monitor.poll_feed", new=AsyncMock()) as poll_feed_mock:
        await bot._trigger_targeted_search(market.ticker)

    poll_feed_mock.assert_not_awaited()
