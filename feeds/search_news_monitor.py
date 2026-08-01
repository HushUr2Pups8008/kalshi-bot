"""
Multi-engine search news RSS monitor (Google News + Bing News).

Generates search queries from active Kalshi market titles and fetches them
from both Google News RSS and Bing News RSS in parallel each cycle.
No API key required for either engine.

Architecture:
  - Each cycle, top markets by open_interest are converted to search queries
  - Up to SEARCH_MAX_QUERIES distinct queries per cycle
  - Each query is fetched from both Google News and Bing News (2x coverage)
  - Feeds fetched in parallel via asyncio.gather
  - Reuses poll_feed() from rss_monitor for feedparser + dedup + NewsItem creation
  - Shared dedup cache across both engines -- cross-engine duplicates suppressed
  - Same NewsItem callback chain as all other news sources

Poll interval: 300s -- both engines index within minutes of publication.
"""

import asyncio
import re
import urllib.parse
from pathlib import Path
from typing import Callable, Awaitable, Sequence

from config import (
    DISABLED_SOURCE_FAMILIES,
    ENABLE_MARKET_FIRST_QUERY_SHADOW,
    ENABLE_NEWS_EDGE_PRIORITIZATION,
    MARKET_SERIES_BLOCKLIST_PREFIXES,
    MARKET_SOURCE_HINTS_QUERY_CAP,
    MARKET_SOURCE_HINTS_QUERY_MODE,
    NEWS_EDGE_SERIES,
)
from feeds import NewsItem
from feeds.rss_monitor import poll_feed
from feeds.seen_state import checkpoint_seen_ids, load_seen_ids
from kalshi import KalshiMarket
from kalshi.series_metadata import KalshiSeriesMetadata
from kalshi.source_hints import build_market_contract_context, build_market_first_queries
from tasks.stats.edge_series import active_edge_series
from utils.logger import get_logger
from utils.output_paths import STATE_ROOT

log = get_logger("search_monitor")

SEARCH_POLL_INTERVAL = 300   # seconds between full fetch cycles
SEARCH_MAX_QUERIES   = 25    # max distinct queries per cycle (ranked by open_interest×uncertainty;
                            # news-edge series first when ENABLE_NEWS_EDGE_PRIORITIZATION)
SEARCH_MAX_SEEN      = 2000  # dedup cache entry limit
SEARCH_SEEN_STATE_PATH = STATE_ROOT / "ingest_seen" / "search_seen_ids.json"

# Hard bounds for the AIMD articles-per-query controller
_SEARCH_ARTICLES_MIN =  1
_SEARCH_ARTICLES_MAX = 15

# ── AIMD rate state ───────────────────────────────────────────────────────────
# articles_cap starts conservative and self-calibrates each cycle based on
# news queue fill ratio: queue shallow (<20%) → +1, queue deep (>60%) → -1.
# The queue depth is the consumer's throughput signal -- if the LLM can't keep
# up, the queue fills and we back off ingestion; if it's idle, we feed it more.
_search_articles_cap: int = 3  # current per-query article cap; AIMD-adjusted each cycle

_GNEWS_BASE = "https://news.google.com/rss/search"
_BING_BASE  = "https://www.bing.com/news/search"

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "will", "would", "could",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "about",
    "and", "or", "but", "not", "this", "that", "it", "if", "as", "be",
    "yes", "no", "new", "more", "how", "what", "when", "who", "which",
    "win", "wins", "its", "has", "have", "had", "can", "get", "per",
    "any", "all", "than", "over", "under", "least", "most", "first",
    "before", "after", "between", "during", "within", "without",
    "exceed", "reach", "hit", "pass", "become", "remain", "stay",
    "there", "their", "they", "them", "then", "that",
})

# Tokens that add no search query value even after stop-word removal
_NOISE_TOKENS = frozenset({
    "100", "2025", "2026", "2027", "percent", "pct", "number",
    "total", "end", "year", "month", "week", "day", "days",
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
})

# Sports/entertainment tokens that indicate a query will produce off-topic results.
# Used as a secondary gate after the ticker-based blocklist -- catches markets whose
# series_ticker doesn't match any blocklist prefix but whose title content is off-topic.
_SPORTS_TOKENS = frozenset({
    # Cricket / IPL
    "cricket", "ipl", "challengers", "bengaluru", "chennai", "kolkata",
    "rajasthan", "royals", "sunrisers", "hyderabad", "punjab",
    # Golf
    "golf", "tiger", "woods", "pga", "masters",
    # Other sports likely to appear in market titles
    "nba", "nfl", "mlb", "nhl", "mls", "soccer", "football",
    "tennis", "boxing", "mma", "wrestling", "racing", "formula",
    "olympics", "superbowl", "championship", "tournament",
    # Crypto / entertainment (also blocklisted by ticker but coverage is incomplete)
    "bitcoin", "ethereum", "dogecoin", "crypto", "blockchain", "doge",
    "oscars", "grammy", "emmy", "celebrity",
})

