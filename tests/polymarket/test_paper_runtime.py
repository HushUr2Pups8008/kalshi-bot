from __future__ import annotations

import asyncio
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import pytest

from analysis import SignalAnalysis
from config import PAPER_MAX_CANDIDATES, cfg
from feeds import NewsItem
import polymarket.paper_runtime as paper_runtime
from polymarket.models import PolymarketMarket
from polymarket.public_client import PolymarketMarketPage
from polymarket.paper_runtime import (
    PolymarketPaperRuntime,
    _market_match_text,
    _horizon_shadow_market_sets,
    match_polymarket_markets,
    polymarket_paper_runtime_disabled_reason,
)
from trading.venue import Venue
from trading.fees import INITIAL_ORDER_FEE_ACCUMULATOR


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
        "close_time": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "is_binary": True,
    }
    values.update(overrides)
    return PolymarketMarket(**values)


def test_counterfactual_snapshot_identity_sanitizers_reject_untrusted_values():
    class HostileTitle(str):
        def __iter__(self):
            raise AssertionError("counterfactual title must not iterate a string subclass")

    class HostileTicker(str):
        def __lt__(self, other):
            raise AssertionError("counterfactual ticker must not reach sorting")

    assert paper_runtime._counterfactual_title("Visible title") == "Visible title"
    assert paper_runtime._counterfactual_title("Visible\u202etitle") == "Visibletitle"
    assert paper_runtime._counterfactual_title("x" * 513) is None
    assert paper_runtime._counterfactual_title(None) is None
    assert paper_runtime._counterfactual_title(HostileTitle("hostile")) is None
    assert paper_runtime._counterfactual_ticker("VALID-TICKER") == "VALID-TICKER"
    assert paper_runtime._counterfactual_ticker("not valid") is None
    assert paper_runtime._counterfactual_ticker("x" * 129) is None
    assert paper_runtime._counterfactual_ticker(123) is None
    assert paper_runtime._counterfactual_ticker(HostileTicker("hostile")) is None


def test_market_match_text_includes_polymarket_public_context_fields():
    market = _market(
        title="Will an agreement be signed?",
        question="Will a ceasefire agreement be signed?",
        description="Market resolves according to the official mediator statement.",
        event_title="Middle East diplomacy",
        series_title="International relations",
        tags=("diplomacy", "iran"),
        public_comments=("Mediator statement is the key source.",),
        resolution_source="https://example.com/resolution",
    )

    text = _market_match_text(market)

    assert "official mediator statement" in text
    assert "Middle East diplomacy" in text
    assert "International relations" in text
    assert "diplomacy" in text
    assert "Mediator statement is the key source." in text
    assert "https://example.com/resolution" in text


def test_horizon_shadow_market_sets_preserves_existing_partition_boundaries():
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    markets = [
        _market(
            market_id="prod-14d",
            close_time=(now + timedelta(days=14)).isoformat(),
        ),
        _market(
            market_id="shadow-over-14d",
            close_time=(now + timedelta(days=14, seconds=1)).isoformat(),
        ),
        _market(
            market_id="shadow-30d",
            close_time=(now + timedelta(days=30)).isoformat(),
        ),
        _market(
            market_id="too-far",
            close_time=(now + timedelta(days=30, seconds=1)).isoformat(),
        ),
        _market(
            market_id="closed-market",
            close_time=(now + timedelta(days=20)).isoformat(),
            status="closed",
        ),
        _market(
            market_id="suppressed-market",
            close_time=(now + timedelta(days=20)).isoformat(),
            category="sports",
            resolution_source="https://example.test/resolution",
        ),
    ]

    production, shadow = _horizon_shadow_market_sets(
        markets,
        now=now,
        production_horizon_days=14.0,
        shadow_horizon_end_days=30.0,
    )

    assert [market.market_id for market in production] == ["prod-14d"]
    assert [market.market_id for market in shadow] == [
        "shadow-over-14d",
        "shadow-30d",
    ]


class _FakeClient:
    def __init__(self, markets, cursor=None):
        self.markets = markets
        self.cursor = cursor
        self.calls = 0
        self.requests = []

    def get_market_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> PolymarketMarketPage:
        self.calls += 1
        self.requests.append({"limit": limit, "cursor": cursor, "offset": offset})
        start = offset or 0
        markets = self.markets[start : start + limit]
        return PolymarketMarketPage(
            markets=markets,
            cursor=self.cursor,
            raw_count=len(markets),
        )


class _CursorPagingClient:
    def __init__(self, first_page, second_page):
        self.first_page = first_page
        self.second_page = second_page
        self.requests = []

    def get_market_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> PolymarketMarketPage:
        self.requests.append({"limit": limit, "cursor": cursor, "offset": offset})
        if cursor is None:
            return PolymarketMarketPage(
                markets=self.first_page,
                cursor="next-page",
                raw_count=len(self.first_page),
            )
        assert cursor == "next-page"
        return PolymarketMarketPage(
            markets=self.second_page,
            cursor=None,
            raw_count=len(self.second_page),
        )


class _NormalizationGapPagingClient:
    def __init__(self, first, second):
        self.first = first
        self.second = second
        self.requests = []

    def get_market_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        offset: int | None = None,
    ):
        self.requests.append({"limit": limit, "cursor": cursor, "offset": offset})
        if offset is None:
            return SimpleNamespace(markets=[self.first], cursor=None, raw_count=2)
        assert offset == 2
        return SimpleNamespace(markets=[self.second], cursor=None, raw_count=1)


class _LegacyClient:
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


@pytest.fixture(autouse=True)
def _empty_runtime_match_weights(monkeypatch):
    monkeypatch.setattr("polymarket.paper_runtime._load_match_weights", lambda: {})


