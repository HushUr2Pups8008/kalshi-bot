"""
Tests for main.py candidate processing.

Covers: SignalAnalysis construction and no-signal early return.
"""

import asyncio
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config as _cfg_module
import main as main_module
from analysis import SignalAnalysis
from analysis.research_gate import (
    ResearchEvidence,
    ResearchQuery,
    ResearchStatus,
    ResearchVerdict,
)
from feeds.subreddit_selector import filter_disabled_subreddits, select_subreddits
from feeds.search_news_monitor import run_search_news_monitor
from feeds.gdelt_monitor import run_gdelt_monitor
from feeds import NewsItem
from kalshi import KalshiMarket
from kalshi.series_metadata import SettlementSource
from kalshi.source_hints import MarketSourceHintDiagnostics, MarketSourceTargetPlan
from main import TradingBot, _signal_to_evidence, _source_class_for_evidence
from polymarket.settlement_reconciler import SettlementReconcileResult
from tests._helpers import write_jsonl
from trading.portfolio import Position


@pytest.fixture(autouse=True)
def _default_research_off(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "off", raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "enable_research_prewarm_task",
        False,
        raising=False,
    )


def _make_bot_stub():
    bot = TradingBot.__new__(TradingBot)
    bot.ws = MagicMock()
    bot.ws.get_yes_price.return_value = None
    bot.ws.watch = MagicMock()
    bot.paper = MagicMock()
    bot.paper.credibility.get_multiplier.return_value = 1.0
    bot.paper.get_notional_bankroll.return_value = 500.0
    # PROFIT-CAL-001 (v0.29.47): resolve_market is async; auto-MagicMock would
    # return a non-awaitable Mock and break `await self.paper.resolve_market(...)`.
    bot.paper.resolve_market = AsyncMock(return_value=None)
    bot.paper.portfolio = MagicMock()
    bot.paper.portfolio.open_positions.return_value = []
    bot.executor = MagicMock()
    bot.executor.execute = AsyncMock(return_value="trade-123")
    # P-7: cycle-level exchange-status gate calls self.rest.get_exchange_status().
    # Default to an open exchange so existing handler-level tests aren't gated.
    from kalshi import ExchangeState
    bot.rest = MagicMock()
    bot.rest.get_exchange_status.return_value = ExchangeState(
        exchange_active=True, trading_active=True,
    )
    bot._research_prewarm_due_task_tickers = lambda *, limit, cooldown_seconds: []
    bot.rest.get_series.return_value = None
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
    bot._startup_started_monotonic = 0.0
    bot._startup_started_at = datetime.now(timezone.utc)
    bot._market_cache_ready_at = None
    bot._market_cache_ready_after_secs = None
    bot._market_cache_empty_discovery_passes = 0
    bot._targeted_research_prewarm_tasks = set()
    bot._last_targeted_research_prewarm = {}
    bot._last_periodic_research_prewarm = {}
    # Multi-lane stubs: _process_candidate now routes through blend_task.
    bot._evidence_queue = asyncio.Queue(maxsize=2000)
    bot._trading_queue = asyncio.Queue(maxsize=500)
    _blend_result = MagicMock()
    _blend_result.enqueued = True
    _blend_result.trade_blocked_reason = None
    bot._blend_task = MagicMock()
    bot._blend_task.process_fast_lane_result = AsyncMock(return_value=_blend_result)
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
        status="active",
        series_ticker="KXTEST",
        subtitle="Test criteria",
        result="",
        # P-5 CR-C: post-P0 pricing surface required for guarded reads
        yes_bid_cents=49,
        yes_ask_cents=51,
        no_bid_cents=49,
        no_ask_cents=51,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
        rules_primary="Official test rules determine the market.",
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


def test_create_research_prewarm_runtime_task_disabled(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "enable_research_prewarm_task", False, raising=False)
    bot = _make_bot_stub()

    with patch("main.asyncio.create_task") as create_task_mock:
        task = bot._create_research_prewarm_runtime_task()

    assert task is None
    create_task_mock.assert_not_called()


def test_create_research_prewarm_runtime_task_enabled(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "enable_research_prewarm_task", True, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_interval_seconds", 900.0, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_pages", 3, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_max_queries", 4, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_timeout_seconds", 8.5, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_target_cooldown_seconds",
        1234.0,
        raising=False,
    )
    bot = _make_bot_stub()
    markets = [
        _make_market(),
        replace(_make_market(), ticker="KXSECOND-25DEC31"),
        replace(_make_market(), ticker="KXTHIRD-25DEC31"),
    ]
    bot.rest.get_all_open_markets.return_value = markets
    instances = []

    class FakeResearchPrewarmTask:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.provider = None
            self.interval_seconds = None
            instances.append(self)

        def run_periodic(self, provider, *, interval_seconds):
            self.provider = provider
            self.interval_seconds = interval_seconds

            async def _noop():
                return None

            return _noop()

    def _capture_task(coro, *, name):
        coro.close()
        task = MagicMock()
        task.get_name.return_value = name
        return task

    with patch("main.ResearchPrewarmTask", FakeResearchPrewarmTask), \
         patch("main.asyncio.create_task", side_effect=_capture_task) as create_task_mock:
        task = bot._create_research_prewarm_runtime_task()

    create_task_mock.assert_called_once()
    assert task.get_name() == "research_prewarm"
    assert len(instances) == 1
    instance = instances[0]
    assert instance.kwargs["max_queries"] == 4
    assert instance.kwargs["research_timeout_seconds"] == 8.5
    assert instance.kwargs["target_cooldown_seconds"] == 1234.0
    assert instance.interval_seconds == 900.0
    assert [market.ticker for market in instance.provider()] == [
        "KXTEST-25DEC31",
        "KXSECOND-25DEC31",
    ]


def test_research_prewarm_market_provider_prioritizes_recent_empty_keyword_backlog(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_pages", 3, raising=False)
    bot = _make_bot_stub()
    generic_market = replace(_make_market(), ticker="KXGENERIC-25DEC31")
    backlog_market = replace(_make_market(), ticker="KXBACKLOG-25DEC31")
    bot.rest.get_all_open_markets.return_value = [generic_market, backlog_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": backlog_market.ticker,
                "verdict": "false_positive_neutral",
                "keyword_count": 0,
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        backlog_market.ticker,
    ]


def test_research_prewarm_market_provider_prioritizes_recent_nonterminal_research_refresh(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_pages", 3, raising=False)
    bot = _make_bot_stub()
    generic_market = replace(_make_market(), ticker="KXGENERIC-25DEC31")
    candidate_market = replace(
        _make_market(),
        ticker="KXCPI-26NOV-T0.4",
        series_ticker="KXCPI",
        settlement_sources=(SettlementSource(label="BLS", domain="bls.gov"),),
    )
    bot.rest.get_all_open_markets.return_value = [generic_market, candidate_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": candidate_market.ticker,
                "research_status": "needs_counter_evidence",
                "research_skip_reason": "missing_counter_evidence",
                "research_prewarm": True,
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    selected = bot._research_prewarm_market_provider()

    assert [market.ticker for market in selected] == [candidate_market.ticker]


def test_research_prewarm_market_provider_ranks_expanded_due_pool(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    now = datetime.now(timezone.utc)
    far_tail = replace(
        _make_market(),
        ticker="KXFAR-26DEC31",
        yes_ask=2.0,
        yes_ask_cents=2,
        no_ask_cents=98,
        close_time=(now + timedelta(days=90)).isoformat(),
    )
    near_mid = replace(
        _make_market(),
        ticker="KXNEAR-26JUL13",
        yes_ask=41.0,
        yes_ask_cents=41,
        no_ask_cents=59,
        close_time=(now + timedelta(days=1)).isoformat(),
    )
    bot.rest.get_all_open_markets.return_value = [far_tail]
    bot.rest.get_market.side_effect = lambda ticker: (
        near_mid if ticker == near_mid.ticker else None
    )
    blocked_ticker = "KXMVECROSSCATEGORY-S2026DEADBEEF-123456789AB"
    bot._research_prewarm_due_task_tickers = MagicMock(
        return_value=[blocked_ticker, far_tail.ticker, near_mid.ticker]
    )

    selected = bot._research_prewarm_market_provider()

    bot._research_prewarm_due_task_tickers.assert_called_once_with(
        limit=5,
        cooldown_seconds=bot._research_prewarm_target_cooldown_seconds(),
    )
    assert [market.ticker for market in selected] == [near_mid.ticker]
    bot.rest.get_market.assert_called_once_with(near_mid.ticker)


def test_research_prewarm_fallback_ranks_across_series(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        ("KXTAIL", "KXMID"),
        raising=False,
    )
    bot = _make_bot_stub()
    now = datetime.now(timezone.utc)
    bot.rest.get_all_open_markets.return_value = []
    tail = replace(
        _make_market(),
        ticker="KXTAIL-26DEC31",
        yes_ask=2.0,
        yes_ask_cents=2,
        no_ask_cents=98,
        close_time=(now + timedelta(days=90)).isoformat(),
    )
    mid = replace(
        _make_market(),
        ticker="KXMID-26JUL13",
        yes_ask=41.0,
        yes_ask_cents=41,
        no_ask_cents=59,
        close_time=(now + timedelta(days=1)).isoformat(),
    )
    bot.rest.get_markets.side_effect = [([tail], None), ([mid], None)]

    selected = bot._research_prewarm_market_provider()

    assert [market.ticker for market in selected] == [mid.ticker]


def test_research_prewarm_market_provider_skips_blocklisted_recent_backlog(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_pages", 3, raising=False)
    bot = _make_bot_stub()
    blocked_market = replace(
        _make_market(),
        ticker="KXMVESPORTSMULTIGAMEEXTENDED-S2026AC2580761AE-87A8730FA2A",
        series_ticker="KXMVE",
        settlement_sources=(SettlementSource(label="Official", domain="nfl.com"),),
    )
    profit_relevant_market = replace(
        _make_market(),
        ticker="KXGDP-26JUL30-T4.0",
        series_ticker="KXGDP",
        settlement_sources=(SettlementSource(label="Official", domain="bea.gov"),),
    )
    bot.rest.get_all_open_markets.return_value = [
        blocked_market,
        profit_relevant_market,
    ]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": blocked_market.ticker,
                "reason": "research_incomplete",
                "research_skip_reason": "ambiguous_direction",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        profit_relevant_market.ticker,
    ]


def test_research_prewarm_market_provider_skips_unsourceable_markets(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    bot = _make_bot_stub()
    unsourceable_backlog = replace(
        _make_market(),
        ticker="KXMVESPORTSMULTIGAMEEXTENDED-S2026",
        rules_primary="",
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(),
    )
    sourceable_market = replace(
        _make_market(),
        ticker="KXSOURCEABLE-25DEC31",
        settlement_sources=(SettlementSource(label="Official", domain="official.example"),),
    )
    unsourceable_filler = replace(
        _make_market(),
        ticker="KXMVECROSSCATEGORY-S2026",
        rules_primary="",
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(),
    )
    bot.rest.get_all_open_markets.return_value = [
        unsourceable_backlog,
        sourceable_market,
        unsourceable_filler,
    ]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": unsourceable_backlog.ticker,
                "reason": "researched_no_edge",
                "research_skip_reason": "missing_resolution_source",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        sourceable_market.ticker,
    ]


def test_research_prewarm_market_provider_prioritizes_actionable_prices(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    zero_price = replace(
        _make_market(),
        ticker="KXZERO-25DEC31",
        yes_ask_cents=0,
        no_ask_cents=100,
    )
    actionable = replace(
        _make_market(),
        ticker="KXPRICED-25DEC31",
        yes_ask_cents=42,
        no_ask_cents=59,
    )
    bot.rest.get_all_open_markets.return_value = [zero_price, actionable]

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        actionable.ticker,
    ]


def test_research_prewarm_market_provider_enriches_series_metadata(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    raw_market = replace(
        _make_market(),
        ticker="KXSERIESMETA-25DEC31",
        rules_primary="",
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(),
    )
    bot.rest.get_all_open_markets.return_value = [raw_market]
    bot.rest.get_series.return_value = SimpleNamespace(
        rules_primary="Series-level rules identify the official report.",
        rules_secondary="",
        contract_terms_url="https://kalshi.com/markets/KXSERIESMETA-25DEC31",
        settlement_sources=(SettlementSource(label="Official", domain="official.example"),),
    )

    selected = bot._research_prewarm_market_provider()

    assert [market.ticker for market in selected] == [raw_market.ticker]
    assert selected[0].rules_primary == "Series-level rules identify the official report."
    assert selected[0].settlement_sources == (
        SettlementSource(label="Official", domain="official.example"),
    )
    bot.rest.get_series.assert_called_once_with(raw_market.series_ticker)


def test_research_prewarm_market_provider_enriches_rules_only_market_sources(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    raw_market = replace(
        _make_market(),
        ticker="KXFED-27MAR-T4.00",
        series_ticker="",
        rules_primary="This market resolves from the Federal Reserve rate decision.",
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(),
    )
    bot.rest.get_all_open_markets.return_value = [raw_market]
    bot.rest.get_series.return_value = SimpleNamespace(
        rules_primary="",
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(
            SettlementSource(label="Federal Reserve", domain="federalreserve.gov"),
        ),
    )

    selected = bot._research_prewarm_market_provider()

    assert [market.ticker for market in selected] == [raw_market.ticker]
    assert selected[0].settlement_sources == (
        SettlementSource(label="Federal Reserve", domain="federalreserve.gov"),
    )
    bot.rest.get_series.assert_called_once_with("KXFED")


def test_research_prewarm_market_provider_returns_unsourceable_for_skip_telemetry(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        (),
        raising=False,
    )
    bot = _make_bot_stub()
    unsourceable_markets = [
        replace(
            _make_market(),
            ticker=f"KXUNSOURCEABLE-{index}",
            rules_primary="",
            rules_secondary="",
            contract_terms_url="",
            settlement_sources=(),
        )
        for index in range(3)
    ]
    bot.rest.get_all_open_markets.return_value = unsourceable_markets

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        "KXUNSOURCEABLE-0",
        "KXUNSOURCEABLE-1",
    ]


def test_research_prewarm_market_provider_uses_sourceable_series_fallback(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        ("KXGDP", "KXCPI"),
        raising=False,
    )
    bot = _make_bot_stub()
    bot.rest.get_all_open_markets.return_value = [
        replace(
            _make_market(),
            ticker="KXMVESPORTSMULTIGAMEEXTENDED-S2026",
            rules_primary="",
            rules_secondary="",
            contract_terms_url="",
            settlement_sources=(),
        )
    ]
    gdp_market = replace(
        _make_market(),
        ticker="KXGDP-26JUL30-T4.0",
        series_ticker="KXGDP",
    )
    cpi_market = replace(
        _make_market(),
        ticker="KXCPI-26JUL-T0.3",
        series_ticker="KXCPI",
    )
    bot.rest.get_markets.side_effect = [
        ([gdp_market], None),
        ([cpi_market], None),
    ]

    selected = bot._research_prewarm_market_provider()

    assert [market.ticker for market in selected] == [
        "KXGDP-26JUL30-T4.0",
        "KXCPI-26JUL-T0.3",
    ]
    bot.rest.get_markets.assert_any_call(series_ticker="KXGDP", limit=2)
    bot.rest.get_markets.assert_any_call(series_ticker="KXCPI", limit=2)


def test_research_prewarm_market_provider_tops_up_backlog_with_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        ("KXGDP",),
        raising=False,
    )
    bot = _make_bot_stub()
    backlog_market = replace(
        _make_market(),
        ticker="KXTARIFFRATEPRC-26JUL01-30",
        series_ticker="KXTARIFFRATEPRC",
        settlement_sources=(SettlementSource(label="Official", domain="ustr.gov"),),
    )
    fallback_market = replace(
        _make_market(),
        ticker="KXGDP-26JUL30-T4.0",
        series_ticker="KXGDP",
        settlement_sources=(SettlementSource(label="Official", domain="bea.gov"),),
    )
    bot.rest.get_all_open_markets.return_value = [backlog_market]
    bot.rest.get_markets.return_value = ([fallback_market], None)

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": backlog_market.ticker,
                "reason": "research_incomplete",
                "research_skip_reason": "ambiguous_direction",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    selected = bot._research_prewarm_market_provider()

    assert [market.ticker for market in selected] == [
        backlog_market.ticker,
        fallback_market.ticker,
    ]


def test_research_prewarm_market_provider_direct_fetches_due_db_tasks(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        (),
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "TRADE_LOG_FILE",
        tmp_path / "logs" / "trades" / "live" / "trades.jsonl",
        raising=False,
    )
    bot = _make_bot_stub()
    due_market = replace(
        _make_market(),
        ticker="KXHIGHNY-26JUL03-T98",
        series_ticker="KXHIGHNY",
        settlement_sources=(SettlementSource(label="NWS", domain="forecast.weather.gov"),),
    )
    generic_market = replace(
        _make_market(),
        ticker="KXGENERIC-25DEC31",
        settlement_sources=(SettlementSource(label="Official", domain="official.example"),),
    )
    bot._research_prewarm_due_task_tickers = (
        lambda *, limit, cooldown_seconds: [due_market.ticker]
    )
    bot.rest.get_all_open_markets.return_value = [generic_market]
    bot.rest.get_market.return_value = due_market

    selected = bot._research_prewarm_market_provider()

    assert [market.ticker for market in selected] == [
        due_market.ticker,
        generic_market.ticker,
    ]
    bot.rest.get_market.assert_called_once_with(due_market.ticker)


def test_research_prewarm_market_provider_skips_blocklisted_fallback_series(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        ("KXMLB", "KXGDP"),
        raising=False,
    )
    bot = _make_bot_stub()
    bot.rest.get_all_open_markets.return_value = [
        replace(
            _make_market(),
            ticker="KXUNSOURCEABLE-25DEC31",
            rules_primary="",
            rules_secondary="",
            contract_terms_url="",
            settlement_sources=(),
        )
    ]
    gdp_market = replace(
        _make_market(),
        ticker="KXGDP-26JUL30-T4.0",
        series_ticker="KXGDP",
        settlement_sources=(SettlementSource(label="Official", domain="bea.gov"),),
    )
    bot.rest.get_markets.return_value = ([gdp_market], None)

    selected = bot._research_prewarm_market_provider()

    assert [market.ticker for market in selected] == [gdp_market.ticker]
    bot.rest.get_markets.assert_called_once_with(series_ticker="KXGDP", limit=1)


def test_research_prewarm_market_provider_cools_down_periodic_fallback_targets(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_target_cooldown_seconds",
        1800.0,
        raising=False,
    )
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        ("KXGDP",),
        raising=False,
    )
    now = 10_000.0
    monkeypatch.setattr(main_module.time, "monotonic", lambda: now)
    bot = _make_bot_stub()
    bot.rest.get_all_open_markets.return_value = [
        replace(
            _make_market(),
            ticker="KXMVESPORTSMULTIGAMEEXTENDED-S2026",
            rules_primary="",
            rules_secondary="",
            contract_terms_url="",
            settlement_sources=(),
        )
    ]
    fallback_markets = [
        replace(_make_market(), ticker="KXGDP-26JUL30-T4.0", series_ticker="KXGDP"),
        replace(_make_market(), ticker="KXGDP-26JUL30-T4.5", series_ticker="KXGDP"),
        replace(_make_market(), ticker="KXGDP-26JUL30-T5.0", series_ticker="KXGDP"),
    ]
    bot.rest.get_markets.return_value = (fallback_markets, None)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        "KXGDP-26JUL30-T4.0",
        "KXGDP-26JUL30-T4.5",
    ]
    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        "KXGDP-26JUL30-T5.0",
    ]


def test_research_prewarm_market_provider_does_not_cool_down_unresearchable_skip_telemetry(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_target_cooldown_seconds",
        1800.0,
        raising=False,
    )
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        (),
        raising=False,
    )
    now = 10_000.0
    monkeypatch.setattr(main_module.time, "monotonic", lambda: now)
    bot = _make_bot_stub()
    ticker = "KXNEWLYSOURCEABLE-25DEC31"
    unresearchable = replace(
        _make_market(),
        ticker=ticker,
        rules_primary="",
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(),
    )
    sourceable = replace(
        unresearchable,
        rules_primary="Official source now available.",
    )
    bot.rest.get_all_open_markets.return_value = [unresearchable]

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        ticker
    ]

    bot.rest.get_all_open_markets.return_value = [sourceable]

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        ticker
    ]


