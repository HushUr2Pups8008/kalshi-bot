"""
Kalshi Geopolitical Trading Bot — entry point.

Concurrent tasks:
  1. RSS monitor        -- polls Reuters/AP/BBC/Al Jazeera every 60s
  2. Reddit monitor     -- polls r/worldnews, r/geopolitics, r/news (public JSON)
  3. WebSocket client   -- real-time Kalshi price feed
  4. Market cache refresh
  5. Daily reporter     -- logs summary every 24h, writes report file
  6. Auto-resolver      -- polls Kalshi every 30 min for settled markets

Paper trading runs INDEFINITELY until you explicitly confirm go-live.
No automatic switchover.

Commands:
    python main.py                          # run (paper mode until --go-live used)
    python main.py --report                 # print full paper trading report and exit
    python main.py --resolve TICKER YES|NO  # manually resolve a paper trade
    python main.py --go-live                # review report, then confirm live trading
    python main.py --credibility            # print source credibility table and exit
"""

import argparse
import asyncio
import dataclasses
import itertools
import signal
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from analysis import SignalAnalysis
from analysis.kelly import kelly_bet
from analysis.keyword_stats import KeywordStats
from analysis.market_matcher import MarketMatcher
from analysis.signal_analyzer import estimate_probability
from analysis.source_stats import SourceStats
from config import (cfg, DATA_DIR, PAPER_MIN_EDGE, PAPER_FLAT_CONTRACTS, VERSION,
                    FADE_TWEET_FEED_URLS,
                    MARKET_SERIES_BLOCKLIST_PREFIXES, MAX_NEWS_AGE_SECONDS,
                    EARLY_MAX_NEWS_AGE_SECONDS, EARLY_MAX_NEWS_AGE_BY_SOURCE,
                    EARLY_DROP_IF_NO_TIMESTAMP, DISABLED_NEWS_SOURCES,
                    FADE_PRICE_HIGH_THRESHOLD, FADE_PRICE_LOW_THRESHOLD,
                    DRIFT_ALERT_CENTS, DRIFT_LOG_COOLDOWN_SECS,
                    PRICE_MOVE_THRESHOLD_CENTS, PRICE_SEARCH_COOLDOWN_SECS,
                    PRICE_VELOCITY_WINDOW_SECS)
from feeds import NewsItem
from feeds.dedup import HeadlineDedup
from feeds.gdelt_monitor import run_gdelt_monitor
from feeds.search_news_monitor import run_search_news_monitor
from feeds.reddit_monitor import run_reddit_monitor
from feeds.rss_monitor import run_rss_monitor
from kalshi.rest_client import KalshiRestClient
from kalshi.websocket_client import KalshiWebSocketClient
from trading.executor import TradeExecutor
from trading.paper_trader import PaperTrader
from utils.logger import get_logger, emit_startup_banner

log = get_logger("main")

# Monotonic counter used as PriorityQueue tiebreaker so NewsItem objects
# are never compared against each other (dataclasses without __lt__ would raise).
_news_counter = itertools.count()


def _source_priority(source: str) -> int:
    """Lower number = higher priority. Wire services (RSS) = 1, Reddit = 2."""
    return 2 if source.startswith("r/") else 1


def _early_max_news_age_seconds_for_source(source: str) -> int:
    if source in EARLY_MAX_NEWS_AGE_BY_SOURCE:
        return EARLY_MAX_NEWS_AGE_BY_SOURCE[source]
    source_lower = source.strip().lower()
    for key, value in EARLY_MAX_NEWS_AGE_BY_SOURCE.items():
        if key.strip().lower() == source_lower:
            return value
    return EARLY_MAX_NEWS_AGE_SECONDS


def _is_disabled_news_source(source: str) -> bool:
    if source in DISABLED_NEWS_SOURCES:
        return True
    source_lower = source.strip().lower()
    return any(key.strip().lower() == source_lower for key in DISABLED_NEWS_SOURCES)


def _account_from_rsshub_url(url: str) -> str:
    """Extract account name from RSSHub URL. e.g. .../user/Kalshi -> 'Kalshi'"""
    if not url:
        return "unknown"
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        return parts[-1]
    return parts[0]  # fallback: domain or bare token


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kalshi Geopolitical Trading Bot")
    p.add_argument("--report",      action="store_true",
                   help="Print paper trading report and exit")
    p.add_argument("--resolve",     nargs=2, metavar=("TICKER", "RESULT"),
                   help="Resolve a paper trade: --resolve TICKER YES|NO")
    p.add_argument("--go-live",     action="store_true",
                   help="Review report then confirm switching to live trading")
    p.add_argument("--credibility", action="store_true",
                   help="Print source credibility table and exit")
    return p.parse_args()