def _configure_legacy_paper_horizon(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "legacy")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "legacy")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)


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
async def test_warm_cache_pages_offsets_deduplicates_and_reports_coverage(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    first = _market(market_id="first")
    second = _market(market_id="second")
    third = _market(market_id="third")
    fourth = _market(market_id="fourth")
    client = _FakeClient([first, second, second, third, fourth])
    runtime = PolymarketPaperRuntime(
        client=client,
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=2,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        warmed = await runtime.warm_cache()

    assert warmed == 4
    assert runtime.cached_markets() == [first, second, third, fourth]
    assert client.requests == [
        {"limit": 2, "cursor": None, "offset": None},
        {"limit": 2, "cursor": None, "offset": 2},
        {"limit": 2, "cursor": None, "offset": 4},
    ]
    write_log_mock.assert_awaited_once_with(
        trade_log_mock.log_polymarket_market_cache,
        raw_fetched=5,
        raw_unique=4,
        pages_fetched=3,
        cursor_present=False,
        pagination_exhausted=True,
        pagination_stop_reason="short_page",
        eligible_30d=4,
        candidate_within_admission_horizon=4,
        admission_horizon_days=30.0,
        market_limit=2,
    )


@pytest.mark.asyncio
async def test_warm_cache_follows_cursor_before_offset(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    first = _market(market_id="first")
    second = _market(market_id="second")
    third = _market(market_id="third")
    client = _CursorPagingClient([first, second], [third])
    runtime = PolymarketPaperRuntime(
        client=client,
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=2,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        warmed = await runtime.warm_cache()

    assert warmed == 3
    assert client.requests == [
        {"limit": 2, "cursor": None, "offset": None},
        {"limit": 2, "cursor": "next-page", "offset": None},
    ]
    write_log_mock.assert_awaited_once_with(
        trade_log_mock.log_polymarket_market_cache,
        raw_fetched=3,
        raw_unique=3,
        pages_fetched=2,
        cursor_present=True,
        pagination_exhausted=True,
        pagination_stop_reason="cursor_end",
        eligible_30d=3,
        candidate_within_admission_horizon=3,
        admission_horizon_days=30.0,
        market_limit=2,
    )


@pytest.mark.asyncio
async def test_warm_cache_uses_raw_page_count_after_normalization_drops(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    first = _market(market_id="first")
    second = _market(market_id="second")
    client = _NormalizationGapPagingClient(first, second)
    runtime = PolymarketPaperRuntime(
        client=client,
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=2,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        warmed = await runtime.warm_cache()

    assert warmed == 2
    assert client.requests == [
        {"limit": 2, "cursor": None, "offset": None},
        {"limit": 2, "cursor": None, "offset": 2},
    ]
    write_log_mock.assert_awaited_once_with(
        trade_log_mock.log_polymarket_market_cache,
        raw_fetched=3,
        raw_unique=2,
        pages_fetched=2,
        cursor_present=False,
        pagination_exhausted=True,
        pagination_stop_reason="short_page",
        eligible_30d=2,
        candidate_within_admission_horizon=2,
        admission_horizon_days=30.0,
        market_limit=2,
    )


@pytest.mark.asyncio
async def test_warm_cache_keeps_legacy_client_single_page_contract(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    first = _market(market_id="first")
    second = _market(market_id="second")
    third = _market(market_id="third")
    client = _LegacyClient([first, second, third])
    runtime = PolymarketPaperRuntime(
        client=client,
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=2,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        warmed = await runtime.warm_cache()

    assert warmed == 2
    assert client.calls == 1
    write_log_mock.assert_awaited_once_with(
        trade_log_mock.log_polymarket_market_cache,
        raw_fetched=2,
        raw_unique=2,
        pages_fetched=1,
        cursor_present=False,
        pagination_exhausted=False,
        pagination_stop_reason="legacy_client_no_page_metadata",
        eligible_30d=2,
        candidate_within_admission_horizon=2,
        admission_horizon_days=30.0,
        market_limit=2,
    )


@pytest.mark.asyncio
async def test_warm_cache_stops_at_bounded_page_cap(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    monkeypatch.setattr(paper_runtime, "_MAX_MARKET_FETCH_PAGES", 2)
    markets = [_market(market_id=f"market-{index}") for index in range(5)]
    client = _FakeClient(markets)
    runtime = PolymarketPaperRuntime(
        client=client,
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=2,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        warmed = await runtime.warm_cache()

    assert warmed == 4
    assert client.requests == [
        {"limit": 2, "cursor": None, "offset": None},
        {"limit": 2, "cursor": None, "offset": 2},
    ]
    write_log_mock.assert_awaited_once_with(
        trade_log_mock.log_polymarket_market_cache,
        raw_fetched=4,
        raw_unique=4,
        pages_fetched=2,
        cursor_present=False,
        pagination_exhausted=False,
        pagination_stop_reason="page_cap",
        eligible_30d=4,
        candidate_within_admission_horizon=4,
        admission_horizon_days=30.0,
        market_limit=2,
    )


@pytest.mark.asyncio
async def test_warm_cache_rejects_missing_and_beyond_universe_horizon_close_times():
    near = _market(market_id="near")
    missing = _market(market_id="missing", close_time="")
    far = _market(
        market_id="far",
        close_time=(datetime.now(timezone.utc) + timedelta(days=31)).isoformat(),
    )
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([near, missing, far]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    warmed = await runtime.warm_cache()

    assert warmed == 1
    assert runtime.cached_markets() == [near]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cohort_id", "cohort_kind"),
    (("active-test", "active"), ("pending-test", "legacy_pending")),
)
async def test_nonlegacy_cache_refresh_reports_14_day_admission_horizon(
    monkeypatch,
    cohort_id,
    cohort_kind,
):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", cohort_id)
    monkeypatch.setattr(cfg, "paper_cohort_kind", cohort_kind)
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    candidate = _market(market_id="candidate")
    eligible_only = _market(
        market_id="eligible-only",
        close_time=(datetime.now(timezone.utc) + timedelta(days=20)).isoformat(),
    )
    beyond_30d = _market(
        market_id="beyond-30d",
        close_time=(datetime.now(timezone.utc) + timedelta(days=31)).isoformat(),
    )
    client = _FakeClient([candidate, eligible_only, beyond_30d])
    runtime = PolymarketPaperRuntime(
        client=client,
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        warmed = await runtime.warm_cache()

    assert warmed == 2
    assert runtime.cached_markets() == [candidate, eligible_only]
    assert client.calls == 1
    write_log_mock.assert_awaited_once_with(
        trade_log_mock.log_polymarket_market_cache,
        raw_fetched=3,
        raw_unique=3,
        pages_fetched=1,
        cursor_present=False,
        pagination_exhausted=True,
        pagination_stop_reason="short_page",
        eligible_30d=2,
        candidate_within_admission_horizon=1,
        admission_horizon_days=14.0,
        market_limit=10,
    )


@pytest.mark.asyncio
async def test_legacy_cache_refresh_reports_30_day_admission_horizon(monkeypatch):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "legacy")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "legacy")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    candidate = _market(market_id="candidate")
    legacy_candidate = _market(
        market_id="legacy-candidate",
        close_time=(datetime.now(timezone.utc) + timedelta(days=20)).isoformat(),
    )
    beyond_30d = _market(
        market_id="beyond-30d",
        close_time=(datetime.now(timezone.utc) + timedelta(days=31)).isoformat(),
    )
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([candidate, legacy_candidate, beyond_30d]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        warmed = await runtime.warm_cache()

    assert warmed == 2
    write_log_mock.assert_awaited_once_with(
        trade_log_mock.log_polymarket_market_cache,
        raw_fetched=3,
        raw_unique=3,
        pages_fetched=1,
        cursor_present=False,
        pagination_exhausted=True,
        pagination_stop_reason="short_page",
        eligible_30d=2,
        candidate_within_admission_horizon=2,
        admission_horizon_days=30.0,
        market_limit=10,
    )


@pytest.mark.asyncio
async def test_legacy_process_news_logs_30_day_post_admission_stage(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [
                _market(
                    title="Will unrelated bill pass?",
                    question="Will unrelated bill pass?",
                    close_time=(now + timedelta(days=20)).isoformat(),
                )
            ]
        ),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )
    news = _news("Central bank releases an interest rate decision")

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    assert write_log_mock.await_count == 2
    fields = write_log_mock.await_args_list[-1].kwargs
    assert fields["candidate_pool_stage"] == "post_admission_no_match"
    assert fields["post_admission_no_token_overlap_count"] == 1
    assert fields["post_admission_below_min_post_weight_score_count"] == 0
    assert fields["post_admission_counterfactual_shadow"]["match_clock_utc"] == (
        now.isoformat()
    )


@pytest.mark.asyncio
async def test_process_news_records_15_to_30_day_horizon_shadow_without_routing(
    monkeypatch,
):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "pending-test")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "legacy_pending")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    production_market = _market(
        market_id="production-unrelated",
        title="Will unrelated bill pass?",
        question="Will unrelated bill pass?",
        close_time=(now + timedelta(days=7)).isoformat(),
    )
    shadow_market = _market(
        market_id="shadow-example-event",
        title="Will example event get more likely?",
        question="Will example event get more likely?",
        close_time=(now + timedelta(days=20)).isoformat(),
    )
    route_analysis = AsyncMock()
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([production_market, shadow_market]),
        route_analysis=route_analysis,
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(_news())

    assert routed_count == 0
    route_analysis.assert_not_awaited()
    horizon_calls = [
        call
        for call in write_log_mock.await_args_list
        if call.args[0] is trade_log_mock.log_polymarket_horizon_shadow
    ]
    assert len(horizon_calls) == 1
    horizon_fields = horizon_calls[0].kwargs
    assert horizon_fields == {
        "source": "Example Wire",
        "headline": "Example event gets more likely",
        "venue": Venue.POLYMARKET_US.value,
        "production_horizon_days": 14.0,
        "shadow_horizon_start_days": 14.0,
        "shadow_horizon_end_days": 30.0,
        "production_candidate_count": 1,
        "shadow_candidate_count": 1,
        "production_qualifying_match_count": 0,
        "shadow_qualifying_match_count": 1,
        "production_no_token_overlap_count": 1,
        "production_below_min_post_weight_score_count": 0,
        "production_weight_demoted_below_min_score_count": 0,
        "production_min_match_score": 0.08,
        "shadow_no_token_overlap_count": 0,
        "shadow_below_min_post_weight_score_count": 0,
        "shadow_weight_demoted_below_min_score_count": 0,
        "shadow_min_match_score": 0.08,
        "shadow_analysis_status": "not_evaluated_shadow_only",
        "production_counterfactual_shadow": horizon_fields[
            "production_counterfactual_shadow"
        ],
    }
    assert horizon_fields["production_counterfactual_shadow"] == {
        "schema_version": 1,
        "match_clock_utc": now.isoformat(),
        "news_headline_token_count": 3,
        "news_match_token_count": 3,
        "candidate_count_total": 1,
        "captured_market_count": 1,
        "omitted_market_count": 0,
        "truncated": False,
        "candidates": [
            {
                "ticker": "production-unrelated",
                "rejection_reason": "no_token_overlap",
                "market_token_count": 4,
                "matched_token_count": 0,
                "market_title": "Will unrelated bill pass?",
            }
        ],
    }


@pytest.mark.asyncio
async def test_process_news_skips_horizon_shadow_for_tokenless_input(monkeypatch):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "pending-test")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "legacy_pending")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [
                _market(close_time=(now + timedelta(days=7)).isoformat()),
                _market(
                    market_id="shadow-market",
                    close_time=(now + timedelta(days=20)).isoformat(),
                ),
            ]
        ),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(_news("and the or"))

    assert routed_count == 0
    assert [
        call
        for call in write_log_mock.await_args_list
        if call.args[0] is trade_log_mock.log_polymarket_horizon_shadow
    ] == []


@pytest.mark.asyncio
async def test_process_news_reuses_one_match_weight_snapshot_for_horizon_shadow(
    monkeypatch,
):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "pending-test")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "legacy_pending")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    production_market = _market(
        market_id="production-unrelated",
        title="Will unrelated bill pass?",
        question="Will unrelated bill pass?",
        close_time=(now + timedelta(days=7)).isoformat(),
    )
    shadow_market = _market(
        market_id="shadow-example-event",
        title="Will example event get more likely?",
        question="Will example event get more likely?",
        close_time=(now + timedelta(days=20)).isoformat(),
    )
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([production_market, shadow_market]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )
    weights: dict[str, dict[str, float]] = {}

    with (
        patch(
            "polymarket.paper_runtime._load_match_weights",
            return_value=weights,
        ) as load_weights_mock,
        patch(
            "polymarket.paper_runtime._match_polymarket_markets_with_rejection_telemetry",
            wraps=paper_runtime._match_polymarket_markets_with_rejection_telemetry,
        ) as match_mock,
        patch("polymarket.paper_runtime.trade_log"),
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ),
    ):
        routed_count = await runtime.process_news(_news())

    assert routed_count == 0
    load_weights_mock.assert_called_once_with()
    assert match_mock.call_count == 2
    assert all(
        call.kwargs["token_weights"] is weights
        for call in match_mock.call_args_list
    )


@pytest.mark.asyncio
async def test_matched_process_news_reuses_weight_snapshot_after_routing(
    monkeypatch,
):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "pending-test")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "legacy_pending")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)

    async def estimate_probability(*_args, **_kwargs):
        return 0.65, 0.8, ["example"], "reason", "yes", "moderate", 0.8

    route_analysis = AsyncMock(return_value=SimpleNamespace(enqueued=True))
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [
                _market(close_time=(now + timedelta(days=7)).isoformat()),
                _market(
                    market_id="shadow-unrelated",
                    title="Will unrelated bill pass?",
                    question="Will unrelated bill pass?",
                    close_time=(now + timedelta(days=20)).isoformat(),
                ),
            ]
        ),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )
    initial_weights: dict[str, dict[str, float]] = {}
    rewritten_weights: dict[str, dict[str, float]] = {
        "polymarket_us:shadow-unrelated:unrelated": {"weight": 0.1}
    }

    with (
        patch(
            "polymarket.paper_runtime._load_match_weights",
            side_effect=[initial_weights, rewritten_weights],
        ) as load_weights_mock,
        patch(
            "polymarket.paper_runtime._match_polymarket_markets_with_rejection_telemetry",
            wraps=paper_runtime._match_polymarket_markets_with_rejection_telemetry,
        ) as match_mock,
        patch("polymarket.paper_runtime.trade_log"),
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ),
    ):
        routed_count = await runtime.process_news(_news())

    assert routed_count == 1
    route_analysis.assert_awaited_once()
    load_weights_mock.assert_called_once_with()
    assert match_mock.call_count == 2
    assert all(
        call.kwargs["token_weights"] is initial_weights
        for call in match_mock.call_args_list
    )


