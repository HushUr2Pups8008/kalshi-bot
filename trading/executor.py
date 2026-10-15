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
from typing import Any, Callable, Mapping, Optional

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
from trading.portfolio import PAPER_SHADOW_SIGNAL_TYPE
from trading.venue import Venue
from utils.logger import get_logger, trade_log, write_trade_log_async

log = get_logger("executor")

# Runtime appends stay out of git. The daily copy writes the report.
_REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_FILL_LOG_PATH = _REPO_ROOT / "logs" / "state" / "live-fill-log.md"
LIVE_FILL_LOG_ARCHIVE_PATH = _REPO_ROOT / "docs" / "reports" / "live-fill-log.md"
_LIVE_BUSINESS_REJECT_STATUSES = frozenset({400, 404, 409, 422})
_LIVE_BUSINESS_REJECT_TOKENS = (
    "not_found",
    "insufficient",
    "invalid_order",
    "invalid_price",
    "price_band",
)

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


def _event_news_official_p_may_live(signal_meta: dict[str, Any]) -> bool:
    """Politics official-p decision-grade may live-POST; generic LLM review may not."""
    from utils.event_news_research import is_event_news_paper_cohort

    if not is_event_news_paper_cohort():
        return False
    if str(signal_meta.get("blend_bypass_reason") or "") == "official_p":
        return True
    return str(signal_meta.get("source_lane") or "") == "research_official_p"


def _position_market_ticker(position: Any) -> str:
    if isinstance(position, dict):
        return str(position.get("ticker") or position.get("market_ticker") or "").strip()
    return str(getattr(position, "ticker", "") or "").strip()


_LIVE_POSITIONS_UNSET = object()


