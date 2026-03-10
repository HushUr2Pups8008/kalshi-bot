"""
Trade executor — the decision gate between analysis and action.

Paper mode: determined by cfg.is_paper_trading (set by PaperTrader reading DB).
  - Relaxed edge and price checks — cast wide net for credibility data.
  - No ticker cooldown — we want as many resolved trades as possible.
  - Dynamic max bet based on notional bankroll.

Live mode: requires explicit --go-live confirmation. Tighter checks.
  - Ticker cooldown active.
  - Live balance verified before each order.
  - Source credibility multiplier applied to Kelly sizing.
"""

import asyncio
import time
from typing import Optional

from analysis import SignalAnalysis
from analysis.kelly import contracts_from_dollars
from config import cfg, PAPER_MIN_EDGE
from kalshi import OrderResult
from kalshi.rest_client import KalshiRestClient
from trading.paper_trader import PaperTrader
from utils.logger import get_logger, trade_log

log = get_logger("executor")

_LIVE_TICKER_COOLDOWN = 600   # seconds between trades on same ticker (live only)


class TradeExecutor:
    """Routes validated trade signals to paper or live execution."""

    def __init__(self, rest_client: KalshiRestClient, paper_trader: PaperTrader):
        self._rest         = rest_client
        self._paper        = paper_trader
        self._last_traded: dict[str, float] = {}

    async def execute(self, analysis: SignalAnalysis) -> Optional[str]:
        """
        Evaluate and execute a trade. Returns trade/order ID or None if skipped.
        """
        skip_reason = self._validate(analysis)
        if skip_reason:
            log.debug("Skipping %s: %s", analysis.market.ticker, skip_reason)
            trade_log.log_skipped(
                reason=skip_reason,
                ticker=analysis.market.ticker,
                headline=analysis.news_item.headline[:80],
            )
            return None

        if cfg.is_paper_trading:
            trade_id = await self._execute_paper(analysis)
        else:
            trade_id = await self._execute_live(analysis)

        if trade_id:
            self._last_traded[analysis.market.ticker] = time.monotonic()

        return trade_id

    def _validate(self, analysis: SignalAnalysis) -> Optional[str]:
        """Return skip reason, or None if the trade should proceed."""
        # Use relaxed edge threshold during paper trading
        effective_min_edge = PAPER_MIN_EDGE if cfg.is_paper_trading else cfg.min_edge

        if analysis.capped_dollars <= 0:
            return "capped_dollars=0 (below minimum bet size)"

        if abs(analysis.edge) < effective_min_edge:
            return f"edge {analysis.edge:+.4f} below min_edge {effective_min_edge}"

        if analysis.market.status != "open":
            return f"market status={analysis.market.status}"

        # Price sanity: during paper trading allow slightly wider range
        yes_price = analysis.market.yes_price
        price_floor = 2 if cfg.is_paper_trading else 3
        price_ceil  = 98 if cfg.is_paper_trading else 97
        if yes_price < price_floor or yes_price > price_ceil:
            return f"price {yes_price:.1f}¢ is near limit (too illiquid)"

        # Ticker cooldown — live only (paper wants max data)
        if not cfg.is_paper_trading:
            last    = self._last_traded.get(analysis.market.ticker, 0.0)
            elapsed = time.monotonic() - last
            if elapsed < _LIVE_TICKER_COOLDOWN:
                return (
                    f"cooldown: last trade {elapsed:.0f}s ago "
                    f"(cooldown={_LIVE_TICKER_COOLDOWN}s)"
                )
            # Live balance check
            balance = self._rest.get_balance()
            if analysis.capped_dollars > balance * 0.9:
                return f"insufficient live balance (${balance:.2f})"

        return None

    async def _execute_paper(self, analysis: SignalAnalysis) -> Optional[str]:
        trade_id = self._paper.record_trade(analysis)
        log.info(
            "[PAPER] %s | %s %s | edge=%+.3f | $%.2f | notional=$%.2f",
            trade_id,
            analysis.market.ticker,
            analysis.side.upper(),
            analysis.edge,
            analysis.capped_dollars,
            self._paper.get_notional_bankroll(),
        )
        return trade_id

    async def _execute_live(self, analysis: SignalAnalysis) -> Optional[str]:
        """Place a real limit order on Kalshi."""
        price_cents = int(analysis.market.yes_ask) if analysis.side == "yes" \
                      else int(100 - analysis.market.yes_bid)
        price_cents  = max(1, min(99, price_cents))
        contracts    = contracts_from_dollars(analysis.capped_dollars, float(price_cents))

        if contracts <= 0:
            log.warning("Live order aborted: contracts=0 for $%.2f @ %d¢",
                        analysis.capped_dollars, price_cents)
            return None

        cost_dollars = contracts * price_cents / 100.0
        log.info(
            "[LIVE] Placing order: %s %s %d @ %d¢ | cost=$%.2f | edge=%+.3f | src_mult=%.2fx",
            analysis.market.ticker, analysis.side.upper(), contracts,
            price_cents, cost_dollars, analysis.edge, analysis.confidence,
        )

        loop   = asyncio.get_event_loop()
        result: OrderResult = await loop.run_in_executor(
            None,
            lambda: self._rest.place_limit_order(
                ticker=analysis.market.ticker,
                side=analysis.side,
                count=contracts,
                limit_price=price_cents,
            ),
        )

        trade_log.log_live_order(
            order_id=result.order_id,
            ticker=analysis.market.ticker,
            side=analysis.side,
            contracts=contracts,
            price_cents=price_cents,
            cost_dollars=cost_dollars,
            status=result.status,
        )

        if result.error:
            log.error("[LIVE] Order failed: %s", result.error)
            return None

        log.info("[LIVE] Order placed: %s | status=%s | filled=%d",
                 result.order_id, result.status, result.filled)
        return result.order_id

    def status(self) -> dict:
        return {
            "mode":             "paper" if cfg.is_paper_trading else "live",
            "notional_bankroll": self._paper.get_notional_bankroll(),
            "ticker_cooldowns": len(self._last_traded),
        }