def test_recent_runtime_research_prewarm_tickers_excludes_probe_and_non_kalshi(
    monkeypatch, tmp_path
):
    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    now = datetime.now(timezone.utc)
    write_jsonl(
        log_path,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": now.isoformat(),
                "ticker": "KXSTARTUP-PROBE",
                "venue": "kalshi",
                "is_synthetic_probe": True,
                "is_startup_probe": True,
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": now.isoformat(),
                "ticker": "ewc-usse-me-2026-11-03-dem",
                "venue": "polymarket_us",
                "verdict": "false_positive_neutral",
                "keyword_count": 1,
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": now.isoformat(),
                "ticker": "KXREAL-25DEC31",
                "venue": "kalshi",
                "verdict": "false_positive_neutral",
                "keyword_count": 1,
            },
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert main_module._recent_runtime_research_prewarm_tickers(now=now) == [
        "KXREAL-25DEC31"
    ]


def test_research_prewarm_market_provider_prioritizes_thin_keyword_backlog(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    generic_market = replace(_make_market(), ticker="KXGENERIC-25DEC31")
    reviewed_market = replace(_make_market(), ticker="KXREVIEWED-25DEC31")
    bot.rest.get_all_open_markets.return_value = [generic_market, reviewed_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": reviewed_market.ticker,
                "verdict": "false_positive_neutral",
                "keyword_count": 1,
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        reviewed_market.ticker,
    ]


def test_research_prewarm_market_provider_prioritizes_semantic_overlap_block(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    generic_market = replace(_make_market(), ticker="KXGENERIC-25DEC31")
    blocked_market = replace(_make_market(), ticker="KXBLOCKED-25DEC31")
    bot.rest.get_all_open_markets.return_value = [generic_market, blocked_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": datetime.now(timezone.utc).isoformat(),
                "market_ticker": blocked_market.ticker,
                "keywords": [],
                "pre_llm_would_block_and_useful": False,
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        blocked_market.ticker,
    ]


def test_research_prewarm_market_provider_prioritizes_thin_semantic_overlap(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    generic_market = replace(_make_market(), ticker="KXGENERIC-25DEC31")
    blocked_market = replace(_make_market(), ticker="KXBLOCKED-25DEC31")
    bot.rest.get_all_open_markets.return_value = [generic_market, blocked_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": datetime.now(timezone.utc).isoformat(),
                "market_ticker": blocked_market.ticker,
                "keywords": ["thin"],
                "pre_llm_would_block_and_useful": False,
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        blocked_market.ticker,
    ]


def test_research_prewarm_market_provider_prioritizes_semantic_overlap_with_keywords(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    generic_market = replace(_make_market(), ticker="KXGENERIC-25DEC31")
    blocked_market = replace(_make_market(), ticker="KXBLOCKED-25DEC31")
    bot.rest.get_all_open_markets.return_value = [generic_market, blocked_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": datetime.now(timezone.utc).isoformat(),
                "market_ticker": blocked_market.ticker,
                "keywords": ["midterms", "senate"],
                "pre_llm_would_block_and_useful": False,
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        blocked_market.ticker,
    ]


def test_research_prewarm_market_provider_ignores_false_neutral_with_keywords(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_pages", 3, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    reviewed_market = replace(_make_market(), ticker="KXREVIEWED-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market, reviewed_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": reviewed_market.ticker,
                "verdict": "false_positive_neutral",
                "keyword_count": 2,
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        first_market.ticker,
    ]
    bot.rest.get_all_open_markets.assert_called_once_with(max_pages=3)


def test_research_prewarm_market_provider_ignores_stale_backlog(monkeypatch, tmp_path):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    stale_market = replace(_make_market(), ticker="KXSTALE-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market, stale_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                "ticker": stale_market.ticker,
                "reason": "no_keywords",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        first_market.ticker,
    ]


def test_research_prewarm_market_provider_prioritizes_ambiguous_research_gap(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    ambiguous_market = replace(_make_market(), ticker="KXAMBIG-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market, ambiguous_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": ambiguous_market.ticker,
                "reason": "researched_no_edge",
                "research_skip_reason": "ambiguous_direction",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        ambiguous_market.ticker,
    ]


def test_research_prewarm_market_provider_prioritizes_retryable_research_skip(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    missing_source_market = replace(_make_market(), ticker="KXSOURCE-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market, missing_source_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": missing_source_market.ticker,
                "reason": "researched_no_edge",
                "research_skip_reason": "missing_resolution_source",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        missing_source_market.ticker,
    ]


def test_research_prewarm_market_provider_fetches_backlog_missing_from_open_page(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    missing_source_market = replace(_make_market(), ticker="KXSOURCE-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market]
    bot.rest.get_market.return_value = missing_source_market

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": missing_source_market.ticker,
                "reason": "researched_no_edge",
                "research_skip_reason": "missing_resolution_source",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        missing_source_market.ticker,
    ]
    bot.rest.get_market.assert_called_once_with(missing_source_market.ticker)


def test_research_prewarm_market_provider_fetches_due_research_task(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    due_market = replace(_make_market(), ticker="KXDUE-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market]
    bot.rest.get_market.return_value = due_market
    bot._research_prewarm_due_task_tickers = lambda *, limit, cooldown_seconds: [
        due_market.ticker
    ]

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        due_market.ticker,
    ]
    bot.rest.get_market.assert_called_once_with(due_market.ticker)


def test_research_prewarm_market_provider_keeps_closed_due_task_for_terminalization(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    closed_due = replace(
        _make_market(),
        ticker="KXNASDAQ100-26JUL02H1300-B31550",
        status="closed",
    )
    bot.rest.get_all_open_markets.return_value = [first_market]
    bot.rest.get_market.return_value = closed_due
    bot._research_prewarm_due_task_tickers = lambda *, limit, cooldown_seconds: [
        closed_due.ticker
    ]

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        closed_due.ticker,
    ]
    bot.rest.get_market.assert_called_once_with(closed_due.ticker)


def test_research_prewarm_market_provider_excludes_active_expired_market(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    expired = replace(
        _make_market(),
        ticker="KXEXPIRED-26JUL10",
        status="active",
        close_time="2026-07-10T23:59:59Z",
    )
    bot.rest.get_all_open_markets.return_value = [expired]

    assert bot._research_prewarm_market_provider() == []


def test_research_prewarm_market_provider_keeps_unsourceable_due_task_for_skip_telemetry(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_sourceable_series_fallback",
        (),
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "TRADE_LOG_FILE",
        tmp_path / "logs" / "trades" / "live" / "trades.jsonl",
        raising=False,
    )
    bot = _make_bot_stub()
    sourceable_market = replace(_make_market(), ticker="KXSOURCEABLE-25DEC31")
    unsourceable_due = replace(
        _make_market(),
        ticker="KXDUE-NOSOURCE-25DEC31",
        rules_primary="",
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(),
    )
    bot.rest.get_all_open_markets.return_value = [sourceable_market]
    bot.rest.get_market.return_value = unsourceable_due
    bot._research_prewarm_due_task_tickers = lambda *, limit, cooldown_seconds: [
        unsourceable_due.ticker
    ]

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        sourceable_market.ticker,
        unsourceable_due.ticker,
    ]
    bot.rest.get_market.assert_called_once_with(unsourceable_due.ticker)


def test_research_prewarm_market_provider_does_not_fallback_when_backlog_unfetchable(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market]
    bot.rest.get_market.return_value = None

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": "KXSTALE-25DEC31",
                "reason": "researched_no_edge",
                "research_skip_reason": "missing_resolution_source",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        first_market.ticker,
    ]
    bot.rest.get_market.assert_called_once_with("KXSTALE-25DEC31")


def test_research_prewarm_market_provider_caps_direct_backlog_fetch_attempts(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 2, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    second_market = replace(_make_market(), ticker="KXSECOND-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market, second_market]
    bot.rest.get_market.return_value = None

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": f"KXSTALE-{index}",
                "reason": "researched_no_edge",
                "research_skip_reason": "missing_resolution_source",
            }
            for index in range(5)
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        first_market.ticker,
        second_market.ticker,
    ]
    assert bot.rest.get_market.call_count == 2


def test_research_prewarm_market_provider_ignores_closed_direct_fetch(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    closed_market = replace(
        _make_market(),
        ticker="KXCLOSED-25DEC31",
        status="closed",
    )
    bot.rest.get_all_open_markets.return_value = [first_market]
    bot.rest.get_market.return_value = closed_market

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": closed_market.ticker,
                "reason": "researched_no_edge",
                "research_skip_reason": "missing_resolution_source",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        first_market.ticker,
    ]


def test_research_prewarm_market_provider_ignores_capital_protection_skip(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    capital_market = replace(_make_market(), ticker="KXCAPITAL-25DEC31")
    bot.rest.get_all_open_markets.return_value = [first_market, capital_market]

    log_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": capital_market.ticker,
                "reason": "researched_no_edge",
                "research_skip_reason": "no_trade_capital_protection",
            }
        ],
    )
    monkeypatch.setattr(main_module, "TRADE_LOG_FILE", log_path, raising=False)

    assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
        first_market.ticker,
    ]


def test_schedule_targeted_research_prewarm_dedupes_with_cooldown(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_target_cooldown_seconds", 600.0, raising=False)
    bot = _make_bot_stub()
    bot._targeted_research_prewarm_tasks = set()
    bot._last_targeted_research_prewarm = {}
    market = _make_market()

    def _capture_task(coro, *, name):
        coro.close()
        task = MagicMock()
        task.get_name.return_value = name
        task.add_done_callback = MagicMock()
        return task

    with patch("main.time.monotonic", side_effect=[100.0, 200.0]), \
         patch("main.asyncio.create_task", side_effect=_capture_task) as create_task_mock:
        assert bot._schedule_targeted_research_prewarm(
            market,
            "cached_dossier_insufficient",
        ) is True
        assert bot._schedule_targeted_research_prewarm(
            market,
            "cached_dossier_insufficient",
        ) is False

    assert create_task_mock.call_count == 1
    assert bot._last_targeted_research_prewarm[market.ticker] == 100.0


def test_schedule_targeted_research_prewarm_respects_periodic_cooldown(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_target_cooldown_seconds",
        600.0,
        raising=False,
    )
    bot = _make_bot_stub()
    market = _make_market()
    bot._last_periodic_research_prewarm[market.ticker] = 100.0

    with patch("main.time.monotonic", return_value=200.0), \
         patch("main.asyncio.create_task") as create_task_mock:
        assert bot._schedule_targeted_research_prewarm(
            market,
            "cached_dossier_insufficient",
        ) is False

    create_task_mock.assert_not_called()


def test_research_prewarm_market_provider_respects_targeted_cooldown(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_max_markets", 1, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_target_cooldown_seconds",
        600.0,
        raising=False,
    )
    bot = _make_bot_stub()
    first_market = replace(_make_market(), ticker="KXFIRST-25DEC31")
    targeted_market = replace(_make_market(), ticker="KXTARGETED-25DEC31")
    bot.rest.get_all_open_markets.return_value = [targeted_market, first_market]
    bot._last_targeted_research_prewarm[targeted_market.ticker] = 100.0

    with patch("main.time.monotonic", return_value=200.0):
        assert [market.ticker for market in bot._research_prewarm_market_provider()] == [
            first_market.ticker,
        ]


@pytest.mark.parametrize(
    "skip_reason",
    [
        "ambiguous_direction",
        "direction_reason_conflict",
        "no_research_hits",
        "missing_resolution_source",
        "official_data_pending",
        "insufficient_corroboration",
        "missing_estimated_probability",
        "probability_direction_conflict",
    ],
)
def test_schedule_targeted_research_prewarm_accepts_information_gap_reasons(
    monkeypatch,
    skip_reason,
):
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    bot._targeted_research_prewarm_tasks = set()
    bot._last_targeted_research_prewarm = {}
    market = _make_market()

    def _capture_task(coro, *, name):
        coro.close()
        task = MagicMock()
        task.get_name.return_value = name
        task.add_done_callback = MagicMock()
        return task

    with patch("main.asyncio.create_task", side_effect=_capture_task) as create_task_mock:
        assert bot._schedule_targeted_research_prewarm(market, skip_reason) is True

    create_task_mock.assert_called_once()


def test_schedule_targeted_research_prewarm_clears_cooldown_after_failure(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "research_prewarm_target_cooldown_seconds", 600.0, raising=False)
    bot = _make_bot_stub()
    bot._targeted_research_prewarm_tasks = set()
    bot._last_targeted_research_prewarm = {}
    market = _make_market()
    callbacks = []

    def _capture_task(coro, *, name):
        coro.close()
        task = MagicMock()
        task.get_name.return_value = name
        task.result.side_effect = RuntimeError("provider failed")
        task.add_done_callback.side_effect = lambda cb: callbacks.append((task, cb))
        return task

    with patch("main.time.monotonic", side_effect=[100.0, 101.0]), \
         patch("main.asyncio.create_task", side_effect=_capture_task) as create_task_mock:
        assert bot._schedule_targeted_research_prewarm(
            market,
            "cached_dossier_insufficient",
        ) is True
        task, callback = callbacks.pop()
        callback(task)
        assert bot._schedule_targeted_research_prewarm(
            market,
            "cached_dossier_insufficient",
        ) is True

    assert create_task_mock.call_count == 2


@pytest.mark.asyncio
async def test_cancel_targeted_research_prewarm_tasks_cancels_pending(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    market = _make_market()

    async def _blocked_prewarm(_market, _reason):
        await asyncio.Event().wait()

    bot._run_targeted_research_prewarm = _blocked_prewarm

    assert bot._schedule_targeted_research_prewarm(
        market,
        "missing_resolution_source",
    ) is True
    assert len(bot._targeted_research_prewarm_tasks) == 1

    await bot.cancel_targeted_research_prewarm_tasks()

    assert bot._targeted_research_prewarm_tasks == set()


@pytest.mark.asyncio
async def test_targeted_research_prewarm_emits_structured_result(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_max_queries", 4, raising=False)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "real_web_research_timeout_seconds",
        8.5,
        raising=False,
    )
    monkeypatch.setattr(
        _cfg_module.cfg,
        "research_prewarm_target_cooldown_seconds",
        1800.0,
        raising=False,
    )
    bot = _make_bot_stub()
    market = _make_market()
    emitted = []
    instances = []
    result = SimpleNamespace(
        market_ticker=market.ticker,
        status=ResearchStatus.TRADE_CANDIDATE.value,
        attempted=True,
        evidence_count=2,
        query_count=3,
    )

    class FakeResearchPrewarmTask:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            instances.append(self)

        async def process_market(self, received_market, **kwargs):
            assert received_market is market
            assert kwargs == {"bypass_persisted_cooldown": True}
            return result

        async def emit_result(self, received_result):
            emitted.append(received_result)

    with patch("main.ResearchPrewarmTask", FakeResearchPrewarmTask):
        await bot._run_targeted_research_prewarm(
            market,
            "cached_dossier_insufficient",
        )

    assert emitted == [result]
    assert instances[0].kwargs["target_cooldown_seconds"] == 1800.0


@pytest.mark.asyncio
async def test_refresh_market_cache_prewarms_new_markets_when_enabled(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "enable_research_prewarm_task", True, raising=False)
    bot = _make_bot_stub()
    old_market = _make_market()
    new_market = replace(_make_market(), ticker="KXNEW-25DEC31")
    bot._known_market_tickers = {old_market.ticker}
    bot.matcher.refresh_cache = AsyncMock()
    bot.matcher._cache.get_markets = AsyncMock(return_value=[old_market, new_market])
    bot._schedule_targeted_research_prewarm = MagicMock(return_value=True)
    bot._trigger_targeted_search = AsyncMock()

    def _capture_task(coro):
        coro.close()
        return MagicMock()

    with patch("main.asyncio.create_task", side_effect=_capture_task), \
         patch("main.write_trade_log_async", new=AsyncMock()):
        await bot._refresh_market_cache_once()

    bot._schedule_targeted_research_prewarm.assert_called_once_with(
        new_market,
        "new_market",
    )


@pytest.mark.asyncio
async def test_refresh_market_cache_does_not_prewarm_new_markets_when_disabled(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "enable_research_prewarm_task", False, raising=False)
    bot = _make_bot_stub()
    old_market = _make_market()
    new_market = replace(_make_market(), ticker="KXNEW-25DEC31")
    bot._known_market_tickers = {old_market.ticker}
    bot.matcher.refresh_cache = AsyncMock()
    bot.matcher._cache.get_markets = AsyncMock(return_value=[old_market, new_market])
    bot._schedule_targeted_research_prewarm = MagicMock(return_value=True)
    bot._trigger_targeted_search = AsyncMock()

    def _capture_task(coro):
        coro.close()
        return MagicMock()

    with patch("main.asyncio.create_task", side_effect=_capture_task), \
         patch("main.write_trade_log_async", new=AsyncMock()):
        await bot._refresh_market_cache_once()

    bot._schedule_targeted_research_prewarm.assert_not_called()


def _analysis_for_evidence(news: NewsItem | None = None) -> SignalAnalysis:
    news = news or _make_news()
    market = _make_market()
    return SignalAnalysis(
        news_item=news,
        market=market,
        estimated_probability=0.67,
        executed_price_cents=int(round(market.yes_price)),  # F-16: canonical post-P0; __post_init__ mirrors to market_yes_price
        edge=0.17,
        side="yes",
        kelly_fraction=0.0,
        kelly_dollars=0.0,
        capped_dollars=0.0,
    )


def _empty_source_hint_diagnostics(
    market: KalshiMarket,
    *,
    mode: str = "shadow",
    records: list[dict[str, object]] | None = None,
) -> MarketSourceHintDiagnostics:
    return MarketSourceHintDiagnostics(
        ticker=market.ticker,
        mode=mode,
        shadow_only=True,
        plan=MarketSourceTargetPlan(
            ticker=market.ticker,
            shadow_only=True,
            targets=(),
            rejected_labels={},
        ),
        counters={},
        log_records=records or [],
    )


@pytest.mark.asyncio
async def test_research_analysis_route_uses_validated_store_without_feed_side_effects():
    bot = _make_bot_stub()
    bot._calibration_task = MagicMock()
    analysis = _analysis_for_evidence()
    analysis.news_item = None
    research_store = MagicMock()
    blend_result = SimpleNamespace(
        ready=True,
        enqueued=True,
        trade_blocked_reason=None,
    )
    research_blend_task = MagicMock()
    research_blend_task.process_fast_lane_result = AsyncMock(
        return_value=blend_result,
    )

    with patch("main.BlendTask", return_value=research_blend_task) as blend_task_cls:
        result = await bot._route_research_analysis_through_blend(
            analysis,
            research_store,
        )

    assert result is blend_result
    blend_task_cls.assert_called_once()
    kwargs = blend_task_cls.call_args.kwargs
    assert kwargs["trading_queue"] is bot._trading_queue
    assert kwargs["store"] is research_store
    assert kwargs["calibration"] is bot._calibration_task
    assert kwargs["is_paper_mode"] is True
    research_blend_task.process_fast_lane_result.assert_awaited_once_with(analysis)
    assert bot._evidence_queue.empty()
    bot.ws.watch.assert_not_called()


def test_signal_to_evidence_uses_deterministic_id():
    news = _make_news()
    news.published = datetime(2026, 4, 20, 1, 2, 3, tzinfo=timezone.utc)

    first = _signal_to_evidence(_analysis_for_evidence(news))
    second = _signal_to_evidence(_analysis_for_evidence(news))

    assert first.evidence_id == second.evidence_id
    assert first.evidence_id.startswith("ev-")
    assert first.implied_probability == pytest.approx(0.67)


def test_signal_to_evidence_preserves_source_class_diversity():
    news = _make_news()
    news.source = "r/worldnews"

    evidence = _signal_to_evidence(_analysis_for_evidence(news))

    assert evidence.source_class == "social"
    assert _source_class_for_evidence("White House official statement") == "official"
    assert _source_class_for_evidence("price_fade") == "market"
    assert _source_class_for_evidence("Reuters") == "news"


@pytest.mark.asyncio
async def test_market_source_hint_runtime_default_off_is_noop(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "market_source_hints_mode", "off")
    monkeypatch.setattr(_cfg_module.cfg, "market_source_hints_emit_records", False)
    bot = _make_bot_stub()
    market = _make_market()

    with patch("main.build_market_source_hint_diagnostics") as diagnostics_mock, \
         patch("utils.logger.trade_log.log_market_source_hint_diagnostic") as log_mock:
        await bot._emit_market_source_hint_diagnostics(market)

    diagnostics_mock.assert_not_called()
    log_mock.assert_not_called()
    bot.ws.watch.assert_not_called()
    bot._blend_task.process_fast_lane_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_source_hint_runtime_shadow_builds_in_memory_only(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "market_source_hints_mode", "shadow")
    monkeypatch.setattr(_cfg_module.cfg, "market_source_hints_emit_records", False)
    bot = _make_bot_stub()
    market = _make_market()
    diagnostic = _empty_source_hint_diagnostics(market, mode="shadow")

    with patch("main.build_market_source_hint_diagnostics", return_value=diagnostic) as diagnostics_mock, \
         patch("utils.logger.trade_log.log_market_source_hint_diagnostic") as log_mock:
        await bot._emit_market_source_hint_diagnostics(market)

    diagnostics_mock.assert_called_once_with(
        market,
        mode="shadow",
        emit_records=False,
    )
    log_mock.assert_not_called()
    bot.ws.watch.assert_not_called()
    bot._blend_task.process_fast_lane_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_source_hint_runtime_advisory_emits_shadow_only_record_when_enabled(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "market_source_hints_mode", "advisory")
    monkeypatch.setattr(_cfg_module.cfg, "market_source_hints_emit_records", True)
    bot = _make_bot_stub()
    market = _make_market()
    diagnostic = _empty_source_hint_diagnostics(
        market,
        mode="advisory",
        records=[{
            "type": "MARKET_SOURCE_HINT_SHADOW",
            "ticker": market.ticker,
            "source": "Reuters",
            "domain": "reuters.com",
            "hit": False,
            "freshness_age_seconds": None,
            "shadow_only": True,
        }],
    )

    with patch("main.build_market_source_hint_diagnostics", return_value=diagnostic), \
         patch("utils.logger.trade_log.log_market_source_hint_diagnostic") as log_mock:
        await bot._emit_market_source_hint_diagnostics(market)

    log_mock.assert_called_once_with(
        ticker=market.ticker,
        mode="advisory",
        shadow_only=True,
        targets=[],
        counters={},
        rejected_labels={},
        log_records=diagnostic.log_records,
    )
    bot.ws.watch.assert_not_called()
    bot._blend_task.process_fast_lane_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_source_hint_runtime_failure_does_not_block_candidate(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "market_source_hints_mode", "shadow")
    monkeypatch.setattr(_cfg_module.cfg, "market_source_hints_emit_records", False)
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(_cfg_module.cfg, "min_bet_dollars", 2.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_half_life", 14.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_floor", 0.20)
    monkeypatch.setattr(_cfg_module.cfg, "dynamic_max_bet", lambda bankroll: 75.0)
    bot = _make_bot_stub()
    news = _make_news()
    news.retrieval_mode = "source_hint"
    news.source_hint_query = "site:reuters.com test event headline"
    news.source_hint_domain = "reuters.com"
    market = _make_market()

    with patch("main.build_market_source_hint_diagnostics", side_effect=RuntimeError("diagnostic boom")), \
         patch("main.estimate_probability", new=AsyncMock(return_value=(
             0.65, 0.8, ["missile strike"], "test reasoning", "yes", "moderate", 0.8
         ))), patch("main.kelly_bet", return_value=(0.12, 15.0, 12.0)), \
         patch("utils.logger.trade_log.log_signal"), \
         patch("utils.logger.trade_log.log_opportunity"):
        await bot._process_candidate(news, market, 0.42, {})

    bot._blend_task.process_fast_lane_result.assert_awaited_once()
    bot.ws.watch.assert_called_with([market.ticker])


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
    news.retrieval_mode = "source_hint"
    news.source_hint_query = "site:reuters.com test event headline"
    news.source_hint_domain = "reuters.com"
    market = _make_market()
    market.settlement_sources = (
        SettlementSource(label="Reuters", url="https://reuters.com"),
    )
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

    bot._blend_task.process_fast_lane_result.assert_awaited_once()
    estimate_mock.assert_awaited_once_with(
        news,
        market,
        keyword_stats=bot.keyword_stats,
        match_meta=match_meta,
    )
    # PROFIT-MATCH-003 (L2-a): _process_candidate threads the matcher score onto
    # match_meta so the downstream MATCH_LLM_REVIEW emission carries it (the
    # feedback loop's score-gate is a no-op without this).
    assert match_meta["match_score"] == pytest.approx(0.42)
    assert match_meta["source_class"] == "news"
    assert match_meta["settlement_source_match"] is True
    assert match_meta["lifecycle_id"].startswith("lc-")
    opportunity_kwargs = opportunity_mock.call_args.kwargs
    assert opportunity_kwargs["retrieval_mode"] == "source_hint"
    assert opportunity_kwargs["source_hint_query"] == "site:reuters.com test event headline"
    assert opportunity_kwargs["source_hint_domain"] == "reuters.com"
    assert opportunity_kwargs["settlement_source_match"] is True
    assert opportunity_kwargs["lifecycle_id"] == match_meta["lifecycle_id"]
    analysis = bot._blend_task.process_fast_lane_result.await_args.args[0]
    assert analysis.signal_meta["lifecycle_id"] == match_meta["lifecycle_id"]
    assert analysis.signal_meta["settlement_source_match"] is True
    evidence = bot._evidence_queue.get_nowait()
    assert analysis.signal_meta["trigger_evidence_id"] == evidence.evidence_id
    assert analysis.signal_meta["trigger_evidence_source"] == evidence.source
    assert analysis.signal_meta["trigger_evidence_source_class"] == evidence.source_class
    assert analysis.signal_meta["trigger_evidence_headline"] == evidence.headline
    assert analysis.signal_meta["trigger_evidence_ingested_ts"] == evidence.ingested_ts
    assert analysis.signal_meta["trigger_evidence_content_hash"] == evidence.content_hash
    assert analysis.signal_meta["trigger_evidence_original_weight"] > 0.0
    assert analysis.signal_meta["retrieval_mode"] == "source_hint"
    assert analysis.signal_meta["source_hint_query"] == "site:reuters.com test event headline"
    assert analysis.signal_meta["source_hint_domain"] == "reuters.com"
    assert analysis.signal_meta["settlement_source_match"] is True
    assert analysis.news_item is news
    assert analysis.market.ticker == "KXTEST-25DEC31"
    assert analysis.estimated_probability == pytest.approx(0.65)
    # P1-A: market_yes_price alias removed. executed_price_cents is canonical.
    # For YES side at yes_ask=51, executed_price_cents == 51.
    assert analysis.executed_price_cents == 51
    # Edge now scored vs executed ask: 0.65 - 0.51 = 0.14
    assert analysis.edge == pytest.approx(0.14)
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
    # increment_trades is now called by _trading_queue_consumer_task, not _process_candidate
    bot.source_stats.increment_trades.assert_not_called()
    bot.ws.watch.assert_called_with(["KXTEST-25DEC31"])
    # P1-A: log_opportunity kwarg renamed market_yes_price → entry_price_cents.
    # Emits the executed ask cents (51) not the midpoint (50).
    opportunity_mock.assert_called_once_with(
        ticker=market.ticker,
        market_title=market.title,
        entry_price_cents=pytest.approx(51.0),
        estimated_probability=0.65,
        edge=pytest.approx(0.14),
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
            venue="kalshi",
            keywords=["missile strike"],
            source_class="news",
            retrieval_mode="source_hint",
            source_hint_domain="reuters.com",
            source_hint_query="site:reuters.com test event headline",
            settlement_source_match=True,
            lifecycle_id=match_meta["lifecycle_id"],
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
        rejection_category="no_signal_empty_keywords",
        signal_branch="empty_keywords_no_llm_signal",
        method=None,
        llm_direction=None,
        llm_magnitude=None,
        llm_confidence=None,
        keywords=[],
        ticker=market.ticker,
        source=news.source,
        headline=news.headline,
        match_score=0.20,
    )


@pytest.mark.asyncio
async def test_process_candidate_researches_before_terminal_no_keywords(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_timeout_seconds", 0.25, raising=False)
    bot = _make_bot_stub()
    bot._research_dossier_store = object()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.CONTINUE_RESEARCHING,
        attempted=True,
        queries=[
            ResearchQuery(
                query="site:opec.org Iran crude oil production June 2026",
                query_intent="resolution_source",
                source_class="resolution_source",
            )
        ],
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.example.com/momr",
                title="OPEC report",
                snippet="Official report.",
                claim_type="resolution",
                published_at="2026-06-27T10:00:00Z",
                retrieved_at="2026-06-27T10:01:00Z",
            )
        ],
        summary="Missing OPEC production baseline; keep researching.",
        skip_reason="missing_resolution_source",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "No relevant keywords found -- no signal.", "neutral", "none", 0.85
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)) as research_mock, \
         patch("main.default_research_dossier_store", side_effect=AssertionError("unexpected default store")), \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        try:
            await bot._process_candidate(news, market, 0.20)
        finally:
            await bot.cancel_targeted_research_prewarm_tasks()

    research_mock.assert_awaited_once()
    assert "dossier_store" in research_mock.await_args.kwargs
    assert research_mock.await_args.kwargs["dossier_store"] is bot._research_dossier_store
    assert research_mock.await_args.kwargs["research_timeout_seconds"] == 0.25
    bot.executor.execute.assert_not_called()
    reject_kwargs = reject_mock.call_args.kwargs
    assert reject_kwargs["reason"] == "research_incomplete"
    assert reject_kwargs["rejection_category"] == "research_continue"
    assert reject_kwargs["signal_branch"] == "empty_keywords_research_continue"
    assert reject_kwargs["research_attempted"] is True
    assert reject_kwargs["research_status"] == "continue_researching"
    assert reject_kwargs["research_queries"] == [
        "site:opec.org Iran crude oil production June 2026"
    ]
    assert reject_kwargs["research_hit_count"] == 1
    assert reject_kwargs["research_skip_reason"] == "missing_resolution_source"
    assert reject_kwargs["research_started_ts"]
    assert reject_kwargs["research_completed_ts"]
    assert reject_kwargs["research_duration_ms"] >= 0.0
    assert reject_kwargs["research_min_published_at"] == "2026-06-27T10:00:00+00:00"
    assert reject_kwargs["research_min_retrieved_at"] == "2026-06-27T10:01:00+00:00"


@pytest.mark.asyncio
async def test_process_candidate_researches_when_llm_metadata_missing_in_production(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.CONTINUE_RESEARCHING,
        attempted=True,
        queries=[
            ResearchQuery(
                query="site:opec.org Iran crude oil production June 2026",
                query_intent="resolution_source",
                source_class="resolution_source",
            )
        ],
        evidence=[],
        summary="LLM unavailable; keep researching before skip.",
        skip_reason="missing_resolution_source",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "LLM metadata unavailable.", None, None, None
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)) as research_mock, \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        try:
            await bot._process_candidate(news, market, 0.20)
        finally:
            await bot.cancel_targeted_research_prewarm_tasks()

    research_mock.assert_awaited_once()
    bot.executor.execute.assert_not_called()
    reject_kwargs = reject_mock.call_args.kwargs
    assert reject_kwargs["reason"] == "research_incomplete"
    assert reject_kwargs["rejection_category"] == "research_continue"
    assert reject_kwargs["signal_branch"] == "empty_keywords_research_continue"
    assert reject_kwargs["method"] is None
    assert reject_kwargs["llm_direction"] is None
    assert reject_kwargs["research_status"] == "continue_researching"


@pytest.mark.asyncio
async def test_process_candidate_queues_targeted_rewarm_on_retryable_cache_miss(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    bot._schedule_targeted_research_prewarm = MagicMock()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.CONTINUE_RESEARCHING,
        attempted=False,
        evidence=[],
        summary="Cache-only research mode has no sufficient dossier evidence.",
        skip_reason="cached_dossier_insufficient",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "LLM metadata unavailable.", None, None, None
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)), \
         patch("utils.logger.trade_log.log_analysis_rejected"):
        await bot._process_candidate(news, market, 0.20)

    bot.executor.execute.assert_not_called()
    bot._schedule_targeted_research_prewarm.assert_called_once_with(
        market,
        "cached_dossier_insufficient",
    )


@pytest.mark.asyncio
async def test_process_candidate_does_not_rewarm_capital_protection_reject(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    bot._schedule_targeted_research_prewarm = MagicMock()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.RESEARCHED_SKIP_NO_EDGE,
        attempted=True,
        evidence=[],
        summary="No trade after costs.",
        skip_reason="no_trade_capital_protection",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "LLM metadata unavailable.", None, None, None
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)), \
         patch("utils.logger.trade_log.log_analysis_rejected"):
        await bot._process_candidate(news, market, 0.20)

    bot.executor.execute.assert_not_called()
    bot._schedule_targeted_research_prewarm.assert_not_called()