# Economic/financial tokens that indicate a market query will produce off-topic results
# for a geopolitical bot.  Treasury yields, CPI, GDP, Fed rate decisions, etc. have no
# bearing on the geopolitical events we trade.  If ANY of the query's 4 tokens appear
# here the market is skipped -- same secondary-gate pattern as _SPORTS_TOKENS.
_ECONOMIC_TOKENS = frozenset({
    # Interest rates / bonds
    "treasury", "yield", "yields", "note", "notes", "bond", "bonds",
    "coupon", "maturity", "spread", "basis", "fed", "rate", "rates",
    # Inflation / macro indicators
    "cpi", "pce", "inflation", "deflation", "gdp", "deficit", "debt",
    "yoy", "qoq", "mom",          # year-over-year / quarter-over-quarter / month-over-month
    # Central banks (abbreviations safe to block -- no geopolitical market uses them)
    "fomc", "boe", "ecb", "boj", "pboc",
    # Rates / employment
    "payroll", "unemployment", "nfp",
    # Commodities (price-level markets, not geopolitical events)
    "brent", "wti", "barrel",
    # Stock indices
    "nasdaq", "snp", "djia",
    # Earnings / corporate finance
    "earnings", "revenue", "ebitda", "eps",
})


def _enabled_search_engines() -> list[tuple[str, Callable[[str], str]]]:
    engines: list[tuple[str, Callable[[str], str]]] = []
    if "google_news_query" not in DISABLED_SOURCE_FAMILIES:
        engines.append(("Google News", _gnews_url))
    if "bing_news_query" not in DISABLED_SOURCE_FAMILIES:
        engines.append(("BingNews", _bing_url))
    return engines


def _gnews_url(query: str) -> str:
    """Build a Google News RSS URL for a search query."""
    params = urllib.parse.urlencode({
        "q":    query,
        "hl":   "en-US",
        "gl":   "US",
        "ceid": "US:en",
    })
    return f"{_GNEWS_BASE}?{params}"


def _bing_url(query: str) -> str:
    """Build a Bing News RSS URL for a search query."""
    params = urllib.parse.urlencode({"q": query, "format": "rss"})
    return f"{_BING_BASE}?{params}"


