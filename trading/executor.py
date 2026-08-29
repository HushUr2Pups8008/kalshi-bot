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
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from numbers import Real
from pathlib import Path
from typing import Any, Callable, Optional

from analysis import SignalAnalysis
from analysis.kelly import contracts_from_dollars, kelly_bet
from config import cfg, PAPER_MIN_EDGE, PAPER_BLOCK_SAME_SIDE_DUPLICATE
from kalshi import OrderResult
from kalshi.rest_client import KalshiRestClient
from polymarket.domain_key import pm_domain_key
from tasks.kalshi_execution_liquidity import fetch_kalshi_execution_liquidity
from trading.execution_terms import FinalExecutionTerms, final_execution_terms
from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    INITIAL_ORDER_FEE_ACCUMULATOR,
    FeeContext,
    FeeRole,
    FeeUnscorableError,
    fee_coefficient_for,
    fee_schedule_at,
    quote_fee,
)
from trading.live_submission_hold import LIVE_SUBMISSION_HOLD_PATH, LiveSubmissionHoldStore
from trading.paper_trader import PaperTrader
from trading.venue import Venue
from utils.logger import get_logger, trade_log, write_trade_log_async

log = get_logger("executor")

# Cooldowns moved to BotConfig (cfg.live_ticker_cooldown / cfg.paper_ticker_cooldown).
# Read from cfg at call time so .env changes take effect without code edits.

_UNVERIFIED_ORDER_IDS = frozenset({"unknown", "none", "null"})
_PAPER_ENTRY_COHORT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}$")
_PAPER_ENTRY_LIFECYCLE_RE = re.compile(r"lc-[0-9a-f]{32}$")


def _is_verified_order_id(order_id: object) -> bool:
    return (
        isinstance(order_id, str) and bool(order_id.strip()) and order_id.strip().lower() not in _UNVERIFIED_ORDER_IDS
    )


def _fee_net_paper_entry_request_id(analysis: SignalAnalysis) -> str | None:
    """Build the retry-stable fee-net entry key from durable execution lineage."""

    signal_meta = getattr(analysis, "signal_meta", None)
    lifecycle_id = signal_meta.get("lifecycle_id") if isinstance(signal_meta, dict) else None
    cohort_id = getattr(cfg, "paper_cohort_id", None)
    if (
        not isinstance(lifecycle_id, str)
        or _PAPER_ENTRY_LIFECYCLE_RE.fullmatch(lifecycle_id) is None
        or not isinstance(cohort_id, str)
        or _PAPER_ENTRY_COHORT_RE.fullmatch(cohort_id) is None
    ):
        return None
    return f"paper-entry:v1:{cohort_id}:{lifecycle_id}"


def classify_skip_category(reason: str | None) -> str:
    """Stable high-level bucket for executor SKIPPED reasons."""
    text = str(reason or "").lower()
    if not text.strip():
        return "unknown"
    if "cooldown" in text:
        return "cooldown"
    if "duplicate" in text or "same-signal" in text:
        return "duplicate"
    if "concentration" in text or "per-prefix cap" in text:
        return "concentration"
    if (
        "illiquid" in text
        or "near limit" in text
        or "price unavailable" in text
        or "not tradeable" in text
        or "execution_depth" in text
    ):
        return "liquidity"
    if "decision_grade_research" in text:
        return "research"
    return "other"


def _is_research_paper_review_signal(signal_meta: dict[str, Any]) -> bool:
    return str(signal_meta.get("research_admission_status") or "") == "decision_grade_candidate"


def _skip_headline(analysis: SignalAnalysis) -> str:
    news = getattr(analysis, "news_item", None)
    headline = getattr(news, "headline", None)
    if isinstance(headline, str) and headline.strip():
        return headline[:80]
    title = getattr(getattr(analysis, "market", None), "title", None)
    if isinstance(title, str) and title.strip():
        return title[:80]
    ticker = getattr(getattr(analysis, "market", None), "ticker", None)
    return str(ticker or "research")[:80]


def _skip_source(analysis: SignalAnalysis) -> str:
    news = getattr(analysis, "news_item", None)
    source = getattr(news, "source", None)
    if isinstance(source, str) and source.strip():
        return source
    meta = getattr(analysis, "signal_meta", None)
    if isinstance(meta, dict):
        trigger = meta.get("trigger_evidence_source")
        if isinstance(trigger, str) and trigger.strip():
            return trigger
    return "research"


def _requires_decision_grade_research_paper_admission(
    analysis: SignalAnalysis,
    signal_meta: dict[str, Any],
) -> bool:
    """Return whether a raw LLM-only paper signal lacks research admission."""
    if _is_research_paper_review_signal(signal_meta):
        return False
    if bool(getattr(analysis, "keywords_matched", ())):
        return False
    magnitude = getattr(analysis, "llm_magnitude", None)
    return magnitude is not None and str(magnitude).strip().lower() not in {"", "none"}