def _positive_dollars(value: Any) -> float | None:
    """A usable cash read. None, bool, and <= 0 do not seed a baseline."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    if number != number or number <= 0:
        return None
    return number


def _position_is_open(position: Any) -> bool:
    if isinstance(position, dict):
        for key in ("position", "exposure", "yes_count", "no_count", "contracts"):
            raw = position.get(key)
            if isinstance(raw, bool) or raw is None:
                continue
            try:
                if abs(float(raw)) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return bool(_position_market_ticker(position))
    return True


def _live_http_result(result: Any) -> str:
    status = getattr(result, "http_status", None)
    error = getattr(result, "error", None)
    if not error:
        return str(status or "2xx")
    if status is not None:
        return f"{status} {error}".strip()
    return str(error)


def _is_live_business_reject(result: Any) -> bool:
    status = getattr(result, "http_status", None)
    if status in _LIVE_BUSINESS_REJECT_STATUSES:
        return True
    error = str(getattr(result, "error", "") or "").lower()
    return any(token in error for token in _LIVE_BUSINESS_REJECT_TOKENS)


def _detail_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except Exception:
        return None
    if not number.is_finite():
        return None
    return number


def _detail_number_text(value: Decimal) -> str:
    integral = value.to_integral()
    if value == integral:
        return str(int(integral))
    return format(value.normalize(), "f")


def _detail_cents_text(dollars: Decimal) -> str:
    return _detail_number_text(dollars * Decimal("100"))


def fill_log_detail_from_receipts(
    *,
    side: str,
    order: Mapping[str, Any] | None,
    fills: list[Any] | None,
) -> dict[str, str | None]:
    """Map a GET order receipt and fills page onto the four detail columns.

    Economics come from the fills page when it has usable rows, and from the
    order receipt otherwise. The logged order_id is the receipt's order_id.
    """
    price_key = "yes_price_dollars" if str(side).lower() == "yes" else "no_price_dollars"
    order_id = None
    if isinstance(order, Mapping):
        raw_order_id = order.get("order_id")
        if isinstance(raw_order_id, str) and raw_order_id.strip():
            order_id = raw_order_id.strip()
    filled_count = None
    avg_fill_cents = None
    fee_cents = None
    total_count = Decimal("0")
    total_cost = Decimal("0")
    total_fee = Decimal("0")
    usable_fills = False
    for fill in fills or ():
        if not isinstance(fill, Mapping):
            continue
        count = _detail_number(fill.get("count_fp"))
        price = _detail_number(fill.get(price_key))
        fee_dollars = _detail_number(fill.get("fee_cost"))
        if count is None or price is None or fee_dollars is None or count <= 0:
            continue
        total_count += count
        total_cost += count * price
        total_fee += fee_dollars
        usable_fills = True
    if usable_fills and total_count > 0:
        filled_count = _detail_number_text(total_count)
        avg_fill_cents = _detail_cents_text(total_cost / total_count)
        fee_cents = _detail_cents_text(total_fee)
    elif isinstance(order, Mapping):
        count = _detail_number(order.get("fill_count_fp"))
        price = _detail_number(order.get(price_key))
        taker = _detail_number(order.get("taker_fees_dollars"))
        maker = _detail_number(order.get("maker_fees_dollars"))
        if count is not None:
            filled_count = _detail_number_text(count)
        if price is not None:
            avg_fill_cents = _detail_cents_text(price)
        if taker is not None or maker is not None:
            fee_cents = _detail_cents_text((taker or Decimal("0")) + (maker or Decimal("0")))
    return {
        "filled_count": filled_count,
        "avg_fill_cents": avg_fill_cents,
        "fee_cents": fee_cents,
        "order_id": order_id,
    }


def fetch_live_fill_detail(rest: Any, order_id: str, side: str) -> dict[str, str | None]:
    """Read fill detail after a 201. A failed GET leaves the columns blank.

    The 201 order_id is only the lookup key. It is not copied into the log.
    """
    blank = {
        "filled_count": None,
        "avg_fill_cents": None,
        "fee_cents": None,
        "order_id": None,
    }
    lookup = str(order_id or "").strip()
    if not lookup:
        log.warning("[LIVE_FILL_LOG] detail GET skipped; 201 had no order_id")
        return blank
    order = None
    fills = None
    reasons: list[str] = []
    try:
        fetched_order = rest.get_order_receipt(lookup)
        if isinstance(fetched_order, Mapping):
            order = fetched_order
        else:
            reasons.append("order:not_object")
    except Exception as exc:
        reasons.append("order:" + type(exc).__name__)
    try:
        page, _cursor = rest.get_fills_page(order_id=lookup)
        if isinstance(page, list):
            fills = page
        else:
            reasons.append("fills:not_list")
    except Exception as exc:
        reasons.append("fills:" + type(exc).__name__)
    detail = fill_log_detail_from_receipts(side=side, order=order, fills=fills)
    if detail["filled_count"] is None and detail["order_id"] is None:
        log.warning(
            "[LIVE_FILL_LOG] detail GET failed reasons=%s",
            ",".join(reasons) or "empty",
        )
    return detail


def append_live_fill_log(
    *,
    ticker: str,
    side: str,
    price_cents: int,
    contracts: int,
    client_order_id: str,
    http_result: str,
    path: Path | None = None,
    filled_count: int | str | None = None,
    avg_fill_cents: int | str | None = None,
    fee_cents: int | str | None = None,
    order_id: str | None = None,
    environ: Mapping[str, str] | None = None,
    role: str | None = None,
) -> None:
    """Append one Desk Ops line. Never logs keys.

    Extra columns are written only when EVENT_NEWS_FILL_LOG_DETAIL=true.
    Callers must pass those fields from a later GET. The 201 body has no
    exchange fee and is not a fill receipt.
    """
    from utils.event_news_grok_flags import event_news_fill_log_detail_enabled

    target = path or LIVE_FILL_LOG_PATH
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (
        f"{stamp}, {ticker}, {side}, {price_cents}, {contracts}, "
        f"{client_order_id}, {http_result}"
    )
    if event_news_fill_log_detail_enabled(environ):
        filled_text = "" if filled_count is None else str(filled_count)
        avg_text = "" if avg_fill_cents is None else str(avg_fill_cents)
        fee_text = "" if fee_cents is None else str(fee_cents)
        order_text = "" if not order_id else str(order_id).replace(",", "")
        line = f"{line}, {filled_text}, {avg_text}, {fee_text}, {order_text}"
    if role in {"maker", "taker"}:
        line = f"{line}, role={role}"
    line = f"{line}\n"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        log.warning(
            "[LIVE_FILL_LOG] append failed ticker=%s",
            ticker,
            exc_info=True,
        )


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
        # Paper rows are queued from the admit gate and from a live
        # decision. The lock keeps those rows ordered. The live consumer
        # does not wait on the paper Grok check.
        self._paper_shadow_tasks: set[asyncio.Task[None]] = set()
        self._paper_shadow_lock = asyncio.Lock()
        self._paper_shadow_entry_mono: dict[str, float] = {}
        log.info(
            "[EXECUTOR_STATE] pid=%s mode=%s bankroll_source=%s",
            __import__("os").getpid(),
            "paper" if self._is_paper else "live",
            "notional_db" if self._is_paper else "kalshi_api",
        )
        from utils.event_news_grok_flags import (
            EVENT_NEWS_FLAGS_FATAL,
            event_news_flags_boot_line,
            event_news_live_band_guard_off,
        )

        # Resolved after env and defaults, from the same functions the band uses.
        log.info("%s", event_news_flags_boot_line())
        if event_news_live_band_guard_off(cfg.live_trading_enabled is True):
            # Stay up. _execute_live refuses the POST; do not sys.exit.
            log.error("%s", EVENT_NEWS_FLAGS_FATAL)
        self._seed_cooldowns_from_db()
        self._arm_persisted_session_halt()

    def set_shutdown_callback(self, cb: Callable) -> None:
        """Register a callback that is invoked when the live loss limit is breached."""
        self._shutdown_callback = cb

    def _session_halt_enabled(self) -> bool:
        """Politics live only. Unset stays off. Paper keeps the old no-op."""
        if self._is_paper:
            return False
        from utils.event_news_desk_flags import (
            EVENT_NEWS_SESSION_HALT,
            event_news_desk_flag_enabled,
        )
        from utils.event_news_research import is_event_news_paper_cohort

        if not is_event_news_paper_cohort():
            return False
        return event_news_desk_flag_enabled(EVENT_NEWS_SESSION_HALT, live=True)

    def _arm_persisted_session_halt(self) -> None:
        if not self._session_halt_enabled():
            return
        from utils.event_news_session_halt import halt_latch_present

        if halt_latch_present():
            self._live_halted = True
            log.critical(
                "[SESSION_HALT] latch present -- no new orders until the latch is cleared"
            )

    def _maybe_record_cash_cents(self, cash: float) -> None:
        from utils.event_news_research import is_event_news_paper_cohort

        if self._is_paper or not is_event_news_paper_cohort():
            return
        from utils.event_news_session_halt import record_cash_cents

        record_cash_cents(cash)

    def _read_live_position_rows(self) -> tuple[str, list | None]:
        """One positions GET per _validate. ``fail`` is not an empty book."""
        cached = getattr(self, "_live_position_cache", _LIVE_POSITIONS_UNSET)
        if cached is not _LIVE_POSITIONS_UNSET:
            return cached
        getter = getattr(self._rest, "get_open_positions", None)
        if not callable(getter):
            result: tuple[str, list | None] = ("absent", None)
        else:
            try:
                try:
                    rows = getter(strict=True)
                except TypeError:
                    rows = getter()
            except Exception:
                log.warning(
                    "[EVENT_NEWS_FAMILY_LOCK] live open ticker lookup failed",
                    exc_info=True,
                )
                result = ("fail", None)
            else:
                result = ("ok", rows) if isinstance(rows, list) else ("absent", None)
        self._live_position_cache = result
        return result

    def _session_open_cost_dollars(self) -> float | None:
        """Sum open market_exposure_dollars. A failed read is not zero."""
        status, rows = self._read_live_position_rows()
        if status != "ok" or rows is None:
            return None
        total = 0.0
        for row in rows:
            if not isinstance(row, dict):
                return None
            size = str(row.get("position_fp") or "").strip()
            if size in {"", "0", "0.0", "0.00"}:
                continue
            try:
                dollars = float(row["market_exposure_dollars"])
            except (KeyError, TypeError, ValueError):
                return None
            if dollars < 0:
                return None
            total += dollars
        return total

    def _check_session_equity_halt(self, equity: float) -> bool:
        """Seed on the first successful equity. Halt at min(pct * seed, $10)."""
        from utils.event_news_session_halt import session_halt_loss_line

        if self._session_start_balance is None:
            self._session_start_balance = equity
            line = session_halt_loss_line(equity, cfg.live_loss_limit_pct)
            log.info(
                "[SESSION_HALT] seed equity $%.2f line $%.2f",
                equity,
                line,
            )
            return False
        loss = self._session_start_balance - equity
        line = session_halt_loss_line(self._session_start_balance, cfg.live_loss_limit_pct)
        return loss >= line

    def _trip_session_halt(self, equity: float) -> str:
        """Hold the process. Do not call shutdown: exit 0 leaves launchd down."""
        from utils.event_news_session_halt import (
            session_halt_loss_line,
            write_halt_latch,
        )

        seed = float(self._session_start_balance if self._session_start_balance is not None else equity)
        loss = seed - equity
        line = session_halt_loss_line(seed, cfg.live_loss_limit_pct)
        self._live_halted = True
        write_halt_latch(seed_equity=seed, loss=loss, line=line)
        log.critical(
            "[SESSION_HALT] tripped loss $%.2f >= line $%.2f -- no new orders",
            loss,
            line,
        )
        return "LIVE HALTED: session loss limit reached -- all trading suspended"

    def _check_live_loss_limit(self, balance: float) -> bool:
        """
        Track session-start Kalshi balance and return True if the loss limit is breached.
        First call seeds the baseline; subsequent calls compare against it.
        Paper-mode executors: always returns False (no live balance to track).
        None, bool, and <= 0 do not seed. The politics flag uses equity instead.
        """
        if self._is_paper:
            return False
        usable = _positive_dollars(balance)
        if usable is None:
            return False
        if self._session_start_balance is None:
            self._session_start_balance = usable
            log.info(
                "[LOSS_LIMIT] Session start balance recorded: $%.2f (halt at $%.2f loss, limit=%.0f%%)",
                usable,
                cfg.bankroll * cfg.live_loss_limit_pct,
                cfg.live_loss_limit_pct * 100,
            )
            return False
        loss = self._session_start_balance - usable
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
            positions = self._paper.portfolio.open_positions(ticker)
            if isinstance(positions, list):
                visible = self._positions_for_live_gates(positions)
                if not visible:
                    continue
                latest = max(str(getattr(pos, "ts", "") or "") for pos in visible)
            else:
                latest = self._paper.portfolio.latest_ts(ticker)
            if not latest:
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
        if (
            not self._is_paper
            and _is_research_paper_review_signal(signal_meta)
            and not _event_news_official_p_may_live(signal_meta)
        ):
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
            from utils.event_news_research import event_news_executed_side_skip_reason

            # The fresh book is already on this analysis. A longshot ask
            # is not a paper row, including this early research-review skip.
            if (
                event_news_executed_side_skip_reason(analysis.market, analysis.side)
                != "executed_ask_in_excluded_band"
            ):
                self._schedule_paper_shadow(analysis)
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
        if skip_reason is None and execution_plan is not None:
            skip_reason = self._event_news_plan_skip(analysis, execution_plan)
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
            skip_log = (
                log.info if "per-prefix cap" in skip_reason else log.debug
            )
            skip_log(
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
            if skip_reason != "executed_ask_in_excluded_band":
                self._schedule_paper_shadow(analysis, source="decision")
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

        self._schedule_paper_shadow(analysis, source="decision")
        return trade_id

    def _paper_shadow_source(self, analysis: object) -> str:
        source = str(getattr(analysis, "_event_news_paper_shadow_source", "") or "")
        if source not in {"prefix_cap", "decision"}:
            return "decision"
        return source

    def _schedule_paper_shadow(
        self,
        analysis: SignalAnalysis,
        *,
        source: str = "decision",
    ) -> None:
        """Queue one paper row. Does not place or cancel a live order."""
        if source not in {"prefix_cap", "decision"}:
            source = "decision"
        if self._is_paper:
            return
        from utils.event_news_research import is_event_news_paper_cohort

        if not is_event_news_paper_cohort():
            return
        ticker = getattr(getattr(analysis, "market", None), "ticker", "")
        try:
            snapshot = copy.copy(analysis)
            meta = getattr(analysis, "signal_meta", None)
            if isinstance(meta, dict):
                snapshot.signal_meta = dict(meta)
            snapshot._event_news_paper_shadow_source = source
            task = asyncio.create_task(self._shadow_live_candidate_locked(snapshot))
            self._paper_shadow_tasks.add(task)
            task.add_done_callback(self._paper_shadow_tasks.discard)
        except Exception:
            log.warning(
                "[EVENT_NEWS_PAPER_SHADOW] schedule failed source=%s ticker=%s",
                source,
                ticker,
                exc_info=True,
            )

    async def _shadow_live_candidate_locked(self, analysis: SignalAnalysis) -> None:
        """Serialize paper-book checks. Live validation does not take this lock."""
        async with self._paper_shadow_lock:
            await self._shadow_live_candidate(analysis)

    async def _shadow_live_candidate(self, analysis: SignalAnalysis) -> None:
        """Record the paper book for a live candidate. Never places an order."""
        if self._is_paper:
            return
        try:
            await self._record_event_news_paper_shadow(analysis)
        except Exception:
            log.warning(
                "[EVENT_NEWS_PAPER_SHADOW] record failed source=%s ticker=%s",
                self._paper_shadow_source(analysis),
                getattr(getattr(analysis, "market", None), "ticker", ""),
                exc_info=True,
            )

    def _paper_shadow_book_cents(self) -> tuple[int, int] | None:
        """Paper bankroll and open paper cost, in cents. Live positions are not included."""
        try:
            bankroll = float(self._paper.get_notional_bankroll())
        except (TypeError, ValueError):
            return None
        if isinstance(bankroll, bool) or bankroll <= 0:
            return None
        cash = int(round(bankroll * 100))
        if cash <= 0:
            return None
        try:
            positions = self._paper.portfolio.open_positions()
        except Exception:
            log.warning("[EVENT_NEWS_PAPER_SHADOW] paper open book unreadable", exc_info=True)
            return None
        if not isinstance(positions, list):
            return None
        open_cost = 0
        for position in positions:
            try:
                open_cost += int(round(float(position.cost_dollars) * 100))
            except (TypeError, ValueError):
                return None
        # Notional is cash after open debits. Add the open cost back so the
        # paper 25% cap uses cohort equity.
        return cash + open_cost, open_cost

    def _paper_shadow_terms(
        self,
        analysis: SignalAnalysis,
    ) -> FinalExecutionTerms | None:
        """Kelly terms resized by the paper risk cap. None means do not record."""
        from trading.execution_terms import final_execution_terms
        from utils.event_news_desk_flags import event_news_risk_capped_contracts
        from utils.event_news_research import event_news_clamp_execution_terms

        price = getattr(analysis, "executed_price_cents", None)
        if isinstance(price, bool) or not isinstance(price, int) or not 0 < price < 100:
            return None
        try:
            plan = final_execution_terms(
                capped_dollars=float(analysis.capped_dollars),
                executed_price_cents=price,
            )
        except (TypeError, ValueError):
            return None
        ticker = str(getattr(getattr(analysis, "market", None), "ticker", "") or "")
        plan = event_news_clamp_execution_terms(plan, ticker=ticker)
        capped = event_news_risk_capped_contracts(
            plan.contracts,
            plan.price_cents,
            live=False,
        )
        if capped <= 0:
            log.info(
                "[EVENT_NEWS_RISK_CAP] ticker=%s ask=%s contracts=0",
                ticker,
                plan.price_cents,
            )
            return None
        if capped < plan.contracts:
            log.info(
                "[EVENT_NEWS_RISK_CAP] ticker=%s ask=%s contracts=%s capped=%s",
                ticker,
                plan.price_cents,
                plan.contracts,
                capped,
            )
            return FinalExecutionTerms(
                price_cents=plan.price_cents,
                contracts=capped,
                cost_dollars=capped * plan.price_cents / 100.0,
            )
        return plan

    def _paper_shadow_capped_dollars(self, plan: FinalExecutionTerms) -> float | None:
        """Dollar budget whose derived contract count equals the paper plan."""
        from trading.execution_terms import final_execution_terms
        from utils.event_news_research import event_news_clamp_execution_terms

        cost = float(plan.cost_dollars)
        for extra in (0.0, 0.001, 0.01, 0.02):
            candidate = cost + extra
            try:
                derived = final_execution_terms(
                    capped_dollars=candidate,
                    executed_price_cents=plan.price_cents,
                )
            except ValueError:
                continue
            derived = event_news_clamp_execution_terms(derived, ticker="")
            if derived == plan:
                return candidate
        return None

    def _bind_paper_shadow_lifecycle(self, analysis: SignalAnalysis) -> Callable[[], None]:
        """Give fee-net a stable lc- id when the candidate does not have one.

        Production accounting skips a row with no ``lc-`` plus 32 hex id.
        The live order is already decided. The original signal meta is
        restored after the paper write.
        """
        if not bool(cfg.enable_fee_net_paper_accounting):
            return lambda: None
        if _fee_net_paper_entry_request_id(analysis) is not None:
            return lambda: None
        previous = getattr(analysis, "signal_meta", None)
        meta = dict(previous) if isinstance(previous, dict) else {}
        news = getattr(analysis, "news_item", None)
        market = getattr(analysis, "market", None)
        from utils.lifecycle import build_lifecycle_id

        meta["lifecycle_id"] = build_lifecycle_id(
            venue=getattr(market, "venue", None) or "kalshi",
            ticker=getattr(market, "ticker", None),
            source=getattr(news, "source", None) or getattr(analysis, "source", None),
            url=getattr(news, "url", None),
            headline=getattr(news, "headline", None) or getattr(analysis, "headline", None),
            published=getattr(news, "published", None),
        )
        analysis.signal_meta = meta

        def restore() -> None:
            analysis.signal_meta = previous

        return restore

    async def _record_event_news_paper_shadow(
        self,
        analysis: SignalAnalysis,
    ) -> str | None:
        """Write one paper row using paper desk flags. Does not post or cancel."""
        from utils.event_news_desk_flags import (
            event_news_exposure_skip_reason,
            event_news_family_skip_reason,
            event_news_yes_floor_skip_reason,
        )
        from utils.event_news_research import (
            event_news_blocked_final_ask,
            event_news_exposure_prefix,
            event_news_min_edge,
            event_news_open_prefix_cap,
            is_event_news_paper_cohort,
        )

        if self._is_paper or not is_event_news_paper_cohort():
            return None
        source = self._paper_shadow_source(analysis)
        self._ensure_sized(analysis)
        price = getattr(analysis, "executed_price_cents", None)
        if isinstance(price, bool) or not isinstance(price, int):
            return None
        raw_invalid = getattr(analysis, "_event_news_final_ask_invalid", False)
        raw_cents = getattr(analysis, "_event_news_final_ask_cents", None)
        blocked = event_news_blocked_final_ask(
            limit_cents=price,
            fresh_ask_cents=raw_cents if type(raw_cents) is int else None,
            fresh_ask_invalid=raw_invalid is True,
        )
        ticker = str(getattr(getattr(analysis, "market", None), "ticker", "") or "")
        if blocked is not None:
            log.info(
                "[EVENT_NEWS_RISK] executed_ask_in_excluded_band ticker=%s side=%s ask=%s",
                ticker,
                getattr(analysis, "side", ""),
                blocked,
            )
            return None
        window = int(getattr(cfg, "paper_ticker_cooldown", 0) or 0)
        if window > 0 and ticker:
            last_entry = self._paper_shadow_entry_mono.get(ticker)
            if last_entry is not None and (time.monotonic() - last_entry) < window:
                log.info(
                    "[EVENT_NEWS_PAPER_SHADOW] skip=entry_window source=%s ticker=%s",
                    source,
                    ticker,
                )
                return None
        try:
            edge_value = float(analysis.edge)
        except (TypeError, ValueError):
            return None
        min_edge = event_news_min_edge(PAPER_MIN_EDGE, analysis.market)
        if abs(edge_value) < min_edge:
            return None
        plan = self._paper_shadow_terms(analysis)
        if plan is None:
            return None
        floor_reason = event_news_yes_floor_skip_reason(
            getattr(analysis, "side", ""),
            plan.price_cents,
            live=False,
        )
        if floor_reason is not None:
            log.info(
                "[EVENT_NEWS_RISK] yes_below_floor ticker=%s side=%s ask=%s",
                ticker,
                getattr(analysis, "side", ""),
                plan.price_cents,
            )
            return None
        book = self._paper_shadow_book_cents()
        if book is None:
            log.info("[EVENT_NEWS_RISK] exposure_cap ticker=%s reason=unreadable", ticker)
            return None
        _equity, _open_cost = book
        positions = self._paper.portfolio.open_positions()
        open_tickers = [
            str(getattr(position, "ticker", "") or "").strip()
            for position in positions
            if str(getattr(position, "ticker", "") or "").strip()
        ]
        if event_news_family_skip_reason(ticker, open_tickers, live=False):
            log.info("[EVENT_NEWS_RISK] one_per_family ticker=%s", ticker)
            return None
        same_ticker = self._paper.portfolio.open_positions(ticker)
        if isinstance(same_ticker, list) and same_ticker:
            log.info(
                "[EVENT_NEWS_PAPER_SHADOW] skip=open_ticker source=%s ticker=%s",
                source,
                ticker,
            )
            return None
        prefix_cap = event_news_open_prefix_cap(cfg.max_open_positions_per_prefix)
        if prefix_cap > 0:
            prefix = event_news_exposure_prefix(
                analysis.market,
                _correlated_exposure_prefix(analysis.market),
            )
            if prefix:
                open_in_prefix = self._paper.portfolio.open_positions_by_prefix(prefix)
                if isinstance(open_in_prefix, list) and len(open_in_prefix) >= prefix_cap:
                    log.info(
                        "[EVENT_NEWS_PAPER_SHADOW] skip=prefix_cap source=%s ticker=%s prefix=%s",
                        source,
                        ticker,
                        prefix,
                    )
                    return None
        order_cost = int(plan.contracts) * int(plan.price_cents)
        exposure_reason = event_news_exposure_skip_reason(
            _open_cost,
            order_cost,
            _equity,
            live=False,
        )
        if exposure_reason is not None:
            log.info(
                "[EVENT_NEWS_RISK] exposure_cap ticker=%s open_cost=%s order_cost=%s equity=%s",
                ticker,
                _open_cost,
                order_cost,
                _equity,
            )
            return None
        aligned = self._paper_shadow_capped_dollars(plan)
        if aligned is None:
            log.info(
                "[EVENT_NEWS_PAPER_SHADOW] skip=terms source=%s ticker=%s",
                source,
                ticker,
            )
            return None
        self._event_news_log_paper_maker(analysis, plan, paper_shadow=True)
        fee_reason = self._paper_fee_admission_skip_reason(analysis, plan)
        if fee_reason is not None:
            log.info(
                "[EVENT_NEWS_PAPER_SHADOW] skip=%s source=%s ticker=%s",
                fee_reason,
                source,
                ticker,
            )
            return None
        restore_lifecycle = self._bind_paper_shadow_lifecycle(analysis)
        previous_capped = analysis.capped_dollars
        previous_signal_type = getattr(analysis, "signal_type", None)
        analysis.capped_dollars = aligned
        analysis.signal_type = PAPER_SHADOW_SIGNAL_TYPE
        try:
            trade_id = await self._execute_paper(analysis, execution_plan=plan)
        finally:
            analysis.capped_dollars = previous_capped
            analysis.signal_type = previous_signal_type
            restore_lifecycle()
        if trade_id:
            if ticker:
                self._paper_shadow_entry_mono[ticker] = time.monotonic()
            log.info(
                "[EVENT_NEWS_PAPER_SHADOW] recorded source=%s ticker=%s",
                source,
                ticker,
            )
            try:
                from utils.event_news_oos_capture import append_politics_oos_capture

                append_politics_oos_capture(
                    trade_id=str(trade_id),
                    ticker=ticker,
                    side=str(getattr(analysis, "side", "") or ""),
                    price_cents=int(plan.price_cents),
                    contracts=int(plan.contracts),
                )
            except Exception:
                log.warning(
                    "[EVENT_NEWS_PAPER_SHADOW] capture failed source=%s ticker=%s",
                    source,
                    ticker,
                    exc_info=True,
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
        if not self._is_paper:
            starting = self._configured_starting_bankroll()
            if starting is not None:
                return starting
        return self._paper_effective_bankroll()

    def _configured_starting_bankroll(self) -> float | None:
        """Configured cohort bankroll. Shadow debits do not move it."""
        starting = getattr(self._paper, "_starting_bankroll", None)
        if isinstance(starting, bool) or not isinstance(starting, (int, float)):
            return None
        if starting <= 0:
            return None
        return float(starting)

    def _paper_effective_bankroll(self) -> float:
        getter = getattr(self._paper, "get_effective_sizing_bankroll", None)
        if callable(getter):
            value = getter()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value)
        return float(self._paper.get_notional_bankroll())

    def _positions_for_live_gates(self, positions: object) -> list:
        """Paper rows the live order may see. Shadow rows stay on the paper book."""
        if not isinstance(positions, list):
            return []
        if self._is_paper:
            return positions
        return [pos for pos in positions if getattr(pos, "paper_shadow", False) is not True]

    def _event_news_open_exposure_tickers(self) -> list[str]:
        """Paper plus live open tickers for politics family/prefix gates."""
        tickers: list[str] = []
        seen: set[str] = set()
        try:
            open_positions = getattr(self._paper.portfolio, "open_positions", None)
            positions = open_positions() if callable(open_positions) else []
            for position in positions or []:
                if (
                    not self._is_paper
                    and getattr(position, "paper_shadow", False) is True
                ):
                    continue
                ticker = str(getattr(position, "ticker", "") or "").strip()
                if ticker and ticker not in seen:
                    seen.add(ticker)
                    tickers.append(ticker)
        except Exception:
            log.warning(
                "[EVENT_NEWS_FAMILY_LOCK] paper open ticker lookup failed",
                exc_info=True,
            )
        status, live_positions = self._read_live_position_rows()
        if status == "fail":
            from utils.event_news_research import EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN

            tickers.append(EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN)
            return tickers
        if status == "ok" and live_positions is not None:
            for position in live_positions:
                ticker = _position_market_ticker(position)
                if (
                    ticker
                    and ticker not in seen
                    and _position_is_open(position)
                ):
                    seen.add(ticker)
                    tickers.append(ticker)
        return tickers

    def _validate(self, analysis: SignalAnalysis) -> Optional[str]:
        """Return skip reason, or None if the trade should proceed."""
        self._live_position_cache = _LIVE_POSITIONS_UNSET
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

        from utils.event_news_research import (
            event_news_executed_side_skip_reason,
            is_event_news_paper_cohort,
        )

        if is_event_news_paper_cohort():
            executed_reason = event_news_executed_side_skip_reason(
                analysis.market, analysis.side
            )
            if executed_reason:
                return executed_reason

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
                open_in_prefix = self._positions_for_live_gates(
                    self._paper.portfolio.open_positions_by_prefix(prefix)
                )
                if len(open_in_prefix) >= prefix_cap:
                    open_tickers = sorted({p.ticker for p in open_in_prefix})
                    return (
                        f"per-prefix cap: {len(open_in_prefix)} open in {prefix} "
                        f"(cap={prefix_cap}, "
                        f"open={open_tickers})"
                    )

        from utils.event_news_research import (
            event_news_family_lock_ticker,
            is_event_news_paper_cohort,
        )

        family_occupied = (
            event_news_family_lock_ticker(
                analysis.market,
                self._event_news_open_exposure_tickers(),
            )
            if is_event_news_paper_cohort()
            else None
        )
        if family_occupied:
            log.info(
                "[EVENT_NEWS_FAMILY_LOCK] open=%s blocked=%s series=KXTRUTHSOCIAL",
                family_occupied,
                analysis.market.ticker,
            )
            return (
                f"event_news_family_lock: open {family_occupied} occupies KXTRUTHSOCIAL"
            )

        # Multi-position guard: block opposing trades (no hedges) and duplicate
        # signals (same side, same probability estimate, same market price).
        # Reads from the in-memory Portfolio — no DB query at decision time.
        for pos in self._positions_for_live_gates(
            self._paper.portfolio.open_positions(analysis.market.ticker)
        ):
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
        notional = self._sizing_bankroll()
        gate_positions = self._positions_for_live_gates(
            self._paper.portfolio.open_positions(analysis.market.ticker)
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
        from utils.event_news_research import event_news_clamp_execution_terms

        final_terms = event_news_clamp_execution_terms(
            final_terms,
            ticker=str(analysis.market.ticker),
        )
        if self._is_paper:
            # PROFIT-SIZING-001b: paper sizes by Kelly now (mirrors live, matches
            # paper_trader). Concentration pre-check uses the actual Kelly
            # contract cost rather than the retired flat-5 estimate.
            trade_cost = final_terms.cost_dollars
        else:
            trade_cost = analysis.capped_dollars
        if self._is_paper and trade_cost > notional:
            return f"paper contract cost ${trade_cost:.2f} exceeds notional bankroll ${notional:.2f}"
        open_cost = 0.0
        for pos in gate_positions:
            try:
                open_cost += float(pos.cost_dollars)
            except (TypeError, ValueError):
                open_cost += 0.0
        if not self._paper.portfolio.is_concentration_ok(
            ticker=analysis.market.ticker,
            additional_dollars=trade_cost,
            max_ticker_pct=cfg.max_ticker_exposure_pct,
            bankroll=notional,
            open_cost=open_cost,
        ):
            current_exposure = open_cost
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
            usable = _positive_dollars(balance)
            if usable is None:
                log.warning(
                    "[SESSION_HALT] balance unreadable; skip and do not seed"
                )
                return "live balance unreadable"
            self._maybe_record_cash_cents(usable)
            if self._session_halt_enabled():
                equity = None
                open_cost_dollars = self._session_open_cost_dollars()
                if open_cost_dollars is not None:
                    equity = usable + open_cost_dollars
                if equity is None:
                    log.warning(
                        "[SESSION_HALT] equity unreadable; skip and do not seed"
                    )
                    return "live equity unreadable"
                if self._check_session_equity_halt(equity):
                    return self._trip_session_halt(equity)
            elif self._check_live_loss_limit(usable):
                self._live_halted = True
                loss = self._session_start_balance - usable
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
            if analysis.capped_dollars > usable * 0.9:
                return f"insufficient live balance (${usable:.2f})"

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
        from utils.event_news_research import event_news_clamp_execution_terms

        plan = event_news_clamp_execution_terms(
            plan,
            ticker=str(analysis.market.ticker),
        )
        plan, risk_reason = self._event_news_risk_capped_plan(analysis, plan)
        if risk_reason is not None or plan is None:
            return None, risk_reason
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
        from utils.event_news_grok_flags import event_news_requote_band_check_enabled
        from utils.event_news_research import (
            event_news_fresh_ask_cents,
            is_event_news_paper_cohort,
        )

        # Keep the fresh ask for the pre-POST guard. Do not skip here:
        # the client_order_id does not exist until _execute_live.
        if event_news_requote_band_check_enabled() and is_event_news_paper_cohort():
            fresh_cents, fresh_invalid = event_news_fresh_ask_cents(liquidity.best_price)
            analysis._event_news_final_ask_cents = fresh_cents
            analysis._event_news_final_ask_invalid = fresh_invalid
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

    def _event_news_plan_skip(
        self,
        analysis: SignalAnalysis,
        execution_plan: FinalExecutionTerms,
    ) -> str | None:
        """Politics desk skips. Paper defaults on. Live stays off until the env is set."""
        from utils.event_news_desk_flags import event_news_yes_floor_skip_reason
        from utils.event_news_research import is_event_news_paper_cohort

        if not is_event_news_paper_cohort():
            return None
        reason = event_news_yes_floor_skip_reason(
            getattr(analysis, "side", ""),
            execution_plan.price_cents,
            live=not self._is_paper,
        )
        if reason is not None:
            log.info(
                "[EVENT_NEWS_RISK] yes_below_floor ticker=%s side=%s ask=%s",
                getattr(getattr(analysis, "market", None), "ticker", ""),
                getattr(analysis, "side", ""),
                execution_plan.price_cents,
            )
            return reason
        family_reason = self._event_news_family_skip(analysis)
        if family_reason is not None:
            return family_reason
        return self._event_news_exposure_skip(analysis, execution_plan)

    def _event_news_family_skip(self, analysis: SignalAnalysis) -> str | None:
        from utils.event_news_desk_flags import (
            EVENT_NEWS_ONE_PER_FAMILY,
            event_news_desk_flag_enabled,
            event_news_family_skip_reason,
        )

        live = not self._is_paper
        if not event_news_desk_flag_enabled(EVENT_NEWS_ONE_PER_FAMILY, live=live):
            return None
        ticker = str(getattr(getattr(analysis, "market", None), "ticker", "") or "")
        reason = event_news_family_skip_reason(
            ticker,
            self._event_news_open_exposure_tickers(),
            live=live,
        )
        if reason is None:
            return None
        log.info(
            "[EVENT_NEWS_RISK] one_per_family ticker=%s",
            ticker,
        )
        return reason

    def _event_news_equity_and_open_cost_cents(self) -> tuple[int | None, int | None]:
        """Cash plus portfolio value, and open market exposure, both in cents."""
        request = getattr(self._rest, "_request", None)
        if not callable(request):
            return None, None
        try:
            balance = request("GET", "/portfolio/balance")
            positions = request("GET", "/portfolio/positions")
        except Exception:
            log.warning("[EVENT_NEWS_RISK] exposure read failed", exc_info=True)
            return None, None
        if not isinstance(balance, dict) or not isinstance(positions, dict):
            return None, None
        try:
            cash = int(balance["balance"])
            portfolio_value = int(balance.get("portfolio_value") or 0)
        except (KeyError, TypeError, ValueError):
            return None, None
        rows = positions.get("market_positions")
        if not isinstance(rows, list):
            return None, None
        open_cost = 0
        for row in rows:
            if not isinstance(row, dict):
                return None, None
            size = str(row.get("position_fp") or "").strip()
            if size in {"", "0", "0.0", "0.00"}:
                continue
            try:
                dollars = float(row.get("market_exposure_dollars"))
            except (TypeError, ValueError):
                return None, None
            open_cost += int(round(dollars * 100))
        return cash + portfolio_value, open_cost

    def _event_news_exposure_skip(
        self,
        analysis: SignalAnalysis,
        execution_plan: FinalExecutionTerms,
    ) -> str | None:
        from utils.event_news_desk_flags import (
            EVENT_NEWS_MAX_EXPOSURE_PCT,
            event_news_desk_flag_enabled,
            event_news_exposure_skip_reason,
        )

        live = not self._is_paper
        if not event_news_desk_flag_enabled(EVENT_NEWS_MAX_EXPOSURE_PCT, live=live):
            return None
        ticker = str(getattr(getattr(analysis, "market", None), "ticker", "") or "")
        equity, open_cost = self._event_news_equity_and_open_cost_cents()
        order_cost = int(execution_plan.contracts) * int(execution_plan.price_cents)
        if equity is None or open_cost is None:
            log.info(
                "[EVENT_NEWS_RISK] exposure_cap ticker=%s reason=unreadable",
                ticker,
            )
            return "exposure_cap"
        reason = event_news_exposure_skip_reason(
            open_cost,
            order_cost,
            equity,
            live=live,
        )
        if reason is None:
            return None
        log.info(
            "[EVENT_NEWS_RISK] exposure_cap ticker=%s open_cost=%s order_cost=%s equity=%s",
            ticker,
            open_cost,
            order_cost,
            equity,
        )
        return reason

    def _event_news_risk_capped_plan(
        self,
        analysis: SignalAnalysis,
        execution_plan: FinalExecutionTerms,
    ) -> tuple[FinalExecutionTerms | None, str | None]:
        """min(Kelly size, 8, floor(cap / ask)). Off leaves the 8-lot cap alone."""
        from utils.event_news_desk_flags import (
            EVENT_NEWS_MAX_RISK,
            event_news_desk_flag_enabled,
            event_news_risk_capped_contracts,
        )
        from utils.event_news_research import is_event_news_paper_cohort

        if not is_event_news_paper_cohort():
            return execution_plan, None
        live = not self._is_paper
        if not event_news_desk_flag_enabled(EVENT_NEWS_MAX_RISK, live=live):
            return execution_plan, None
        capped = event_news_risk_capped_contracts(
            execution_plan.contracts,
            execution_plan.price_cents,
            live=live,
        )
        ticker = getattr(getattr(analysis, "market", None), "ticker", "")
        if capped <= 0:
            log.info(
                "[EVENT_NEWS_RISK_CAP] ticker=%s ask=%s contracts=0",
                ticker,
                execution_plan.price_cents,
            )
            return None, "risk_cap"
        if capped < execution_plan.contracts:
            log.info(
                "[EVENT_NEWS_RISK_CAP] ticker=%s ask=%s contracts=%s capped=%s",
                ticker,
                execution_plan.price_cents,
                execution_plan.contracts,
                capped,
            )
            return (
                FinalExecutionTerms(
                    price_cents=execution_plan.price_cents,
                    contracts=capped,
                    cost_dollars=capped * execution_plan.price_cents / 100.0,
                ),
                None,
            )
        return execution_plan, None

    def _event_news_pre_post_band_skip(
        self,
        analysis: SignalAnalysis,
        *,
        limit_cents: int | None,
        client_order_id: str,
        log_skip: bool = True,
    ) -> bool:
        """Skip a politics order whose final executed ask is 0c through 35c.

        ``limit_cents`` is the price that would be posted. The fresh book
        ask, when one was stored, is checked too. Call this only after
        that price is final.
        """
        from utils.event_news_research import event_news_blocked_final_ask

        # MagicMock analyses invent missing attributes. Only an explicit
        # store from the fresh book may fail this gate closed.
        raw_invalid = getattr(analysis, "_event_news_final_ask_invalid", False)
        raw_cents = getattr(analysis, "_event_news_final_ask_cents", None)
        fresh_invalid = raw_invalid is True
        fresh_cents = raw_cents if type(raw_cents) is int else None
        blocked = event_news_blocked_final_ask(
            limit_cents=limit_cents,
            fresh_ask_cents=fresh_cents,
            fresh_ask_invalid=fresh_invalid,
        )
        if blocked is None:
            return False
        if log_skip:
            log.info(
                "[EVENT_NEWS_RISK] executed_ask_in_excluded_band ticker=%s "
                "side=%s ask=%s client_order_id=%s",
                analysis.market.ticker,
                analysis.side,
                blocked,
                client_order_id,
            )
        return True

    async def _execute_paper(
        self,
        analysis: SignalAnalysis,
        *,
        execution_plan: FinalExecutionTerms | None = None,
    ) -> Optional[str]:
        limit_cents = (
            execution_plan.price_cents
            if execution_plan is not None
            else getattr(analysis, "executed_price_cents", None)
        )
        if self._event_news_pre_post_band_skip(
            analysis,
            limit_cents=limit_cents,
            client_order_id="",
        ):
            return None
        if execution_plan is not None:
            execution_plan, risk_reason = self._event_news_risk_capped_plan(
                analysis,
                execution_plan,
            )
            if risk_reason is not None or execution_plan is None:
                return None
            if self._event_news_plan_skip(analysis, execution_plan):
                return None
            self._event_news_log_paper_maker(analysis, execution_plan)
            from utils.event_news_grok_veto import event_news_paper_grounding_skip

            if await asyncio.to_thread(
                event_news_paper_grounding_skip,
                analysis,
                contracts=execution_plan.contracts,
            ):
                log.info(
                    "[EVENT_NEWS_GROK_GROUNDING] skip=no ticker=%s",
                    analysis.market.ticker,
                )
                return None
            from utils.event_news_rules_check import event_news_rules_check_skip

            if await asyncio.to_thread(
                event_news_rules_check_skip,
                analysis,
                live=False,
            ):
                log.info(
                    "[EVENT_NEWS_RULES] skip=mismatch ticker=%s",
                    analysis.market.ticker,
                )
                return None
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
        maker_passthrough: bool = False,
        post_only: bool = False,
        fill_role: str | None = None,
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

        from utils.event_news_grok_flags import (
            EVENT_NEWS_FLAGS_FATAL,
            event_news_live_band_guard_off,
        )

        # Before any client_order_id is minted and before place_limit_order.
        # Both band flags on: this is false and the rest of the function is unchanged.
        if event_news_live_band_guard_off(cfg.live_trading_enabled is True):
            log.error("%s", EVENT_NEWS_FLAGS_FATAL)
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
            from utils.event_news_research import (
                EVENT_NEWS_MAX_CONTRACTS,
                event_news_clamp_execution_terms,
                is_event_news_paper_cohort,
            )

            if is_event_news_paper_cohort() and contracts > EVENT_NEWS_MAX_CONTRACTS:
                fallback_terms = event_news_clamp_execution_terms(
                    FinalExecutionTerms(
                        price_cents=price_cents,
                        contracts=contracts,
                        cost_dollars=contracts * price_cents / 100.0,
                    ),
                    ticker=str(analysis.market.ticker),
                )
                contracts = fallback_terms.contracts
                cost_dollars = fallback_terms.cost_dollars
            else:
                cost_dollars = contracts * price_cents / 100.0
        else:
            price_cents = execution_plan.price_cents
            contracts = execution_plan.contracts
            cost_dollars = execution_plan.cost_dollars

        execution_plan, risk_reason = self._event_news_risk_capped_plan(
            analysis,
            FinalExecutionTerms(
                price_cents=price_cents,
                contracts=contracts,
                cost_dollars=cost_dollars,
            ),
        )
        if risk_reason is not None or execution_plan is None:
            return None
        price_cents = execution_plan.price_cents
        contracts = execution_plan.contracts
        cost_dollars = execution_plan.cost_dollars

        if contracts <= 0:
            log.warning("Live order aborted: contracts=0 for $%.2f @ %dc", analysis.capped_dollars, price_cents)
            return None

        from utils.event_news_desk_flags import (
            EVENT_NEWS_GROK_RULES_CHECK,
            EVENT_NEWS_MAKER_FIRST,
            event_news_desk_flag_enabled,
        )
        from utils.event_news_research import is_event_news_paper_cohort as _cohort_for_maker

        # One check when the flag is off. The body below stays the taker path.
        if (
            not maker_passthrough
            and _cohort_for_maker()
            and event_news_desk_flag_enabled(EVENT_NEWS_MAKER_FIRST, live=True)
        ):
            return await self._event_news_maker_first(analysis, execution_plan)

        from utils.event_news_research import is_event_news_paper_cohort
        from utils.event_news_grok_veto import event_news_grok_veto_allows_live_post

        # price_cents is final. A band skip must not call Grok or POST.
        band_blocked = self._event_news_pre_post_band_skip(
            analysis,
            limit_cents=price_cents,
            client_order_id="",
            log_skip=False,
        )
        # New desk skips return before a client_order_id exists. The 0-35c
        # band still mints an id only so the existing skip line can name it.
        if (
            not band_blocked
            and event_news_desk_flag_enabled(EVENT_NEWS_GROK_RULES_CHECK, live=True)
        ):
            from utils.event_news_rules_check import event_news_rules_check_skip

            if event_news_rules_check_skip(analysis, live=True):
                log.info(
                    "[EVENT_NEWS_RULES] skip=mismatch ticker=%s",
                    analysis.market.ticker,
                )
                return None
        if not band_blocked and self._event_news_plan_skip(
            analysis,
            FinalExecutionTerms(
                price_cents=price_cents,
                contracts=contracts,
                cost_dollars=cost_dollars,
            ),
        ):
            return None
        if not band_blocked and is_event_news_paper_cohort():
            if not event_news_grok_veto_allows_live_post(
                analysis,
                contracts=contracts,
            ):
                log.info(
                    "[LIVE] BLOCKED %s: EVENT_NEWS_GROK_VETO did not return yes; no POST",
                    analysis.market.ticker,
                )
                return None

        client_order_id = uuid.uuid4().hex
        # Last price check before the submission intent, reserve, and POST.
        # Nothing below assigns price_cents.
        if self._event_news_pre_post_band_skip(
            analysis,
            limit_cents=price_cents,
            client_order_id=client_order_id,
        ):
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
                exc_info=True,
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
                    exc_info=True,
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
            order_kwargs = {
                "ticker": analysis.market.ticker,
                "side": analysis.side,
                "count": contracts,
                "limit_price": price_cents,
                "client_order_id": client_order_id,
            }
            if post_only:
                order_kwargs["post_only"] = True
            result: OrderResult = await loop.run_in_executor(
                None,
                lambda: self._rest.place_limit_order(**order_kwargs),
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

        if not result_error or _is_live_business_reject(result):
            from utils.event_news_grok_flags import event_news_fill_log_detail_enabled

            http_result = _live_http_result(result)
            detail: dict[str, str | None] = {}
            if event_news_fill_log_detail_enabled() and http_result == "201":
                detail = fetch_live_fill_detail(
                    self._rest,
                    str(getattr(result, "order_id", "") or ""),
                    str(analysis.side),
                )
            append_live_fill_log(
                ticker=str(analysis.market.ticker),
                side=str(analysis.side),
                price_cents=price_cents,
                contracts=contracts,
                client_order_id=client_order_id,
                http_result=http_result,
                filled_count=detail.get("filled_count"),
                avg_fill_cents=detail.get("avg_fill_cents"),
                fee_cents=detail.get("fee_cents"),
                order_id=detail.get("order_id"),
                role=fill_role,
            )

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
        try:
            analysis._event_news_last_filled = int(filled)
            analysis._event_news_last_status = str(status)
        except Exception:
            log.warning("[EVENT_NEWS_MAKER] fill stamp failed ticker=%s", analysis.market.ticker)
        return order_id

    def _event_news_log_paper_maker(
        self,
        analysis: SignalAnalysis,
        execution_plan: FinalExecutionTerms,
        *,
        paper_shadow: bool = False,
    ) -> None:
        """Paper records the maker price it would post. It does not sleep."""
        from utils.event_news_desk_flags import (
            EVENT_NEWS_MAKER_FIRST,
            EVENT_NEWS_YES_FLOOR,
            event_news_desk_flag_enabled,
            event_news_maker_limit_cents,
            event_news_yes_floor_cents,
        )
        from utils.event_news_grok_flags import EVENT_NEWS_BAND_FLOOR_CENTS
        from utils.event_news_research import is_event_news_paper_cohort

        if paper_shadow:
            if not is_event_news_paper_cohort():
                return
        elif not self._is_paper or not is_event_news_paper_cohort():
            return
        if not event_news_desk_flag_enabled(EVENT_NEWS_MAKER_FIRST, live=False):
            return
        side = str(getattr(analysis, "side", "") or "").lower()
        if side == "yes" and event_news_desk_flag_enabled(EVENT_NEWS_YES_FLOOR, live=False):
            floor = event_news_yes_floor_cents()
        else:
            floor = EVENT_NEWS_BAND_FLOOR_CENTS
        limit = event_news_maker_limit_cents(execution_plan.price_cents, floor_cents=floor)
        log.info(
            "[EVENT_NEWS_MAKER] paper role=maker limit=%s ask=%s sleep=0 ticker=%s",
            "" if limit is None else limit,
            execution_plan.price_cents,
            getattr(getattr(analysis, "market", None), "ticker", ""),
        )

    async def _event_news_maker_first(
        self,
        analysis: SignalAnalysis,
        execution_plan: FinalExecutionTerms | None,
    ) -> Optional[str]:
        """Post one post-only order, then take the remainder only if the edge holds.

        A new client_order_id is minted inside each _execute_live call.
        Cancel uses the maker order id only. An unknown maker result is not
        cancelled and is not followed by a taker.
        """
        from utils.event_news_desk_flags import (
            EVENT_NEWS_YES_FLOOR,
            event_news_desk_flag_enabled,
            event_news_maker_limit_cents,
            event_news_maker_wait_s,
            event_news_yes_floor_cents,
        )
        from utils.event_news_grok_flags import EVENT_NEWS_BAND_FLOOR_CENTS
        from utils.event_news_research import event_news_fresh_ask_cents

        if execution_plan is None:
            return None
        side = str(getattr(analysis, "side", "") or "").lower()
        if side == "yes" and event_news_desk_flag_enabled(EVENT_NEWS_YES_FLOOR, live=True):
            floor = event_news_yes_floor_cents()
        else:
            floor = EVENT_NEWS_BAND_FLOOR_CENTS
        maker_price = event_news_maker_limit_cents(
            execution_plan.price_cents,
            floor_cents=floor,
        )
        if maker_price is None:
            log.info(
                "[EVENT_NEWS_MAKER] no_rest ticker=%s ask=%s",
                analysis.market.ticker,
                execution_plan.price_cents,
            )
            return await self._execute_live(
                analysis,
                execution_plan=execution_plan,
                maker_passthrough=True,
                fill_role="taker",
            )
        if self._event_news_pre_post_band_skip(
            analysis,
            limit_cents=maker_price,
            client_order_id="",
            log_skip=True,
        ):
            return None
        maker_plan = FinalExecutionTerms(
            price_cents=maker_price,
            contracts=execution_plan.contracts,
            cost_dollars=execution_plan.contracts * maker_price / 100.0,
        )
        maker_order_id = await self._execute_live(
            analysis,
            execution_plan=maker_plan,
            maker_passthrough=True,
            post_only=True,
            fill_role="maker",
        )
        if not _is_verified_order_id(maker_order_id):
            return None
        filled = getattr(analysis, "_event_news_last_filled", 0)
        status = str(getattr(analysis, "_event_news_last_status", "") or "")
        try:
            filled_count = int(filled)
        except (TypeError, ValueError):
            filled_count = 0
        if status == "executed" or filled_count >= execution_plan.contracts:
            return str(maker_order_id)
        await asyncio.sleep(event_news_maker_wait_s())
        filled_after_wait = self._event_news_maker_filled_after_wait(str(maker_order_id))
        if filled_after_wait is None:
            log.info("[EVENT_NEWS_MAKER] fill_unknown order_id=%s", maker_order_id)
            try:
                self._rest.cancel_order(str(maker_order_id))
            except Exception:
                log.warning(
                    "[EVENT_NEWS_MAKER] cancel_failed order_id=%s",
                    maker_order_id,
                    exc_info=True,
                )
            return str(maker_order_id)
        filled_count = filled_after_wait
        if filled_count >= execution_plan.contracts:
            return str(maker_order_id)
        try:
            cancelled = bool(self._rest.cancel_order(str(maker_order_id)))
        except Exception:
            log.warning(
                "[EVENT_NEWS_MAKER] cancel_failed order_id=%s",
                maker_order_id,
                exc_info=True,
            )
            return str(maker_order_id)
        if not cancelled:
            log.info(
                "[EVENT_NEWS_MAKER] cancel_failed order_id=%s no_taker",
                maker_order_id,
            )
            return str(maker_order_id)
        remaining = execution_plan.contracts - max(filled_count, 0)
        if remaining <= 0:
            return str(maker_order_id)
        try:
            liquidity = await fetch_kalshi_execution_liquidity(self._rest, analysis)
            fresh, invalid = event_news_fresh_ask_cents(liquidity.best_price)
        except Exception:
            log.info("[EVENT_NEWS_MAKER] requote_unavailable ticker=%s", analysis.market.ticker)
            return str(maker_order_id) if filled_count > 0 else None
        analysis._event_news_final_ask_cents = fresh
        analysis._event_news_final_ask_invalid = invalid is True
        if invalid or not isinstance(fresh, int):
            return str(maker_order_id) if filled_count > 0 else None
        if self._event_news_pre_post_band_skip(
            analysis,
            limit_cents=fresh,
            client_order_id="",
            log_skip=True,
        ):
            return str(maker_order_id) if filled_count > 0 else None
        estimated = float(getattr(analysis, "estimated_probability", 0.0) or 0.0)
        market_p = fresh / 100.0
        edge = (estimated - market_p) if side == "yes" else ((1.0 - estimated) - market_p)
        if edge <= self._min_edge_threshold(analysis):
            log.info(
                "[EVENT_NEWS_MAKER] edge_gone ticker=%s edge=%s ask=%s",
                analysis.market.ticker,
                edge,
                fresh,
            )
            return str(maker_order_id) if filled_count > 0 else None
        taker_plan = FinalExecutionTerms(
            price_cents=fresh,
            contracts=remaining,
            cost_dollars=remaining * fresh / 100.0,
        )
        return await self._execute_live(
            analysis,
            execution_plan=taker_plan,
            maker_passthrough=True,
            fill_role="taker",
        )

    def _event_news_maker_filled_after_wait(self, order_id: str) -> int | None:
        """Read the maker fill after the wait. Unknown means do not take."""
        try:
            order = self._rest.get_order_receipt(order_id)
        except Exception:
            log.info("[EVENT_NEWS_MAKER] receipt_unavailable order_id=%s", order_id)
            return None
        if not isinstance(order, Mapping):
            return None
        count = _detail_number(order.get("fill_count_fp"))
        if count is None:
            count = _detail_number(order.get("fill_count"))
        if count is None or count < 0:
            return None
        return int(count.to_integral_value(rounding="ROUND_FLOOR"))

    def status(self) -> dict:
        return {
            "mode": "paper" if self._is_paper else "live",
            "notional_bankroll": self._paper.get_notional_bankroll(),
            "ticker_cooldowns": len(self._last_traded),
            "portfolio": self._paper.portfolio.summary(),
        }