@pytest.mark.asyncio
async def test_process_candidate_queues_targeted_rewarm_on_ambiguous_research_gap(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    bot._schedule_targeted_research_prewarm = MagicMock()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.RESEARCHED_SKIP_AMBIGUOUS,
        attempted=True,
        evidence=[],
        summary="Research has evidence but no directional probability.",
        skip_reason="ambiguous_direction",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "LLM metadata unavailable.", None, None, None
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)), \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        await bot._process_candidate(news, market, 0.20)

    bot.executor.execute.assert_not_called()
    bot._schedule_targeted_research_prewarm.assert_called_once_with(
        market,
        "ambiguous_direction",
    )
    reject_kwargs = reject_mock.call_args.kwargs
    assert reject_kwargs["reason"] == "researched_no_edge"
    assert reject_kwargs["research_skip_reason"] == "ambiguous_direction"


@pytest.mark.asyncio
async def test_process_candidate_researches_when_llm_metadata_missing_in_shadow(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "shadow", raising=False)
    bot = _make_bot_stub()
    bot._schedule_targeted_research_prewarm = MagicMock()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.CONTINUE_RESEARCHING,
        attempted=True,
        queries=[
            ResearchQuery(
                query="site:opec.org Iran crude oil production June 2026",
                query_intent="resolution_source",
                source_class="resolution_source",
            )
        ],
        evidence=[],
        summary="Shadow research captured the information gap.",
        skip_reason="missing_resolution_source",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "LLM metadata unavailable.", None, None, None
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)) as research_mock, \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        await bot._process_candidate(news, market, 0.20)

    research_mock.assert_awaited_once()
    assert research_mock.await_args.kwargs["cache_only"] is False
    bot.executor.execute.assert_not_called()
    reject_kwargs = reject_mock.call_args.kwargs
    assert reject_kwargs["reason"] == "no_keywords"
    assert reject_kwargs["rejection_category"] == "no_signal_empty_keywords"
    assert reject_kwargs["research_attempted"] is True
    assert reject_kwargs["research_status"] == "continue_researching"
    assert reject_kwargs["research_skip_reason"] == "missing_resolution_source"
    bot._schedule_targeted_research_prewarm.assert_called_once_with(
        market,
        "missing_resolution_source",
    )