class TradingBot:
    def __init__(self):
        self.rest          = KalshiRestClient()
        self.ws            = KalshiWebSocketClient()
        self.matcher       = MarketMatcher(self.rest)
        self.paper         = PaperTrader()
        self.executor      = TradeExecutor(self.rest, self.paper)
        # Wire live loss limit shutdown: executor calls this when the session loss
        # threshold is breached. Uses asyncio.create_task so it's safe from any context.
        self.executor.set_shutdown_callback(
            lambda: asyncio.create_task(_shutdown(self))
        )
        self.source_stats  = SourceStats(db_path=DATA_DIR / "paper_trades.db")
        self.keyword_stats = KeywordStats(DATA_DIR / "paper_trades.db")
        self.ws.on_price_update(self._on_price_update)
        # Priority queue decouples feed pollers from slow LLM inference.
        # RSS/wire services (priority 1) are processed before Reddit (priority 2).
        # Tuple layout: (priority, seq, news) — seq prevents NewsItem comparison.
        self._news_queue: asyncio.PriorityQueue[tuple[int, int, NewsItem]] = asyncio.PriorityQueue(maxsize=2000)
        # Cross-source dedup: Reuters/AP/BBC often publish the same story within
        # minutes. Skip near-identical headlines seen in the last 15 minutes.
        self._dedup = HeadlineDedup()
        # Previous WS mid-price per ticker -- used to detect threshold crossings
        # for the price-based fade signal.
        self._ws_prev_prices: dict[str, float] = {}
        # Loop A: price velocity tracker (ticker -> deque of (monotonic_ts, mid_price))
        # Used to detect >10c moves in the last 5 minutes and trigger targeted search.
        self._ws_velocity: dict[str, deque] = defaultdict(lambda: deque(maxlen=60))
        self._last_search_triggered: dict[str, float] = {}  # ticker -> monotonic
        # Loop C: drift log cooldown (ticker -> last log monotonic time)
        self._last_drift_logged: dict[str, float] = {}
        # Loop D: previous market ticker set for new-market detection
        self._known_market_tickers: set[str] = set()

    # ── News pipeline ─────────────────────────────────────────────────────────

    def _source_family(self, source: str) -> str:
        if not source:
            return "other"

        s = source.strip()

        if s.endswith(" - BingNews"):
            return "bing_news_query"
        if s.endswith(" - Google News"):
            return "google_news_query"
        if s.startswith("r/"):
            return "reddit"

        lower = s.lower()
        if any(k in lower for k in [
            "reuters", "bbc", "nyt", "guardian",
            "al jazeera", "france 24", "deutsche welle",
            "defense one", "foreign policy", "politics"
        ]):
            return "publisher_rss"

        if s == "Just In News":
            return "direct_feed"

        return "other"

    def _is_disabled_source_family(self, source: str) -> bool:
        from config import DISABLED_SOURCE_FAMILIES
        family = self._source_family(source)
        return family in DISABLED_SOURCE_FAMILIES

    async def _enqueue_news(self, news: NewsItem) -> None:
        """Non-blocking enqueue from feed pollers. Drops items if queue is full."""
        from utils.logger import trade_log
        source = news.source
        headline = news.headline
        if self._is_disabled_source_family(source):
            trade_log.log_early_stale_drop(
                reason="disabled_source_family",
                source=source,
                headline=headline,
            )
            return
        if _is_disabled_news_source(source):
            trade_log.log_early_stale_drop(
                reason="disabled_source",
                source=source,
                headline=headline,
            )
            log.debug("News dropped for disabled source [%s]: %s", source, headline[:60])
            return
        published = getattr(news, "published", None)
        if not isinstance(published, datetime) or published.tzinfo is None:
            if EARLY_DROP_IF_NO_TIMESTAMP:
                trade_log.log_early_stale_drop(
                    reason="missing_timestamp",
                    source=source,
                    headline=headline,
                )
                log.debug("News dropped for missing/invalid timestamp: %s", headline[:60])
                return
        else:
            age_secs = (datetime.now(timezone.utc) - published).total_seconds()
            threshold_secs = _early_max_news_age_seconds_for_source(source)
            if age_secs > threshold_secs:
                trade_log.log_early_stale_drop(
                    reason="stale_by_source_policy",
                    source=source,
                    headline=headline,
                    age_seconds=age_secs,
                    threshold_seconds=threshold_secs,
                )
                log.debug(
                    "Early stale news dropped (%.0fs > %ds for %s): %s",
                    age_secs, threshold_secs, source, headline[:60],
                )
                return
        if self._dedup.is_duplicate(headline, source=source):
            return
        priority = _source_priority(source)
        try:
            self._news_queue.put_nowait((priority, next(_news_counter), news))
        except asyncio.QueueFull:
            log.warning("News queue full (%d items) -- dropping: %s",
                        self._news_queue.maxsize, headline[:60])

    async def _news_consumer_task(self) -> None:
        """Drain the priority news queue, processing one item at a time."""
        processed = 0
        while True:
            _priority, _seq, news = await self._news_queue.get()
            try:
                await self.on_news_item(news)
            except Exception as exc:
                log.error("Unhandled error processing news item: %s", exc)
            finally:
                self._news_queue.task_done()
                processed += 1
                if processed % 10 == 0:
                    log.debug(
                        "News consumer: %d processed, queue depth=%d",
                        processed, self._news_queue.qsize(),
                    )

    async def on_news_item(self, news: NewsItem) -> None:
        log.info("[NEWS] [%s] %s", news.source, news.headline[:100])
        self.source_stats.increment_posts(news.source)

        candidates = await self.matcher.find_candidates(news)
        if not candidates:
            log.debug("No matching markets for: %s", news.headline[:60])
            return

        for market, match_score in candidates:
            await self._process_candidate(news, market, match_score)

    async def _process_candidate(self, news: NewsItem, market, match_score: float) -> None:
        from utils.logger import trade_log
        # Staleness check: skip if the article is too old when we process it.
        # With a queue, items can sit for several minutes; old news is already priced in.
        age_secs = (datetime.now(timezone.utc) - news.published).total_seconds()
        market_yes_price = market.yes_price
        if age_secs > MAX_NEWS_AGE_SECONDS:
            trade_log.log_analysis_rejected(
                reason="stale_news",
                ticker=market.ticker,
                source=news.source,
                headline=news.headline,
                match_score=match_score,
                age_seconds=age_secs,
            )
            log.debug(
                "Stale news skipped (%.0fs > %ds): %s",
                age_secs, MAX_NEWS_AGE_SECONDS, news.headline[:60],
            )
            return

        # Use WS price if available -- defensive copy before mutation so the shared cache
        # object is never modified in-place (avoids race with concurrent WS price updates)
        ws_price = self.ws.get_yes_price(market.ticker)
        if ws_price is not None:
            market = dataclasses.replace(market)
            market.yes_price = ws_price
            market.yes_bid   = max(1, ws_price - 1)
            market.yes_ask   = min(99, ws_price + 1)
            price_source = "ws"
        else:
            price_source = "cache"

        log.debug(
            "[ANALYSIS] candidate ticker=%s source=%s match_score=%.3f age=%.0fs "
            "price_source=%s yes_price=%.1fc cache_yes_price=%.1fc",
            market.ticker,
            news.source,
            match_score,
            age_secs,
            price_source,
            market.yes_price,
            market_yes_price,
        )

        estimated_prob, confidence, keywords, reasoning, llm_dir, llm_mag, llm_conf = \
            await estimate_probability(news, market, keyword_stats=self.keyword_stats)
        if not keywords:
            trade_log.log_analysis_rejected(
                reason="no_keywords",
                ticker=market.ticker,
                source=news.source,
                headline=news.headline,
                match_score=match_score,
            )
            log.debug(
                "[ANALYSIS] no_signal ticker=%s source=%s match_score=%.3f",
                market.ticker,
                news.source,
                match_score,
            )
            return

        self.source_stats.increment_signals(news.source)
        edge = estimated_prob - market.yes_prob
        side = "yes" if edge > 0 else "no"

        # Get source credibility multiplier
        source_mult = self.paper.credibility.get_multiplier(news.source)

        # Dynamic max bet based on current notional bankroll
        notional   = self.paper.get_notional_bankroll()
        max_bet    = cfg.dynamic_max_bet(notional)
        min_edge   = PAPER_MIN_EDGE if cfg.is_paper_trading else cfg.min_edge

        # Days to close -- used by kelly_bet() for time discount
        from analysis.market_matcher import _days_to_close
        days_to_close = _days_to_close(market.close_time) or 14.0

        kelly_frac, kelly_dollars, capped_dollars = kelly_bet(
            estimated_probability=estimated_prob,
            market_price_cents=market.yes_price,
            bankroll=notional,
            kelly_fraction=cfg.kelly_fraction,
            max_bet_dollars=max_bet,
            min_bet_dollars=cfg.min_bet_dollars,
            min_edge=min_edge,
            source_multiplier=source_mult,
            confidence=confidence,
            days_to_close=days_to_close,
            time_discount_half_life=cfg.time_discount_half_life,
            time_discount_floor=cfg.time_discount_floor,
        )

        log.debug(
            "[ANALYSIS] decision_input ticker=%s source=%s side=%s edge=%+.4f "
            "est_prob=%.3f market_prob=%.3f confidence=%.2f keywords=%d "
            "src_mult=%.2f kelly=$%.2f capped=$%.2f llm_dir=%s llm_mag=%s",
            market.ticker,
            news.source,
            side.upper(),
            edge,
            estimated_prob,
            market.yes_prob,
            confidence,
            len(keywords),
            source_mult,
            kelly_dollars,
            capped_dollars,
            llm_dir or "-",
            llm_mag or "-",
        )

        trade_log.log_signal(
            source=news.source,
            headline=news.headline,
            url=news.url,
            signal_strength=abs(edge),
            keywords_matched=keywords,
        )
        trade_log.log_opportunity(
            ticker=market.ticker,
            market_title=market.title,
            market_yes_price=market.yes_price,
            estimated_probability=estimated_prob,
            edge=edge,
            kelly_fraction=kelly_frac,
            kelly_dollars=kelly_dollars,
            capped_dollars=capped_dollars,
            side=side,
            reasoning=reasoning,
        )
        self.source_stats.increment_opportunities(news.source)

        if capped_dollars <= 0:
            if not cfg.is_paper_trading:
                log.debug("No bet for %s: edge=%+.4f below threshold", market.ticker, edge)
                return
            # Paper mode uses flat contracts regardless of Kelly sizing.
            # Set a placeholder so the executor runs its own edge/position checks
            # and logs a proper SKIPPED event if needed.
            capped_dollars = PAPER_FLAT_CONTRACTS * max(1, min(99, int(market.yes_price))) / 100.0
            log.debug(
                "[ANALYSIS] paper_placeholder ticker=%s placeholder=$%.2f",
                market.ticker,
                capped_dollars,
            )

        analysis = SignalAnalysis(
            news_item=news,
            market=market,
            estimated_probability=estimated_prob,
            market_yes_price=market.yes_price,
            edge=edge,
            side=side,
            kelly_fraction=kelly_frac,
            kelly_dollars=kelly_dollars,
            capped_dollars=capped_dollars,
            keywords_matched=keywords,
            reasoning=reasoning,
            confidence=confidence,
            match_score=match_score,
            signal_type="news",
            llm_direction=llm_dir,
            llm_magnitude=llm_mag,
            llm_confidence=llm_conf,
        )

        log.debug(
            "[ANALYSIS] handoff ticker=%s side=%s signal_type=%s "
            "capped=$%.2f match_score=%.3f",
            analysis.market.ticker,
            analysis.side.upper(),
            analysis.signal_type,
            analysis.capped_dollars,
            analysis.match_score,
        )

        trade_id = await self.executor.execute(analysis)
        if trade_id:
            self.source_stats.increment_trades(news.source)
        else:
            log.debug(
                "[ANALYSIS] no_execution ticker=%s source=%s",
                market.ticker,
                news.source,
            )
        self.ws.watch([market.ticker])

    # ── Fade tweet pipeline ────────────────────────────────────────────────────

    async def _on_fade_tweet(self, tweet: NewsItem) -> None:
        """Callback for the fade-tweet RSS monitor. Extracts source account and routes."""
        account = _account_from_rsshub_url(tweet.url or "")
        log.info("[FADE] [%s] %s", account, tweet.headline[:100])
        await self._process_fade_tweet(tweet, account)

    async def _process_fade_tweet(self, tweet: NewsItem, account: str) -> None:
        """
        Separate code path for prediction-market hype tweets.

        Detects hype/ATH patterns and fades them: bullish tweet → buy NO.
        Searches ALL active markets (geo + sports) so we can compare efficacy
        across both categories during the paper trading validation phase.
        All trades are tagged [FADE/GEO/@account] or [FADE/SPORTS/@account] for DB analysis.
        """
        from analysis.fade_signal import detect_fade_pattern

        pattern = detect_fade_pattern(tweet)
        if not pattern:
            log.debug("[FADE] [%s] No fade pattern: %s", account, tweet.headline[:80])
            return

        candidates = await self.matcher.find_all_candidates(tweet)
        if not candidates:
            log.debug("[FADE] [%s] No matching market: %s", account, tweet.headline[:80])
            return

        market, score = candidates[0]

        ws_price = self.ws.get_yes_price(market.ticker)
        if ws_price is not None:
            market = dataclasses.replace(market)
            market.yes_price = ws_price
            market.yes_bid   = max(1, ws_price - 1)
            market.yes_ask   = min(99, ws_price + 1)

        # Fade: bullish tweet → buy NO (short the hype)
        fade_side = "no" if pattern == "bullish" else "yes"
        edge_sign = -1.0 if fade_side == "no" else 1.0
        fake_edge = edge_sign * (PAPER_MIN_EDGE + 0.01)   # just above threshold

        # Tag by market category for later sports-vs-geo win-rate comparison
        _ticker = (market.series_ticker or market.ticker).upper()
        is_sports = any(_ticker.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES)
        category  = "SPORTS" if is_sports else "GEO"

        analysis = SignalAnalysis(
            news_item=tweet,
            market=market,
            estimated_probability=market.yes_prob + fake_edge,
            market_yes_price=market.yes_price,
            edge=fake_edge,
            side=fade_side,
            kelly_fraction=cfg.kelly_fraction,
            kelly_dollars=0.0,
            capped_dollars=0.0,
            keywords_matched=[],
            reasoning=(
                f"[FADE/{category}/@{account}] {pattern}: "
                f"{tweet.headline[:80]}"
            ),
            confidence=0.3,
            match_score=score,
            signal_type="fade_tweet",
        )
        log.info("[FADE/%s/@%s] %s | %s | pattern=%s | match_score=%.3f",
                 category, account, market.ticker, fade_side.upper(), pattern, score)
        await self.executor.execute(analysis)
        self.ws.watch([market.ticker])

    async def _on_price_update(self, ticker: str, yes_bid: float, yes_ask: float) -> None:
        now_mid  = (yes_bid + yes_ask) / 2.0
        now_mono = time.monotonic()
        log.debug("[WS] %s bid=%.1fc ask=%.1fc mid=%.1fc", ticker, yes_bid, yes_ask, now_mid)

        prev_mid = self._ws_prev_prices.get(ticker)
        self._ws_prev_prices[ticker] = now_mid

        if prev_mid is None:
            # First tick: seed the velocity deque and return (no crossing yet)
            self._ws_velocity[ticker].append((now_mono, now_mid))
            return

        # ── Loop A: price velocity -> targeted search ─────────────────────────
        # Track rolling price history; trigger a targeted news search if price
        # has moved >= PRICE_MOVE_THRESHOLD_CENTS within the velocity window.
        vq = self._ws_velocity[ticker]
        vq.append((now_mono, now_mid))
        # Purge ticks older than PRICE_VELOCITY_WINDOW_SECS
        cutoff = now_mono - PRICE_VELOCITY_WINDOW_SECS
        while vq and vq[0][0] < cutoff:
            vq.popleft()
        if vq:
            oldest_price = vq[0][1]
            price_move   = abs(now_mid - oldest_price)
            last_search  = self._last_search_triggered.get(ticker, 0.0)
            if (price_move >= PRICE_MOVE_THRESHOLD_CENTS
                    and now_mono - last_search >= PRICE_SEARCH_COOLDOWN_SECS):
                self._last_search_triggered[ticker] = now_mono
                log.info(
                    "[PRICE_MOVE] %s moved %.1fc in %.0fs -- triggering targeted search",
                    ticker, price_move, PRICE_VELOCITY_WINDOW_SECS,
                )
                asyncio.create_task(self._trigger_targeted_search(ticker))

        # ── Loop C: open position drift logging ───────────────────────────────
        # If we have an open paper position on this ticker and the price has
        # drifted >= DRIFT_ALERT_CENTS from entry, log a POSITION_DRIFT event.
        open_positions = self.paper.portfolio.open_positions(ticker)
        if open_positions:
            last_drift = self._last_drift_logged.get(ticker, 0.0)
            if now_mono - last_drift >= DRIFT_LOG_COOLDOWN_SECS:
                for pos in open_positions:
                    drift = now_mid - pos.market_yes_price
                    if abs(drift) >= DRIFT_ALERT_CENTS:
                        self._last_drift_logged[ticker] = now_mono
                        from utils.logger import trade_log as _tl
                        _tl.log_position_drift(
                            ticker=ticker,
                            entry_price=pos.market_yes_price,
                            current_price=now_mid,
                            drift_cents=drift,
                            side=pos.side,
                            open_since=pos.ts,
                        )
                        log.info(
                            "[DRIFT] %s: entry=%.1fc now=%.1fc drift=%+.1fc (%s)",
                            ticker, pos.market_yes_price, now_mid, drift, pos.side.upper(),
                        )
                        break  # one log event per ticker per cooldown period

        # ── Fade signal (existing) ────────────────────────────────────────────
        from analysis.fade_signal import detect_price_fade
        crossing = detect_price_fade(
            ticker, prev_mid, now_mid,
            FADE_PRICE_HIGH_THRESHOLD, FADE_PRICE_LOW_THRESHOLD,
        )
        if crossing is not None:
            await self._process_price_fade(ticker, crossing, now_mid, yes_bid, yes_ask)

    async def _trigger_targeted_search(self, ticker: str) -> None:
        """
        Loop A: fetch targeted Google News + GDELT results for a single market
        that just had a significant price move. Emits articles through the normal
        news queue so they go through the full signal pipeline.

        Rate-limiting is handled by the caller (_on_price_update checks
        _last_search_triggered before calling this).
        """
        try:
            markets = await self.matcher._cache.get_markets()
            market  = next((m for m in markets if m.ticker == ticker), None)
            if market is None:
                log.debug("[PRICE_MOVE] %s not in geo cache, skipping targeted search", ticker)
                return

            from feeds.search_news_monitor import _markets_to_queries, _gnews_url, _bing_url
            from feeds.rss_monitor import poll_feed
            from collections import OrderedDict

            queries = _markets_to_queries([market])
            if not queries:
                log.debug("[PRICE_MOVE] %s: no query tokens, skipping", ticker)
                return

            seen: OrderedDict = OrderedDict()
            log.debug("[PRICE_MOVE] %s: targeted search query='%s'", ticker, queries[0])
            for q in queries[:1]:  # single query per triggered search
                await asyncio.gather(
                    poll_feed(_gnews_url(q), self._enqueue_news, seen),
                    poll_feed(_bing_url(q),  self._enqueue_news, seen),
                    return_exceptions=True,
                )
        except Exception as exc:
            log.warning("[PRICE_MOVE] Targeted search failed for %s: %s", ticker, exc)

    async def _process_price_fade(
        self,
        ticker: str,
        crossing: str,
        now_mid: float,
        yes_bid: float,
        yes_ask: float,
    ) -> None:
        """
        Execute a price-based fade when a geo market crosses the high/low threshold.

        high_cross (>=85c) -> buy NO  (fade the spike)
        low_cross  (<=15c) -> buy YES (fade the collapse)
        """
        # Only fade geo/political markets
        _t = ticker.upper()
        if any(_t.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES):
            log.debug("[PRICE_FADE] Skipping sports ticker %s", ticker)
            return

        markets = await self.matcher._cache.get_markets()
        market = next((m for m in markets if m.ticker == ticker), None)
        if market is None:
            log.debug("[PRICE_FADE] %s not in geo cache, skipping", ticker)
            return

        # Update market price to reflect live WS data -- defensive copy first
        market = dataclasses.replace(market)
        market.yes_price = now_mid
        market.yes_bid   = max(1, yes_bid)
        market.yes_ask   = min(99, yes_ask)

        fade_side = "no" if crossing == "high_cross" else "yes"
        edge_sign = -1.0 if fade_side == "no" else 1.0
        fake_edge = edge_sign * (PAPER_MIN_EDGE + 0.01)

        threshold_val = (FADE_PRICE_HIGH_THRESHOLD if crossing == "high_cross"
                         else FADE_PRICE_LOW_THRESHOLD)
        direction = "above" if crossing == "high_cross" else "below"

        synthetic_news = NewsItem(
            headline=(f"[PRICE_FADE] {ticker} crossed {direction} {threshold_val}c"
                      f" (mid={now_mid:.1f}c)"),
            url=f"kalshi://price_fade/{ticker}",
            source="price_fade",
        )

        analysis = SignalAnalysis(
            news_item=synthetic_news,
            market=market,
            estimated_probability=market.yes_prob + fake_edge,
            market_yes_price=market.yes_price,
            edge=fake_edge,
            side=fade_side,
            kelly_fraction=cfg.kelly_fraction,
            kelly_dollars=0.0,
            capped_dollars=0.0,
            keywords_matched=[],
            reasoning=(
                f"[PRICE_FADE] {ticker} {crossing}: mid={now_mid:.1f}c "
                f"threshold={threshold_val}c -> {fade_side.upper()}"
            ),
            confidence=0.4,
            signal_type="price_fade",
        )

        log.info("[PRICE_FADE] %s | %s | mid=%.1fc | threshold=%dc | side=%s",
                 ticker, crossing, now_mid, threshold_val, fade_side.upper())
        await self.executor.execute(analysis)

    # ── Scheduled tasks ───────────────────────────────────────────────────────

    async def _daily_report_task(self) -> None:
        from utils.logger import LOG_REPORTS_DIR
        while True:
            await asyncio.sleep(86_400)
            self.paper.daily_summary()
            report      = self.paper.generate_report()
            report_path = LOG_REPORTS_DIR / f"report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.txt"
            report_path.write_text(report, encoding="utf-8")
            log.info("Daily report written to %s", report_path)

    async def _warm_ws_subscriptions(self) -> None:
        """
        Background task: wait for the market cache to populate on first run,
        then subscribe the WS to every geo market ticker so the price-fade
        detector has live prices from the start.
        """
        try:
            markets = await self.matcher._cache.get_markets()
            tickers = [m.ticker for m in markets]
            if tickers:
                self.ws.watch(tickers)
                log.info("WS: subscribed to %d geo market tickers for price fade",
                         len(tickers))
        except Exception as exc:
            log.warning("WS price-fade warm-up failed: %s", exc)

    async def _auto_resolve_task(self) -> None:
        """Poll Kalshi API for settled markets and auto-resolve open paper trades.

        Runs every 30 minutes. For each unique ticker with open paper trades,
        fetches the market from Kalshi. If status is 'finalized' or 'settled'
        and a result is present, resolves all open trades for that ticker.
        """
        _RESOLVE_INTERVAL = 1800  # 30 minutes
        while True:
            await asyncio.sleep(_RESOLVE_INTERVAL)
            try:
                await self._check_and_resolve()
            except Exception as exc:
                log.error("Auto-resolve cycle failed: %s", exc)

    async def _check_and_resolve(self) -> None:
        """Single pass: check all open paper trade tickers for settlement."""
        self.source_stats.flush()
        open_trades = self.paper._conn.execute(
            "SELECT DISTINCT ticker FROM paper_trades WHERE resolved = 0"
        ).fetchall()
        if not open_trades:
            return

        tickers = [row[0] for row in open_trades]
        log.debug("Auto-resolve: checking %d open tickers", len(tickers))

        loop = asyncio.get_running_loop()
        resolved_count = 0
        for ticker in tickers:
            try:
                market = await loop.run_in_executor(
                    None, self.rest.get_market, ticker
                )
                if market is None:
                    continue
                if market.status not in ("finalized", "settled"):
                    continue
                result_str = (market.result or "").lower().strip()
                if result_str not in ("yes", "no"):
                    log.warning(
                        "Auto-resolve: %s is %s but result=%r -- skipping",
                        ticker, market.status, market.result,
                    )
                    continue
                resolved_yes = result_str == "yes"
                self.paper.resolve_market(ticker, resolved_yes)
                resolved_count += 1
                log.info(
                    "Auto-resolve: %s -> %s",
                    ticker, "YES" if resolved_yes else "NO",
                )
            except Exception as exc:
                log.warning("Auto-resolve: failed to check %s: %s", ticker, exc)

        if resolved_count:
            log.info(
                "Auto-resolve: resolved %d/%d tickers | bankroll=$%.2f",
                resolved_count, len(tickers),
                self.paper.get_notional_bankroll(),
            )

    async def _market_refresh_task(self) -> None:
        from config import MARKET_CACHE_TTL_SECONDS
        while True:
            await asyncio.sleep(MARKET_CACHE_TTL_SECONDS)
            await self.matcher.refresh_cache()
            # Re-subscribe WS to catch any newly listed geo markets
            try:
                markets    = await self.matcher._cache.get_markets()
                new_tickers = {m.ticker for m in markets}
                self.ws.watch(list(new_tickers))

                # Loop D: detect newly listed markets and proactively search for news
                if self._known_market_tickers:
                    added = new_tickers - self._known_market_tickers
                    for m in markets:
                        if m.ticker not in added:
                            continue
                        from utils.logger import trade_log as _tl
                        _tl.log_new_market(
                            ticker=m.ticker,
                            title=m.title,
                            series_ticker=m.series_ticker,
                        )
                        log.info(
                            "[NEW_MARKET] %s listed: '%s' -- triggering targeted search",
                            m.ticker, m.title[:60],
                        )
                        asyncio.create_task(self._trigger_targeted_search(m.ticker))
                self._known_market_tickers = new_tickers
            except Exception as exc:
                log.warning("Market refresh task error: %s", exc)

    async def _log_maintenance_task(self) -> None:
        """
        Daily housekeeping for the logs/ directory.

        Retention rules:
          logs/reports/report_*.txt       -- 90 days
          logs/reports/analysis_*.txt     -- 30 days
          logs/service_stderr-*.log       -- 30 days (NSSM archives in root)
          logs/trades/archive/*.jsonl.gz  -- 12 months

        Monthly rotation:
          When the month rolls over, gzip-compresses trades.jsonl to
          logs/trades/archive/trades-YYYYMM.jsonl.gz and truncates it in-place.
          The active file is never deleted -- only truncated.

        Migration sweep (one-time, idempotent):
          Moves any bot.log.YYYY-MM-DD / errors.log.YYYY-MM-DD files still
          in logs/ root to logs/app/ so the TimedRotatingFileHandler's
          backupCount=90 enforcement covers them.
        """
        import gzip
        import shutil as _shutil
        from config import LOGS_DIR
        from utils.logger import _LOG_APP_DIR, _LOG_TRADES_DIR, _LOG_REPORTS_DIR, _LOG_ARCHIVE_DIR

        _MAINT_INTERVAL = 86_400  # 24 hours
        _INITIAL_DELAY  = 60      # 1 minute -- let the bot fully start first

        await asyncio.sleep(_INITIAL_DELAY)

        while True:
            now = datetime.now(timezone.utc)
            deleted_count = 0
            moved_count   = 0

            try:
                # ── Retention: reports/ ───────────────────────────────────────
                for pattern, max_days in (("report_*.txt", 90), ("analysis_*.txt", 30)):
                    for p in _LOG_REPORTS_DIR.glob(pattern):
                        age_days = (now.timestamp() - p.stat().st_mtime) / 86_400
                        if age_days > max_days:
                            p.unlink()
                            deleted_count += 1
                            log.info("[LOG_MAINT] Deleted old report: %s (%.0f days)", p.name, age_days)

                # ── Retention: NSSM service log archives in logs/ root ────────
                for p in LOGS_DIR.glob("service_stderr-*.log"):
                    age_days = (now.timestamp() - p.stat().st_mtime) / 86_400
                    if age_days > 30:
                        p.unlink()
                        deleted_count += 1
                        log.info("[LOG_MAINT] Deleted old service log: %s (%.0f days)", p.name, age_days)

                # ── Retention: trade archives older than 12 months ────────────
                for p in _LOG_ARCHIVE_DIR.glob("*.jsonl.gz"):
                    age_days = (now.timestamp() - p.stat().st_mtime) / 86_400
                    if age_days > 366:
                        p.unlink()
                        deleted_count += 1
                        log.info("[LOG_MAINT] Deleted old trade archive: %s (%.0f days)", p.name, age_days)

                # ── Monthly trades.jsonl rotation ─────────────────────────────
                # Rotates on the 1st of each month: archives last month's records
                # to trades/archive/trades-YYYYMM.jsonl.gz and truncates in-place.
                # Only fires on the 1st so mid-month restarts never prematurely
                # archive the current month's active data.
                trades_path = _LOG_TRADES_DIR / "trades.jsonl"
                if now.day == 1 and trades_path.exists() and trades_path.stat().st_size > 0:
                    prev_month   = (now.replace(day=1) - __import__("datetime").timedelta(days=1)).strftime("%Y%m")
                    archive_name = f"trades-{prev_month}.jsonl.gz"
                    archive_path = _LOG_ARCHIVE_DIR / archive_name
                    if not archive_path.exists():
                        with open(trades_path, "rb") as f_in:
                            with gzip.open(archive_path, "wb") as f_out:
                                _shutil.copyfileobj(f_in, f_out)
                        with open(trades_path, "w", encoding="utf-8"):
                            pass  # truncate in-place -- never delete the active file
                        log.info(
                            "[LOG_MAINT] Rotated trades.jsonl -> %s (%.1f KB compressed)",
                            archive_name,
                            archive_path.stat().st_size / 1024,
                        )

                # ── Migration sweep ───────────────────────────────────────────
                # Move rotated app logs from root to logs/app/
                for prefix in ("bot.log.", "errors.log."):
                    for p in LOGS_DIR.glob(f"{prefix}????-??-??"):
                        dest = _LOG_APP_DIR / p.name
                        if not dest.exists():
                            p.rename(dest)
                            moved_count += 1
                            log.debug("[LOG_MAINT] Migrated %s -> app/", p.name)

                # Move orphaned active logs (bot.log / errors.log in root).
                # After a restart from <v0.25.0 these are no longer written --
                # the new service opens logs/app/bot.log instead. Safe to move.
                for name in ("bot.log", "errors.log"):
                    p = LOGS_DIR / name
                    dest = _LOG_APP_DIR / name
                    if p.exists() and p.stat().st_size > 0:
                        if not dest.exists() or dest.stat().st_size == 0:
                            p.rename(dest)
                            moved_count += 1
                            log.info("[LOG_MAINT] Migrated orphaned %s -> app/", name)
                        elif (now.timestamp() - p.stat().st_mtime) > 3600:
                            # dest has data AND root copy hasn't been written to in
                            # over an hour -- it's a true orphan, safe to delete
                            p.unlink()
                            deleted_count += 1
                            log.info("[LOG_MAINT] Removed stale orphan %s from root", name)

                # Move old report_*.txt and analysis_*.txt from root to logs/reports/
                for pattern in ("report_*.txt", "analysis_*.txt"):
                    for p in LOGS_DIR.glob(pattern):
                        dest = _LOG_REPORTS_DIR / p.name
                        if not dest.exists():
                            p.rename(dest)
                            moved_count += 1
                            log.debug("[LOG_MAINT] Migrated %s -> reports/", p.name)

                # Move trades.jsonl from root to logs/trades/ (one-time)
                old_trades = LOGS_DIR / "trades.jsonl"
                new_trades = _LOG_TRADES_DIR / "trades.jsonl"
                if old_trades.exists() and old_trades.stat().st_size > 0:
                    if not new_trades.exists() or new_trades.stat().st_size == 0:
                        old_trades.rename(new_trades)
                        moved_count += 1
                        log.info("[LOG_MAINT] Migrated trades.jsonl -> trades/")
                    else:
                        # Both exist with data -- append old root content to new, then remove
                        with open(old_trades, "rb") as src, open(new_trades, "ab") as dst:
                            _shutil.copyfileobj(src, dst)
                        old_trades.unlink()
                        moved_count += 1
                        log.info("[LOG_MAINT] Merged root trades.jsonl -> trades/ and removed original")

                if deleted_count or moved_count:
                    log.info(
                        "[LOG_MAINT] Maintenance complete: %d deleted, %d migrated",
                        deleted_count, moved_count,
                    )
                else:
                    log.debug("[LOG_MAINT] Maintenance pass complete -- nothing to clean")

            except Exception as exc:
                log.warning("[LOG_MAINT] Maintenance pass failed: %s", exc)

            await asyncio.sleep(_MAINT_INTERVAL)

    async def _subreddit_discovery_task(self) -> None:
        """
        Periodically discover new candidate subreddits via Reddit post search.
        Waits 5 minutes on startup so the market cache is warm before the first pass.
        """
        from feeds.subreddit_discovery import run_discovery_pass
        await asyncio.sleep(300)  # Let market cache warm up
        while True:
            try:
                markets = self.matcher._cache._markets
                if markets:
                    count = await run_discovery_pass(markets, DATA_DIR / "paper_trades.db")
                    if count:
                        log.info("[DISCOVERY] Found %d new candidate subreddits", count)
                else:
                    log.debug("[DISCOVERY] Market cache empty, skipping discovery pass")
            except Exception as exc:
                log.warning("[DISCOVERY] Discovery pass failed: %s", exc)
            from config import SUBREDDIT_DISCOVERY_INTERVAL_SECS
            await asyncio.sleep(SUBREDDIT_DISCOVERY_INTERVAL_SECS)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _make_subreddit_getter(self) -> "Callable[[], Awaitable[list[str]]]":
        """
        Return an async callable that computes the subreddit list from the live
        market cache. Called once per Reddit poll cycle (~300s) to enable
        adaptive selection: topic subreddits are added only when matching markets
        are open. Falls back to REDDIT_CORE_SUBREDDITS if the cache is cold or
        the selector raises.
        """
        from feeds.subreddit_selector import select_subreddits

        async def _get() -> list[str]:
            try:
                markets = self.matcher._cache._markets
                result = select_subreddits(
                    markets,
                    source_stats=self.source_stats,
                    db_path=DATA_DIR / "paper_trades.db",
                )
                log.debug(
                    "Subreddit selector: %d subs for this cycle (%d active markets)",
                    len(result), len(markets),
                )
                return result
            except Exception as exc:
                log.warning("Subreddit selector error, using core list: %s", exc)
                from config import REDDIT_CORE_SUBREDDITS
                return list(REDDIT_CORE_SUBREDDITS)

        return _get

    def _make_market_getter(self) -> "Callable[[], list]":
        """Sync callable returning the live market cache for search/GDELT query generation."""
        def _get() -> list:
            return self.matcher._cache._markets
        return _get

    async def _check_llm_health(self) -> None:
        """Log LLM availability at startup so the operator knows what's active."""
        import aiohttp
        base = cfg.ollama_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{base}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    if r.status == 200:
                        data   = await r.json()
                        models = [m["name"] for m in data.get("models", [])]
                        log.info("LLM: Ollama running | model=%s | available=%s",
                                 cfg.ollama_model, models)
                        return
        except Exception:
            pass
        if cfg.anthropic_api_key:
            log.info("LLM: Ollama not reachable -- Anthropic fallback active")
        else:
            log.warning("LLM: Ollama not reachable and no ANTHROPIC_API_KEY "
                        "-- keyword scoring only (reduced signal accuracy)")

    async def run(self) -> None:
        notional = self.paper.get_notional_bankroll()
        max_bet  = cfg.dynamic_max_bet(notional)

        emit_startup_banner(VERSION, cfg.ollama_model, cfg.kalshi_env)
        log.info("=" * 60)
        log.info("Kalshi Trading Bot v%s starting", VERSION)
        log.info("Mode:             %s", "PAPER TRADING" if cfg.is_paper_trading else "LIVE TRADING")
        log.info("Notional bankroll: $%.2f", notional)
        log.info("Max bet (dynamic): $%.2f  (%.0f%% of bankroll, hard cap $%.2f)",
                 max_bet, cfg.max_bet_pct_bankroll * 100, cfg.max_bet_hard_cap)
        log.info("Kelly fraction:   %.0f%%", cfg.kelly_fraction * 100)
        if cfg.is_paper_trading:
            log.info("Paper trading -- run `python main.py --go-live` when ready to switch.")
            log.info(
                "Go-live gates: min_resolved=%d, min_win_rate=%.0f%%, max_drawdown=%.0f%%",
                cfg.go_live_min_resolved,
                cfg.go_live_min_win_rate * 100,
                cfg.go_live_max_drawdown_pct * 100,
            )
        log.info("=" * 60)

        try:
            balance = self.rest.get_balance()
            log.info("Kalshi account balance: $%.2f", balance)
        except Exception as exc:
            log.warning("Could not fetch Kalshi balance: %s", exc)

        await self._check_llm_health()

        tasks = [
            asyncio.create_task(run_rss_monitor(self._enqueue_news),    name="rss"),
            asyncio.create_task(
                run_reddit_monitor(self._enqueue_news, subreddits=self._make_subreddit_getter()),
                name="reddit",
            ),
            asyncio.create_task(
                run_search_news_monitor(
                    self._enqueue_news,
                    self._make_market_getter(),
                    queue_depth_fn=lambda: (
                        self._news_queue.qsize() / self._news_queue.maxsize
                    ),
                ),
                name="search",
            ),
            asyncio.create_task(
                run_gdelt_monitor(self._enqueue_news, self._make_market_getter()),
                name="gdelt",
            ),
            asyncio.create_task(self._news_consumer_task(),             name="news_consumer"),
            asyncio.create_task(self.ws.run(),                          name="websocket"),
            asyncio.create_task(self._warm_ws_subscriptions(),          name="ws_warm"),
            asyncio.create_task(self._daily_report_task(),              name="daily_report"),
            asyncio.create_task(self._market_refresh_task(),            name="market_refresh"),
            asyncio.create_task(self._auto_resolve_task(),              name="auto_resolve"),
            asyncio.create_task(self._subreddit_discovery_task(),       name="sub_discovery"),
            asyncio.create_task(self._log_maintenance_task(),           name="log_maint"),
            *([asyncio.create_task(
                run_rss_monitor(self._on_fade_tweet, feeds=FADE_TWEET_FEED_URLS),
                name="fade_tweets",
            )] if FADE_TWEET_FEED_URLS else []),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("Bot shutting down...")
        finally:
            for task in tasks:
                task.cancel()
            self.ws.stop()

    def stop(self) -> None:
        self.ws.stop()


# ── CLI handlers ──────────────────────────────────────────────────────────────

async def async_main() -> None:
    args = parse_args()

    if args.report or args.credibility or args.resolve or args.go_live:
        paper = PaperTrader()
        if args.report:
            print(paper.generate_report())
            return
        if args.credibility:
            print("\nSOURCE CREDIBILITY TABLE")
            print(paper.credibility.format_table())
            return
        if args.resolve:
            ticker, result_str = args.resolve
            resolved_yes = result_str.upper() == "YES"
            paper.resolve_market(ticker, resolved_yes)
            log.info("Resolved %s as %s", ticker, "YES" if resolved_yes else "NO")
            return
        if args.go_live:
            _handle_go_live(paper)
            return

    bot = TradingBot()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(bot)))
        except (NotImplementedError, RuntimeError):
            pass

    await bot.run()


