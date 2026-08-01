"""Isolated source-hint RSS evidence capture with no intake-side effects."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
import logging
from pathlib import Path
from typing import Protocol
import urllib.parse

from feeds import NewsItem
from feeds.rss_monitor import poll_feed
from feeds.seen_state import checkpoint_seen_ids, load_seen_ids
from kalshi import KalshiMarket
from kalshi.series_metadata import KalshiSeriesMetadata
from kalshi.source_hints import build_market_contract_context, build_market_first_queries
from tasks.market_source_hint_shadow_store import (
    MarketSourceHintShadowObservation,
)
from trading.venue import Venue, normalize_venue
from utils.output_paths import STATE_ROOT


logger = logging.getLogger(__name__)

MARKET_SOURCE_HINT_SHADOW_SEEN_STATE_PATH = (
    STATE_ROOT / "ingest_seen" / "market_source_hint_shadow_seen_ids.json"
)
_MAX_MARKETS = 50
_MAX_QUERIES_PER_MARKET = 5
_MAX_RECORDS_PER_CYCLE = 200
_MAX_CONCURRENCY = 8
_MAX_TIMEOUT_SECONDS = 30.0
_MAX_SEEN = 2_000


class SourceHintShadowStore(Protocol):
    async def initialize(self) -> None: ...

    async def append(self, observation: MarketSourceHintShadowObservation) -> None: ...


@dataclass(frozen=True)
class MarketSourceHintShadowCycleResult:
    markets_considered: int = 0
    queries_attempted: int = 0
    feeds_attempted: int = 0
    records_captured: int = 0
    timed_out_feeds: int = 0
    failed_feeds: int = 0
    store_failures: int = 0


FeedUrlBuilder = Callable[[str], str]
FeedPoller = Callable[[str, Callable[[NewsItem], Awaitable[None]], OrderedDict], Awaitable[None]]


def _google_news_url(query: str) -> str:
    params = urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )
    return f"https://news.google.com/rss/search?{params}"


def _bing_news_url(query: str) -> str:
    return "https://www.bing.com/news/search?" + urllib.parse.urlencode(
        {"q": query, "format": "rss"}
    )


def _default_feed_url_builders() -> tuple[tuple[str, FeedUrlBuilder], ...]:
    return (("google_news", _google_news_url), ("bing_news", _bing_news_url))


class MarketSourceHintShadowMonitor:
    """Collect source-hint retrieval evidence without forwarding news anywhere."""

    def __init__(
        self,
        *,
        store: SourceHintShadowStore,
        get_markets: Callable[[], Sequence[object]],
        get_series_metadata: Callable[[], Mapping[str, KalshiSeriesMetadata]],
        interval_seconds: float = 900.0,
        seen_state_path: Path = MARKET_SOURCE_HINT_SHADOW_SEEN_STATE_PATH,
        feed_url_builders: Sequence[tuple[str, FeedUrlBuilder]] | None = None,
        feed_poller: FeedPoller = poll_feed,
        max_markets: int = 5,
        max_queries_per_market: int = 2,
        max_records_per_cycle: int = 20,
        max_concurrency: int = 2,
        feed_timeout_seconds: float = 20.0,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._get_markets = get_markets
        self._get_series_metadata = get_series_metadata
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._seen_state_path = Path(seen_state_path)
        self._feed_url_builders = tuple(feed_url_builders or _default_feed_url_builders())
        self._feed_poller = feed_poller
        self._max_markets = _bounded_int(max_markets, _MAX_MARKETS)
        self._max_queries_per_market = _bounded_int(
            max_queries_per_market, _MAX_QUERIES_PER_MARKET
        )
        self._max_records_per_cycle = _bounded_int(
            max_records_per_cycle, _MAX_RECORDS_PER_CYCLE
        )
        self._max_concurrency = max(1, min(int(max_concurrency), _MAX_CONCURRENCY))
        self._feed_timeout_seconds = max(
            0.01, min(float(feed_timeout_seconds), _MAX_TIMEOUT_SECONDS)
        )
        self._now = now
        self._sleep = sleep

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Run independently; setup and cycle failures remain in this task."""
        initialized = False
        while stop_event is None or not stop_event.is_set():
            if not initialized:
                try:
                    await self._store.initialize()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "source-hint shadow capture initialization failed: %s",
                        type(exc).__name__,
                    )
                    await self._sleep(self._interval_seconds)
                    continue
                initialized = True

            try:
                result = await self.run_once()
                logger.info(
                    "source-hint shadow capture cycle "
                    "markets=%d queries=%d feeds=%d captured=%d "
                    "timeouts=%d failures=%d store_failures=%d",
                    result.markets_considered,
                    result.queries_attempted,
                    result.feeds_attempted,
                    result.records_captured,
                    result.timed_out_feeds,
                    result.failed_feeds,
                    result.store_failures,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "source-hint shadow capture cycle failed: %s",
                    type(exc).__name__,
                )
            if stop_event is None or not stop_event.is_set():
                await self._sleep(self._interval_seconds)

    async def run_once(self) -> MarketSourceHintShadowCycleResult:
        """Capture one bounded evidence cycle and contain all feed/store failures."""
        try:
            market_snapshot = tuple(islice(self._get_markets(), self._max_markets))
        except Exception as exc:
            logger.warning("source-hint shadow market snapshot failed: %s", type(exc).__name__)
            return MarketSourceHintShadowCycleResult()

        markets = tuple(
            market for market in market_snapshot if _is_genuine_kalshi_market(market)
        )

        if not markets or not self._feed_url_builders or self._max_records_per_cycle <= 0:
            return MarketSourceHintShadowCycleResult(markets_considered=len(markets))

        try:
            metadata_by_ticker = self._get_series_metadata() or {}
        except Exception as exc:
            logger.warning(
                "source-hint shadow series metadata snapshot failed: %s",
                type(exc).__name__,
            )
            metadata_by_ticker = {}

        seen = load_seen_ids(self._seen_state_path, _MAX_SEEN)
        semaphore = asyncio.Semaphore(self._max_concurrency)
        write_lock = asyncio.Lock()
        counters = _CycleCounters(markets_considered=len(markets))
        poll_tasks: list[Awaitable[None]] = []

        for market in markets:
            ticker = str(getattr(market, "ticker", "")).strip()
            if not ticker:
                continue
            try:
                series_key = (
                    str(getattr(market, "series_ticker", "") or ticker)
                    .split("-", 1)[0]
                    .upper()
                )
                context = build_market_contract_context(
                    market,
                    metadata_by_ticker.get(series_key),
                )
                queries = build_market_first_queries(
                    context,
                    max_queries=self._max_queries_per_market,
                )
            except Exception as exc:
                logger.warning(
                    "source-hint shadow query planning failed ticker=%s error=%s",
                    ticker,
                    type(exc).__name__,
                )
                continue

            for query in queries:
                domain = _source_hint_domain(query)
                if domain is None:
                    continue
                counters.queries_attempted += 1
                for _engine, url_builder in self._feed_url_builders:
                    try:
                        feed_url = url_builder(query)
                    except Exception as exc:
                        counters.failed_feeds += 1
                        logger.warning(
                            "source-hint shadow URL construction failed ticker=%s error=%s",
                            ticker,
                            type(exc).__name__,
                        )
                        continue
                    counters.feeds_attempted += 1
                    poll_tasks.append(
                        self._poll_one(
                            feed_url=feed_url,
                            market=market,
                            ticker=ticker,
                            query=query,
                            domain=domain,
                            seen=seen,
                            semaphore=semaphore,
                            write_lock=write_lock,
                            counters=counters,
                        )
                    )

        if poll_tasks:
            await asyncio.gather(*poll_tasks, return_exceptions=True)
        try:
            checkpoint_seen_ids(self._seen_state_path, seen, _MAX_SEEN)
        except OSError as exc:
            logger.warning("source-hint shadow seen-state checkpoint failed: %s", exc)
        return counters.to_result()

    async def _poll_one(
        self,
        *,
        feed_url: str,
        market: object,
        ticker: str,
        query: str,
        domain: str,
        seen: OrderedDict,
        semaphore: asyncio.Semaphore,
        write_lock: asyncio.Lock,
        counters: "_CycleCounters",
    ) -> None:
        async def capture(item: NewsItem) -> None:
            async with write_lock:
                if counters.records_captured >= self._max_records_per_cycle:
                    return
                observation = _observation_for_item(
                    item=item,
                    captured_at=self._now(),
                    ticker=ticker,
                    market_title=str(getattr(market, "title", "")).strip(),
                    query=query,
                    domain=domain,
                    feed_url=feed_url,
                )
                if observation is None:
                    counters.store_failures += 1
                    return
                try:
                    await self._store.append(observation)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    counters.store_failures += 1
                    logger.warning(
                        "source-hint shadow evidence append failed ticker=%s error=%s",
                        ticker,
                        type(exc).__name__,
                    )
                    return
                counters.records_captured += 1

        async with semaphore:
            try:
                await asyncio.wait_for(
                    self._feed_poller(feed_url, capture, seen),
                    timeout=self._feed_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                counters.timed_out_feeds += 1
                logger.warning("source-hint shadow feed timed out: %s", feed_url)
            except Exception as exc:
                counters.failed_feeds += 1
                logger.warning(
                    "source-hint shadow feed failed error=%s", type(exc).__name__
                )


@dataclass
class _CycleCounters:
    markets_considered: int = 0
    queries_attempted: int = 0
    feeds_attempted: int = 0
    records_captured: int = 0
    timed_out_feeds: int = 0
    failed_feeds: int = 0
    store_failures: int = 0

    def to_result(self) -> MarketSourceHintShadowCycleResult:
        return MarketSourceHintShadowCycleResult(
            markets_considered=self.markets_considered,
            queries_attempted=self.queries_attempted,
            feeds_attempted=self.feeds_attempted,
            records_captured=self.records_captured,
            timed_out_feeds=self.timed_out_feeds,
            failed_feeds=self.failed_feeds,
            store_failures=self.store_failures,
        )


def _bounded_int(value: int, upper_bound: int) -> int:
    return max(0, min(int(value), upper_bound))


def _is_genuine_kalshi_market(market: object) -> bool:
    """Accept only the canonical Kalshi model and reject unknown venue identity."""
    if not isinstance(market, KalshiMarket):
        return False
    raw_venue = getattr(market, "venue", None)
    if raw_venue is None:
        return True
    if hasattr(raw_venue, "value"):
        raw_venue = raw_venue.value
    try:
        return normalize_venue(raw_venue) is Venue.KALSHI
    except Exception:
        return False


def _source_hint_domain(query: str) -> str | None:
    prefix = query.strip().split(maxsplit=1)[0] if query.strip() else ""
    if not prefix.startswith("site:"):
        return None
    domain = prefix.removeprefix("site:").strip().lower()
    return domain or None


def _observation_for_item(
    *,
    item: NewsItem,
    captured_at: datetime,
    ticker: str,
    market_title: str,
    query: str,
    domain: str,
    feed_url: str,
) -> MarketSourceHintShadowObservation | None:
    headline = str(getattr(item, "headline", "")).strip()
    item_url = str(getattr(item, "url", "")).strip()
    item_source = str(getattr(item, "source", "")).strip()
    item_id = str(getattr(item, "item_id", "")).strip()
    if not all((ticker, market_title, query, domain, feed_url, headline, item_url, item_source, item_id)):
        logger.warning("source-hint shadow skipped incomplete RSS provenance")
        return None
    published_at = getattr(item, "published", None)
    if not isinstance(published_at, datetime):
        published_at = None
    return MarketSourceHintShadowObservation(
        captured_at=captured_at,
        ticker=ticker,
        market_title=market_title,
        source_hint_query=query,
        source_hint_domain=domain,
        feed_url=feed_url,
        item_id=item_id,
        headline=headline,
        item_url=item_url,
        item_source=item_source,
        published_at=published_at,
    )