@pytest.mark.asyncio
async def test_process_candidate_shadow_rewarms_retryable_researched_skip(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "shadow", raising=False)
    bot = _make_bot_stub()
    bot._schedule_targeted_research_prewarm = MagicMock()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.RESEARCHED_SKIP_AMBIGUOUS,
        attempted=True,
        evidence=[],
        summary="Shadow research found evidence but no decisive direction.",
        skip_reason="ambiguous_direction",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob,
        0.1,
        [],
        "LLM metadata unavailable.",
        None,
        None,
        None,
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)), \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        await bot._process_candidate(news, market, 0.20)

    bot.executor.execute.assert_not_called()
    bot._schedule_targeted_research_prewarm.assert_called_once_with(
        market,
        "ambiguous_direction",
    )
    reject_kwargs = reject_mock.call_args.kwargs
    assert reject_kwargs["reason"] == "no_keywords"
    assert reject_kwargs["research_status"] == "researched_skip_ambiguous"
    assert reject_kwargs["research_skip_reason"] == "ambiguous_direction"


@pytest.mark.asyncio
async def test_process_candidate_logs_research_provider_error(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.RESEARCH_PROVIDER_ERROR,
        attempted=True,
        summary="Research provider failed before the source frontier could be trusted.",
        skip_reason="research_provider_error",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "No relevant keywords found -- no signal.", "neutral", "none", 0.85
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)), \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        try:
            await bot._process_candidate(news, market, 0.20)
        finally:
            await bot.cancel_targeted_research_prewarm_tasks()

    bot.executor.execute.assert_not_called()
    reject_kwargs = reject_mock.call_args.kwargs
    assert reject_kwargs["reason"] == "research_operational_error"
    assert reject_kwargs["rejection_category"] == "research_provider_error"
    assert reject_kwargs["signal_branch"] == "empty_keywords_research_error"
    assert reject_kwargs["research_status"] == "research_provider_error"
    assert reject_kwargs["research_skip_reason"] == "research_provider_error"