def _check_go_live_gates(paper: PaperTrader) -> list[str]:
    """
    Evaluate config-driven go-live readiness gates.

    Returns a list of failure reasons (empty = all gates passed).
    Gates are set in .env and logged at startup:
      GO_LIVE_MIN_RESOLVED    -- minimum resolved trades (default 20)
      GO_LIVE_MIN_WIN_RATE    -- minimum win rate (default 0.52)
      GO_LIVE_MAX_DRAWDOWN_PCT -- max drawdown as fraction of starting bankroll (default 0.20)
    """
    trades   = paper.get_all_trades()
    resolved = [t for t in trades if t["resolved"]]
    notional = paper.get_notional_bankroll()
    failures = []

    n_resolved = len(resolved)
    if n_resolved < cfg.go_live_min_resolved:
        failures.append(
            f"Resolved trades: {n_resolved} < minimum {cfg.go_live_min_resolved}"
        )

    if resolved:
        wins     = sum(1 for t in resolved if (t["pnl_dollars"] or 0) > 0)
        win_rate = wins / n_resolved
        if win_rate < cfg.go_live_min_win_rate:
            failures.append(
                f"Win rate: {win_rate:.1%} < minimum {cfg.go_live_min_win_rate:.1%}"
            )

    drawdown_pct = (cfg.bankroll - notional) / cfg.bankroll if cfg.bankroll > 0 else 0.0
    if drawdown_pct > cfg.go_live_max_drawdown_pct:
        failures.append(
            f"Drawdown: {drawdown_pct:.1%} > maximum {cfg.go_live_max_drawdown_pct:.1%}"
        )

    return failures


