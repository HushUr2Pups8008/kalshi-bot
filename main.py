"""
Kalshi Geopolitical Trading Bot — entry point.

Concurrent tasks:
  1. RSS monitor        — polls Reuters/AP/BBC/Al Jazeera every 60s
  2. Reddit monitor     — polls r/worldnews, r/geopolitics, r/news (public JSON)
  3. WebSocket client   — real-time Kalshi price feed
  4. Market cache refresh
  5. Daily reporter     — logs summary every 24h, writes report file

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
import signal
import sys
from datetime import datetime, timezone

from analysis import SignalAnalysis
from analysis.kelly import kelly_bet
from analysis.market_matcher import MarketMatcher
from analysis.signal_analyzer import estimate_probability
from config import cfg, PAPER_MIN_EDGE, VERSION, FADE_TWEET_FEED_URLS, MARKET_SERIES_BLOCKLIST_PREFIXES, MAX_NEWS_AGE_SECONDS
from feeds import NewsItem
from feeds.dedup import HeadlineDedup
from feeds.reddit_monitor import run_reddit_monitor
from feeds.rss_monitor import run_rss_monitor
from kalshi.rest_client import KalshiRestClient
from kalshi.websocket_client import KalshiWebSocketClient
from trading.executor import TradeExecutor
from trading.paper_trader import PaperTrader
from utils.logger import get_logger

log = get_logger("main")


def _account_from_rsshub_url(url: str) -> str:
    """Extract account name from RSSHub URL. e.g. .../user/Kalshi → 'Kalshi'"""
    parts = url.rstrip("/").split("/")
    if len(parts) >= 1:
        return parts[-1]
    return url.split("/")[2]  # fallback: domain


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
        self.rest     = KalshiRestClient()
        self.ws       = KalshiWebSocketClient()
        self.matcher  = MarketMatcher(self.rest)
        self.paper    = PaperTrader()
        self.executor = TradeExecutor(self.rest, self.paper)
        self.ws.on_price_update(self._on_price_update)
        # Bounded queue decouples feed pollers from slow LLM inference.
        # Feed pollers enqueue instantly; a single consumer drains sequentially.
        self._news_queue: asyncio.Queue[NewsItem] = asyncio.Queue(maxsize=500)
        # Cross-source dedup: Reuters/AP/BBC often publish the same story within
        # minutes. Skip near-identical headlines seen in the last 15 minutes.
        self._dedup = HeadlineDedup()

    # ── News pipeline ─────────────────────────────────────────────────────────

    async def _enqueue_news(self, news: NewsItem) -> None:
        """Non-blocking enqueue from feed pollers. Drops items if queue is full."""
        if self._dedup.is_duplicate(news.headline, source=news.source):
            return
        try:
            self._news_queue.put_nowait(news)
        except asyncio.QueueFull:
            log.warning("News queue full (%d items) — dropping: %s",
                        self._news_queue.maxsize, news.headline[:60])

    async def _news_consumer_task(self) -> None:
        """Drain the news queue, processing one item at a time."""
        processed = 0
        while True:
            news = await self._news_queue.get()
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

        candidates = await self.matcher.find_candidates(news)
        if not candidates:
            log.debug("No matching markets for: %s", news.headline[:60])
            return

        for market, match_score in candidates:
            await self._process_candidate(news, market, match_score)

    async def _process_candidate(self, news: NewsItem, market, match_score: float) -> None:
        # Staleness check: skip if the article is too old when we process it.
        # With a queue, items can sit for several minutes; old news is already priced in.
        age_secs = (datetime.now(timezone.utc) - news.published).total_seconds()
        if age_secs > MAX_NEWS_AGE_SECONDS:
            log.debug(
                "Stale news skipped (%.0fs > %ds): %s",
                age_secs, MAX_NEWS_AGE_SECONDS, news.headline[:60],
            )
            return

        # Use WS price if available
        ws_price = self.ws.get_yes_price(market.ticker)
        if ws_price is not None:
            market.yes_price = ws_price
            market.yes_bid   = max(1, ws_price - 1)
            market.yes_ask   = min(99, ws_price + 1)

        estimated_prob, confidence, keywords, reasoning = await estimate_probability(
            news, market
        )
        if not keywords:
            return

        edge = estimated_prob - market.yes_prob
        side = "yes" if edge > 0 else "no"

        # Get source credibility multiplier
        source_mult = self.paper.credibility.get_multiplier(news.source)

        # Dynamic max bet based on current notional bankroll
        notional   = self.paper.get_notional_bankroll()
        max_bet    = cfg.dynamic_max_bet(notional)
        min_edge   = PAPER_MIN_EDGE if cfg.is_paper_trading else cfg.min_edge

        kelly_frac, kelly_dollars, capped_dollars = kelly_bet(
            estimated_probability=estimated_prob,
            market_price_cents=market.yes_price,
            bankroll=notional,
            kelly_fraction=cfg.kelly_fraction,
            max_bet_dollars=max_bet,
            min_bet_dollars=cfg.min_bet_dollars,
            min_edge=min_edge,
            source_multiplier=source_mult,
        )

        from utils.logger import trade_log
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

        if capped_dollars <= 0:
            log.debug("No bet for %s: edge=%+.4f below threshold", market.ticker, edge)
            return

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
        )

        await self.executor.execute(analysis)
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
        )
        log.info("[FADE/%s/@%s] %s | %s | pattern=%s | match_score=%.3f",
                 category, account, market.ticker, fade_side.upper(), pattern, score)
        await self.executor.execute(analysis)
        self.ws.watch([market.ticker])

    async def _on_price_update(self, ticker: str, yes_bid: float, yes_ask: float) -> None:
        log.debug("[WS] %s bid=%.1f¢ ask=%.1f¢", ticker, yes_bid, yes_ask)

    # ── Scheduled tasks ───────────────────────────────────────────────────────

    async def _daily_report_task(self) -> None:
        from config import LOGS_DIR
        while True:
            await asyncio.sleep(86_400)
            self.paper.daily_summary()
            report      = self.paper.generate_report()
            report_path = LOGS_DIR / f"report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.txt"
            report_path.write_text(report, encoding="utf-8")
            log.info("Daily report written to %s", report_path)

    async def _market_refresh_task(self) -> None:
        from config import MARKET_CACHE_TTL_SECONDS
        while True:
            await asyncio.sleep(MARKET_CACHE_TTL_SECONDS)
            await self.matcher.refresh_cache()

    # ── Run ───────────────────────────────────────────────────────────────────

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

        log.info("=" * 60)
        log.info("Kalshi Trading Bot v%s starting", VERSION)
        log.info("Mode:             %s", "PAPER TRADING" if cfg.is_paper_trading else "LIVE TRADING")
        log.info("Notional bankroll: $%.2f", notional)
        log.info("Max bet (dynamic): $%.2f  (%.0f%% of bankroll, hard cap $%.2f)",
                 max_bet, cfg.bet_pct_bankroll * 100, cfg.max_bet_hard_cap)
        log.info("Kelly fraction:   %.0f%%", cfg.kelly_fraction * 100)
        if cfg.is_paper_trading:
            log.info("Paper trading — run `python main.py --go-live` when ready to switch.")
        log.info("=" * 60)

        try:
            balance = self.rest.get_balance()
            log.info("Kalshi account balance: $%.2f", balance)
        except Exception as exc:
            log.warning("Could not fetch Kalshi balance: %s", exc)

        await self._check_llm_health()

        tasks = [
            asyncio.create_task(run_rss_monitor(self._enqueue_news),    name="rss"),
            asyncio.create_task(run_reddit_monitor(self._enqueue_news), name="reddit"),
            asyncio.create_task(self._news_consumer_task(),             name="news_consumer"),
            asyncio.create_task(self.ws.run(),                          name="websocket"),
            asyncio.create_task(self._daily_report_task(),              name="daily_report"),
            asyncio.create_task(self._market_refresh_task(),            name="market_refresh"),
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

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(bot)))
        except (NotImplementedError, RuntimeError):
            pass

    await bot.run()


def _handle_go_live(paper: PaperTrader) -> None:
    """
    Interactive go-live confirmation gate.

    Prints the full report, then requires the user to type CONFIRM to proceed.
    This is the ONLY way to switch the bot to live trading.
    """
    print(paper.generate_report())
    print()
    print("=" * 60)
    print("  WARNING: You are about to switch to LIVE TRADING.")
    print("  Real money will be at risk. Review the report above.")
    print("=" * 60)
    notional = paper.get_notional_bankroll()
    print(f"  Notional bankroll at switch: ${notional:.2f}")
    print(f"  Live max bet: ${cfg.dynamic_max_bet(notional):.2f}")
    print()
    answer = input("  Type CONFIRM to go live, or anything else to cancel: ").strip()
    if answer == "CONFIRM":
        paper.confirm_go_live()
        print("Live trading confirmed. Restart the bot with `python main.py` to begin.")
    else:
        print("Cancelled — still in paper trading mode.")


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
    except KeyboardInterrupt:
        pass
