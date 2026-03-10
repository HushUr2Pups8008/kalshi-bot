"""
Trade executor — the decision gate between analysis and action.

For every (SignalAnalysis) it receives, it:
  1. Validates the edge meets the minimum threshold.
  2. Validates the bankroll constraints.
  3. Checks for duplicate / conflicting positions.
  4. Routes to paper_trader.record_trade() OR live order placement.

Paper mode vs. live mode is determined entirely by cfg.is_paper_trading.
When paper trading ends, the executor automatically switches to live trading
with no code change required.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from analysis import SignalAnalysis
from analysis.kelly import contracts_from_dollars
from config import cfg
from kalshi import OrderResult
from kalshi.rest_client import KalshiRestClient
from trading.paper_trader import PaperTrader
from utils.logger import get_logger, trade_log

log = get_logger("executor")

# Cooldown: don't trade the same ticker within this many seconds
_TICKER_COOLDOWN_SECONDS = 600


class TradeExecutor:
    """
    Routes validated trade signals to paper or live execution.

    Maintains a simple in-memory cooldown set to avoid duplicate trades
    on the same market within a short window.
    """

    def __init__(self, rest_client: KalshiRestClient, paper_trader: PaperTrader):
        self._rest        = rest_client
        self._paper       = paper_trader
        self._last_traded: dict[str, float] = {}  # ticker → monotonic timestamp

    # ── Main entry point ──────────────────────────────────────────────────────

    async def execute(self, analysis: SignalAnalysis) -> Optional[str]:
        """
        Evaluate and execute a trade based on a SignalAnalysis.

        Returns a trade/order ID on success, None if the trade was skipped.
        """
        # ── Pre-flight checks ────────────────────────────────────────────────
        skip_reason = self._validate(analysis)
        if skip_reason:
            log.debug("Skipping %s: %s", analysis.market.ticker, skip_reason)
            trade_log.log_skipped(
                reason=skip_reason,
                ticker=analysis.market.ticker,
                headline=analysis.news_item.headline[:80],
            )
            return None

        # ── Route: paper or live ─────────────────────────────────────────────
        if cfg.is_paper_trading:
            trade_id = await self._execute_paper(analysis)
        else:
            trade_id = await self._execute_live(analysis)

        if trade_id:
            import time
            self._last_traded[analysis.market.ticker] = time.monotonic()

        return trade_id

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate(self, analysis: SignalAnalysis) -> Optional[str]:
        """Return a skip reason string, or None if the trade should proceed."""
        import time

        if analysis.capped_dollars <= 0:
            return "capped_dollars=0 (below minimum bet size)"

        if abs(analysis.edge) < cfg.min_edge:
            return f"edge {analysis.edge:+.4f} below min_edge {cfg.min_edge}"

        if analysis.market.status != "open":
            return f"market status={analysis.market.status}"

        # Cooldown check
        last = self._last_traded.get(analysis.market.ticker, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < _TICKER_COOLDOWN_SECONDS:
            return (
                f"cooldown: last trade was {elapsed:.0f}s ago "
                f"(cooldown={_TICKER_COOLDOWN_SECONDS}s)"
            )

        # Price sanity: skip if price is very near 0 or 100 (illiquid extremes)
        yes_price = analysis.market.yes_price
        if yes_price < 3 or yes_price > 97:
            return f"price {yes_price:.1f}¢ is near limit (too illiquid)"

        # Bankroll check (live only)
        if not cfg.is_paper_trading:
            balance = self._rest.get_balance()
            if analysis.capped_dollars > balance * 0.9:
                return f"insufficient live balance (${balance:.2f})"

        return None

    # ── Paper execution ───────────────────────────────────────────────────────

    async def _execute_paper(self, analysis: SignalAnalysis) -> Optional[str]:
        trade_id = self._paper.record_trade(analysis)

        mode_msg = (
            f"[PAPER] ({cfg.days_remaining_paper:.1f} days left in paper phase)"
            if cfg.days_remaining_paper > 0
            else "[PAPER]"
        )
        log.info(
            "%s Trade recorded: %s | %s %s | edge=%+.3f | $%.2f",
            mode_msg,
            trade_id,
            analysis.market.ticker,
            analysis.side.upper(),
            analysis.edge,
            analysis.capped_dollars,
        )
        return trade_id

    # ── Live execution ────────────────────────────────────────────────────────

    async def _execute_live(self, analysis: SignalAnalysis) -> Optional[str]:
        """Place a real limit order on Kalshi."""
        if analysis.side == "yes":
            price_cents = int(analysis.market.yes_ask)  # use ask when buying YES
        else:
            price_cents = int(100 - analysis.market.yes_bid)  # use spread for NO

        price_cents = max(1, min(99, price_cents))
        contracts   = contracts_from_dollars(analysis.capped_dollars, float(price_cents))

        if contracts <= 0:
            log.warning("Live order aborted: contracts=0 for $%.2f @ %d¢",
                        analysis.capped_dollars, price_cents)
            return None

        cost_dollars = contracts * price_cents / 100.0
        log.info(
            "[LIVE] Placing order: %s %s %d @ %d¢ | cost=$%.2f | edge=%+.3f",
            analysis.market.ticker,
            analysis.side.upper(),
            contracts,
            price_cents,
            cost_dollars,
            analysis.edge,
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

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "mode":                  "paper" if cfg.is_paper_trading else "live",
            "days_remaining_paper":  cfg.days_remaining_paper,
            "ticker_cooldowns":      len(self._last_traded),
        }