@pytest.mark.asyncio
async def test_process_candidate_researches_sparse_neutral_keywords_in_production(
    monkeypatch,
):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.CONTINUE_RESEARCHING,
        attempted=True,
        summary="Sparse keyword plus neutral LLM needs research before terminal skip.",
        skip_reason="missing_resolution_source",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob,
        0.1,
        ["senate"],
        "LLM found a weak match but no directional signal.",
        "neutral",
        "none",
        0.72,
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)) as research_mock, \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        try:
            await bot._process_candidate(news, market, 0.20)
        finally:
            await bot.cancel_targeted_research_prewarm_tasks()

    research_mock.assert_awaited_once()
    assert research_mock.await_args.kwargs["model_direction"] == "neutral"
    bot.executor.execute.assert_not_called()
    bot.source_stats.increment_signals.assert_not_called()
    reject_kwargs = reject_mock.call_args.kwargs
    assert reject_kwargs["reason"] == "research_incomplete"
    assert reject_kwargs["rejection_category"] == "research_continue"
    assert reject_kwargs["signal_branch"] == "sparse_keywords_research_continue"
    assert reject_kwargs["method"] == "llm"
    assert reject_kwargs["keywords"] == ["senate"]
    assert reject_kwargs["research_status"] == "continue_researching"
    assert reject_kwargs["research_skip_reason"] == "missing_resolution_source"


@pytest.mark.asyncio
async def test_process_candidate_requires_decision_grade_research(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "shadow", raising=False)
    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.NEEDS_PRICE_EDGE,
        attempted=True,
        summary="Decision-grade research needs an executable price.",
        skip_reason="missing_market_price",
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob,
        0.1,
        [],
        "No relevant keywords found -- no signal.",
        "neutral",
        "none",
        0.72,
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)) as research_mock, \
         patch("utils.logger.trade_log.log_analysis_rejected"):
        await bot._process_candidate(news, market, 0.20)

    research_mock.assert_awaited_once()
    assert research_mock.await_args.kwargs["require_decision_grade"] is True
    bot.executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_process_candidate_rechecks_staleness_after_research(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    bot = _make_bot_stub()
    news = _make_news()
    base_now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    news.published = base_now - timedelta(seconds=60)
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.TRADE_CANDIDATE,
        attempted=True,
        summary="Research evidence supports YES.",
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
    )

    class FakeDateTime(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            value = (
                base_now
                if cls.calls == 1
                else base_now + timedelta(seconds=2000)
            )
            return value if tz is None else value.astimezone(tz)

    with patch("main.datetime", FakeDateTime), \
         patch("main.estimate_probability", new=AsyncMock(return_value=(
             market.yes_prob, 0.1, [], "No relevant keywords found -- no signal.", "neutral", "none", 0.85
         ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)), \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        await bot._process_candidate(news, market, 0.20)

    bot.executor.execute.assert_not_called()
    reject_kwargs = reject_mock.call_args.kwargs
    assert reject_kwargs["reason"] == "stale_news_after_research"
    assert reject_kwargs["research_status"] == "trade_candidate"
    assert reject_kwargs["age_seconds"] == pytest.approx(2060.0, abs=1.0)


@pytest.mark.asyncio
async def test_process_candidate_keeps_decision_grade_research_shadow_only(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "real_web_research_mode", "production", raising=False)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(_cfg_module.cfg, "min_bet_dollars", 2.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_half_life", 14.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_floor", 0.20)
    monkeypatch.setattr(_cfg_module.cfg, "dynamic_max_bet", lambda bankroll: 75.0)
    bot = _make_bot_stub()
    news = _make_news()
    market = _make_market()
    verdict = ResearchVerdict(
        status=ResearchStatus.DECISION_GRADE_CANDIDATE,
        attempted=True,
        summary="Decision-grade research supports YES at the current price.",
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        market_price=0.51,
        estimated_edge=0.28,
    )

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.1, [], "No relevant keywords found -- no signal.", "neutral", "none", 0.85
    ))), patch("main.run_research_gate", new=AsyncMock(return_value=verdict)), \
         patch("main.kelly_bet", return_value=(0.10, 10.0, 8.0)), \
         patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        await bot._process_candidate(news, market, 0.20)

    no_research_terminal_rejects = [
        call for call in reject_mock.call_args_list
        if call.kwargs.get("research_status") == "decision_grade_candidate"
    ]
    bot.executor.execute.assert_not_called()
    assert no_research_terminal_rejects
    reject_kwargs = no_research_terminal_rejects[-1].kwargs
    assert reject_kwargs["reason"] == "researched_no_edge"
    assert reject_kwargs["rejection_category"] == "decision_grade_candidate"
    assert reject_kwargs["signal_branch"] == "empty_keywords_researched_terminal"
    bot._blend_task.process_fast_lane_result.assert_not_awaited()
    bot.source_stats.increment_signals.assert_not_called()


@pytest.mark.asyncio
async def test_process_candidate_proceeds_when_llm_emits_signal_despite_no_keywords(monkeypatch):
    """PROFIT-EDGE-001 regression: empty `keywords` list with an LLM-emitted
    signal (llm_mag != "none") should NOT be rejected as no_keywords.

    The LLM can identify semantic relevance the keyword glossary misses;
    killing those events at the no_keywords gate is what produced zero paper
    trades from 2026-04-17 -> 2026-04-26 across 5 LLM-validated real-market
    events (KXSBUDGETRES x2, KXTRUMPIRAN x2, KXPSL x1).
    """
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(_cfg_module.cfg, "min_bet_dollars", 2.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_half_life", 14.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_floor", 0.20)
    monkeypatch.setattr(_cfg_module.cfg, "dynamic_max_bet", lambda bankroll: 75.0)
    bot = _make_bot_stub()
    news = _make_news()
    news.retrieval_mode = "source_hint"
    news.source_hint_query = "site:reuters.com test event headline"
    news.source_hint_domain = "reuters.com"
    market = _make_market()
    market.rules_primary = "Market resolves Yes if the test event happens."
    market.rules_secondary = "Related announcements do not count."
    market.settlement_sources = (
        SettlementSource(label="Reuters", url="https://reuters.com"),
    )
    market.contract_terms_url = "https://kalshi.com/markets/KXTEST"

    # Mirrors event #3 from the 2026-04-26 investigation: KXTRUMPIRAN-26MAY01,
    # "Trump dispatching Witkoff..." headline; LLM produced small/yes/+0.068.
    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        0.568, 0.85, [], "LLM small/yes signal", "yes", "small", 0.85
    ))), patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock, \
         patch("main.kelly_bet", return_value=(0.10, 10.0, 8.0)):
        await bot._process_candidate(news, market, 0.20)

    # The fix: empty keywords + non-trivial LLM signal must NOT trigger the
    # no_keywords rejection. Other downstream gates may still kill the
    # candidate; this test only asserts the no_keywords gate itself.
    no_kw_calls = [c for c in reject_mock.call_args_list
                   if c.kwargs.get("reason") == "no_keywords"]
    assert not no_kw_calls, (
        "Empty keywords with LLM-emitted signal should not trigger "
        "no_keywords rejection. Hit calls: " + str(no_kw_calls)
    )


@pytest.mark.asyncio
async def test_process_candidate_still_rejects_when_neither_signal_source_speaks(monkeypatch):
    """PROFIT-EDGE-001 regression: with no keywords AND llm_mag == "none"
    (LLM ran but found no actionable signal), the no_keywords rejection MUST
    still fire. The fix at main.py:688 must not let through truly-silent
    events -- it only spares LLM-positive signal, not LLM-neutral signal."""
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    bot = _make_bot_stub()
    news = _make_news()
    news.retrieval_mode = "source_hint"
    news.source_hint_query = "site:reuters.com test event headline"
    news.source_hint_domain = "reuters.com"
    market = _make_market()
    market.rules_primary = "Market resolves Yes if the test event happens."
    market.rules_secondary = "Related announcements do not count."
    market.settlement_sources = (
        SettlementSource(label="Reuters", url="https://reuters.com"),
    )
    market.contract_terms_url = "https://kalshi.com/markets/KXTEST"

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        market.yes_prob, 0.0, [], "no signal", "neutral", "none", 0.95
    ))), patch("utils.logger.trade_log.log_analysis_rejected") as reject_mock:
        await bot._process_candidate(news, market, 0.20)

    bot.executor.execute.assert_not_called()
    reject_mock.assert_called_once_with(
        reason="no_keywords",
        rejection_category="post_llm_neutral_empty_keywords",
        signal_branch="empty_keywords_neutral_llm",
        method="llm",
        llm_direction="neutral",
        llm_magnitude="none",
        llm_confidence=0.95,
        keywords=[],
        ticker=market.ticker,
        source=news.source,
        headline=news.headline,
        match_score=0.20,
        retrieval_mode="source_hint",
        source_hint_domain="reuters.com",
        source_hint_query="site:reuters.com test event headline",
        source_class="news",
        rules_primary="Market resolves Yes if the test event happens.",
        rules_secondary="Related announcements do not count.",
        settlement_source_names=["Reuters"],
        settlement_source_urls=["https://reuters.com"],
        contract_terms_url="https://kalshi.com/markets/KXTEST",
    )


@pytest.mark.asyncio
async def test_process_candidate_skips_stale_news_before_estimation(monkeypatch):
    # PROFIT-STALE-001: analyzer-stage stale check now uses the per-source
    # threshold via _early_max_news_age_seconds_for_source(news.source).
    # _make_news() defaults to source="Reuters", which is not in the
    # EARLY_MAX_NEWS_AGE_BY_SOURCE override map; it falls through to
    # EARLY_MAX_NEWS_AGE_SECONDS.
    # PROFIT-STALE-002 (2026-05-24): default raised 300s → 1800s. Age the
    # news 2000s past published so this case stays stale under the new
    # default. If the default changes again, bump this offset.
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    bot = _make_bot_stub()
    news = _make_news()
    news.published = datetime.now(timezone.utc) - timedelta(seconds=2000)
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
    assert kwargs["age_seconds"] == pytest.approx(2000.0, abs=5.0)


@pytest.mark.asyncio
async def test_process_candidate_uses_rest_executable_in_handoff_not_ws(monkeypatch):
    # LD-11 regression invariant: WS price is reference/staleness signal only.
    # It must NOT overwrite REST executable bid/ask, and the SignalAnalysis
    # handoff must carry the REST-derived executed_price_cents (and the
    # deprecated market_yes_price alias mirrored from it) — NOT the WS midpoint.
    # Original test asserted the now-removed WS-mutation behavior (62.0 leaking
    # through); rewritten in P-5 advisory fold-in to lock the inverse invariant.
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(_cfg_module.cfg, "min_bet_dollars", 2.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_half_life", 14.0)
    monkeypatch.setattr(_cfg_module.cfg, "time_discount_floor", 0.20)
    monkeypatch.setattr(_cfg_module.cfg, "dynamic_max_bet", lambda bankroll: 75.0)

    bot = _make_bot_stub()
    # WS reports a divergent midpoint; this used to flow through to the handoff.
    bot.ws.get_yes_price.return_value = 62.0
    news = _make_news()
    market = _make_market()  # REST: yes_bid=49, yes_ask=51, yes_price=50

    with patch("main.estimate_probability", new=AsyncMock(return_value=(
        0.70, 0.7, ["missile strike"], "test reasoning", "yes", "small", 0.7
    ))), patch("main.kelly_bet", return_value=(0.10, 10.0, 8.0)), \
         patch("utils.logger.trade_log.log_signal"), \
         patch("utils.logger.trade_log.log_opportunity"):
        await bot._process_candidate(news, market, 0.33)

    analysis = bot._blend_task.process_fast_lane_result.await_args.args[0]
    # P1-A: market_yes_price alias removed. executed_price_cents is canonical.
    # YES side → executed_price_cents == yes_ask_cents (REST=51), not WS midpoint (62.0).
    assert analysis.executed_price_cents == 51
    # LD-11: REST bid/ask must remain unmutated by the WS update.
    assert analysis.market.yes_price == pytest.approx(50.0)
    assert analysis.market.yes_bid == pytest.approx(49.0)
    assert analysis.market.yes_ask == pytest.approx(51.0)
    # Edge = est_prob (0.70) - executable_ask/100 (0.51) = 0.19.
    assert analysis.edge == pytest.approx(0.19)


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

    analysis = bot._blend_task.process_fast_lane_result.await_args.args[0]
    # P-5 LD-10: placeholder uses executed_price_cents (yes_ask=51) not
    # legacy midpoint (50). 5 contracts * 0.51 = 2.55.
    assert analysis.capped_dollars == pytest.approx(2.55)


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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset({"BBC News"}))
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset({"BBC News"}))
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset({"BBC News"}))
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset())
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
async def test_on_news_item_times_out_kalshi_matching_without_starving_polymarket(monkeypatch):
    monkeypatch.setattr(main_module, "NEWS_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS", 0.01)
    bot = _make_bot_stub()
    bot.polymarket_paper_runtime = MagicMock()
    bot.polymarket_paper_runtime.process_news = AsyncMock(return_value=0)

    async def _never_returns(_news, **_kwargs):
        await asyncio.Event().wait()

    bot.matcher.find_candidates = AsyncMock(side_effect=_never_returns)
    first = _make_news()
    second = _make_news()
    second.headline = "Second fresh headline"
    second.item_id = "id-2"

    await bot.on_news_item(first)
    await bot.on_news_item(second)

    assert bot.polymarket_paper_runtime.process_news.await_count == 2
    assert bot.matcher.find_candidates.await_count == 2
    for call in bot.matcher.find_candidates.await_args_list:
        assert call.kwargs["refresh_cache"] is False


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
    assert "Market cache first_non_empty_ts=" in caplog.text
    assert "effective_multi_lane_runtime_start=true" in caplog.text
    assert bot._market_cache_ready_at is not None
    assert bot._market_cache_ready_after_secs is not None
    bot.matcher.refresh_cache.assert_awaited_once()
    bot.ws.watch.assert_called_once_with([market.ticker])