def _handle_go_live(paper: PaperTrader) -> None:
    """
    Interactive go-live confirmation gate.

    Checks config-driven readiness gates first, then prints the full report
    and requires the user to type CONFIRM to proceed.
    This is the ONLY way to switch the bot to live trading.

    Requires LIVE_TRADING_ENABLED=true in .env -- a hard kill-switch that prevents
    accidental live activation. Policy: do not enable until Mac Studio is in place.
    """
    if not cfg.live_trading_enabled:
        print("=" * 60)
        print("  LIVE TRADING BLOCKED")
        print("=" * 60)
        print()
        print("  LIVE_TRADING_ENABLED is not set to 'true' in .env.")
        print()
        print("  Policy: no live trading until Mac Studio hardware is in place,")
        print("  or until an explicit request is made with stated goals and costs.")
        print()
        print("  To unlock: set LIVE_TRADING_ENABLED=true in .env, then re-run")
        print("  python main.py --go-live")
        print()
        print("=" * 60)
        return
    print(paper.generate_report())
    print()
    print("=" * 60)
    print("  GO-LIVE READINESS GATES")
    print("=" * 60)
    gate_failures = _check_go_live_gates(paper)
    if gate_failures:
        print("  GATES FAILED -- resolve these before going live:")
        for f in gate_failures:
            print(f"    [FAIL] {f}")
        print()
        print("  Override gates with GO_LIVE_MIN_RESOLVED=0 etc. in .env if intentional.")
        print("=" * 60)
    else:
        print("  All readiness gates passed.")
        print("=" * 60)
    print()
    print("=" * 60)
    print("  WARNING: You are about to switch to LIVE TRADING.")
    print("  Real money will be at risk. Review the report above.")
    print("=" * 60)
    notional = paper.get_notional_bankroll()
    print(f"  Notional bankroll at switch: ${notional:.2f}")
    print(f"  Live max bet: ${cfg.dynamic_max_bet(notional):.2f}")
    if gate_failures:
        print()
        print("  NOTE: Readiness gates have FAILED. Proceeding anyway is your choice.")
    print()
    answer = input("  Type CONFIRM to go live, or anything else to cancel: ").strip()
    if answer == "CONFIRM":
        paper.confirm_go_live()
        print("Live trading confirmed. Restart the bot with `python main.py` to begin.")
    else:
        print("Cancelled -- still in paper trading mode.")


async def _shutdown(bot: TradingBot) -> None:
    log.info("Shutdown signal received")
    bot.stop()
    try:
        report = bot.paper.generate_report()
        log.info("\n%s", report)
    except Exception as exc:
        log.warning("Report generation failed: %s", exc)
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