@pytest.mark.asyncio
async def test_horizon_shadow_compute_failure_does_not_replay_routed_analysis(
    monkeypatch,
):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "pending-test")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "legacy_pending")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)

    async def estimate_probability(*_args, **_kwargs):
        return 0.65, 0.8, ["example"], "reason", "yes", "moderate", 0.8

    route_analysis = AsyncMock(return_value=SimpleNamespace(enqueued=True))
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [_market(close_time=(now + timedelta(days=7)).isoformat())]
        ),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )

    with (
        patch(
            "polymarket.paper_runtime._horizon_shadow_market_sets",
            side_effect=RuntimeError("shadow telemetry failure"),
        ) as market_sets_mock,
        patch("polymarket.paper_runtime.trade_log"),
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ),
    ):
        routed_count = await runtime.process_news(_news())

    assert routed_count == 1
    route_analysis.assert_awaited_once()
    market_sets_mock.assert_called_once()
    assert runtime.stats().routed_count == 1


@pytest.mark.asyncio
async def test_process_news_logs_no_candidate_for_empty_eligible_cache(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )
    news = _news()

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    write_log_mock.assert_has_awaits(
        [
            call(
                trade_log_mock.log_polymarket_market_cache,
                raw_fetched=0,
                raw_unique=0,
                pages_fetched=1,
                cursor_present=False,
                pagination_exhausted=True,
                pagination_stop_reason="short_page",
                eligible_30d=0,
                candidate_within_admission_horizon=0,
                admission_horizon_days=30.0,
                market_limit=10,
            ),
            call(
                trade_log_mock.log_match_no_candidate,
                source=news.source,
                headline=news.headline,
                venue=Venue.POLYMARKET_US.value,
                eligible_market_count=0,
                reason="no_eligible_markets",
                candidate_pool_stage="eligible_cache_empty",
                pre_admission_matchable_market_count=0,
                within_admission_horizon_market_count=0,
                admission_horizon_days=30.0,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_process_news_logs_eligible_cache_empty_after_universe_filter(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [_market(close_time=(now + timedelta(days=31)).isoformat())]
        ),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )
    news = _news()

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    write_log_mock.assert_has_awaits(
        [
            call(
                trade_log_mock.log_polymarket_market_cache,
                raw_fetched=1,
                raw_unique=1,
                pages_fetched=1,
                cursor_present=False,
                pagination_exhausted=True,
                pagination_stop_reason="short_page",
                eligible_30d=0,
                candidate_within_admission_horizon=0,
                admission_horizon_days=30.0,
                market_limit=10,
            ),
            call(
                trade_log_mock.log_match_no_candidate,
                source=news.source,
                headline=news.headline,
                venue=Venue.POLYMARKET_US.value,
                eligible_market_count=0,
                reason="no_eligible_markets",
                candidate_pool_stage="eligible_cache_empty",
                pre_admission_matchable_market_count=0,
                within_admission_horizon_market_count=0,
                admission_horizon_days=30.0,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_process_news_logs_no_candidate_when_no_market_scores(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    fixed_now = datetime(2026, 7, 29, 9, 8, 36, tzinfo=timezone.utc)
    market_title = "Will a volcano erupt this year? " + "x" * 200
    market = _market(
        title=market_title,
        question=market_title,
        close_time=(fixed_now + timedelta(days=7)).isoformat(),
        description="Do not persist this market description.",
        public_comments=("Do not persist this market comment.",),
    )
    route_analysis = AsyncMock()
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([market]),
        route_analysis=route_analysis,
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )
    monkeypatch.setattr(runtime, "_horizon_now", lambda: fixed_now)
    news = _news("Central bank releases an interest rate decision")

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    assert write_log_mock.await_count == 2
    fields = write_log_mock.await_args_list[-1].kwargs
    assert fields["candidate_pool_stage"] == "post_admission_no_match"
    assert fields["post_admission_no_token_overlap_count"] == 1
    assert fields["post_admission_below_min_post_weight_score_count"] == 0
    snapshot = fields["post_admission_counterfactual_shadow"]
    assert set(snapshot) == {
        "schema_version",
        "match_clock_utc",
        "news_headline_token_count",
        "news_match_token_count",
        "candidate_count_total",
        "captured_market_count",
        "omitted_market_count",
        "truncated",
        "candidates",
    }
    assert snapshot["schema_version"] == 1
    assert snapshot["match_clock_utc"] == fixed_now.isoformat()
    assert snapshot["news_headline_token_count"] > 0
    assert snapshot["news_match_token_count"] > 0
    assert snapshot["candidate_count_total"] == 1
    assert snapshot["captured_market_count"] == 1
    assert snapshot["omitted_market_count"] == 0
    assert snapshot["truncated"] is False
    candidate = snapshot["candidates"][0]
    assert set(candidate) == {
        "ticker",
        "market_title",
        "rejection_reason",
        "market_token_count",
        "matched_token_count",
    }
    assert candidate["ticker"] == market.ticker
    assert candidate["market_title"] == market_title[:160]
    assert candidate["rejection_reason"] == "no_token_overlap"
    assert candidate["market_token_count"] > 0
    assert candidate["matched_token_count"] == 0
    assert "matched_tokens" not in candidate
    assert "Do not persist" not in str(snapshot)
    route_analysis.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_news_omits_hostile_counterfactual_tickers_without_losing_base_event(
    monkeypatch,
):
    class HostileTicker(str):
        def __lt__(self, other):
            raise AssertionError("counterfactual ticker must not reach sorting")

    _configure_legacy_paper_horizon(monkeypatch)
    fixed_now = datetime(2026, 7, 29, 9, 8, 36, tzinfo=timezone.utc)
    markets = [
        _market(
            market_id=HostileTicker("hostile-a"),
            title="Volcano eruption forecast",
            question="Volcano eruption forecast",
            close_time=(fixed_now + timedelta(days=7)).isoformat(),
        ),
        _market(
            market_id=HostileTicker("hostile-b"),
            title="Tropical storm forecast",
            question="Tropical storm forecast",
            close_time=(fixed_now + timedelta(days=7)).isoformat(),
        ),
    ]
    route_analysis = AsyncMock()
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(markets),
        route_analysis=route_analysis,
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )
    monkeypatch.setattr(runtime, "_horizon_now", lambda: fixed_now)

    with (
        patch("polymarket.paper_runtime.trade_log"),
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(
            _news("Central bank releases an interest rate decision")
        )

    assert routed_count == 0
    assert write_log_mock.await_count == 2
    fields = write_log_mock.await_args_list[-1].kwargs
    assert fields["candidate_pool_stage"] == "post_admission_no_match"
    assert fields["post_admission_no_token_overlap_count"] == 2
    snapshot = fields["post_admission_counterfactual_shadow"]
    assert snapshot["candidate_count_total"] == 2
    assert snapshot["captured_market_count"] == 0
    assert snapshot["omitted_market_count"] == 2
    assert snapshot["truncated"] is True
    assert snapshot["candidates"] == []
    route_analysis.assert_not_awaited()


def test_empty_market_tokens_count_as_no_overlap_for_counterfactual_shadow(
    monkeypatch,
):
    fixed_now = datetime(2026, 7, 29, 9, 8, 36, tzinfo=timezone.utc)
    market = _market(
        title="",
        question="",
        subtitle="",
        description="",
        category="",
        resolution_source="",
        event_title="",
        event_slug="",
        series_title="",
        series_slug="",
        tags=(),
        public_comments=(),
        close_time=(fixed_now + timedelta(days=7)).isoformat(),
    )
    monkeypatch.setattr(
        paper_runtime,
        "_is_pre_admission_matchable_market",
        lambda _market: True,
    )

    matches, rejection = paper_runtime._match_polymarket_markets_with_rejection_telemetry(
        _news("Central bank releases an interest rate decision"),
        [market],
        token_weights={},
        now=fixed_now,
        admission_horizon_days=30,
    )

    assert matches == []
    fields = rejection.as_log_fields()
    assert fields["post_admission_no_token_overlap_count"] == 1
    assert fields["post_admission_below_min_post_weight_score_count"] == 0
    snapshot = fields["post_admission_counterfactual_shadow"]
    assert snapshot["candidate_count_total"] == 1
    assert snapshot["captured_market_count"] == 1
    assert snapshot["candidates"][0]["rejection_reason"] == (
        "market_without_match_tokens"
    )


@pytest.mark.asyncio
async def test_process_news_logs_input_without_match_tokens_stage(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    route_analysis = AsyncMock()
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market()]),
        route_analysis=route_analysis,
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )
    news = _news("the and or")

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    route_analysis.assert_not_awaited()
    write_log_mock.assert_has_awaits(
        [
            call(
                trade_log_mock.log_polymarket_market_cache,
                raw_fetched=1,
                raw_unique=1,
                pages_fetched=1,
                cursor_present=False,
                pagination_exhausted=True,
                pagination_stop_reason="short_page",
                eligible_30d=1,
                candidate_within_admission_horizon=1,
                admission_horizon_days=30.0,
                market_limit=10,
            ),
            call(
                trade_log_mock.log_match_no_candidate,
                source=news.source,
                headline=news.headline,
                venue=Venue.POLYMARKET_US.value,
                eligible_market_count=1,
                reason="no_match",
                candidate_pool_stage="input_without_match_tokens",
                pre_admission_matchable_market_count=1,
                within_admission_horizon_market_count=1,
                admission_horizon_days=30.0,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_process_news_prioritizes_input_stage_before_admission_horizon(monkeypatch):
    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "active-test")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "active")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [_market(close_time=(now + timedelta(days=15)).isoformat())]
        ),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )
    news = _news("the and or")

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    write_log_mock.assert_has_awaits(
        [
            call(
                trade_log_mock.log_polymarket_market_cache,
                raw_fetched=1,
                raw_unique=1,
                pages_fetched=1,
                cursor_present=False,
                pagination_exhausted=True,
                pagination_stop_reason="short_page",
                eligible_30d=1,
                candidate_within_admission_horizon=0,
                admission_horizon_days=14.0,
                market_limit=10,
            ),
            call(
                trade_log_mock.log_match_no_candidate,
                source=news.source,
                headline=news.headline,
                venue=Venue.POLYMARKET_US.value,
                eligible_market_count=1,
                reason="no_match",
                candidate_pool_stage="input_without_match_tokens",
                pre_admission_matchable_market_count=1,
                within_admission_horizon_market_count=0,
                admission_horizon_days=14.0,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_process_news_logs_post_admission_no_match_after_low_score(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    fixed_now = datetime(2026, 7, 29, 9, 8, 36, tzinfo=timezone.utc)
    market_text = "Example amber birch cedar dogwood elm fir granite hazel ivy juniper"
    market = _market(
        title=market_text,
        question=market_text,
        close_time=(fixed_now + timedelta(days=7)).isoformat(),
    )
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([market]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )
    monkeypatch.setattr(runtime, "_horizon_now", lambda: fixed_now)
    news = _news("Example atlas beacon citadel delta ember frost glacier harbor island jade")

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    assert write_log_mock.await_count == 2
    fields = write_log_mock.await_args_list[-1].kwargs
    assert fields["post_admission_below_min_post_weight_score_count"] == 1
    snapshot = fields["post_admission_counterfactual_shadow"]
    assert snapshot["candidate_count_total"] == 1
    assert snapshot["captured_market_count"] == 1
    assert snapshot["omitted_market_count"] == 0
    candidate = snapshot["candidates"][0]
    assert candidate["ticker"] == market.ticker
    assert candidate["market_title"] == market_text
    assert candidate["rejection_reason"] == "below_min_post_weight_score"
    assert candidate["matched_token_count"] == 1
    assert candidate["pre_weight_score"] == pytest.approx(1 / 22)
    assert candidate["post_weight_score"] == pytest.approx(1 / 22)
    assert "matched_tokens" not in candidate


@pytest.mark.asyncio
async def test_process_news_logs_weight_demoted_post_admission_rejection(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    fixed_now = datetime(2026, 7, 29, 9, 8, 36, tzinfo=timezone.utc)
    news = _news("Kansas governor election tightens after new polling")
    market = _market(
        market_id="ewc-usgub-ks-2026-11-03-dem",
        title="Democratic Party",
        question="Kansas Governor Election Winner",
        subtitle="2026 race",
        close_time=(fixed_now + timedelta(days=7)).isoformat(),
    )
    token_weights = {
        "polymarket_us:ewc-usgub-ks:election": {"weight": 0.1},
        "polymarket_us:ewc-usgub-ks:governor": {"weight": 0.1},
        "polymarket_us:ewc-usgub-ks:kansas": {"weight": 0.1},
    }
    monkeypatch.setattr(
        "polymarket.paper_runtime._load_match_weights", lambda: token_weights
    )
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([market]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )
    monkeypatch.setattr(runtime, "_horizon_now", lambda: fixed_now)

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    fields = write_log_mock.await_args_list[-1].kwargs
    assert fields["post_admission_below_min_post_weight_score_count"] == 1
    assert fields["post_admission_weight_demoted_below_min_score_count"] == 1
    assert fields["post_admission_best_rejected_pre_weight_score"] >= 0.08
    assert fields["post_admission_best_rejected_post_weight_score"] < 0.08
    snapshot = fields["post_admission_counterfactual_shadow"]
    candidate = snapshot["candidates"][0]
    assert candidate["rejection_reason"] == "weight_demoted_below_min_score"
    assert candidate["matched_token_count"] > 0
    assert candidate["pre_weight_score"] >= 0.08
    assert candidate["post_weight_score"] < 0.08
    assert "matched_tokens" not in candidate
    assert trade_log_mock.log_match_weight_applied.call_count == 1


@pytest.mark.asyncio
async def test_process_news_omits_rejection_breakdown_when_candidate_limit_truncates(
    monkeypatch,
):
    _configure_legacy_paper_horizon(monkeypatch)
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market()]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
        max_candidates=0,
    )

    with (
        patch("polymarket.paper_runtime.trade_log"),
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(_news())

    assert routed_count == 0
    fields = write_log_mock.await_args_list[-1].kwargs
    assert fields["candidate_pool_stage"] == "post_admission_no_match"
    assert not any(key.startswith("post_admission_") for key in fields)
    assert "post_admission_counterfactual_shadow" not in fields


@pytest.mark.asyncio
async def test_process_news_logs_pre_admission_filter_empty_stage(monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market(yes_ask_cents=None)]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )
    news = _news()

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    write_log_mock.assert_has_awaits(
        [
            call(
                trade_log_mock.log_polymarket_market_cache,
                raw_fetched=1,
                raw_unique=1,
                pages_fetched=1,
                cursor_present=False,
                pagination_exhausted=True,
                pagination_stop_reason="short_page",
                eligible_30d=1,
                candidate_within_admission_horizon=0,
                admission_horizon_days=30.0,
                market_limit=10,
            ),
            call(
                trade_log_mock.log_match_no_candidate,
                source=news.source,
                headline=news.headline,
                venue=Venue.POLYMARKET_US.value,
                eligible_market_count=1,
                reason="no_match",
                candidate_pool_stage="pre_admission_filter_empty",
                pre_admission_matchable_market_count=0,
                within_admission_horizon_market_count=0,
                admission_horizon_days=30.0,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_process_news_logs_admission_horizon_pruned_stage(monkeypatch):
    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "active-test")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "active")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [_market(close_time=(now + timedelta(days=15)).isoformat())]
        ),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
        now_provider=lambda: now,
    )
    news = _news()

    with (
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    write_log_mock.assert_has_awaits(
        [
            call(
                trade_log_mock.log_polymarket_market_cache,
                raw_fetched=1,
                raw_unique=1,
                pages_fetched=1,
                cursor_present=False,
                pagination_exhausted=True,
                pagination_stop_reason="short_page",
                eligible_30d=1,
                candidate_within_admission_horizon=0,
                admission_horizon_days=14.0,
                market_limit=10,
            ),
            call(
                trade_log_mock.log_match_no_candidate,
                source=news.source,
                headline=news.headline,
                venue=Venue.POLYMARKET_US.value,
                eligible_market_count=1,
                reason="no_match",
                candidate_pool_stage="admission_horizon_pruned",
                pre_admission_matchable_market_count=1,
                within_admission_horizon_market_count=0,
                admission_horizon_days=14.0,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_process_news_empty_cache_ignores_funnel_telemetry_write_failures():
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log"),
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new=AsyncMock(side_effect=OSError("disk unavailable")),
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(_news())

    assert routed_count == 0
    assert runtime.stats().last_match_count == 0
    assert runtime.stats().last_error is None
    assert write_log_mock.await_count == 2


@pytest.mark.asyncio
async def test_process_news_no_match_ignores_funnel_telemetry_write_failures(
    monkeypatch,
):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "pending-test")
    monkeypatch.setattr(cfg, "paper_cohort_kind", "legacy_pending")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    route_analysis = AsyncMock()
    runtime = PolymarketPaperRuntime(
        client=_FakeClient(
            [
                _market(
                    title="Will a volcano erupt this year?",
                    question="Will a volcano erupt this year?",
                )
            ]
        ),
        route_analysis=route_analysis,
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log"),
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new=AsyncMock(side_effect=OSError("disk unavailable")),
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(
            _news("Central bank releases an interest rate decision")
        )

    assert routed_count == 0
    assert runtime.stats().no_match_count == 1
    assert write_log_mock.await_count == 3
    route_analysis.assert_not_awaited()


@pytest.mark.asyncio
async def test_warm_cache_keeps_fresh_state_when_funnel_telemetry_write_fails():
    market = _market()
    client = _FakeClient([market])
    runtime = PolymarketPaperRuntime(
        client=client,
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    with (
        patch("polymarket.paper_runtime.trade_log"),
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new=AsyncMock(side_effect=OSError("disk unavailable")),
        ) as write_log_mock,
    ):
        first_warm = await runtime.warm_cache()
        second_warm = await runtime.warm_cache()

    assert (first_warm, second_warm) == (1, 1)
    assert runtime.cached_markets() == [market]
    assert runtime.stats().last_error is None
    assert client.calls == 1
    assert write_log_mock.await_count == 1


@pytest.mark.asyncio
async def test_cache_telemetry_does_not_hold_fetch_lock():
    market = _market()
    client = _FakeClient([market])
    runtime = PolymarketPaperRuntime(
        client=client,
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )
    telemetry_started = asyncio.Event()
    release_telemetry = asyncio.Event()

    async def stalled_write_trade_log_async(*_args, **_kwargs):
        telemetry_started.set()
        await release_telemetry.wait()

    with patch(
        "polymarket.paper_runtime.write_trade_log_async",
        new=stalled_write_trade_log_async,
    ):
        refresh = asyncio.create_task(runtime.warm_cache())
        await telemetry_started.wait()
        cached_read = asyncio.create_task(runtime.warm_cache())
        try:
            assert await asyncio.wait_for(asyncio.shield(cached_read), timeout=0.1) == 1
        finally:
            release_telemetry.set()
            await refresh
            await cached_read

    assert client.calls == 1


@pytest.mark.asyncio
async def test_active_paper_horizon_rejects_far_candidate_before_route(monkeypatch):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg, "paper_cohort_id", "active-20260728")
    monkeypatch.setattr(cfg, "paper_active_cohort_max_days_to_close", 14.0)
    far = _market(
        market_id="will-example-event-happen-far",
        close_time=(datetime.now(timezone.utc) + timedelta(days=15)).isoformat(),
    )
    route_analysis = AsyncMock()
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([far]),
        route_analysis=route_analysis,
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    routed = await runtime.process_news(_news())

    assert far in runtime.cached_markets()
    assert runtime.cached_candidate_markets() == []
    assert routed == 0
    route_analysis.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_candidate_markets_excludes_suppressed_polymarket_categories():
    politics = _market(market_id="will-us-iran-deal-happen", category="politics")
    sports = _market(market_id="will-nba-finals-game-seven-happen", category="sports")
    culture = _market(market_id="will-tommy-lee-jones-attend", category="culture")
    macro = _market(market_id="will-fed-cut-rates", category="macro")
    world_with_resolution_source = _market(
        market_id="will-iran-ceasefire-be-signed",
        category="world",
        event_title="Middle East diplomacy",
        tags=("geopolitics", "iran"),
        resolution_source="https://reuters.com/world/",
        volume_dollars=500.0,
        open_interest_dollars=250.0,
    )
    world_without_resolution_source = _market(
        market_id="will-rumor-trend-online",
        category="world",
        event_title="Middle East diplomacy",
        tags=("geopolitics",),
        resolution_source="",
        volume_dollars=500.0,
        open_interest_dollars=250.0,
    )
    runtime = PolymarketPaperRuntime(
        client=_FakeClient([
            sports,
            culture,
            macro,
            politics,
            world_with_resolution_source,
            world_without_resolution_source,
        ]),
        route_analysis=AsyncMock(),
        keyword_stats=None,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    await runtime.warm_cache()

    assert runtime.cached_candidate_markets() == [politics, world_with_resolution_source]


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

    with patch("polymarket.paper_runtime.trade_log") as trade_log_mock, \
         caplog.at_level("INFO", logger="polymarket.paper_runtime"):
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
    lifecycle_id = analysis.signal_meta["lifecycle_id"]
    assert lifecycle_id.startswith("lc-")
    match_kwargs = trade_log_mock.log_match_diagnostic.call_args.kwargs
    opportunity_kwargs = trade_log_mock.log_opportunity.call_args.kwargs
    assert match_kwargs["lifecycle_id"] == lifecycle_id
    assert opportunity_kwargs["lifecycle_id"] == lifecycle_id
    assert match_kwargs["venue"] == "polymarket_us"
    assert opportunity_kwargs["venue"] == "polymarket_us"
    assert match_kwargs["settlement_source_match"] is None
    assert opportunity_kwargs["settlement_source_match"] is None
    feedback = trade_log_mock.log_feedback_decision.call_args.args[0]
    assert feedback.venue == "polymarket_us"
    assert feedback.ticker == "will-example-event-happen-2026"
    assert feedback.source_receipt.status == "not_applicable_venue"
    assert feedback.actual["source_multiplier"] == pytest.approx(1.0)
    assert feedback.source_neutral["status"] == "not_applicable_venue"
    assert feedback.keyword_counterfactual_status == "unavailable_estimator_feedback_collector"
    assert feedback.gate["enqueued"] is True
    provenance = analysis.decision_financial_provenance
    assert provenance is not None
    assert provenance.sizing_bankroll_dollars == Decimal(str(cfg.bankroll))
    assert provenance.max_position_dollars == Decimal(
        str(cfg.dynamic_max_bet(cfg.bankroll))
    )
    assert provenance.max_ticker_exposure_dollars == (
        Decimal(str(cfg.max_ticker_exposure_pct)) * Decimal(str(cfg.bankroll))
    )
    assert provenance.fee_account_precision_dollars is None
    assert provenance.fee_accumulator_dollars == INITIAL_ORDER_FEE_ACCUMULATOR
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
async def test_process_news_financial_provenance_preserves_queue_admission_and_sizing(
    monkeypatch,
):
    """Financial provenance is capture-only metadata at the Polymarket route boundary."""

    def legacy_signal_analysis(*args, **kwargs):
        kwargs.pop("decision_financial_provenance", None)
        return SignalAnalysis(*args, **kwargs)

    async def run_once(*, omit_provenance: bool):
        queued = []
        routed_analyses = []

        async def route_analysis(analysis, **kwargs):
            admitted = (
                analysis.capped_dollars > 0
                and analysis.edge >= cfg.min_edge
                and analysis.executed_price_cents > 0
            )
            if admitted:
                queued.append(
                    {
                        "ticker": analysis.market.ticker,
                        "side": analysis.side,
                        "executed_price_cents": analysis.executed_price_cents,
                        "edge": analysis.edge,
                        "kelly_fraction": analysis.kelly_fraction,
                        "kelly_dollars": analysis.kelly_dollars,
                        "capped_dollars": analysis.capped_dollars,
                        "route_kwargs": kwargs,
                    }
                )
            routed_analyses.append(analysis)
            return SimpleNamespace(enqueued=admitted)

        async def estimate_probability(*_args, **_kwargs):
            return 0.65, 0.8, ["example"], "reason", "yes", "moderate", 0.8

        runtime = PolymarketPaperRuntime(
            client=_FakeClient([_market()]),
            route_analysis=route_analysis,
            keyword_stats=None,
            estimate_probability_fn=estimate_probability,
            market_limit=10,
            market_cache_ttl_seconds=300,
        )

        with patch("polymarket.paper_runtime.trade_log"):
            if omit_provenance:
                with monkeypatch.context() as patcher:
                    patcher.setattr(
                        "polymarket.paper_runtime.SignalAnalysis",
                        legacy_signal_analysis,
                    )
                    routed_count = await runtime.process_news(_news())
            else:
                routed_count = await runtime.process_news(_news())

        assert len(routed_analyses) == 1
        return routed_count, queued, routed_analyses[0]

    legacy_routed, legacy_queue, legacy_analysis = await run_once(omit_provenance=True)
    current_routed, current_queue, current_analysis = await run_once(omit_provenance=False)

    assert legacy_analysis.decision_financial_provenance is None
    assert current_analysis.decision_financial_provenance is not None
    assert legacy_routed == current_routed == 1
    assert legacy_queue == current_queue


@pytest.mark.asyncio
async def test_process_news_uses_current_cohort_sizing_bankroll_provider():
    routed = []
    current_bankroll = [50.0]

    async def route_analysis(analysis, **_kwargs):
        routed.append(analysis)
        return SimpleNamespace(enqueued=True)

    async def estimate_probability(*_args, **_kwargs):
        return 0.65, 0.8, ["example"], "reason", "yes", "moderate", 0.8

    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market()]),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
        market_limit=10,
        market_cache_ttl_seconds=300,
        sizing_bankroll_provider=lambda: current_bankroll[0],
    )

    await runtime.process_news(_news("Example event gets more likely"))
    current_bankroll[0] = 15.0
    await runtime.process_news(_news("Example event gets much more likely"))

    assert [
        analysis.decision_financial_provenance.sizing_bankroll_dollars
        for analysis in routed
    ] == [Decimal("50.0"), Decimal("15.0")]
    assert routed[0].decision_financial_provenance.max_position_dollars == Decimal(
        str(cfg.dynamic_max_bet(50.0))
    )
    assert routed[1].decision_financial_provenance.max_position_dollars == Decimal(
        str(cfg.dynamic_max_bet(15.0))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_bankroll", (0.0, float("nan")))
async def test_process_news_fails_closed_on_invalid_cohort_sizing_bankroll(invalid_bankroll):
    route_analysis = AsyncMock()

    async def estimate_probability(*_args, **_kwargs):
        return 0.65, 0.8, ["example"], "reason", "yes", "moderate", 0.8

    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market()]),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
        market_limit=10,
        market_cache_ttl_seconds=300,
        sizing_bankroll_provider=lambda: invalid_bankroll,
    )

    routed = await runtime.process_news(_news())

    assert routed == 0
    route_analysis.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_news_emits_match_but_not_opportunity_when_analysis_fails():
    route_analysis = AsyncMock()

    async def estimate_probability(*_args, **_kwargs):
        raise ValueError("no executable side")

    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market()]),
        route_analysis=route_analysis,
        keyword_stats=None,
        estimate_probability_fn=estimate_probability,
        market_limit=10,
        market_cache_ttl_seconds=300,
    )

    with patch("polymarket.paper_runtime.trade_log") as trade_log_mock:
        routed_count = await runtime.process_news(_news())

    assert routed_count == 0
    trade_log_mock.log_match_diagnostic.assert_called_once()
    trade_log_mock.log_opportunity.assert_not_called()
    route_analysis.assert_not_awaited()


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
async def test_process_news_fail_closed_when_public_market_fetch_fails(caplog, monkeypatch):
    _configure_legacy_paper_horizon(monkeypatch)
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

    news = _news()
    with (
        caplog.at_level("WARNING", logger="polymarket.paper_runtime"),
        patch("polymarket.paper_runtime.trade_log") as trade_log_mock,
        patch(
            "polymarket.paper_runtime.write_trade_log_async",
            new_callable=AsyncMock,
        ) as write_log_mock,
    ):
        routed_count = await runtime.process_news(news)

    assert routed_count == 0
    assert "public_market_fetch_failed" in caplog.text
    stats = runtime.stats()
    assert stats.market_count == 0
    assert stats.news_processed == 1
    assert stats.last_error == "public_market_fetch_failed"
    write_log_mock.assert_awaited_once_with(
        trade_log_mock.log_match_no_candidate,
        source=news.source,
        headline=news.headline,
        venue=Venue.POLYMARKET_US.value,
        eligible_market_count=0,
        reason="market_fetch_failed",
        candidate_pool_stage="provider_fetch_failed",
        pre_admission_matchable_market_count=0,
        within_admission_horizon_market_count=0,
        admission_horizon_days=30.0,
    )


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