def _tokenize(text: str) -> list[str]:
    """Extract meaningful tokens from a market title for query construction."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [
        t for t in text.split()
        if t not in _STOP_WORDS
        and t not in _NOISE_TOKENS
        and len(t) >= 3
    ]


def _market_query_text(market: KalshiMarket) -> str:
    return " ".join(
        part
        for part in (
            getattr(market, "title", ""),
            getattr(market, "question", ""),
            getattr(market, "subtitle", ""),
        )
        if part
    )


def _dedupe_preserving_order(tokens: list[str]) -> list[str]:
    return list(dict.fromkeys(tokens))


def _markets_to_queries(
    markets: Sequence[KalshiMarket],
    edge_series: "frozenset[str] | set[str] | None" = None,
    *,
    series_metadata_by_ticker: "dict[str, KalshiSeriesMetadata] | None" = None,
    market_first_query_shadow: bool = False,
    market_source_hint_query_mode: str = "off",
    market_source_hint_query_cap: int | None = None,
) -> list[str]:
    """
    Convert active market titles to deduplicated search queries.

    Markets on the active news-edge set (series with demonstrated news-edge) are
    ranked first so they always receive targeted retrieval within the
    SEARCH_MAX_QUERIES budget — otherwise high-open-interest macro / "mention"
    markets the bot has no news-edge on crowd them out (option A, 2026-05-30).
    The edge set is **self-maintaining** (option A-2): ``active_edge_series``
    derives it from a rolling window of OPPORTUNITY history (auto-promote /
    auto-age-out), with the static ``config.NEWS_EDGE_SERIES`` used only as a
    cold-start seed. Callers may inject ``edge_series`` directly (tests).
    Within each tier markets are ranked by open_interest * (1 - |price - 50|/50),
    which prioritizes contested markets (price near 50c) with meaningful volume
    over already-decided or illiquid ones. Fresh news moves contested markets
    most. Each market contributes up to 4 key tokens. Markets with identical
    token sets are skipped (deduped). Capped at SEARCH_MAX_QUERIES.
    """
    if edge_series is None:
        # DECISION-AFFECTING + IC §16-gated: default OFF -> empty set -> pure
        # open_interest×uncertainty ranking (prior production behavior). Only an
        # operator who has flipped ENABLE_NEWS_EDGE_PRIORITIZATION (with replay-EV
        # evidence / override) gets edge-first prioritization.
        edge_series = (
            active_edge_series(seed=NEWS_EDGE_SERIES)
            if ENABLE_NEWS_EDGE_PRIORITIZATION
            else frozenset()
        )

    def _is_edge_series(m: KalshiMarket) -> int:
        series = (
            (getattr(m, "series_ticker", None) or getattr(m, "ticker", "") or "")
            .split("-")[0]
            .upper()
        )
        return 1 if series in edge_series else 0

    def _interest_weight(m: KalshiMarket) -> float:
        return float(
            getattr(m, "open_interest", 0)
            or getattr(m, "open_interest_dollars", 0)
            or getattr(m, "volume_dollars", 0)
            or 1.0
        )

    sorted_markets = sorted(
        markets,
        key=lambda m: (
            _is_edge_series(m),
            _interest_weight(m)
            * (1.0 - abs(getattr(m, "yes_price", 50) - 50) / 50.0),
        ),
        reverse=True,
    )

    seen_sets: set[frozenset] = set()
    queries: list[str] = []
    source_hint_mode = (market_source_hint_query_mode or "off").strip().lower()
    if market_first_query_shadow and source_hint_mode == "off":
        source_hint_mode = "shadow"
    # Shadow remains diagnostic-only; only explicit production may consume
    # search capacity or enter the normal NewsItem callback chain.
    source_hint_enabled = source_hint_mode == "production"
    source_hint_cap = (
        SEARCH_MAX_QUERIES
        if market_source_hint_query_cap is None
        else max(0, int(market_source_hint_query_cap))
    )
    source_hint_queries = 0

    for market in sorted_markets:
        if len(queries) >= SEARCH_MAX_QUERIES:
            break
        # Skip sports/blocklisted markets -- their tokens produce irrelevant queries.
        # Primary gate: series_ticker prefix.
        _ticker = (getattr(market, "series_ticker", None) or market.ticker).upper()
        if any(_ticker.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES):
            continue
        tokens = _dedupe_preserving_order(_tokenize(_market_query_text(market)))[:4]
        if len(tokens) < 2:
            continue
        # Secondary gate: content-based off-topic token filter.
        # Catches markets whose series_ticker doesn't match any blocklist prefix
        # but whose title contains known off-topic terms: sports, entertainment,
        # economic indicators (treasury yields, CPI, GDP, etc.).
        if frozenset(tokens) & (_SPORTS_TOKENS | _ECONOMIC_TOKENS):
            continue
        token_set = frozenset(tokens)
        if token_set in seen_sets:
            continue
        seen_sets.add(token_set)
        queries.append(" ".join(tokens))
        if (
            source_hint_enabled
            and source_hint_queries < source_hint_cap
            and len(queries) < SEARCH_MAX_QUERIES
        ):
            series_key = (
                (getattr(market, "series_ticker", None) or getattr(market, "ticker", "") or "")
                .split("-")[0]
                .upper()
            )
            series_metadata = (series_metadata_by_ticker or {}).get(series_key)
            context = build_market_contract_context(market, series_metadata)
            for shadow_query in build_market_first_queries(
                context,
                max_queries=min(
                    SEARCH_MAX_QUERIES - len(queries),
                    source_hint_cap - source_hint_queries,
                ),
            ):
                if shadow_query in queries:
                    continue
                queries.append(shadow_query)
                source_hint_queries += 1
                if len(queries) >= SEARCH_MAX_QUERIES:
                    break

    return queries


def _tag_source_hint_item(item: NewsItem, query: str, mode: str) -> NewsItem:
    if mode.strip().lower() not in {"shadow", "production"}:
        return item
    if not query.startswith("site:"):
        return item
    domain = query.split(maxsplit=1)[0].removeprefix("site:")
    item.retrieval_mode = "source_hint"
    item.source_hint_query = query
    item.source_hint_domain = domain
    return item


async def run_search_news_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    get_markets: Callable[[], Sequence[KalshiMarket]],
    poll_interval: int = SEARCH_POLL_INTERVAL,
    queue_depth_fn: "Callable[[], float] | None" = None,
    get_series_metadata: "Callable[[], dict[str, KalshiSeriesMetadata]] | None" = None,
    seen_state_path: Path = SEARCH_SEEN_STATE_PATH,
) -> None:
    """
    Poll Google News RSS and Bing News RSS for queries derived from active
    Kalshi market titles. Runs indefinitely; cancel the task to stop.

    Each query is fetched from both engines in parallel with a per-query
    article cap that self-adjusts via AIMD based on queue fill ratio.

    Args:
        callback:       Called for each new NewsItem.
        get_markets:    Sync callable returning the current market cache list.
        poll_interval:  Seconds between fetch cycles (default 300).
        queue_depth_fn: Optional sync callable returning queue fill ratio (0.0-1.0).
                        When provided, the articles-per-query cap rises when the
                        queue is shallow and falls when it is deep. Pass None to
                        keep the cap static at its current AIMD value.
    """
    global _search_articles_cap
    seen = load_seen_ids(seen_state_path, SEARCH_MAX_SEEN)
    engines = _enabled_search_engines()
    if not engines:
        log.info("Search news monitor disabled by source-family policy; skipping polling")
        return
    log.info(
        "Search news monitor started (poll interval %ds, max %d queries/cycle,"
        " engines=%s, AIMD articles/query start=%d max=%d)",
        poll_interval,
        SEARCH_MAX_QUERIES,
        ",".join(name for name, _ in engines),
        _search_articles_cap,
        _SEARCH_ARTICLES_MAX,
    )

    while True:
        try:
            markets = get_markets()
            source_hint_query_mode = (MARKET_SOURCE_HINTS_QUERY_MODE or "off").strip().lower()
            source_hint_query_enabled = source_hint_query_mode == "production"
            series_metadata = (
                get_series_metadata()
                if source_hint_query_enabled and get_series_metadata is not None
                else None
            )
            queries = _markets_to_queries(
                markets,
                series_metadata_by_ticker=series_metadata,
                market_first_query_shadow=ENABLE_MARKET_FIRST_QUERY_SHADOW,
                market_source_hint_query_mode=source_hint_query_mode,
                market_source_hint_query_cap=MARKET_SOURCE_HINTS_QUERY_CAP,
            )

            if not queries:
                log.debug("Search news: no active markets, skipping cycle")
                await asyncio.sleep(poll_interval)
                continue

            # ── AIMD adjustment (queue depth signal) ──────────────────────────
            # Adjust articles-per-query cap before this cycle based on how full
            # the consumer queue is. Queue depth is the LLM throughput signal:
            # shallow = consumer keeping up, we can feed more;
            # deep    = consumer falling behind, back off ingestion.
            if queue_depth_fn is not None:
                depth = queue_depth_fn()   # 0.0 (empty) .. 1.0 (full)
                old_cap = _search_articles_cap
                if depth > 0.6:
                    _search_articles_cap = max(_search_articles_cap - 1, _SEARCH_ARTICLES_MIN)
                elif depth < 0.2:
                    _search_articles_cap = min(_search_articles_cap + 1, _SEARCH_ARTICLES_MAX)
                if _search_articles_cap != old_cap:
                    log.info(
                        "Search AIMD: queue %.0f%% full -- articles/query %d -> %d",
                        depth * 100, old_cap, _search_articles_cap,
                    )

            log.debug(
                "Search news: fetching %d queries x %d engines for %d active markets"
                " (cap %d articles/query)",
                len(queries), len(engines), len(markets), _search_articles_cap,
            )

            for q in queries:
                _q_count = 0
                _cap = _search_articles_cap  # snapshot cap for this query

                async def _capped(item: NewsItem, _cb=callback, _c=_cap) -> None:
                    nonlocal _q_count
                    if _q_count >= _c:
                        return
                    _q_count += 1
                    await _cb(_tag_source_hint_item(item, q, source_hint_query_mode))

                await asyncio.gather(
                    *[
                        poll_feed(url_builder(q), _capped, seen)
                        for _name, url_builder in engines
                    ],
                    return_exceptions=True,
                )

            # Trim dedup cache to bound memory
            while len(seen) > SEARCH_MAX_SEEN:
                seen.popitem(last=False)
            checkpoint_seen_ids(seen_state_path, seen, SEARCH_MAX_SEEN)

        except Exception as exc:
            log.warning("Search news monitor cycle error: %s", exc)

        await asyncio.sleep(poll_interval)
