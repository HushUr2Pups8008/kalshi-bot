"""
Kalshi Geopolitical Trading Bot — entry point.

Runs these async tasks concurrently:
  1. RSS monitor       — polls Reuters/AP/BBC/Al Jazeera every 60s
  2. Reddit monitor    — streams r/worldnews, r/geopolitics, r/news
  3. WebSocket client  — real-time Kalshi price feed
  4. Market cache refresh — keeps the market list fresh (every 5 min)
  5. Daily reporter    — logs a summary + full report each morning
  6. Go-live watchdog  — announces when paper trading phase ends

Each news item flows through:
  news item → market matcher → signal analyzer → kelly sizer → executor

Usage:
    python main.py                  # normal run
    python main.py --report         # print paper trading report and exit
    python main.py --resolve TICKER YES|NO  # manually resolve a paper trade
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
from config import cfg
from feeds import NewsItem
from feeds.reddit_monitor import run_reddit_monitor
from feeds.rss_monitor import run_rss_monitor
from kalshi.rest_client import KalshiRestClient
from kalshi.websocket_client import KalshiWebSocketClient
from trading.executor import TradeExecutor
from trading.paper_trader import PaperTrader
from utils.logger import get_logger

log = get_logger("main")

# ── CLI args ──────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kalshi Geopolitical Trading Bot")
    p.add_argument("--report", action="store_true",
                   help="Print the paper trading performance report and exit")
    p.add_argument("--resolve", nargs=2, metavar=("TICKER", "RESULT"),
                   help="Resolve a paper trade: --resolve TICKER YES|NO")
    p.add_argument("--paper-days", type=int, default=None,
                   help="Override paper trading duration (days)")
    return p.parse_args()


# ── Core pipeline ─────────────────────────────────────────────────────────────

class TradingBot:
    """Wires all components together and manages the async task graph."""

    def __init__(self):
        self.rest    = KalshiRestClient()
        self.ws      = KalshiWebSocketClient()
        self.matcher = MarketMatcher(self.rest)
        self.paper   = PaperTrader()
        self.executor = TradeExecutor(self.rest, self.paper)

        # Register WS price callback for live price updates
        self.ws.on_price_update(self._on_price_update)

    # ── News processing ───────────────────────────────────────────────────────

    async def on_news_item(self, news: NewsItem) -> None:
        """Called for every new news item from any feed."""
        log.info("[NEWS] [%s] %s", news.source, news.headline[:100])

        # Find relevant markets
        candidates = await self.matcher.find_candidates(news)
        if not candidates:
            log.debug("No matching markets for: %s", news.headline[:60])
            return

        for market, match_score in candidates:
            await self._process_candidate(news, market, match_score)

    async def _process_candidate(self, news: NewsItem, market, match_score: float) -> None:
        """Analyze a single (news, market) pair and potentially trade."""
        # Fetch latest price from WS if available, otherwise use REST
        ws_price = self.ws.get_yes_price(market.ticker)
        if ws_price is not None:
            market.yes_price = ws_price
            market.yes_bid   = max(1, ws_price - 1)
            market.yes_ask   = min(99, ws_price + 1)

        # Estimate probability
        estimated_prob, confidence, keywords, reasoning = await estimate_probability(
            news, market
        )

        if not keywords:
            return  # no signal found

        # Log the opportunity
        from utils.logger import trade_log
        edge = estimated_prob - market.yes_prob
        side = "yes" if edge > 0 else "no"

        # Kelly sizing
        kelly_frac, kelly_dollars, capped_dollars = kelly_bet(
            estimated_probability=estimated_prob,
            market_price_cents=market.yes_price,
            bankroll=cfg.bankroll,
            kelly_fraction=cfg.kelly_fraction,
            max_bet_dollars=cfg.max_bet_dollars,
            min_edge=cfg.min_edge,
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

        if capped_dollars <= 0:
            log.debug(
                "No bet for %s: edge=%+.4f kelly=$%.2f",
                market.ticker, edge, kelly_dollars,
            )
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

        # Subscribe to WS price updates for this market now that we care about it
        self.ws.watch([market.ticker])

    # ── WebSocket callback ────────────────────────────────────────────────────

    async def _on_price_update(
        self, ticker: str, yes_bid: float, yes_ask: float
    ) -> None:
        """Called by the WS client when a market's prices change."""
        yes_price = (yes_bid + yes_ask) / 2.0
        log.debug("[WS] %s yes_bid=%.1f¢ yes_ask=%.1f¢ mid=%.1f¢",
                  ticker, yes_bid, yes_ask, yes_price)

    # ── Scheduled tasks ───────────────────────────────────────────────────────

    async def _daily_report_task(self) -> None:
        """Emit a summary log every 24 hours and write the full report to file."""
        import asyncio
        from config import LOGS_DIR

        while True:
            await asyncio.sleep(86_400)  # 24 hours
            self.paper.daily_summary()
            report = self.paper.generate_report()
            report_path = LOGS_DIR / f"report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.txt"
            report_path.write_text(report, encoding="utf-8")
            log.info("Daily report written to %s", report_path)

    async def _golive_watchdog_task(self) -> None:
        """Poll every minute and announce when paper trading ends."""
        import asyncio
        was_paper = cfg.is_paper_trading
        while True:
            await asyncio.sleep(60)
            is_paper = cfg.is_paper_trading
            if was_paper and not is_paper:
                log.warning(
                    "=" * 60 + "\n"
                    "PAPER TRADING PHASE COMPLETE — SWITCHING TO LIVE TRADING\n"
                    "Bankroll: $%.2f | Max bet: $%.2f | Kelly: %.0f%%\n"
                    + "=" * 60,
                    cfg.bankroll, cfg.max_bet_dollars, cfg.kelly_fraction * 100,
                )
                # Print the final paper trading report
                report = self.paper.generate_report()
                log.info("\n%s", report)
            was_paper = is_paper

    async def _market_refresh_task(self) -> None:
        """Periodically force-refresh the market cache."""
        import asyncio
        from config import MARKET_CACHE_TTL_SECONDS
        while True:
            await asyncio.sleep(MARKET_CACHE_TTL_SECONDS)
            await self.matcher.refresh_cache()

    # ── Run ───────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        log.info("=" * 60)
        log.info("Kalshi Trading Bot starting")
        log.info("Mode: %s", "PAPER TRADING" if cfg.is_paper_trading else "LIVE TRADING")
        if cfg.is_paper_trading:
            log.info("Paper trading for %.1f more days", cfg.days_remaining_paper)
        log.info("Bankroll: $%.2f | Max bet: $%.2f | Kelly: %.0f%%",
                 cfg.bankroll, cfg.max_bet_dollars, cfg.kelly_fraction * 100)
        log.info("=" * 60)

        # Verify Kalshi connectivity
        try:
            balance = self.rest.get_balance()
            log.info("Kalshi account balance: $%.2f", balance)
        except Exception as exc:
            log.warning("Could not fetch Kalshi balance (API keys not set?): %s", exc)

        tasks = [
            asyncio.create_task(run_rss_monitor(self.on_news_item),          name="rss"),
            asyncio.create_task(run_reddit_monitor(self.on_news_item),        name="reddit"),
            asyncio.create_task(self.ws.run(),                                name="websocket"),
            asyncio.create_task(self._daily_report_task(),                    name="daily_report"),
            asyncio.create_task(self._golive_watchdog_task(),                 name="golive_watchdog"),
            asyncio.create_task(self._market_refresh_task(),                  name="market_refresh"),
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


# ── Entry point ───────────────────────────────────────────────────────────────

async def async_main() -> None:
    args = parse_args()

    if args.paper_days is not None:
        cfg.paper_trading_days = args.paper_days
        log.info("Paper trading days overridden to %d", args.paper_days)

    paper = PaperTrader()

    if args.report:
        print(paper.generate_report())
        return

    if args.resolve:
        ticker, result_str = args.resolve
        resolved_yes = result_str.upper() == "YES"
        paper.resolve_market(ticker, resolved_yes)
        log.info("Resolved %s as %s", ticker, "YES" if resolved_yes else "NO")
        return

    bot = TradingBot()

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(bot)))
        except (NotImplementedError, RuntimeError):
            pass  # Windows doesn't support add_signal_handler

    await bot.run()


async def _shutdown(bot: TradingBot) -> None:
    log.info("Shutdown signal received")
    bot.stop()
    # Print a final paper report on exit
    try:
        report = bot.paper.generate_report()
        log.info("\n%s", report)
    except Exception:
        pass
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
