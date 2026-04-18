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
from collections import OrderedDict
from typing import Callable, Awaitable, Sequence

from config import DISABLED_SOURCE_FAMILIES, MARKET_SERIES_BLOCKLIST_PREFIXES
from feeds import NewsItem
from feeds.rss_monitor import poll_feed
from kalshi import KalshiMarket
from utils.logger import get_logger

log = get_logger("search_monitor")

SEARCH_POLL_INTERVAL = 300   # seconds between full fetch cycles
SEARCH_MAX_QUERIES   = 25    # max distinct queries per cycle (prioritized by open_interest)
SEARCH_MAX_SEEN      = 2000  # dedup cache entry limit

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
    "coupon", "maturity", "spread", "basis",
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


def _markets_to_queries(markets: Sequence[KalshiMarket]) -> list[str]:
    """
    Convert active market titles to deduplicated search queries.

    Markets are ranked by open_interest * (1 - |price - 50| / 50), which
    prioritizes contested markets (price near 50c) with meaningful volume over
    already-decided or illiquid ones. Fresh news moves contested markets most.
    Each market contributes up to 4 key tokens. Markets with identical token
    sets are skipped (deduped). Capped at SEARCH_MAX_QUERIES.
    """
    sorted_markets = sorted(
        markets,
        key=lambda m: (
            getattr(m, "open_interest", 0)
            * (1.0 - abs(getattr(m, "yes_price", 50) - 50) / 50.0)
        ),
        reverse=True,
    )

    seen_sets: set[frozenset] = set()
    queries: list[str] = []

    for market in sorted_markets:
        if len(queries) >= SEARCH_MAX_QUERIES:
            break
        # Skip sports/blocklisted markets -- their tokens produce irrelevant queries.
        # Primary gate: series_ticker prefix.
        _ticker = (getattr(market, "series_ticker", None) or market.ticker).upper()
        if any(_ticker.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES):
            continue
        tokens = _tokenize(market.title)[:4]
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

    return queries


async def run_search_news_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    get_markets: Callable[[], Sequence[KalshiMarket]],
    poll_interval: int = SEARCH_POLL_INTERVAL,
    queue_depth_fn: "Callable[[], float] | None" = None,
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
    seen: OrderedDict = OrderedDict()
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
            queries = _markets_to_queries(markets)

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
                    await _cb(item)

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

        except Exception as exc:
            log.warning("Search news monitor cycle error: %s", exc)

        await asyncio.sleep(poll_interval)