def test_match_polymarket_markets_applies_shared_token_weights():
    news = _news("Kansas governor election tightens after new polling")
    market = _market(
        market_id="ewc-usgub-ks-2026-11-03-dem",
        title="Democratic Party",
        question="Kansas Governor Election Winner",
        subtitle="2026 race",
        category="politics",
    )
    # PROFIT-VENUE-PARITY V03/V17: token-downweight buckets are now PER-FAMILY.
    # The key is "{market_prefix}:{token}" where market_prefix is the per-family
    # series_ticker pm_domain_key("ewc-usgub-ks-2026-11-03-dem") ==
    # "polymarket_us:ewc-usgub-ks". Efficacy: a downweight learned for THIS
    # family floors the score (matches==[]); the OLD venue-wide
    # "polymarket_us:<token>" keys are inert (belong to no family now).
    token_weights = {
        "polymarket_us:ewc-usgub-ks:election": {"weight": 0.1},
        "polymarket_us:ewc-usgub-ks:governor": {"weight": 0.1},
        "polymarket_us:ewc-usgub-ks:kansas": {"weight": 0.1},
        "polymarket_us:election": {"weight": 0.1},
        "polymarket_us:governor": {"weight": 0.1},
        "polymarket_us:kansas": {"weight": 0.1},
    }

    with patch("polymarket.paper_runtime.trade_log.log_match_weight_applied") as log_mock:
        matches = match_polymarket_markets(
            news,
            [market],
            max_results=5,
            min_score=0.08,
            token_weights=token_weights,
        )

    assert matches == []
    log_mock.assert_called_once()
    # PROFIT-VENUE-PARITY V03/V17: the downweight is logged against the PER-FAMILY
    # prefix (the efficacy signal that PM feedback is now domain-scoped).
    assert log_mock.call_args.kwargs["market_prefix"] == "polymarket_us:ewc-usgub-ks"
    assert log_mock.call_args.kwargs["final_multiplier"] == pytest.approx(0.1)
    assert log_mock.call_args.kwargs["post_weight_score"] < 0.08


def test_horizon_shadow_matching_does_not_emit_normal_weight_events():
    news = _news("Kansas governor election tightens after new polling")
    market = _market(
        market_id="ewc-usgub-ks-2026-11-03-dem",
        title="Democratic Party",
        question="Kansas Governor Election Winner",
        subtitle="2026 race",
        category="politics",
    )
    token_weights = {
        "polymarket_us:ewc-usgub-ks:election": {"weight": 0.1},
        "polymarket_us:ewc-usgub-ks:governor": {"weight": 0.1},
        "polymarket_us:ewc-usgub-ks:kansas": {"weight": 0.1},
    }

    with patch("polymarket.paper_runtime.trade_log.log_match_weight_applied") as log_mock:
        matches, rejection = paper_runtime._match_polymarket_markets_with_rejection_telemetry(
            news,
            [market],
            max_results=5,
            min_score=0.08,
            token_weights=token_weights,
            emit_match_weight_telemetry=False,
        )

    assert matches == []
    assert rejection.weight_demoted_below_min_score_count == 1
    log_mock.assert_not_called()


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