def _correlated_exposure_prefix(market: Any) -> str:
    """Return the per-prefix cap key identifying a market's correlated-exposure family.

    WHY this is venue-aware: the per-prefix cap exists to stop one news event
    from opening N concurrent bets on the SAME underlying contest. The cap key
    must therefore name the *contest family*, not just any shared leading token.

    Kalshi: the leading ``ticker.split('-', 1)[0]`` token IS the contest series
    (e.g. ``KXUSAIRANAGREEMENT-27-26JUL`` -> ``KXUSAIRANAGREEMENT``, shared by
    all its multi-outcome contracts). Kept BYTE-IDENTICAL — this is the default
    branch for anything not flagged as Polymarket.

    Polymarket: the leading slug token is a market-MAKER prefix (e.g. ``ewc``)
    shared across many INDEPENDENT contests (Maine Senate, Georgia Senate, Iowa
    Governor, ...). Splitting on ``-`` would lump ~30 unrelated contests into one
    cap bucket and over-throttle the venue. The canonical per-contest family is
    ``pm_domain_key``'s stem (PROFIT-VENUE-PARITY-001 open-risk #1 mandates a
    SINGLE PM family key — we delegate, never re-derive with a fresh regex). We
    strip the leading ``polymarket_us:`` namespace so the bare stem
    startswith-matches the ticker via ``Portfolio.open_positions_by_prefix``:
    ``ewc-usse-me`` matches both ``...-dem`` and ``...-rep`` of the Maine Senate
    contest (correctly capped together) but not ``ewc-usse-ga-...`` (Georgia,
    correctly independent). If ``pm_domain_key`` degrades to the bare venue
    segment (empty/unrecognized slug, no ``:``), fall back to the coarse
    ``split('-', 1)[0]`` — the conservative direction for an exposure cap.
    """
    ticker = getattr(market, "ticker", "") or ""
    if getattr(market, "venue", None) == Venue.POLYMARKET_US.value:
        try:
            key = pm_domain_key(ticker)
        except ValueError:
            # Defensive: a PM-tagged market carrying a non-PM (e.g. KX*) ticker
            # makes pm_domain_key raise. Never let that crash the cap evaluation
            # (it runs on every trade decision); fall back to the coarse stem —
            # conservative direction (over-group, not over-trade) for a cap.
            return ticker.split("-", 1)[0]
        namespace, sep, stem = key.partition(":")
        if sep and stem:
            return stem
        # Bare 'polymarket_us' (no stem) -> coarse fallback, never crash the cap.
        return ticker.split("-", 1)[0]
    return ticker.split("-", 1)[0]


