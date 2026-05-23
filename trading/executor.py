"""
Trade executor -- the decision gate between analysis and action.

Execution mode is frozen at construction time from cfg.is_paper_trading.
Once set, self._is_paper cannot change for the lifetime of this instance.
Any post-construction change to cfg.is_paper_trading is deliberately ignored.

Paper mode: relaxed edge/price checks, 4h ticker cooldown, flat contracts.
Live mode: tighter checks, live balance verified, source credibility applied.
"""

import asyncio
import copy
import time
from datetime import datetime, timezone
from numbers import Real
from typing import Any, Callable, Optional

from analysis import SignalAnalysis
from analysis.kelly import contracts_from_dollars
from config import cfg, PAPER_MIN_EDGE, PAPER_FLAT_CONTRACTS, PAPER_BLOCK_SAME_SIDE_DUPLICATE
from kalshi import OrderResult
from kalshi.rest_client import KalshiRestClient
from trading.paper_trader import PaperTrader
from utils.logger import get_logger, trade_log, write_trade_log_async

log = get_logger("executor")

# Cooldowns moved to BotConfig (cfg.live_ticker_cooldown / cfg.paper_ticker_cooldown).
# Read from cfg at call time so .env changes take effect without code edits.


class TradeExecutor:
    """Routes validated trade signals to paper or live execution."""

    def __init__(self, rest_client: KalshiRestClient, paper_trader: PaperTrader):
        self._rest         = rest_client
        self._paper        = paper_trader
        self._last_traded: dict[str, float] = {}
        # Execution mode frozen at construction -- never re-derived from cfg or DB.
        # cfg.is_paper_trading may be mutated by CLI commands or tests after this
        # point; self._is_paper is intentionally immune to those mutations.
        self._is_paper: bool = cfg.is_paper_trading
        # Live session loss limit state
        self._session_start_balance: Optional[float] = None
        self._live_halted:           bool             = False
        self._shutdown_callback:     Optional[Callable] = None
        log.info(
            "[EXECUTOR_STATE] pid=%s mode=%s bankroll_source=%s",
            __import__("os").getpid(),
            "paper" if self._is_paper else "live",
            "notional_db" if self._is_paper else "kalshi_api",
        )
        self._seed_cooldowns_from_db()

    def set_shutdown_callback(self, cb: Callable) -> None:
        """Register a callback that is invoked when the live loss limit is breached."""
        self._shutdown_callback = cb

    def _check_live_loss_limit(self, balance: float) -> bool:
        """
        Track session-start Kalshi balance and return True if the loss limit is breached.
        First call seeds the baseline; subsequent calls compare against it.
        Paper-mode executors: always returns False (no live balance to track).
        """
        if self._is_paper:
            return False
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

    async def execute(self, candidate: Any) -> Optional[str]:
        """
        Evaluate and execute a trade. Returns trade/order ID or None if skipped.
        """
        analysis = await self._analysis_from_candidate(candidate)
        if analysis is None:
            # CR-D: re-fetch hook fail-closed (market not tradeable post-blend
            # or side selection no longer yields positive edge). Caller logs
            # the reason; no trade attempted.
            return None
        # P-5 CR-C: legacy yes_price read in this log line is guarded by the
        # candidate-side `is_tradeable()` check inside `_analysis_from_candidate`.
        log.debug(
            "[DECISION] start ticker=%s mode=%s side=%s edge=%+.4f "
            "capped=$%.2f yes_price=%.1fc est_prob=%.3f confidence=%.2f",
            analysis.market.ticker,
            "paper" if self._is_paper else "live",
            analysis.side.upper(),
            analysis.edge,
            analysis.capped_dollars,
            analysis.market.yes_price if analysis.market.price_available else 0.0,
            analysis.estimated_probability,
            analysis.confidence,
        )
        skip_reason = self._validate(analysis)
        if skip_reason:
            effective_min_edge = self._min_edge_threshold(analysis)
            signal_meta = self._signal_meta(analysis)
            method = (
                "llm"
                if any(value is not None for value in (analysis.llm_direction, analysis.llm_magnitude, analysis.llm_confidence))
                else "keyword"
            )
            log.debug(
                "[DECISION] skip ticker=%s mode=%s side=%s reason=%s",
                analysis.market.ticker,
                "paper" if self._is_paper else "live",
                analysis.side.upper(),
                skip_reason,
            )
            skipped_kwargs = {
                "reason": skip_reason,
                "ticker": analysis.market.ticker,
                "headline": analysis.news_item.headline[:80],
                "source": analysis.news_item.source,
                "method": method,
                "llm_direction": analysis.llm_direction,
                "llm_magnitude": analysis.llm_magnitude,
                "model_probability": analysis.estimated_probability,
                "market_price": float(analysis.executed_price_cents) if analysis.executed_price_cents is not None else 0.0,
                "edge": analysis.edge,
                "min_edge_threshold": effective_min_edge,
            }
            if signal_meta:
                skipped_kwargs["signal_meta"] = signal_meta
            await write_trade_log_async(trade_log.log_skipped, **skipped_kwargs)
            return None

        if self._is_paper:
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
                "paper" if self._is_paper else "live",
                analysis.side.upper(),
            )

        return trade_id

    def _validate(self, analysis: SignalAnalysis) -> Optional[str]:
        """Return skip reason, or None if the trade should proceed."""
        # Use relaxed edge threshold during paper trading
        effective_min_edge = self._min_edge_threshold(analysis)

        # Paper mode uses flat contracts -- skip the dollars gate entirely
        if not self._is_paper and analysis.capped_dollars <= 0:
            return "capped_dollars=0 (below minimum bet size)"

        if abs(analysis.edge) < effective_min_edge:
            return f"edge {analysis.edge:+.4f} below min_edge {effective_min_edge}"

        if analysis.market.status not in ("open", "active"):
            return f"market status={analysis.market.status}"

        # Price sanity: during paper trading allow slightly wider range.
        # P-5 LD-10: prefer the executed-side ask cents (canonical post-P0).
        # F-08: fail-closed when executed_price_cents is None — the side selector
        # must always populate this field for any tradeable candidate. Falling back
        # to the YES midpoint silently masked producer-side bugs and caused the
        # F-06 ALARM (4 opportunities, 0 SQL trades, 11.5h gap).
        if analysis.executed_price_cents is not None:
            yes_price = float(analysis.executed_price_cents)
        elif not analysis.market.price_available:
            return "price_unavailable: market.price_available=False"
        else:
            return (
                "price_unavailable: executed_price_cents is None despite "
                "price_available=True (side selector did not set canonical price)"
            )
        price_floor = 2 if self._is_paper else 3
        price_ceil  = 98 if self._is_paper else 97
        if yes_price < price_floor or yes_price > price_ceil:
            return f"price {yes_price:.1f}c is near limit (too illiquid)"

        # Paper ticker cooldown (4h): prevents same ticker being spammed by a burst
        # of headlines on the same topic (e.g. 30 Iran-war articles in one poll cycle).
        if self._is_paper:
            last    = self._last_traded.get(analysis.market.ticker, float("-inf"))
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
            analysis_price = float(analysis.executed_price_cents) if analysis.executed_price_cents is not None else 0.0
            if self._is_paper and PAPER_BLOCK_SAME_SIDE_DUPLICATE:
                prob_delta_paper  = abs(pos.estimated_prob - analysis.estimated_probability)
                price_delta_paper = abs(pos.entry_price_cents - analysis_price)
                if prob_delta_paper < 0.07 and price_delta_paper < 5.0:
                    return (
                        f"paper duplicate skip: open {pos.side.upper()} at "
                        f"est={pos.estimated_prob:.3f} (delta={prob_delta_paper:.3f}) "
                        f"-- prob shift too small to add position"
                    )
            prob_delta  = abs(pos.estimated_prob - analysis.estimated_probability)
            price_delta = abs(pos.entry_price_cents - analysis_price)
            if prob_delta < 0.02 and price_delta < 2.0:
                return (
                    f"same-signal skip: open {pos.side.upper()} at "
                    f"est={pos.estimated_prob:.3f} "
                    f"mkt={pos.entry_price_cents:.1f}c -- no new information"
                )

        # Concentration risk: cap exposure per ticker at max_ticker_exposure_pct
        # of the notional bankroll. Prevents a flood of signals on one ticker
        # from deploying an outsized fraction of capital on a single outcome.
        notional = self._paper.get_notional_bankroll()
        # F-08 (Site 2): executed_price_cents must be non-None here — Site 1's
        # fail-closed gate returns early when it is None. The assert encodes that
        # invariant; it should never fire in practice. The legacy midpoint fallback
        # (elif price_available / else 50) has been removed; it masked producer bugs.
        assert analysis.executed_price_cents is not None, (  # noqa: S101
            "Site 1 F-08 gate failed: executed_price_cents is None at Site 2. "
            "This is a bug in _validate control flow."
        )
        paper_unit_price = max(1, min(99, int(analysis.executed_price_cents)))
        trade_cost = (
            PAPER_FLAT_CONTRACTS * paper_unit_price / 100.0
            if self._is_paper
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
        if not self._is_paper:
            # Halt check: if a previous call already triggered the limit, reject all trades
            if self._live_halted:
                return "LIVE HALTED: session loss limit reached -- all trading suspended"

            last    = self._last_traded.get(analysis.market.ticker, float("-inf"))
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

    async def _analysis_from_candidate(self, candidate: Any) -> Optional[SignalAnalysis]:
        """Normalize legacy SignalAnalysis or S3.4 TradeCandidate inputs.

        P-5 CR-D (LD-13 stale-candidate guard): for blended candidates the
        upstream `candidate.market` snapshot reflects fast-lane-time book
        state. Between fast-lane match and blend completion, the executable
        bid/ask can move. Re-fetch the market via `self._rest.get_market`
        and re-derive side + executed_price_cents against the fresh book
        before sizing/placing. Returns ``None`` (fail-closed) when the
        market is no longer tradeable or neither side has positive edge —
        the caller short-circuits without trading.

        Async since P-5 Q4: `self._rest.get_market` is a synchronous HTTP
        round trip; running it directly from the consumer-worker event loop
        serialized all blended-candidate executions. Wrap in
        `asyncio.to_thread` so the loop stays responsive.
        """
        # Shape-based detection: isinstance(candidate, TradeCandidate) would require
        # importing tasks.blend_task into /trading, violating layer boundaries.
        # If fast_lane_analysis is renamed in TradeCandidate, this check must be updated.
        if "fast_lane_analysis" not in vars(candidate):
            return candidate

        signal_meta = self._candidate_signal_meta(candidate)
        analysis = copy.copy(candidate.fast_lane_analysis)

        ticker = candidate.market.ticker
        fresh_market = None
        try:
            fresh_market = await asyncio.to_thread(self._rest.get_market, ticker)
        except Exception as exc:
            log.warning(
                "[BLEND_REFETCH] %s fetch failed: %s -- falling back to candidate snapshot",
                ticker, exc,
            )

        if fresh_market is None:
            # Fetch unavailable. Fall back to candidate snapshot if it is
            # itself tradeable; otherwise fail-closed.
            if not candidate.market.is_tradeable():
                log.warning(
                    "[BLEND_REFETCH] %s candidate market not tradeable and "
                    "refetch returned None -- skipping",
                    ticker,
                )
                return None
            log.warning(
                "[BLEND_REFETCH] fallback_to_snapshot ticker=%s — fresh REST get_market returned None; "
                "using fast-lane-time candidate market (CR-D degraded path)",
                ticker,
            )
            fresh_market = candidate.market

        if not fresh_market.is_tradeable():
            log.warning(
                "[BLEND_REFETCH] %s not tradeable post-blend "
                "(price_available=%s) -- skipping",
                ticker, fresh_market.price_available,
            )
            return None

        analysis.market = fresh_market
        analysis.estimated_probability = candidate.blended_probability

        # Re-derive side + executable price against the fresh book.
        try:
            from analysis.side_selection import select_side, compute_edge
            new_side, new_executed_cents = select_side(
                fresh_market, candidate.blended_probability
            )
            edges = compute_edge(fresh_market, candidate.blended_probability)
        except ValueError as exc:
            log.debug(
                "[BLEND_REFETCH] %s side selection failed post-refetch: %s",
                ticker, exc,
            )
            return None

        analysis.side = new_side
        analysis.executed_price_cents = new_executed_cents
        analysis.edge = edges.yes_edge if new_side == "yes" else edges.no_edge
        analysis.signal_type = "blend"
        analysis.signal_meta = signal_meta
        return analysis

    @staticmethod
    def _candidate_signal_meta(candidate: Any) -> dict[str, Any]:
        attrs = vars(candidate)
        if "signal_meta" not in attrs:
            return {}
        signal_meta = attrs["signal_meta"]
        if signal_meta is None:
            return {}
        if not isinstance(signal_meta, dict):
            raise TypeError("signal_meta must be a dict when provided")
        return dict(signal_meta)

    @staticmethod
    def _signal_meta(analysis: SignalAnalysis) -> dict[str, Any]:
        attrs = vars(analysis)
        if "signal_meta" not in attrs:
            return {}
        signal_meta = attrs["signal_meta"]
        if signal_meta is None:
            return {}
        if not isinstance(signal_meta, dict):
            raise TypeError("signal_meta must be a dict when provided")
        return dict(signal_meta)

    def _min_edge_threshold(self, analysis: SignalAnalysis) -> float:
        base_threshold = PAPER_MIN_EDGE if self._is_paper else cfg.min_edge
        signal_meta = self._signal_meta(analysis)
        override = signal_meta.get("readiness_gate_min_edge_override")
        if override is None:
            return base_threshold
        if isinstance(override, bool) or not isinstance(override, Real):
            raise ValueError("readiness_gate_min_edge_override must be numeric")
        if override < 0.0:
            raise ValueError("readiness_gate_min_edge_override must be non-negative")
        return float(override)

    async def _execute_paper(self, analysis: SignalAnalysis) -> Optional[str]:
        # record_trade() and the bankroll read both hit SQLite synchronously.
        # Batch them in one to_thread call so neither blocks the event loop.
        def _record() -> tuple[str, float]:
            trade_id = self._paper.record_trade(analysis)
            bankroll = self._paper.get_notional_bankroll()
            return trade_id, bankroll

        trade_id, bankroll = await asyncio.to_thread(_record)
        log.info(
            "[PAPER] %s | %s %s | edge=%+.3f | $%.2f | notional=$%.2f",
            trade_id,
            analysis.market.ticker,
            analysis.side.upper(),
            analysis.edge,
            analysis.capped_dollars,
            bankroll,
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
        # Hard safety gate: executor initialized in paper mode must never place live orders.
        # This fires only if the routing logic is wrong -- belt-and-suspenders.
        if self._is_paper:
            log.error(
                "[LIVE_GUARD] BLOCKED live order for %s -- executor initialized in paper mode. "
                "This is a bug: _execute_live must only be called from live-mode executors.",
                analysis.market.ticker,
            )
            return None

        # P-5 LD-10: live order price comes from the executed-side ask
        # cents the side selector chose, not a yes_ask/yes_bid pair that
        # silently inverted on NO trades. Fail-closed when missing.
        if analysis.executed_price_cents is None:
            log.error(
                "[LIVE_GUARD] BLOCKED live order for %s -- executed_price_cents is None. "
                "P-5 contract violation: side selector must set executed_price_cents.",
                analysis.market.ticker,
            )
            return None
        price_cents  = max(1, min(99, int(analysis.executed_price_cents)))
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
            live_order_kwargs = {
                "order_id": result.order_id,
                "ticker": analysis.market.ticker,
                "side": analysis.side,
                "contracts": contracts,
                "price_cents": price_cents,
                "cost_dollars": cost_dollars,
                "status": result.status,
                "model_probability": analysis.estimated_probability,
                "market_price": float(analysis.executed_price_cents) if analysis.executed_price_cents is not None else 0.0,
                "edge": analysis.edge,
                "min_edge_threshold": self._min_edge_threshold(analysis),
            }
            signal_meta = self._signal_meta(analysis)
            if signal_meta:
                live_order_kwargs["signal_meta"] = signal_meta
            await write_trade_log_async(trade_log.log_live_order, **live_order_kwargs)
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
            "mode":              "paper" if self._is_paper else "live",
            "notional_bankroll": self._paper.get_notional_bankroll(),
            "ticker_cooldowns":  len(self._last_traded),
            "portfolio":         self._paper.portfolio.summary(),
        }
