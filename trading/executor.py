"""
Trade executor — the decision gate between analysis and action.

Paper mode: determined by cfg.is_paper_trading (set by PaperTrader reading DB).
  - Relaxed edge and price checks — cast wide net for credibility data.
  - Per-ticker cooldown 4h — prevents same ticker spammed by article bursts.
  - Dynamic max bet based on notional bankroll.

Live mode: requires explicit --go-live confirmation. Tighter checks.
  - Ticker cooldown active.
  - Live balance verified before each order.
  - Source credibility multiplier applied to Kelly sizing.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from analysis import SignalAnalysis
from analysis.kelly import contracts_from_dollars
from config import cfg, PAPER_MIN_EDGE, PAPER_FLAT_CONTRACTS, PAPER_BLOCK_SAME_SIDE_DUPLICATE
from kalshi import OrderResult
from kalshi.rest_client import KalshiRestClient
from trading.paper_trader import PaperTrader
from utils.logger import get_logger, trade_log

log = get_logger("executor")

# Cooldowns moved to BotConfig (cfg.live_ticker_cooldown / cfg.paper_ticker_cooldown).
# Read from cfg at call time so .env changes take effect without code edits.


class TradeExecutor:
    """Routes validated trade signals to paper or live execution."""

    def __init__(self, rest_client: KalshiRestClient, paper_trader: PaperTrader):
        self._rest         = rest_client
        self._paper        = paper_trader
        self._last_traded: dict[str, float] = {}
        # Live session loss limit state
        self._session_start_balance: Optional[float] = None
        self._live_halted:           bool             = False
        self._shutdown_callback:     Optional[Callable] = None
        self._seed_cooldowns_from_db()

    def set_shutdown_callback(self, cb: Callable) -> None:
        """Register a callback that is invoked when the live loss limit is breached."""
        self._shutdown_callback = cb

    def _check_live_loss_limit(self, balance: float) -> bool:
        """
        Track session-start Kalshi balance and return True if the loss limit is breached.
        First call seeds the baseline; subsequent calls compare against it.
        """
        if self._session_start_balance is None:
            self._session_start_balance = balance
            log.info(
                "[LOSS_LIMIT] Session start balance recorded: $%.2f "
                "(halt at $%.2f loss, limit=%.0f%%)",
                balance,
                cfg.bankroll * cfg.live_loss_limit_pct,
                cfg.live_loss_limit_pct * 100,
            )
            return False
        loss = self._session_start_balance - balance
        limit = cfg.bankroll * cfg.live_loss_limit_pct
        return loss >= limit

    def _seed_cooldowns_from_db(self) -> None:
        """
        Seed per-ticker cooldowns from portfolio so restarts don't reset the gate.
        Reads from the already-loaded Portfolio instead of re-querying the DB.
        """
        now_wall = datetime.now(timezone.utc)
        now_mono = time.monotonic()
        for ticker in self._paper.portfolio.tickers():
            latest = self._paper.portfolio.latest_ts(ticker)
            if latest is None:
                continue
            trade_ts = datetime.fromisoformat(latest)
            if trade_ts.tzinfo is None:
                trade_ts = trade_ts.replace(tzinfo=timezone.utc)
            age_secs = (now_wall - trade_ts).total_seconds()
            if age_secs < cfg.paper_ticker_cooldown:
                self._last_traded[ticker] = now_mono - age_secs
                log.debug(
                    "Cooldown seeded for %s from portfolio (%.1fh ago)",
                    ticker, age_secs / 3600,
                )

    async def execute(self, analysis: SignalAnalysis) -> Optional[str]:
        """
        Evaluate and execute a trade. Returns trade/order ID or None if skipped.
        """
        log.debug(
            "[DECISION] start ticker=%s mode=%s side=%s edge=%+.4f "
            "capped=$%.2f yes_price=%.1fc est_prob=%.3f confidence=%.2f",
            analysis.market.ticker,
            "paper" if cfg.is_paper_trading else "live",
            analysis.side.upper(),
            analysis.edge,
            analysis.capped_dollars,
            analysis.market.yes_price,
            analysis.estimated_probability,
            analysis.confidence,
        )
        skip_reason = self._validate(analysis)
        if skip_reason:
            effective_min_edge = PAPER_MIN_EDGE if cfg.is_paper_trading else cfg.min_edge
            log.debug(
                "[DECISION] skip ticker=%s mode=%s side=%s reason=%s",
                analysis.market.ticker,
                "paper" if cfg.is_paper_trading else "live",
                analysis.side.upper(),
                skip_reason,
            )
            trade_log.log_skipped(
                reason=skip_reason,
                ticker=analysis.market.ticker,
                headline=analysis.news_item.headline[:80],
                model_probability=analysis.estimated_probability,
                market_price=analysis.market_yes_price,
                edge=analysis.edge,
                min_edge_threshold=effective_min_edge,
            )
            return None

        if cfg.is_paper_trading:
            log.debug(
                "[DECISION] route ticker=%s mode=paper side=%s",
                analysis.market.ticker,
                analysis.side.upper(),
            )
            trade_id = await self._execute_paper(analysis)
        else:
            log.debug(
                "[DECISION] route ticker=%s mode=live side=%s",
                analysis.market.ticker,
                analysis.side.upper(),
            )
            trade_id = await self._execute_live(analysis)

        if trade_id:
            self._last_traded[analysis.market.ticker] = time.monotonic()
        else:
            log.debug(
                "[DECISION] no_trade_id ticker=%s mode=%s side=%s "
                "after successful validation",
                analysis.market.ticker,
                "paper" if cfg.is_paper_trading else "live",
                analysis.side.upper(),
            )

        return trade_id

    def _validate(self, analysis: SignalAnalysis) -> Optional[str]:
        """Return skip reason, or None if the trade should proceed."""
        # Use relaxed edge threshold during paper trading
        effective_min_edge = PAPER_MIN_EDGE if cfg.is_paper_trading else cfg.min_edge

        # Paper mode uses flat contracts -- skip the dollars gate entirely
        if not cfg.is_paper_trading and analysis.capped_dollars <= 0:
            return "capped_dollars=0 (below minimum bet size)"

        if abs(analysis.edge) < effective_min_edge:
            return f"edge {analysis.edge:+.4f} below min_edge {effective_min_edge}"

        if analysis.market.status not in ("open", "active"):
            return f"market status={analysis.market.status}"

        # Price sanity: during paper trading allow slightly wider range
        yes_price = analysis.market.yes_price
        price_floor = 2 if cfg.is_paper_trading else 3
        price_ceil  = 98 if cfg.is_paper_trading else 97
        if yes_price < price_floor or yes_price > price_ceil:
            return f"price {yes_price:.1f}c is near limit (too illiquid)"

        # Paper ticker cooldown (4h): prevents same ticker being spammed by a burst
        # of headlines on the same topic (e.g. 30 Iran-war articles in one poll cycle).
        if cfg.is_paper_trading:
            last    = self._last_traded.get(analysis.market.ticker, 0.0)
            elapsed = time.monotonic() - last
            if elapsed < cfg.paper_ticker_cooldown:
                return (
                    f"paper cooldown: last trade {elapsed/3600:.1f}h ago "
                    f"(cooldown={cfg.paper_ticker_cooldown//3600}h)"
                )

        # Multi-position guard: block opposing trades (no hedges) and duplicate
        # signals (same side, same probability estimate, same market price).
        # Reads from the in-memory Portfolio — no DB query at decision time.
        for pos in self._paper.portfolio.open_positions(analysis.market.ticker):
            if pos.side != analysis.side:
                return (
                    f"opposing position exists: open {pos.side.upper()} "
                    f"at est={pos.estimated_prob:.3f} -- no hedging"
                )
            # Paper phase: allow same-side re-entry only if the estimated probability
            # has shifted significantly (>=0.07) OR market price has moved (>=5c).
            # This replaces the former flat block (PAPER_BLOCK_SAME_SIDE_DUPLICATE)
            # which suppressed 11 valid follow-on signals over the first 30 days.
            if cfg.is_paper_trading and PAPER_BLOCK_SAME_SIDE_DUPLICATE:
                prob_delta_paper  = abs(pos.estimated_prob - analysis.estimated_probability)
                price_delta_paper = abs(pos.market_yes_price - analysis.market_yes_price)
                if prob_delta_paper < 0.07 and price_delta_paper < 5.0:
                    return (
                        f"paper duplicate skip: open {pos.side.upper()} at "
                        f"est={pos.estimated_prob:.3f} (delta={prob_delta_paper:.3f}) "
                        f"-- prob shift too small to add position"
                    )
            prob_delta  = abs(pos.estimated_prob - analysis.estimated_probability)
            price_delta = abs(pos.market_yes_price - analysis.market_yes_price)
            if prob_delta < 0.02 and price_delta < 2.0:
                return (
                    f"same-signal skip: open {pos.side.upper()} at "
                    f"est={pos.estimated_prob:.3f} "
                    f"mkt={pos.market_yes_price:.1f}c -- no new information"
                )

        # Concentration risk: cap exposure per ticker at max_ticker_exposure_pct
        # of the notional bankroll. Prevents a flood of signals on one ticker
        # from deploying an outsized fraction of capital on a single outcome.
        notional = self._paper.get_notional_bankroll()
        trade_cost = (
            PAPER_FLAT_CONTRACTS * max(1, min(99, int(analysis.market.yes_price))) / 100.0
            if cfg.is_paper_trading
            else analysis.capped_dollars
        )
        if not self._paper.portfolio.is_concentration_ok(
            ticker=analysis.market.ticker,
            additional_dollars=trade_cost,
            max_ticker_pct=cfg.max_ticker_exposure_pct,
            bankroll=notional,
        ):
            current_exposure = self._paper.portfolio.exposure(analysis.market.ticker)
            cap = cfg.max_ticker_exposure_pct * notional
            return (
                f"concentration limit: {analysis.market.ticker} exposure "
                f"${current_exposure:.2f} + ${trade_cost:.2f} would exceed "
                f"{cfg.max_ticker_exposure_pct:.0%} cap (${cap:.2f})"
            )

        # Live ticker cooldown + balance check + session loss limit
        if not cfg.is_paper_trading:
            # Halt check: if a previous call already triggered the limit, reject all trades
            if self._live_halted:
                return "LIVE HALTED: session loss limit reached -- all trading suspended"

            last    = self._last_traded.get(analysis.market.ticker, 0.0)
            elapsed = time.monotonic() - last
            if elapsed < cfg.live_ticker_cooldown:
                return (
                    f"cooldown: last trade {elapsed:.0f}s ago "
                    f"(cooldown={cfg.live_ticker_cooldown}s)"
                )
            # Live balance check + session loss limit (single API call covers both)
            balance = self._rest.get_balance()
            if self._check_live_loss_limit(balance):
                self._live_halted = True
                loss = self._session_start_balance - balance
                log.critical(
                    "LIVE LOSS LIMIT BREACHED -- halting all trading. "
                    "Session loss $%.2f >= limit $%.2f (%.0f%% of $%.2f bankroll). "
                    "Restart bot to reset.",
                    loss,
                    cfg.bankroll * cfg.live_loss_limit_pct,
                    cfg.live_loss_limit_pct * 100,
                    cfg.bankroll,
                )
                if self._shutdown_callback:
                    self._shutdown_callback()
                return "LIVE HALTED: session loss limit reached -- all trading suspended"
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

    @staticmethod
    def _is_retryable_error(error: Optional[str]) -> bool:
        """Return True for transient errors worth retrying (network, 5xx, rate-limit)."""
        if not error:
            return False
        transient_markers = ("timeout", "429", "500", "502", "503", "504",
                             "connection", "network", "reset")
        err_lower = error.lower()
        return any(m in err_lower for m in transient_markers)

    async def _execute_live(self, analysis: SignalAnalysis) -> Optional[str]:
        """Place a real limit order on Kalshi with exponential backoff on transient errors."""
        price_cents = int(analysis.market.yes_ask) if analysis.side == "yes" \
                      else int(100 - analysis.market.yes_bid)
        price_cents  = max(1, min(99, price_cents))
        contracts    = contracts_from_dollars(analysis.capped_dollars, float(price_cents))

        if contracts <= 0:
            log.warning("Live order aborted: contracts=0 for $%.2f @ %dc",
                        analysis.capped_dollars, price_cents)
            return None

        cost_dollars = contracts * price_cents / 100.0
        log.info(
            "[LIVE] Placing order: %s %s %d @ %dc | cost=$%.2f | edge=%+.3f | confidence=%.2f",
            analysis.market.ticker, analysis.side.upper(), contracts,
            price_cents, cost_dollars, analysis.edge, analysis.confidence,
        )

        loop = asyncio.get_running_loop()
        last_error: Optional[str] = None

        for attempt in range(3):
            if attempt > 0:
                delay = 2 ** (attempt - 1)   # 1s, 2s
                log.warning(
                    "[LIVE] Retrying order for %s (attempt %d/3, backoff=%ds): %s",
                    analysis.market.ticker, attempt + 1, delay, last_error,
                )
                await asyncio.sleep(delay)

            try:
                result: OrderResult = await loop.run_in_executor(
                    None,
                    lambda: self._rest.place_limit_order(
                        ticker=analysis.market.ticker,
                        side=analysis.side,
                        count=contracts,
                        limit_price=price_cents,
                    ),
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt < 2:
                    continue
                log.error("[LIVE] Order failed after 3 attempts (exception): %s", last_error)
                return None

            if result.error:
                last_error = result.error
                if self._is_retryable_error(result.error) and attempt < 2:
                    continue
                # Non-retryable or final attempt
                log.error(
                    "[LIVE] Order failed%s: %s",
                    " after 3 attempts" if attempt == 2 else "",
                    result.error,
                )
                return None

            # Success
            trade_log.log_live_order(
                order_id=result.order_id,
                ticker=analysis.market.ticker,
                side=analysis.side,
                contracts=contracts,
                price_cents=price_cents,
                cost_dollars=cost_dollars,
                status=result.status,
                model_probability=analysis.estimated_probability,
                market_price=analysis.market_yes_price,
                edge=analysis.edge,
                min_edge_threshold=(PAPER_MIN_EDGE if cfg.is_paper_trading else cfg.min_edge),
            )
            if attempt > 0:
                log.info(
                    "[LIVE] Order placed on attempt %d: %s | status=%s | filled=%d",
                    attempt + 1, result.order_id, result.status, result.filled,
                )
            else:
                log.info(
                    "[LIVE] Order placed: %s | status=%s | filled=%d",
                    result.order_id, result.status, result.filled,
                )
            return result.order_id

        return None

    def status(self) -> dict:
        return {
            "mode":              "paper" if cfg.is_paper_trading else "live",
            "notional_bankroll": self._paper.get_notional_bankroll(),
            "ticker_cooldowns":  len(self._last_traded),
            "portfolio":         self._paper.portfolio.summary(),
        }