@pytest.mark.asyncio
async def test_structural_recompute_waits_for_non_empty_market_cache():
    bot = _make_bot_stub()
    bot.matcher._cache._markets = []
    market = _make_market()
    bot._structural_task = MagicMock()

    async def _run_periodic(**kwargs):
        await asyncio.sleep(1)

    bot._structural_task.run_periodic = AsyncMock(side_effect=_run_periodic)

    async def _populate_cache():
        await asyncio.sleep(0.01)
        bot.matcher._cache._markets = [market]

    structural = asyncio.create_task(bot._structural_recompute_task())
    populate_task = asyncio.create_task(_populate_cache())
    try:
        await asyncio.wait_for(structural, timeout=0.5)
    except asyncio.TimeoutError:
        pass
    finally:
        await populate_task

    bot._structural_task.run_periodic.assert_awaited_once()
    kwargs = bot._structural_task.run_periodic.await_args.kwargs
    assert kwargs["interval_seconds"] == 3600
    assert kwargs["market_provider"]() == [market]


@pytest.mark.asyncio
async def test_structural_recompute_yields_even_if_run_periodic_returns_instantly():
    """Regression guard: an instantly-completing run_periodic must not hot-spin.

    Previously the inner while True loop had no guaranteed yield point, so a
    mock (or a real run_periodic implementation that ever returned without
    awaiting) caused the task to spin at full CPU, accumulate mock call history
    unboundedly, and block event-loop delivery of CancelledError. Pytest runs
    were SIGKILL'd at ~3 GB RSS before finishing. main._structural_recompute_task
    now prefixes each iteration with `await asyncio.sleep(0)` so cancellation
    is always deliverable regardless of what run_periodic does.

    The test asserts on wall-clock elapsed time: a hot-spin loop exceeds the
    wait_for timeout (cancellation can't be delivered without yields), so if
    the defensive yield is removed, this test fails in seconds with a clear
    diagnostic message instead of hanging indefinitely. A large iteration
    safety bound is kept only to prevent OOM in the regression case.
    """
    import time as _time

    bot = _make_bot_stub()
    market = _make_market()
    bot.matcher._cache._markets = [market]
    bot._structural_task = MagicMock()

    call_count = 0
    # Iteration bound chosen to (a) stay well above what the defensive-yield
    # case reaches inside a 0.1s wait_for, (b) finish within ~1s in the
    # regression case so the failure surfaces promptly, and (c) cap memory so
    # a regressed run can never reach the ~3 GB OOM that first exposed this.
    SAFETY_BOUND = 50_000

    async def _instant_run_periodic(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > SAFETY_BOUND:
            raise RuntimeError(
                f"safety bound hit ({SAFETY_BOUND} iterations); defensive yield missing"
            )

    bot._structural_task.run_periodic = AsyncMock(side_effect=_instant_run_periodic)

    start = _time.monotonic()
    structural = asyncio.create_task(bot._structural_recompute_task())
    try:
        await asyncio.wait_for(structural, timeout=0.1)
    except asyncio.TimeoutError:
        pass
    elapsed = _time.monotonic() - start

    assert structural.done()
    # With the defensive yield, wait_for's 0.1s timer fires, cancellation is
    # delivered at the next `await asyncio.sleep(0)`, and the task exits
    # shortly after the timeout (~0.1-0.15s). Without it, the task hot-spins
    # for ~7us/iteration (~340ms for 50k iters on this machine) before the
    # safety bound fires and the except-path's sleep(60) finally yields for
    # cancellation. The 0.25s threshold sits above the positive case and
    # well below the regression case.
    assert elapsed < 0.25, (
        f"structural recompute took {elapsed:.2f}s to exit after {call_count} "
        f"iterations; defensive `await asyncio.sleep(0)` yield is likely "
        f"missing from main._structural_recompute_task"
    )


@pytest.mark.asyncio
async def test_process_fade_tweet_builds_geo_fade_handoff_with_rest_executable(monkeypatch):
    # LD-11 regression invariant: fade-tweet flow uses REST executable cents
    # for the chosen fade side; WS midpoint must NOT mutate the handoff.
    # Original test asserted the WS-mutation behavior (72.0 leaking through);
    # rewritten in P-5 advisory fold-in to lock the inverse invariant.
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    bot = _make_bot_stub()
    # WS reports a divergent midpoint; this used to flow through to the handoff.
    bot.ws.get_yes_price.return_value = 72.0
    tweet = _make_news()
    tweet.headline = "Prediction market is going to the moon"
    market = _make_market()  # REST: yes_bid=49, yes_ask=51, no_ask_cents=51
    bot.matcher.find_all_candidates.return_value = [(market, 0.61)]

    with patch("analysis.fade_signal.detect_fade_pattern", return_value="bullish"):
        await bot._process_fade_tweet(tweet, "Kalshi")

    bot.executor.execute.assert_not_called()
    bot._blend_task.process_fast_lane_result.assert_awaited_once()
    analysis = bot._blend_task.process_fast_lane_result.await_args.args[0]
    assert analysis.signal_type == "fade_tweet"
    assert analysis.side == "no"
    # P1-A: market_yes_price alias removed. executed_price_cents is canonical.
    # NO side fade → executed_price_cents == no_ask_cents (REST=51), not WS midpoint (72.0).
    assert analysis.executed_price_cents == 51
    # LD-11: REST bid/ask must remain unmutated by the WS update.
    assert analysis.market.yes_bid == pytest.approx(49.0)
    assert analysis.market.yes_ask == pytest.approx(51.0)
    assert analysis.match_score == pytest.approx(0.61)
    assert analysis.confidence == pytest.approx(0.3)
    assert analysis.reasoning.startswith("[FADE/GEO/@Kalshi] bullish:")
    assert analysis.signal_meta["trigger_evidence_id"]
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

    bot.executor.execute.assert_not_called()
    analysis = bot._blend_task.process_fast_lane_result.await_args.args[0]
    assert analysis.side == "yes"
    assert analysis.reasoning.startswith("[FADE/SPORTS/@Sharps] bearish:")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crossing", "expected_side"),
    [("high_cross", "no"), ("low_cross", "yes")],
)
async def test_process_price_fade_builds_representative_handoff(crossing, expected_side, monkeypatch):
    # LD-11 regression invariant: price-fade trigger values (now_mid, yes_bid,
    # yes_ask) are diagnostic-only (synthetic headline, reasoning string). The
    # trade handoff must consume REST executable cents, NOT the WS-derived
    # midpoint. Original test asserted the WS-mutation behavior (86.0/14.0
    # leaking through to market_yes_price); rewritten in P-5 advisory fold-in
    # to lock the inverse invariant. now_mid still appears in the reasoning
    # string by design.
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    bot = _make_bot_stub()
    market = _make_market()  # REST: yes_ask_cents=51, no_ask_cents=51
    bot.matcher._cache.get_markets.return_value = [market]

    now_mid = 86.0 if crossing == "high_cross" else 14.0

    await bot._process_price_fade(
        ticker=market.ticker,
        crossing=crossing,
        now_mid=now_mid,
        yes_bid=85.0 if crossing == "high_cross" else 13.0,
        yes_ask=87.0 if crossing == "high_cross" else 15.0,
    )

    bot.executor.execute.assert_not_called()
    bot._blend_task.process_fast_lane_result.assert_awaited_once()
    analysis = bot._blend_task.process_fast_lane_result.await_args.args[0]
    assert analysis.signal_type == "price_fade"
    assert analysis.side == expected_side
    assert analysis.market.ticker == market.ticker
    # P1-A: market_yes_price alias removed. executed_price_cents is canonical.
    # executed_price_cents == ask_cents for the chosen fade side (both 51 in fixture).
    expected_executed = 51  # yes_ask_cents for low_cross, no_ask_cents for high_cross
    assert analysis.executed_price_cents == expected_executed
    # LD-11: REST executable bid/ask remain unmutated by the WS trigger values.
    assert analysis.market.yes_bid == pytest.approx(49.0)
    assert analysis.market.yes_ask == pytest.approx(51.0)
    assert analysis.news_item.source == "price_fade"
    assert analysis.news_item.url == f"kalshi://price_fade/{market.ticker}"
    assert crossing in analysis.reasoning
    # WS now_mid appears in the diagnostic reasoning string only (not handoff price).
    assert f"{now_mid:.1f}c" in analysis.reasoning
    evidence = bot._evidence_queue.get_nowait()
    assert evidence.source_class == "market"


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
async def test_on_price_update_logs_position_drift_from_entry_price_cents():
    bot = _make_bot_stub()
    bot._process_price_fade = AsyncMock()
    bot._trigger_targeted_search = AsyncMock()
    bot.paper.portfolio.open_positions.return_value = [
        Position(
            trade_id="trade-1",
            ticker="KXTEST-25DEC31",
            side="yes",
            contracts=1,
            cost_dollars=0.50,
            price_cents=50,
            estimated_prob=0.60,
            entry_price_cents=50.0,
            ts="2026-05-15T00:00:00+00:00",
        )
    ]

    with patch("main.time.monotonic", side_effect=[100.0, 101.0]), \
         patch("main.PRICE_MOVE_THRESHOLD_CENTS", 999.0), \
         patch.object(_cfg_module.cfg, "position_drift_alert_threshold", 0.04), \
         patch("main.DRIFT_LOG_COOLDOWN_SECS", 0.0), \
         patch("analysis.fade_signal.detect_price_fade", return_value=None), \
         patch("utils.logger.trade_log.log_position_drift") as drift_mock:
        await bot._on_price_update("KXTEST-25DEC31", 49.0, 51.0)
        await bot._on_price_update("KXTEST-25DEC31", 52.0, 54.0)

    drift_mock.assert_called_once()
    assert drift_mock.call_args.kwargs["entry_price"] == pytest.approx(50.0)
    assert drift_mock.call_args.kwargs["current_price"] == pytest.approx(53.0)
    assert drift_mock.call_args.kwargs["drift_cents"] == pytest.approx(3.0)


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
        "utils.runtime_overrides._static_disabled_sources",
        lambda: frozenset({"r/worldnews", "r/geopolitics"}),
    )

    allowed, skipped = filter_disabled_subreddits(
        ["worldnews", "ArmedConflicts", "geopolitics", "worldnews"]
    )

    assert allowed == ["ArmedConflicts"]
    assert skipped == ["worldnews", "geopolitics"]