class TradeExecutor:
    """Routes validated trade signals to paper or live execution."""

    def __init__(
        self,
        rest_client: KalshiRestClient,
        paper_trader: PaperTrader,
        live_submission_hold_path: Path | None = None,
    ):
        self._rest = rest_client
        self._paper = paper_trader
        self._last_traded: dict[str, float] = {}
        self._live_submission_holds = LiveSubmissionHoldStore(
            live_submission_hold_path if live_submission_hold_path is not None else LIVE_SUBMISSION_HOLD_PATH
        )
        # Execution mode frozen at construction -- never re-derived from cfg or DB.
        # cfg.is_paper_trading may be mutated by CLI commands or tests after this
        # point; self._is_paper is intentionally immune to those mutations.
        self._is_paper: bool = cfg.is_paper_trading
        # Live session loss limit state
        self._session_start_balance: Optional[float] = None
        self._live_halted: bool = False
        self._shutdown_callback: Optional[Callable] = None
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
                "[LOSS_LIMIT] Session start balance recorded: $%.2f (halt at $%.2f loss, limit=%.0f%%)",
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
                    ticker,
                    age_secs / 3600,
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
        self._ensure_sized(analysis)
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
        signal_meta = self._signal_meta(analysis)
        if not self._is_paper and _is_research_paper_review_signal(signal_meta):
            skip_reason = "research_paper_review_live_block"
            log.warning(
                "[DECISION] skip ticker=%s mode=live side=%s reason=%s",
                analysis.market.ticker,
                analysis.side.upper(),
                skip_reason,
            )
            await write_trade_log_async(
                trade_log.log_skipped,
                reason=skip_reason,
                skip_category=classify_skip_category(skip_reason),
                ticker=analysis.market.ticker,
                side=analysis.side,
                headline=_skip_headline(analysis),
                source=_skip_source(analysis),
                method="research_decision_grade",
                llm_direction=analysis.llm_direction,
                llm_magnitude=analysis.llm_magnitude,
                model_probability=analysis.estimated_probability,
                market_price=(
                    float(analysis.executed_price_cents) if analysis.executed_price_cents is not None else 0.0
                ),
                edge=analysis.edge,
                min_edge_threshold=self._min_edge_threshold(analysis),
                venue=self._venue_value(analysis.market),
                signal_meta=signal_meta,
            )
            return None
        if self._is_paper and _requires_decision_grade_research_paper_admission(
            analysis,
            signal_meta,
        ):
            skip_reason = "paper_llm_only_requires_decision_grade_research"
        else:
            skip_reason = self._validate(analysis)
        execution_plan: FinalExecutionTerms | None = None
        if skip_reason is None:
            execution_plan, skip_reason = await self._final_execution_plan(analysis)
        if skip_reason is None and self._is_paper:
            assert execution_plan is not None
            skip_reason = self._paper_fee_admission_skip_reason(
                analysis,
                execution_plan,
            )
        if skip_reason:
            effective_min_edge = self._min_edge_threshold(analysis)
            method = (
                "llm"
                if any(
                    value is not None
                    for value in (analysis.llm_direction, analysis.llm_magnitude, analysis.llm_confidence)
                )
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
                "skip_category": classify_skip_category(skip_reason),
                "ticker": analysis.market.ticker,
                "side": analysis.side,
                "headline": _skip_headline(analysis),
                "source": _skip_source(analysis),
                "method": method,
                "llm_direction": analysis.llm_direction,
                "llm_magnitude": analysis.llm_magnitude,
                "model_probability": analysis.estimated_probability,
                "market_price": float(analysis.executed_price_cents)
                if analysis.executed_price_cents is not None
                else 0.0,
                "edge": analysis.edge,
                "min_edge_threshold": effective_min_edge,
                "venue": self._venue_value(analysis.market),
            }
            execution_signal_meta = self._signal_meta(analysis)
            if execution_signal_meta:
                skipped_kwargs["signal_meta"] = execution_signal_meta
            await write_trade_log_async(trade_log.log_skipped, **skipped_kwargs)
            return None
        assert execution_plan is not None

        if self._is_paper:
            log.debug(
                "[DECISION] route ticker=%s mode=paper side=%s",
                analysis.market.ticker,
                analysis.side.upper(),
            )
            trade_id = await self._execute_paper(analysis, execution_plan=execution_plan)
        else:
            log.debug(
                "[DECISION] route ticker=%s mode=live side=%s",
                analysis.market.ticker,
                analysis.side.upper(),
            )
            trade_id = await self._execute_live(analysis, execution_plan=execution_plan)

        if trade_id:
            self._last_traded[analysis.market.ticker] = time.monotonic()
        else:
            log.debug(
                "[DECISION] no_trade_id ticker=%s mode=%s side=%s after successful validation",
                analysis.market.ticker,
                "paper" if self._is_paper else "live",
                analysis.side.upper(),
            )

        return trade_id

    def _ensure_sized(self, analysis: SignalAnalysis) -> None:
        """Kelly-size research admissions. News already sized in main.py."""
        if float(getattr(analysis, "capped_dollars", 0.0) or 0.0) > 0:
            return
        executed = analysis.executed_price_cents
        if (
            isinstance(executed, bool)
            or not isinstance(executed, int)
            or not 0 < executed < 100
        ):
            return
        side = str(getattr(analysis, "side", "") or "").strip().lower()
        estimated = float(analysis.estimated_probability)
        side_probability = estimated if side == "yes" else 1.0 - estimated
        notional = self._sizing_bankroll()
        from analysis.market_matcher import _days_to_close
        from utils.event_news_research import event_news_horizon_days

        days_to_close = event_news_horizon_days(
            analysis.market, _days_to_close(analysis.market.close_time) or 14.0
        )
        min_edge = PAPER_MIN_EDGE if self._is_paper else cfg.min_edge
        _frac, kelly_dollars, capped_dollars = kelly_bet(
            estimated_probability=side_probability,
            market_price_cents=float(executed),
            bankroll=notional,
            kelly_fraction=cfg.kelly_fraction,
            max_bet_dollars=cfg.dynamic_max_bet(notional),
            min_bet_dollars=cfg.min_bet_dollars,
            min_edge=min_edge,
            confidence=float(getattr(analysis, "confidence", 1.0) or 1.0),
            days_to_close=days_to_close,
            time_discount_half_life=cfg.time_discount_half_life,
            time_discount_floor=cfg.time_discount_floor,
        )
        analysis.kelly_fraction = _frac
        analysis.kelly_dollars = kelly_dollars
        analysis.capped_dollars = capped_dollars

    def _sizing_bankroll(self) -> float:
        getter = getattr(self._paper, "get_effective_sizing_bankroll", None)
        if callable(getter):
            value = getter()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value)
        return float(self._paper.get_notional_bankroll())

    def _validate(self, analysis: SignalAnalysis) -> Optional[str]:
        """Return skip reason, or None if the trade should proceed."""
        # Use relaxed edge threshold during paper trading
        effective_min_edge = self._min_edge_threshold(analysis)

        if analysis.capped_dollars <= 0:
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
        price_ceil = 98 if self._is_paper else 97
        if yes_price < price_floor or yes_price > price_ceil:
            return f"price {yes_price:.1f}c is near limit (too illiquid)"

        if self._is_paper:
            from utils.event_news_research import (
                event_news_crossed_asks,
                event_news_missing_snapshot_ask,
                event_news_spread_disagreement,
            )

            snapshot_reason = event_news_missing_snapshot_ask(analysis.market)
            if snapshot_reason:
                return snapshot_reason
            divergence = event_news_spread_disagreement(analysis.market)
            if divergence:
                return divergence
            crossed = event_news_crossed_asks(analysis.market)
            if crossed:
                return crossed

        # Paper ticker cooldown (4h): prevents same ticker being spammed by a burst
        # of headlines on the same topic (e.g. 30 Iran-war articles in one poll cycle).
        if self._is_paper:
            last = self._last_traded.get(analysis.market.ticker, float("-inf"))
            elapsed = time.monotonic() - last
            if elapsed < cfg.paper_ticker_cooldown:
                return (
                    f"paper cooldown: last trade {elapsed / 3600:.1f}h ago "
                    f"(cooldown={cfg.paper_ticker_cooldown // 3600}h)"
                )

        # PROFIT-ALIGN-004 (2026-05-25): per-market-prefix open-position cap.
        # The matcher can produce multiple outcome-contract matches in the same
        # series (e.g. KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH / -KPAX / -JCOR all
        # share the KXTXRUNOFFENDORSE prefix). Same-signal-guard catches the
        # exact-ticker case but not the multi-outcome case. Without this cap,
        # one piece of news can cause N concurrent bets against the same
        # underlying topic; if the bot's read is wrong, losses compound.
        # cfg.max_open_positions_per_prefix (default 2) gates the Nth open.
        from utils.event_news_research import (
            event_news_exposure_prefix,
            event_news_open_prefix_cap,
        )

        prefix_cap = event_news_open_prefix_cap(cfg.max_open_positions_per_prefix)
        if prefix_cap > 0:
            prefix = event_news_exposure_prefix(
                analysis.market,
                _correlated_exposure_prefix(analysis.market),
            )
            if prefix:
                open_in_prefix = self._paper.portfolio.open_positions_by_prefix(prefix)
                if len(open_in_prefix) >= prefix_cap:
                    open_tickers = sorted({p.ticker for p in open_in_prefix})
                    return (
                        f"per-prefix cap: {len(open_in_prefix)} open in {prefix} "
                        f"(cap={prefix_cap}, "
                        f"open={open_tickers})"
                    )

        # Multi-position guard: block opposing trades (no hedges) and duplicate
        # signals (same side, same probability estimate, same market price).
        # Reads from the in-memory Portfolio — no DB query at decision time.
        for pos in self._paper.portfolio.open_positions(analysis.market.ticker):
            if pos.side != analysis.side:
                return (
                    f"opposing position exists: open {pos.side.upper()} at est={pos.estimated_prob:.3f} -- no hedging"
                )
            # Paper phase: allow same-side re-entry only if the estimated probability
            # has shifted significantly (>=0.07) OR market price has moved (>=5c).
            # This replaces the former flat block (PAPER_BLOCK_SAME_SIDE_DUPLICATE)
            # which suppressed 11 valid follow-on signals over the first 30 days.
            analysis_price = float(analysis.executed_price_cents) if analysis.executed_price_cents is not None else 0.0
            if self._is_paper and PAPER_BLOCK_SAME_SIDE_DUPLICATE:
                prob_delta_paper = abs(pos.estimated_prob - analysis.estimated_probability)
                price_delta_paper = abs(pos.entry_price_cents - analysis_price)
                if prob_delta_paper < 0.07 and price_delta_paper < 5.0:
                    return (
                        f"paper duplicate skip: open {pos.side.upper()} at "
                        f"est={pos.estimated_prob:.3f} (delta={prob_delta_paper:.3f}) "
                        f"-- prob shift too small to add position"
                    )
            prob_delta = abs(pos.estimated_prob - analysis.estimated_probability)
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
        notional = (
            self._paper.get_effective_sizing_bankroll()
            if isinstance(self._paper, PaperTrader)
            else self._paper.get_notional_bankroll()
        )
        # F-08 (Site 2): executed_price_cents must be non-None here — Site 1's
        # fail-closed gate returns early when it is None. The assert encodes that
        # invariant; it should never fire in practice. The legacy midpoint fallback
        # (elif price_available / else 50) has been removed; it masked producer bugs.
        assert analysis.executed_price_cents is not None, (  # noqa: S101
            "Site 1 F-08 gate failed: executed_price_cents is None at Site 2. This is a bug in _validate control flow."
        )
        try:
            final_terms = final_execution_terms(
                capped_dollars=analysis.capped_dollars,
                executed_price_cents=analysis.executed_price_cents,
            )
        except ValueError as exc:
            self._set_final_execution_depth_meta(
                analysis,
                {
                    "source": "execution_terms",
                    "status": "invalid",
                    "reason": str(exc),
                },
            )
            return "G8_execution_depth_invalid_terms"
        if self._is_paper:
            # PROFIT-SIZING-001b: paper sizes by Kelly now (mirrors live, matches
            # paper_trader). Concentration pre-check uses the actual Kelly
            # contract cost rather than the retired flat-5 estimate.
            trade_cost = final_terms.cost_dollars
        else:
            trade_cost = analysis.capped_dollars
        if self._is_paper and trade_cost > notional:
            return f"paper contract cost ${trade_cost:.2f} exceeds notional bankroll ${notional:.2f}"
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

            last = self._last_traded.get(analysis.market.ticker, float("-inf"))
            elapsed = time.monotonic() - last
            if elapsed < cfg.live_ticker_cooldown:
                return f"cooldown: last trade {elapsed:.0f}s ago (cooldown={cfg.live_ticker_cooldown}s)"
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

    async def _final_execution_plan(
        self,
        analysis: SignalAnalysis,
    ) -> tuple[FinalExecutionTerms | None, str | None]:
        """Fail closed unless the final Kalshi order has full fresh executable depth."""

        try:
            plan = final_execution_terms(
                capped_dollars=analysis.capped_dollars,
                executed_price_cents=analysis.executed_price_cents,
            )
        except ValueError as exc:
            self._set_final_execution_depth_meta(
                analysis,
                {
                    "source": "execution_terms",
                    "status": "invalid",
                    "reason": str(exc),
                },
            )
            return None, "G8_execution_depth_invalid_terms"
        if self._venue_value(analysis.market) != Venue.KALSHI.value:
            return plan, None

        try:
            liquidity = await fetch_kalshi_execution_liquidity(self._rest, analysis)
            available_contracts = Decimal(liquidity.executable_quantity)
            record = {
                "source": "kalshi_orderbook",
                "status": "sufficient"
                if available_contracts >= Decimal(plan.contracts)
                else "insufficient",
                "side": liquidity.side,
                "limit_price": float(liquidity.limit_price),
                "requested_contracts": plan.contracts,
                "available_contracts": float(available_contracts),
                "available_notional": float(liquidity.executable_notional),
                "as_of": liquidity.as_of.isoformat(),
                "raw_payload_hash": liquidity.raw_payload_hash,
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            fallback_plan, fallback_reason = self._event_news_research_rest_execution_plan(
                analysis,
                plan,
            )
            if fallback_plan is not None:
                return fallback_plan, None
            if fallback_reason is not None:
                return None, fallback_reason
            self._set_final_execution_depth_meta(
                analysis,
                {
                    "source": "kalshi_orderbook",
                    "status": "unavailable",
                    "reason": type(exc).__name__,
                    "requested_contracts": plan.contracts,
                },
            )
            log.warning(
                "[EXECUTION_DEPTH] %s unavailable for %d contracts: %s",
                analysis.market.ticker,
                plan.contracts,
                exc,
            )
            return None, "G8_execution_depth_unavailable"

        self._set_final_execution_depth_meta(analysis, record)
        if available_contracts < Decimal(plan.contracts):
            log.warning(
                "[EXECUTION_DEPTH] %s insufficient side=%s requested=%d available=%s",
                analysis.market.ticker,
                analysis.side,
                plan.contracts,
                available_contracts,
            )
            return None, "G8_execution_depth_insufficient"
        return plan, None

    def _event_news_research_rest_execution_plan(
        self,
        analysis: SignalAnalysis,
        plan: FinalExecutionTerms,
    ) -> tuple[FinalExecutionTerms | None, str | None]:
        """Paper politics official-p only. Freeze and live stay fail-closed."""
        if not self._is_paper:
            return None, None
        if str(getattr(analysis, "signal_type", "") or "") != "research_decision_grade":
            return None, None
        from utils.event_news_research import event_news_executable_top_size

        available = event_news_executable_top_size(
            analysis.market,
            str(getattr(analysis, "side", "") or ""),
        )
        if available is None:
            return None, None
        available_contracts = Decimal(str(available))
        if available_contracts < Decimal(plan.contracts):
            self._set_final_execution_depth_meta(
                analysis,
                {
                    "source": "rest_top_size",
                    "status": "insufficient",
                    "reason": "orderbook_unavailable",
                    "requested_contracts": plan.contracts,
                    "available_contracts": float(available_contracts),
                },
            )
            return None, "G8_execution_depth_insufficient"
        self._set_final_execution_depth_meta(
            analysis,
            {
                "source": "rest_top_size",
                "status": "fallback",
                "reason": "orderbook_unavailable",
                "requested_contracts": plan.contracts,
                "available_contracts": float(available_contracts),
            },
        )
        return plan, None

    def _set_final_execution_depth_meta(
        self,
        analysis: SignalAnalysis,
        record: dict[str, object],
    ) -> None:
        analysis.signal_meta = {
            **self._signal_meta(analysis),
            "final_execution_depth": record,
        }

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
        preserved_signal_type = str(getattr(analysis, "signal_type", "") or "")

        ticker = candidate.market.ticker
        fresh_market = None
        candidate_venue = self._venue_value(candidate.market)
        if candidate_venue == "kalshi":
            try:
                fresh_market = await asyncio.to_thread(self._rest.get_market, ticker)
            except Exception as exc:
                log.warning(
                    "[BLEND_REFETCH] %s fetch failed: %s -- falling back to candidate snapshot",
                    ticker,
                    exc,
                )
        else:
            fresh_market = candidate.market
            log.debug(
                "[BLEND_REFETCH] %s venue=%s using venue snapshot (no Kalshi refetch)",
                ticker,
                candidate_venue,
            )

        if fresh_market is None:
            # Fetch unavailable. Fall back to candidate snapshot if it is
            # itself tradeable; otherwise fail-closed.
            if not candidate.market.is_tradeable():
                log.warning(
                    "[BLEND_REFETCH] %s candidate market not tradeable and refetch returned None -- skipping",
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
                "[BLEND_REFETCH] %s not tradeable post-blend (price_available=%s) -- skipping",
                ticker,
                fresh_market.price_available,
            )
            return None

        analysis.market = fresh_market
        analysis.estimated_probability = candidate.blended_probability

        # Re-derive side + executable price against the fresh book.
        try:
            from analysis.side_selection import select_side, compute_edge

            new_side, new_executed_cents = select_side(fresh_market, candidate.blended_probability)
            edges = compute_edge(fresh_market, candidate.blended_probability)
        except ValueError as exc:
            log.debug(
                "[BLEND_REFETCH] %s side selection failed post-refetch: %s",
                ticker,
                exc,
            )
            return None

        analysis.side = new_side
        analysis.executed_price_cents = new_executed_cents
        analysis.edge = edges.yes_edge if new_side == "yes" else edges.no_edge
        if preserved_signal_type != "research_decision_grade":
            analysis.signal_type = "blend"
        analysis.signal_meta = signal_meta
        return analysis

    @staticmethod
    def _venue_value(market: Any) -> str:
        raw = getattr(market, "venue", None)
        if raw is None:
            return "kalshi"
        if isinstance(raw, str):
            return raw.strip().lower() or "kalshi"
        value = getattr(raw, "value", None)
        if isinstance(value, str):
            return value.strip().lower() or "kalshi"
        return "kalshi"

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
        if self._is_paper:
            from utils.event_news_research import event_news_min_edge

            base_threshold = event_news_min_edge(base_threshold, analysis.market)
        signal_meta = self._signal_meta(analysis)
        override = signal_meta.get("readiness_gate_min_edge_override")
        if override is None:
            return base_threshold
        if isinstance(override, bool) or not isinstance(override, Real):
            raise ValueError("readiness_gate_min_edge_override must be numeric")
        if override < 0.0:
            raise ValueError("readiness_gate_min_edge_override must be non-negative")
        return float(override)

    def _paper_fee_admission_skip_reason(
        self,
        analysis: SignalAnalysis,
        execution_plan: FinalExecutionTerms,
    ) -> str | None:
        """Return a skip reason unless a final Kalshi paper entry clears its entry fee."""
        if self._venue_value(analysis.market) != Venue.KALSHI.value:
            return None

        quantity = Decimal(execution_plan.contracts)
        price = Decimal(execution_plan.price_cents) / Decimal("100")
        timestamp = datetime.now(timezone.utc)
        try:
            schedule_id = fee_schedule_at(
                venue=Venue.KALSHI,
                timestamp=timestamp,
            )
            context = FeeContext(
                schedule_id=schedule_id,
                role=FeeRole.TAKER,
                quantity=quantity,
                price=price,
                signed_revenue=-(quantity * price),
                order_id="paper-admission",
                accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
                multiplier=Decimal("1"),
                coefficient=fee_coefficient_for(schedule_id, FeeRole.TAKER),
                account_precision=DIRECT_ACCOUNT_PRECISION,
                timestamp=timestamp,
            )
            entry_fee = quote_fee(context).net_fee
            gross_edge = Decimal(str(analysis.edge)) * quantity
            if (
                not isinstance(entry_fee, Decimal)
                or not entry_fee.is_finite()
                or entry_fee < 0
                or not gross_edge.is_finite()
            ):
                raise FeeUnscorableError("entry fee or expected gross edge is not finite")
        except (ArithmeticError, FeeUnscorableError, TypeError, ValueError) as exc:
            log.warning(
                "[FEE_ADMISSION] skip ticker=%s reason=unscorable error=%s",
                analysis.market.ticker,
                type(exc).__name__,
            )
            return "fee_admission_unscorable"

        if gross_edge <= entry_fee:
            log.warning(
                "[FEE_ADMISSION] skip ticker=%s schedule=%s gross_edge=$%s entry_fee=$%s",
                analysis.market.ticker,
                schedule_id.name,
                gross_edge,
                entry_fee,
            )
            return "fee_admission_non_positive_net_edge"
        return None

    async def _execute_paper(
        self,
        analysis: SignalAnalysis,
        *,
        execution_plan: FinalExecutionTerms | None = None,
    ) -> Optional[str]:
        entry_request_id: str | None = None
        fee_net_accounting = bool(cfg.enable_fee_net_paper_accounting)
        if fee_net_accounting:
            entry_request_id = _fee_net_paper_entry_request_id(analysis)
            if entry_request_id is None:
                log.error(
                    "[PAPER] Skipping fee-net paper entry for %s: missing or invalid lifecycle identity",
                    analysis.market.ticker,
                )
                return None

        # record_trade() and the bankroll read both hit SQLite synchronously.
        # Batch them in one to_thread call so neither blocks the event loop.
        def _record() -> tuple[str, float]:
            if fee_net_accounting:
                assert entry_request_id is not None
                if execution_plan is None:
                    result = self._paper.record_trade_result(
                        analysis,
                        entry_request_id=entry_request_id,
                    )
                else:
                    result = self._paper.record_trade_result(
                        analysis,
                        entry_request_id=entry_request_id,
                        execution_terms=execution_plan,
                    )
                trade_id = result.trade_id if result.created else ""
            elif execution_plan is None:
                trade_id = self._paper.record_trade(analysis)
            else:
                trade_id = self._paper.record_trade(
                    analysis,
                    execution_terms=execution_plan,
                )
            bankroll = self._paper.get_notional_bankroll()
            return trade_id, bankroll

        trade_id, bankroll = await asyncio.to_thread(_record)
        if not trade_id:
            if fee_net_accounting:
                log.debug(
                    "[PAPER] Fee-net entry replayed or rejected for %s",
                    analysis.market.ticker,
                )
            return trade_id
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

    async def _execute_live(
        self,
        analysis: SignalAnalysis,
        *,
        execution_plan: FinalExecutionTerms | None = None,
    ) -> Optional[str]:
        """Place one legacy live order, preserving unknown outcomes for reconciliation."""
        # Hard safety gate: executor initialized in paper mode must never place live orders.
        # This fires only if the routing logic is wrong -- belt-and-suspenders.
        if self._is_paper:
            log.error(
                "[LIVE_GUARD] BLOCKED live order for %s -- executor initialized in paper mode. "
                "This is a bug: _execute_live must only be called from live-mode executors.",
                analysis.market.ticker,
            )
            return None

        if not self._live_submission_holds.can_submit(analysis.market.ticker):
            log.error(
                "[LIVE_GUARD] BLOCKED live order for %s: unknown submission hold is active or unavailable.",
                analysis.market.ticker,
            )
            return None

        # P-5 LD-10: live order price comes from the executed-side ask
        # cents the side selector chose, not a yes_ask/yes_bid pair that
        # silently inverted on NO trades. Fail-closed when missing.
        if execution_plan is None:
            if analysis.executed_price_cents is None:
                log.error(
                    "[LIVE_GUARD] BLOCKED live order for %s -- executed_price_cents is None. "
                    "P-5 contract violation: side selector must set executed_price_cents.",
                    analysis.market.ticker,
                )
                return None
            price_cents = max(1, min(99, int(analysis.executed_price_cents)))
            contracts = contracts_from_dollars(analysis.capped_dollars, float(price_cents))
            cost_dollars = contracts * price_cents / 100.0
        else:
            price_cents = execution_plan.price_cents
            contracts = execution_plan.contracts
            cost_dollars = execution_plan.cost_dollars

        if contracts <= 0:
            log.warning("Live order aborted: contracts=0 for $%.2f @ %dc", analysis.capped_dollars, price_cents)
            return None

        submission_summary = {
            "submission_id": uuid.uuid4().hex,
            "ticker": analysis.market.ticker,
            "side": analysis.side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": cost_dollars,
        }
        signal_meta = self._signal_meta(analysis)
        intent_kwargs = {
            **submission_summary,
            "venue": self._venue_value(analysis.market),
        }
        if signal_meta:
            intent_kwargs["signal_meta"] = signal_meta

        try:
            await write_trade_log_async(
                trade_log.log_live_submission_intent,
                **intent_kwargs,
            )
        except Exception:
            log.error(
                "[LIVE] BLOCKED order for %s: submission intent persistence failed",
                analysis.market.ticker,
            )
            return None

        if not self._live_submission_holds.reserve(analysis.market.ticker):
            log.error(
                "[LIVE_GUARD] BLOCKED live order for %s: submission reservation is active "
                "or could not be persisted; no POST was made.",
                analysis.market.ticker,
            )
            return None

        async def record_unknown_and_hold(
            outcome: str,
            *,
            venue_order_id: str | None = None,
        ) -> None:
            if not self._live_submission_holds.hold(analysis.market.ticker):
                log.error(
                    "[LIVE_GUARD] Submission reservation persistence failed for %s; blocking later live posts.",
                    analysis.market.ticker,
                )
            unknown_kwargs = {
                **submission_summary,
                "outcome": outcome,
                "venue": self._venue_value(analysis.market),
            }
            if signal_meta:
                unknown_kwargs["signal_meta"] = signal_meta
            if venue_order_id is not None:
                unknown_kwargs["venue_order_id"] = venue_order_id
            try:
                await write_trade_log_async(
                    trade_log.log_live_submission_unknown,
                    **unknown_kwargs,
                )
            except Exception:
                log.error(
                    "[LIVE] Submission outcome persistence failed for %s; manual reconciliation required",
                    analysis.market.ticker,
                )

        log.info(
            "[LIVE] Submitting one order: %s %s %d @ %dc | cost=$%.2f | edge=%+.3f | confidence=%.2f",
            analysis.market.ticker,
            analysis.side.upper(),
            contracts,
            price_cents,
            cost_dollars,
            analysis.edge,
            analysis.confidence,
        )

        loop = asyncio.get_running_loop()
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
            result_error = result.error
        except asyncio.CancelledError:
            await record_unknown_and_hold("cancelled")
            raise
        except Exception:
            await record_unknown_and_hold("exception")
            log.error(
                "[LIVE] Submission outcome unknown after one POST for %s",
                analysis.market.ticker,
            )
            return None

        if result_error:
            await record_unknown_and_hold("error_result")
            log.error(
                "[LIVE] Submission outcome unknown after one POST for %s",
                analysis.market.ticker,
            )
            return None

        try:
            order_id = result.order_id
        except asyncio.CancelledError:
            await record_unknown_and_hold("cancelled")
            raise
        except Exception:
            await record_unknown_and_hold("unverified_receipt")
            log.error(
                "[LIVE] Submission receipt is unverified after one POST for %s",
                analysis.market.ticker,
            )
            return None

        if not _is_verified_order_id(order_id):
            await record_unknown_and_hold("unverified_receipt")
            log.error(
                "[LIVE] Submission receipt is unverified after one POST for %s",
                analysis.market.ticker,
            )
            return None

        try:
            status = result.status
            filled = result.filled
            live_order_kwargs = {
                "order_id": order_id,
                **submission_summary,
                "status": status,
                "venue": self._venue_value(analysis.market),
                "model_probability": analysis.estimated_probability,
                "market_price": float(analysis.executed_price_cents),
                "edge": analysis.edge,
                "min_edge_threshold": self._min_edge_threshold(analysis),
            }
            if signal_meta:
                live_order_kwargs["signal_meta"] = signal_meta
            await write_trade_log_async(trade_log.log_live_order, **live_order_kwargs)
        except asyncio.CancelledError:
            await record_unknown_and_hold(
                "live_order_journal_failure",
                venue_order_id=order_id,
            )
            raise
        except Exception:
            await record_unknown_and_hold(
                "live_order_journal_failure",
                venue_order_id=order_id,
            )
            log.error(
                "[LIVE] LIVE_ORDER journal failed after venue response for %s",
                analysis.market.ticker,
            )
            return None
        if not self._live_submission_holds.release(analysis.market.ticker):
            log.error(
                "[LIVE_GUARD] LIVE_ORDER journal is durable for %s, but submission "
                "reservation release failed; later live posts remain blocked.",
                analysis.market.ticker,
            )
        log.info(
            "[LIVE] Order placed: %s | status=%s | filled=%d",
            order_id,
            status,
            filled,
        )
        return order_id

    def status(self) -> dict:
        return {
            "mode": "paper" if self._is_paper else "live",
            "notional_bankroll": self._paper.get_notional_bankroll(),
            "ticker_cooldowns": len(self._last_traded),
            "portfolio": self._paper.portfolio.summary(),
        }