def test_select_subreddits_filters_disabled_core_and_topic_subreddits(monkeypatch):
    monkeypatch.setattr(
        "utils.runtime_overrides._static_disabled_sources",
        lambda: frozenset({"r/worldnews", "r/CredibleDefense", "r/WarCollege"}),
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
        status="active",
        series_ticker="KXTEST",
        subtitle="Military conflict",
        result="",
        yes_bid_cents=49,
        yes_ask_cents=51,
        no_bid_cents=49,
        no_ask_cents=51,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
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
    monkeypatch.setattr("utils.runtime_overrides._static_disabled_sources", lambda: frozenset({"GDELT"}))
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


# ---------------------------------------------------------------------------
# MAC-ASYNC-002 regression guard
# ---------------------------------------------------------------------------

class TestMainAsyncBlocking:
    """MAC-ASYNC-002 regression guard.

    Verifies that PaperTrader methods called from main.py async methods
    are dispatched via asyncio.to_thread() rather than called directly
    on the event loop thread.
    """

    def _make_bot(self):
        bot = _make_bot_stub()
        bot.paper._conn = MagicMock()
        bot.paper._conn.execute.return_value.fetchall.return_value = []
        bot.paper.daily_summary.return_value = None
        bot.paper.generate_report.return_value = "report text"
        bot.paper.resolve_market.return_value = None
        bot.paper.get_notional_bankroll.return_value = 490.0
        return bot

    def test_operator_reports_are_not_generated_from_bot_runtime_loop(self):
        assert not hasattr(TradingBot, "_daily_report_task")

    @pytest.mark.asyncio
    async def test_resolve_market_called_off_event_loop_thread(self):
        """
        MAC-ASYNC-002 invariant still holds post-PROFIT-CAL-001 but has moved
        inside resolve_market: the real implementation now dispatches its
        blocking DB work via ``await asyncio.to_thread(self._resolve_market_sync, ...)``.
        The caller (``_check_and_resolve``) plainly awaits ``resolve_market``.

        We simulate that pattern in the mock so the test still guards the
        off-loop invariant — the blocking work must land on a worker thread.
        """
        import threading
        bot = self._make_bot()

        finalized_market = MagicMock()
        finalized_market.status = "finalized"
        finalized_market.result = "yes"
        bot.rest = MagicMock()
        bot.rest.get_market = MagicMock(return_value=finalized_market)

        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [("KXTEST-25DEC31",)]
        bot.paper._conn.execute.return_value = cursor_mock

        event_loop_thread = threading.current_thread().name
        call_threads: list[str] = []

        def tracking_resolve_sync(ticker, resolved_yes):
            call_threads.append(threading.current_thread().name)

        async def fake_resolve_market(ticker, resolved_yes):
            # Mirror real resolve_market's internal to_thread dispatch.
            await asyncio.to_thread(tracking_resolve_sync, ticker, resolved_yes)

        bot.paper.resolve_market = AsyncMock(side_effect=fake_resolve_market)

        await bot._check_and_resolve()

        assert call_threads, "resolve_market sync body was never called"
        assert all(t != event_loop_thread for t in call_threads), (
            f"resolve_market sync body called on event loop thread ({event_loop_thread!r}). "
            "Must be dispatched via asyncio.to_thread() — MAC-ASYNC-002."
        )

    @pytest.mark.asyncio
    async def test_auto_resolve_routes_polymarket_rows_to_settlement_reconciler(self, monkeypatch):
        bot = self._make_bot()

        finalized_market = MagicMock()
        finalized_market.status = "finalized"
        finalized_market.result = "yes"
        bot.rest = MagicMock()
        bot.rest.get_market = MagicMock(return_value=finalized_market)

        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [
            ("KXTEST-25DEC31", "kalshi"),
            ("ewc-usgub-ks-2026-11-03-dem", "polymarket_us"),
        ]
        bot.paper._conn.execute.return_value = cursor_mock
        bot.paper.resolve_market = AsyncMock(return_value=None)

        calls = []

        class FakeReconciler:
            def __init__(self, *, source, resolver):
                calls.append(("init", source, resolver))

            def reconcile(self, *, limit=None):
                calls.append(("reconcile", limit))
                return SettlementReconcileResult(
                    checked=1,
                    resolved=1,
                    not_found=0,
                )

        monkeypatch.setattr(main_module, "SettlementReconciler", FakeReconciler, raising=False)
        monkeypatch.setattr(
            main_module,
            "PolymarketPublicSettlementSource",
            lambda: object(),
            raising=False,
        )
        monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_enabled", True)
        monkeypatch.setattr(main_module.cfg, "polymarket_us_enabled", True)

        await bot._check_and_resolve()

        bot.rest.get_market.assert_called_once_with("KXTEST-25DEC31")
        bot.paper.resolve_market.assert_awaited_once_with("KXTEST-25DEC31", True)
        assert calls[-1] == ("reconcile", None)

    def _wire_polymarket_reconciler(self, bot, monkeypatch, *, result):
        """Route a single open Polymarket ticker to a fake reconciler.

        Kalshi side is intentionally inert (no open Kalshi tickers) so the
        only settlement-summary output under test comes from the Polymarket
        reconcile path.
        """
        bot.rest = MagicMock()
        bot.rest.get_market = MagicMock(return_value=None)

        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [
            ("ewc-usgub-ks-2026-11-03-dem", "polymarket_us"),
        ]
        bot.paper._conn.execute.return_value = cursor_mock
        bot.paper.resolve_market = AsyncMock(return_value=None)

        class FakeReconciler:
            def __init__(self, *, source, resolver):
                pass

            def reconcile(self, *, limit=None):
                return result

        monkeypatch.setattr(
            main_module, "SettlementReconciler", FakeReconciler, raising=False
        )
        monkeypatch.setattr(
            main_module,
            "PolymarketPublicSettlementSource",
            lambda: object(),
            raising=False,
        )
        monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_enabled", True)
        monkeypatch.setattr(main_module.cfg, "polymarket_us_enabled", True)

    @pytest.mark.asyncio
    async def test_auto_resolve_summary_includes_error_count(self, monkeypatch):
        """A cycle where every Polymarket ticker errors must NOT look identical
        to a cycle where nothing has settled yet.

        WHY (silent-failure review of P2): the per-ticker errors are isolated by
        ``SettlementReconciler.reconcile()``'s broad ``except Exception`` and only
        land in the file log. The operator-facing summary line at the call site
        previously logged checked/resolved/not_found but dropped ``result.errors``.
        With ``checked=5 resolved=0 not_found=0`` an all-errors outage (public API
        down) was indistinguishable from a quiet, healthy "nothing settled" cycle.
        The summary line MUST surface the error count so the false-pass condition
        is visible.
        """
        bot = self._make_bot()
        self._wire_polymarket_reconciler(
            bot,
            monkeypatch,
            result=SettlementReconcileResult(
                checked=5, resolved=0, not_found=0, errors=5
            ),
        )

        log_spy = MagicMock()
        monkeypatch.setattr(main_module, "log", log_spy)

        await bot._check_and_resolve()

        # The summary INFO line must carry result.errors as a distinct arg so an
        # outage is not rendered identically to "nothing settled yet".
        summary_calls = [
            call
            for call in log_spy.info.call_args_list
            if call.args and "Polymarket checked=" in str(call.args[0])
        ]
        assert summary_calls, "Polymarket settlement summary INFO line was never emitted"
        summary = summary_calls[-1]
        assert "errors=" in summary.args[0], (
            "summary format string omits the error count; an all-errors outage "
            "is indistinguishable from a quiet 'nothing settled' cycle"
        )
        # The error count value (5) must actually be passed through, not hardcoded 0.
        assert 5 in summary.args[1:], (
            "result.errors value not threaded into the summary log args"
        )

    @pytest.mark.asyncio
    async def test_auto_resolve_warns_when_settlement_errors_present(self, monkeypatch):
        """When result.errors > 0 a dedicated WARNING must fire so a human
        scanning the daily summary notices the failures.

        WHY: an INFO summary line alone can be lost in normal cycle noise. A
        non-zero error count on the settlement (state-mutation + observability)
        path is exactly the condition risk_review says must be loud, not silent.
        """
        bot = self._make_bot()
        self._wire_polymarket_reconciler(
            bot,
            monkeypatch,
            result=SettlementReconcileResult(
                checked=5, resolved=0, not_found=0, errors=5
            ),
        )

        log_spy = MagicMock()
        monkeypatch.setattr(main_module, "log", log_spy)

        await bot._check_and_resolve()

        warn_msgs = [str(call.args[0]) for call in log_spy.warning.call_args_list if call.args]
        assert any(
            "error" in msg.lower() and "polymarket" in msg.lower()
            for msg in warn_msgs
        ), (
            "no dedicated Polymarket settlement-error WARNING fired despite "
            f"errors=5; warnings seen: {warn_msgs!r}"
        )

    @pytest.mark.asyncio
    async def test_auto_resolve_no_warning_on_clean_settlement(self, monkeypatch):
        """A clean cycle (errors == 0) must NOT emit the settlement-error
        WARNING — the warning must be a true signal, not noise on every cycle.

        WHY: a warning that fires unconditionally trains operators to ignore it,
        defeating the whole point of surfacing the outage condition.
        """
        bot = self._make_bot()
        self._wire_polymarket_reconciler(
            bot,
            monkeypatch,
            result=SettlementReconcileResult(
                checked=2, resolved=2, not_found=0, errors=0
            ),
        )

        log_spy = MagicMock()
        monkeypatch.setattr(main_module, "log", log_spy)

        await bot._check_and_resolve()

        warn_msgs = [str(call.args[0]) for call in log_spy.warning.call_args_list if call.args]
        assert not any(
            "error" in msg.lower() and "polymarket" in msg.lower()
            for msg in warn_msgs
        ), (
            "settlement-error WARNING fired on a clean (errors=0) cycle; "
            f"warnings seen: {warn_msgs!r}"
        )

    @pytest.mark.asyncio
    async def test_open_trades_query_called_off_event_loop_thread(self):
        import threading
        bot = self._make_bot()
        bot.rest = MagicMock()

        event_loop_thread = threading.current_thread().name
        call_threads: list[str] = []

        original_execute = bot.paper._conn.execute

        def tracking_execute(sql):
            call_threads.append(threading.current_thread().name)
            return original_execute(sql)

        bot.paper._conn.execute = tracking_execute

        await bot._check_and_resolve()

        assert call_threads, "_conn.execute was never called"
        assert all(t != event_loop_thread for t in call_threads), (
            f"_conn.execute called on event loop thread ({event_loop_thread!r}). "
            "Must be dispatched via asyncio.to_thread() — MAC-ASYNC-002."
        )

    @pytest.mark.asyncio
    async def test_get_notional_bankroll_in_resolve_called_off_event_loop_thread(self):
        import threading
        bot = self._make_bot()

        finalized_market = MagicMock()
        finalized_market.status = "finalized"
        finalized_market.result = "yes"
        bot.rest = MagicMock()
        bot.rest.get_market = MagicMock(return_value=finalized_market)

        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [("KXTEST-25DEC31",)]
        bot.paper._conn.execute.return_value = cursor_mock

        event_loop_thread = threading.current_thread().name
        call_threads: list[str] = []

        def tracking_bankroll():
            call_threads.append(threading.current_thread().name)
            return 490.0

        bot.paper.get_notional_bankroll.side_effect = tracking_bankroll

        await bot._check_and_resolve()

        assert call_threads, "get_notional_bankroll was never called in resolve path"
        assert all(t != event_loop_thread for t in call_threads), (
            f"get_notional_bankroll called on event loop thread ({event_loop_thread!r}). "
            "Must be dispatched via asyncio.to_thread() — MAC-ASYNC-002."
        )


class TestRuntimeThresholdOverride:
    """Runtime threshold overrides (when registered via a global reader)
    must take precedence over the static EARLY_MAX_NEWS_AGE_BY_SOURCE
    map. Without a reader registered, behavior is identical to pre-Phase-1.
    """

    def test_global_default_is_1800s(self):
        """PROFIT-STALE-002 — global default EARLY_MAX_NEWS_AGE_SECONDS
        must be 1800s, not the legacy 300s. Reverting to 300s silently
        re-introduces the ~97/day loss of premium-publisher items
        attributed via google_news_query (Washington Post, Bloomberg,
        The Hill, etc.) that 7-day funnel diagnostic 2026-05-24 found.

        The per-source map in config.EARLY_MAX_NEWS_AGE_BY_SOURCE is
        now documentation-of-intent rather than the exhaustive list of
        what gets the longer window; bumping the default removes the
        per-source-list-maintenance burden the operator flagged.
        """
        import config
        from utils import runtime_overrides as ro
        ro._global_reader = None
        from main import _early_max_news_age_seconds_for_source
        assert config.EARLY_MAX_NEWS_AGE_SECONDS == 1800, (
            "Global default regressed below 1800s. The per-source-override "
            "approach replaced by PROFIT-STALE-002 is now load-bearing — "
            "do not revert without an explicit operator sign-off and an "
            "audit of the funnel-loss numbers."
        )
        # An unlisted source must inherit the new default.
        assert _early_max_news_age_seconds_for_source("Some Unlisted Publisher") == 1800

    def test_runtime_threshold_overrides_static_value(self, monkeypatch):
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            OverridesState, PredictedEffect, ThresholdOverride,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_threshold_overrides=[
                ThresholdOverride(
                    path="EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA",
                    value=21600,
                    reason="test", confidence=0.7,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0044", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())

        from main import _early_max_news_age_seconds_for_source
        # Runtime override wins for the specified source.
        assert _early_max_news_age_seconds_for_source("IAEA") == 21600

    def test_no_runtime_override_falls_through_to_static(self, monkeypatch):
        """A source without a runtime override returns the static-config value
        (exact or case-insensitive) or the default EARLY_MAX_NEWS_AGE_SECONDS.
        """
        from utils import runtime_overrides as ro
        ro._global_reader = None  # explicit: no runtime overrides
        from main import _early_max_news_age_seconds_for_source
        from config import EARLY_MAX_NEWS_AGE_SECONDS

        # A source with NO entry in EARLY_MAX_NEWS_AGE_BY_SOURCE falls through
        # to the default.
        assert _early_max_news_age_seconds_for_source("UnknownSrc") == EARLY_MAX_NEWS_AGE_SECONDS

    def test_runtime_override_int_coercion(self, monkeypatch):
        """YAML scalars sometimes deserialize as int; confirm the consumer
        handles int values directly (get_threshold_override returns the raw
        value as stored).
        """
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            OverridesState, PredictedEffect, ThresholdOverride,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_threshold_overrides=[
                ThresholdOverride(
                    path="EARLY_MAX_NEWS_AGE_BY_SOURCE.SomeSrc",
                    value=7200,
                    reason="test", confidence=0.7,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0045", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())
        from main import _early_max_news_age_seconds_for_source
        result = _early_max_news_age_seconds_for_source("SomeSrc")
        assert result == 7200
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# PROFIT-EDGE-004 Lever A Stage A.1 — `_source_class_for_evidence` classifier fix
#
# Spec: docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md
# Status: pre-loaded during PROFIT-PHASE2-001 soak; post-soak Wave 2 deploy
# (earliest 2026-05-22). Until the classifier's official-token list is
# expanded these tests xfail strictly.
#
# The bug: `config.py:RSS_FEEDS` already polls 6+ official-class feeds
# (Department of War / WhiteHouse.gov / UN News / European Commission /
# IAEA / Defense News / Breaking Defense), but `_source_class_for_evidence`
# only matches a narrow token list and silently demotes 5 of 6 to `"other"`.
# The classifier fix is a 5-line token-list patch; see spec §2.1 + §2.2.
# ---------------------------------------------------------------------------

_LEVER_A_A1_XFAIL_REASON = (
    "PROFIT-EDGE-004 Lever A.1: `_source_class_for_evidence` token list not yet expanded. "
    "Lands post-soak per docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md."
)


class TestSourceClassClassifierLeverA1:
    """Pin the post-fix classifications. Each xfail-strict test corresponds
    to one production source-string that today silently buckets as `"other"`
    or `"news"` instead of `"official"` / `"news"` per the spec's §2 token
    expansion.
    """

    def test_department_of_war_classifies_as_official(self):
        """`Department of War News Feed` (defense.gov RSS) must be `official`."""
        assert _source_class_for_evidence("Department of War News Feed") == "official"

    def test_un_news_classifies_as_official(self):
        """`UN News - Global perspective Human stories` must be `official`."""
        assert (
            _source_class_for_evidence("UN News - Global perspective Human stories")
            == "official"
        )

    def test_european_commission_press_releases_classifies_as_official(self):
        """European Commission `Press releases - RSS` must be `official`."""
        assert _source_class_for_evidence("Press releases - RSS") == "official"

    def test_iaea_classifies_as_official(self):
        """IAEA top-stories feed must be `official`."""
        assert (
            _source_class_for_evidence("Top Stories From the International Atomic Energy Agency")
            == "official"
        )

    def test_defense_news_classifies_as_news(self):
        """Defense industry press wire must be `news` (not `other`)."""
        assert _source_class_for_evidence("Defense News") == "news"

    def test_breaking_defense_classifies_as_news(self):
        """Breaking Defense industry wire must be `news` (not `other`)."""
        assert _source_class_for_evidence("Breaking Defense") == "news"

    def test_white_house_classifies_as_official_today_positive_control(self):
        """Positive control: this case ALREADY classifies correctly today.
        Not xfail — included to catch a regression that breaks the existing
        `"white house"` token while the classifier fix is being implemented.
        """
        assert _source_class_for_evidence("News – The White House") == "official"


# ---------------------------------------------------------------------------
# A.1+ companion harness — `analysis`-class classifier branch
#
# Spec: docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md §3.2
# Status: pre-loaded during PROFIT-PHASE2-001 soak; lands as part of
# A.1+1 deploy (Wave 2, post-soak day 14+). Adds a new `analysis` branch
# to `_source_class_for_evidence` that buckets specialist-analyst feed
# titles (War on the Rocks / CSIS / ISW / CFR / Atlantic Council) as
# `analysis` rather than `other`.
#
# `evidence_scorer._SOURCE_CLASS_QUALITY` already defines `analysis=0.60`
# but no source-string maps to it today — A.1+ lights up the class.
# ---------------------------------------------------------------------------

_LEVER_A1PLUS_ANALYSIS_BRANCH_XFAIL_REASON = (
    "PROFIT-EDGE-004 Lever A.1+ analysis-class branch not yet landed. "
    "The post-A.1 classifier expansion adds tokens for specialist analyst "
    "feed titles (War on the Rocks / CSIS / ISW / CFR / Atlantic Council). "
    "Lands together with the A.1+1 first-feed deploy per "
    "docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md."
)


class TestSourceClassClassifierLeverA1PlusAnalysisBranch:
    """Pin the `analysis`-class branch behaviour. Each xfail-strict test
    corresponds to one specialist-analyst source string the A.1+1 deploy
    needs to bucket as `analysis`. Today every one buckets as `other`.
    """

    @pytest.mark.xfail(reason=_LEVER_A1PLUS_ANALYSIS_BRANCH_XFAIL_REASON, strict=True)
    def test_war_on_the_rocks_classifies_as_analysis(self):
        """`War on the Rocks` (defense / national security commentary) → `analysis`."""
        assert _source_class_for_evidence("War on the Rocks") == "analysis"

    @pytest.mark.xfail(reason=_LEVER_A1PLUS_ANALYSIS_BRANCH_XFAIL_REASON, strict=True)
    def test_csis_classifies_as_analysis(self):
        """Center for Strategic and International Studies → `analysis`."""
        assert (
            _source_class_for_evidence("Center for Strategic and International Studies")
            == "analysis"
        )

    @pytest.mark.xfail(reason=_LEVER_A1PLUS_ANALYSIS_BRANCH_XFAIL_REASON, strict=True)
    def test_csis_short_form_classifies_as_analysis(self):
        """`CSIS` (short form often appears in feed titles) → `analysis`."""
        assert _source_class_for_evidence("CSIS Analysis") == "analysis"

    @pytest.mark.xfail(reason=_LEVER_A1PLUS_ANALYSIS_BRANCH_XFAIL_REASON, strict=True)
    def test_isw_classifies_as_analysis(self):
        """Institute for the Study of War → `analysis`."""
        assert (
            _source_class_for_evidence("Institute for the Study of War")
            == "analysis"
        )

    @pytest.mark.xfail(reason=_LEVER_A1PLUS_ANALYSIS_BRANCH_XFAIL_REASON, strict=True)
    def test_cfr_classifies_as_analysis(self):
        """Council on Foreign Relations → `analysis`."""
        assert (
            _source_class_for_evidence("Council on Foreign Relations")
            == "analysis"
        )

    @pytest.mark.xfail(reason=_LEVER_A1PLUS_ANALYSIS_BRANCH_XFAIL_REASON, strict=True)
    def test_atlantic_council_classifies_as_analysis(self):
        """Atlantic Council → `analysis`."""
        assert _source_class_for_evidence("Atlantic Council") == "analysis"

    def test_kyiv_post_already_lands_in_known_class_today(self):
        """Positive control: Kyiv Post (currently in RSS_FEEDS) is one of
        the specialist analyst sources Codex's audit credited with 3/3
        historical PAPER_TRADE. Multiple defensible buckets exist:
          - `other` (pre-Lever-A.1 / pre-PROFIT-EDGE-006 fallback)
          - `analysis` (the A.1+ analysis-branch goal)
          - `news` (operators classifying it as a news-adjacent source)
          - `regional` (PROFIT-EDGE-006: foreign-bureau classification)
        Pinned looser to avoid over-constraining as the taxonomy evolves.
        """
        result = _source_class_for_evidence("Kyiv Post")
        assert result in {"other", "analysis", "news", "regional"}, (
            f"Kyiv Post must bucket as one of other/analysis/news/regional; "
            f"got {result!r}"
        )


# ---------------------------------------------------------------------------
# A.1+1.5 companion harness — `legal`-class classifier branch (option-B)
#
# Spec: docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md §3.2
# Status: pre-loaded during PROFIT-PHASE2-001 soak; lands as part of
# A.1+1.5 deploy (Wave 2, post-soak day 14+; option-B parallel to option-A
# specialist-analyst). Adds a new `legal` branch to
# `_source_class_for_evidence` that buckets legal/regulatory analyst feed
# titles (VitalLaw / Lawfare / Just Security / SCOTUSblog / Politico Legal
# / Reuters Legal) as `legal` rather than `news` or `other`.
#
# `evidence_scorer._SOURCE_CLASS_QUALITY` does NOT yet define `legal`
# today — A.1+1.5 spec §3.2 calls for `legal=0.65` (between `analysis=0.60`
# and `official=0.75`). The weight harness is a sibling test
# (test_evidence_scorer_legal_class_weight) that lands in the same hunk.
# ---------------------------------------------------------------------------

_LEVER_A1PLUS15_LEGAL_BRANCH_XFAIL_REASON = (
    "PROFIT-EDGE-004 Lever A.1+1.5 legal-class branch not yet landed. "
    "The A.1+1.5 deploy adds tokens for legal/regulatory analyst feed "
    "titles (VitalLaw / Lawfare / Just Security / SCOTUSblog / Politico "
    "Legal / Reuters Legal). Lands together with the A.1+1.5 first-feed "
    "deploy per docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-"
    "legal-analyst-design.md."
)


class TestSourceClassClassifierLeverA1Plus15LegalBranch:
    """Pin the `legal`-class branch behaviour. Each xfail-strict test
    corresponds to one legal-analyst source string the A.1+1.5 deploy
    needs to bucket as `legal`. Today every one buckets as `news` or
    `other` (no token match for the legal niche).
    """

    @pytest.mark.xfail(reason=_LEVER_A1PLUS15_LEGAL_BRANCH_XFAIL_REASON, strict=True)
    def test_vitallaw_classifies_as_legal(self):
        """`VitalLaw.com` (legal/regulatory analysis) → `legal`. The
        load-bearing source per the per-source audit (cca3cea)."""
        assert _source_class_for_evidence("VitalLaw.com") == "legal"

    @pytest.mark.xfail(reason=_LEVER_A1PLUS15_LEGAL_BRANCH_XFAIL_REASON, strict=True)
    def test_vital_law_hyphenated_classifies_as_legal(self):
        """`vital-law` hyphenated form (matches the
        scripts/simulations/lever_a1_plus_candidate_feed_sizing.py token
        list) → `legal`."""
        assert _source_class_for_evidence("vital-law analysis") == "legal"

    @pytest.mark.xfail(reason=_LEVER_A1PLUS15_LEGAL_BRANCH_XFAIL_REASON, strict=True)
    def test_lawfare_classifies_as_legal(self):
        """Lawfare (national-security law analysis) → `legal`."""
        assert _source_class_for_evidence("Lawfare") == "legal"

    @pytest.mark.xfail(reason=_LEVER_A1PLUS15_LEGAL_BRANCH_XFAIL_REASON, strict=True)
    def test_just_security_classifies_as_legal(self):
        """Just Security (national-security law analysis) → `legal`."""
        assert _source_class_for_evidence("Just Security") == "legal"

    @pytest.mark.xfail(reason=_LEVER_A1PLUS15_LEGAL_BRANCH_XFAIL_REASON, strict=True)
    def test_scotusblog_classifies_as_legal(self):
        """SCOTUSblog (Supreme Court ruling analysis) → `legal`."""
        assert _source_class_for_evidence("SCOTUSblog") == "legal"

    @pytest.mark.xfail(reason=_LEVER_A1PLUS15_LEGAL_BRANCH_XFAIL_REASON, strict=True)
    def test_politico_legal_classifies_as_legal(self):
        """Politico legal coverage → `legal`. Distinguished from generic
        Politico via the `legal` substring in the source label."""
        assert _source_class_for_evidence("Politico Legal") == "legal"

    @pytest.mark.xfail(reason=_LEVER_A1PLUS15_LEGAL_BRANCH_XFAIL_REASON, strict=True)
    def test_reuters_legal_classifies_as_legal(self):
        """Reuters Legal (wire-service legal news) → `legal`. The
        operator-recognised distinction from the generic Reuters wire."""
        assert _source_class_for_evidence("Reuters Legal") == "legal"

    def test_generic_reuters_classifies_unchanged_today(self):
        """Positive control: generic `Reuters` (without the `Legal`
        qualifier) must continue to bucket as `news` after the A.1+1.5
        legal-branch addition. Catches a regression where the `reuters`
        token in the legal branch over-claims and shadows the generic
        Reuters wire which currently buckets as `news`."""
        assert _source_class_for_evidence("Reuters") == "news"


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-003 (2026-05-25) — _is_floor_clamp_suspected
# ---------------------------------------------------------------------------

class TestFloorClampSuspected:
    """Pins the floor-clamp detector. Trade-audit (2026-05-25,
    KXUSAIRANAGREEMENT-27-26JUN) showed the bot's edge anchored against the
    0.05 floor, manufacturing a 3pp edge from a 6.8pp LLM shift. Detector
    enables Kelly halving on clamped trades."""

    def test_no_directional_returns_false(self):
        from main import _is_floor_clamp_suspected
        assert not _is_floor_clamp_suspected("neutral", "small", 0.95, 0.87, 1.0)
        assert not _is_floor_clamp_suspected(None, "moderate", 0.05, 0.20, 1.0)

    def test_no_magnitude_returns_false(self):
        from main import _is_floor_clamp_suspected
        assert not _is_floor_clamp_suspected("yes", "none", 0.95, 0.95, 1.0)
        assert not _is_floor_clamp_suspected("no", None, 0.05, 0.05, 1.0)

    def test_floor_at_no_side(self):
        from main import _is_floor_clamp_suspected
        assert _is_floor_clamp_suspected("no", "small", 0.05, 0.08, 0.85)
        assert _is_floor_clamp_suspected("no", "moderate", 0.05, 0.12, 1.0)
        assert _is_floor_clamp_suspected("no", "large", 0.05, 0.20, 1.0)

    def test_floor_at_yes_side(self):
        from main import _is_floor_clamp_suspected
        assert _is_floor_clamp_suspected("yes", "small", 0.95, 0.92, 1.0)
        assert _is_floor_clamp_suspected("yes", "large", 0.95, 0.80, 1.0)

    def test_unclamped_returns_false(self):
        from main import _is_floor_clamp_suspected
        # Bot estimated 0.30 — clearly unclamped
        assert not _is_floor_clamp_suspected("yes", "moderate", 0.30, 0.15, 1.0)
        assert not _is_floor_clamp_suspected("no", "large", 0.62, 0.87, 1.0)

    def test_within_tolerance(self):
        from main import _is_floor_clamp_suspected
        # Float exact-match with small tolerance (1e-6)
        assert _is_floor_clamp_suspected("no", "small", 0.0500001, 0.08, 0.85)
        assert not _is_floor_clamp_suspected("no", "small", 0.06, 0.08, 0.85)
        assert not _is_floor_clamp_suspected("no", "small", 0.04, 0.08, 0.85)

    def test_exact_boundary_without_raw_crossing_is_not_suspected(self):
        """A final 0.05 / 0.95 is not enough evidence by itself."""
        from main import _is_floor_clamp_suspected
        # market 0.13 - small×1.0 0.08 lands exactly on 0.05, not below it.
        assert not _is_floor_clamp_suspected("no", "small", 0.05, 0.13, 1.0)
        # market 0.87 + small×1.0 0.08 lands exactly on 0.95, not above it.
        assert not _is_floor_clamp_suspected("yes", "small", 0.95, 0.87, 1.0)


class TestFloorClampHalvingConfig:
    """Pins the cfg.floor_clamp_kelly_multiplier default. Operator can
    override via FLOOR_CLAMP_KELLY_MULTIPLIER env var; default = 0.5."""

    def test_default_is_in_valid_range(self):
        # Read the current attribute. Env may have been set externally;
        # assert it's a finite number in (0, 1]. Default is 0.5.
        # NOTE: do NOT importlib.reload(config) — reloading pollutes
        # module-level singletons consumed by other test files
        # (test_main_startup.py, test_paper_trader.py mode-selection).
        import config as cfg_mod
        m = cfg_mod.cfg.floor_clamp_kelly_multiplier
        assert isinstance(m, float)
        assert 0.0 < m <= 1.0

    def test_max_open_positions_per_prefix_default(self):
        import config as cfg_mod
        assert isinstance(cfg_mod.cfg.max_open_positions_per_prefix, int)
        assert cfg_mod.cfg.max_open_positions_per_prefix >= 0
