"""Politics/event-news cohort research overrides.

Isolated to PAPER_COHORT_ID=kalshi-event-news-20260820. The DJI freeze
process uses a different cohort id and must not take these paths.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Any, Mapping

from analysis.research_gate import ResearchStatus, run_research_gate
from config import cfg
from tasks.research_dossier import default_store as default_research_dossier_store
from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    INITIAL_ORDER_FEE_ACCUMULATOR,
    FeeContext,
    FeeRole,
    fee_coefficient_for,
    fee_schedule_at,
    quote_fee,
)
from trading.venue import Venue
from utils.event_news_grok_flags import (
    event_news_exclude_exact_35_enabled,
    event_news_requote_band_check_enabled,
)
from utils.logger import get_logger
from utils.series_universe import (
    live_series_tickers,
    market_edge_min,
    market_transcript_skip,
    match_market,
    paper_score_entries,
    series_ticker_matches,
    universe_failed,
)

log = get_logger("event_news_research")

EVENT_NEWS_COHORT_ID = "kalshi-event-news-20260820"
_PRIMARY_STATUSES = {
    ResearchStatus.TRADE_CANDIDATE,
    ResearchStatus.DECISION_GRADE_CANDIDATE,
}
# Skip taking when last trade and YES ask disagree by this many cents.
_LAST_ASK_DIVERGENCE_CENTS = 15
# Round-trip cents (yes_ask + no_ask - 100). Wide books are not favorites.
_WIDE_SPREAD_CENTS = 15
# Top-of-book size below this is treated as untradeable on the politics desk.
_MIN_TOP_SIZE = 2.0
# Joint OI + 24h volume floor; missing fields do not skip.
_MIN_OPEN_INTEREST = 10.0
_MIN_VOLUME_24H = 5.0
# Early-close contracts get a compressed Kelly horizon (days).
_EARLY_CLOSE_HORIZON_DAYS = 2.0
# Fallback favorite band when config bands are missing and the series has
# no universe row. Universe rows carry their own bands and do not stack
# the 0-35c executed-ask exclusion on top.
_FAVORITE_ASK_LOW = 0.55
_FAVORITE_ASK_HIGH = 0.99
# Near-certain asks (98¢ NO "won't leave Congress") have ~2¢ max payoff after
# fees and currently burn every research cycle. Do not treat them as the
# money path; keep 0.55-0.90 as the tradeable favorite band.
_FAVORITE_ASK_CERTAIN_CENTS = 91
# T4 bought 22 lots at 33c and lost $7.60. Dollar cap held; contract count did not.
# Clamp AFTER Kelly conversion. Do not change kelly_bet math.
EVENT_NEWS_MAX_CONTRACTS = 8
# Fail-closed occupancy marker when live GET /portfolio/positions fails.
EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN = "KXTRUTHSOCIAL-LIVE-UNKNOWN"



def is_event_news_paper_cohort(config: Any = None) -> bool:
    active = config if config is not None else cfg
    return str(getattr(active, "paper_cohort_id", "")).strip().lower() == EVENT_NEWS_COHORT_ID


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _cents_or_probability(value: Any) -> float | None:
    number = _as_float(value)
    if number is None:
        return None
    if number > 1.0:
        number = number / 100.0
    if 0.0 < number < 1.0:
        return number
    return None


def routing_yes_probability(market: Any, *, config: Any = None) -> tuple[float, str]:
    """Band-routing probability. Politics prefers executable YES ask over mid."""
    mid = _cents_or_probability(getattr(market, "yes_prob", None))
    if mid is None:
        mid = _cents_or_probability(getattr(market, "yes_price", None)) or 0.5
    ask = _cents_or_probability(
        getattr(market, "yes_ask_cents", None)
    ) or _cents_or_probability(getattr(market, "yes_ask", None))
    if ask is None:
        return mid, "mid"
    return ask, "yes_ask"


def event_news_spread_disagreement(market: Any, *, config: Any = None) -> str | None:
    """Do not take when last trade and ask disagree badly (politics only)."""
    last = _as_float(getattr(market, "last_price_cents", None))
    ask = _as_float(getattr(market, "yes_ask_cents", None))
    if last is None:
        last_prob = _cents_or_probability(getattr(market, "last_price", None))
        last = last_prob * 100.0 if last_prob is not None else None
    if ask is None:
        ask_prob = _cents_or_probability(getattr(market, "yes_ask", None))
        ask = ask_prob * 100.0 if ask_prob is not None else None
    if last is None or ask is None:
        return None
    gap = abs(ask - last)
    if gap < _LAST_ASK_DIVERGENCE_CENTS:
        return None
    log.info(
        "[EVENT_NEWS_RISK] last_ask_divergence ticker=%s last=%s ask=%s gap=%.1f",
        getattr(market, "ticker", ""),
        last,
        ask,
        gap,
    )
    return "last_ask_divergence"


def event_news_illiquid(market: Any, *, config: Any = None) -> str | None:
    """Skip obviously empty books before spending LLM/research (politics only)."""
    sizes = [
        _as_float(getattr(market, name, None))
        for name in ("yes_ask_size", "no_ask_size")
    ]
    present_sizes = [size for size in sizes if size is not None]
    oi = _as_float(getattr(market, "open_interest_fp", None))
    if oi is None:
        oi = _as_float(getattr(market, "open_interest", None))
    vol = _as_float(getattr(market, "volume_24h_fp", None))
    if vol is None:
        vol = _as_float(getattr(market, "volume_24h", None))
    traded = (oi is not None and oi >= _MIN_OPEN_INTEREST) or (
        vol is not None and vol >= _MIN_VOLUME_24H
    )
    if present_sizes and max(present_sizes) < _MIN_TOP_SIZE and not traded:
        log.info(
            "[EVENT_NEWS_LIQUIDITY] skip ticker=%s reason=illiquid_top_size size=%s",
            getattr(market, "ticker", ""),
            max(present_sizes),
        )
        return "illiquid_top_size"
    if oi is not None and vol is not None and oi < _MIN_OPEN_INTEREST and vol < _MIN_VOLUME_24H:
        log.info(
            "[EVENT_NEWS_LIQUIDITY] skip ticker=%s reason=illiquid_open_interest oi=%s vol24h=%s",
            getattr(market, "ticker", ""),
            oi,
            vol,
        )
        return "illiquid_open_interest"
    return None


def event_news_executable_top_size(
    market: Any,
    side: str,
    *,
    config: Any = None,
) -> float | None:
    """REST top size for the intended side. Freeze is a no-op.

    Kalshi ``/markets`` often omits ``no_ask_size_fp``. Buying NO is lifting
    YES bids, so ``yes_bid_size`` is the complementary size.
    """
    wanted = str(side or "").strip().lower()
    if wanted == "yes":
        size = _as_float(getattr(market, "yes_ask_size", None))
        if size is None:
            size = _as_float(getattr(market, "no_bid_size", None))
    elif wanted == "no":
        size = _as_float(getattr(market, "no_ask_size", None))
        if size is None:
            size = _as_float(getattr(market, "yes_bid_size", None))
    else:
        return None
    if size is None or size <= 0.0:
        return None
    return size


def event_news_executable_top_notional(
    market: Any,
    side: str,
    *,
    config: Any = None,
) -> float | None:
    """REST top-of-book notional for G7 when the Kalshi orderbook fetch fails.

    Freeze is a no-op. A zero REST ``liquidity_dollars`` field is not proof
    the book is empty; Kalshi often reports 0 while top size is live.
    """
    size = event_news_executable_top_size(market, side, config=config)
    if size is None:
        return None
    wanted = str(side or "").strip().lower()
    cents_name = "yes_ask_cents" if wanted == "yes" else "no_ask_cents"
    cents = _as_float(getattr(market, cents_name, None))
    price = None
    if cents is not None and 1.0 <= cents <= 99.0:
        price = cents / 100.0
    if price is None:
        price = _ask_probability(market, "yes_ask" if wanted == "yes" else "no_ask")
    if price is None or not (0.0 < price < 1.0):
        return None
    notional = size * price
    if notional <= 0.0:
        return None
    return notional


def event_news_horizon_days(market: Any, days: float, *, config: Any = None) -> float:
    """Compress Kelly horizon when Kalshi says the market can close early."""
    early = getattr(market, "can_close_early", None)
    condition = str(getattr(market, "early_close_condition", "") or "").strip()
    if early is not True and not condition:
        return days
    adjusted = min(float(days), _EARLY_CLOSE_HORIZON_DAYS)
    log.info(
        "[EVENT_NEWS_HORIZON] ticker=%s days=%.2f early_close=%s condition=%s",
        getattr(market, "ticker", ""),
        adjusted,
        early,
        condition[:80],
    )
    return adjusted


def event_news_open_prefix_cap(default_cap: int, *, config: Any = None) -> int:
    """One open position per event on the politics desk."""
    return 1


def event_news_exposure_prefix(market: Any, fallback: str, *, config: Any = None) -> str:
    """Prefer event_ticker so one headline cannot spray a whole ladder."""
    event = str(getattr(market, "event_ticker", "") or "").strip()
    if event:
        return event
    return fallback


def event_news_blocking_open_ticker(
    market: Any,
    open_tickers: list[str] | tuple[str, ...] | None,
    *,
    config: Any = None,
    default_cap: int = 1,
) -> str | None:
    """Existing open paper ticker that occupies this event, else None.

    Politics allows one open row per event_ticker. Sibling strikes
    (T6 vs T8) share the event and must not enqueue a second fill.
    """
    cap = event_news_open_prefix_cap(default_cap, config=config)
    if cap <= 0:
        return None
    fallback = str(getattr(market, "ticker", "") or "").strip()
    prefix = event_news_exposure_prefix(market, fallback, config=config)
    if not prefix:
        return None
    occupied: list[str] = []
    for raw in open_tickers or ():
        ticker = str(raw or "").strip()
        if not ticker:
            continue
        if ticker == prefix or ticker.startswith(f"{prefix}-"):
            occupied.append(ticker)
    if len(occupied) < cap:
        return None
    return occupied[0]


def event_news_series_prefix(ticker: str) -> str:
    """Ticker prefix before the first hyphen. KXTRUTHSOCIAL-26OCT03-B169 -> KXTRUTHSOCIAL."""
    return str(ticker or "").strip().split("-", 1)[0].strip().upper()


def event_news_fill_series_ticker(ticker: str, series_ticker: Any = "") -> str:
    """Fill empty series_ticker from the ticker prefix. New rows only."""
    existing = str(series_ticker or "").strip()
    if existing:
        return existing
    return str(ticker or "").strip().split("-", 1)[0].strip()


def event_news_family_lock_ticker(
    market: Any,
    open_tickers: list[str] | tuple[str, ...] | None,
    *,
    config: Any = None,
) -> str | None:
    """Occupying ticker when this order must not join an open desk family.

    A failed universe file returns the positions-unknown sentinel.
    A failed live positions read occupies KXTRUMPACT, KXTRUMPSAY, and
    KXTRUTHSOCIAL. An open Truth Social week still blocks only that family.
    Sibling strikes of the same event use event_open_prefix_cap.
    Does not cancel an existing open row.
    """
    ticker = str(getattr(market, "ticker", "") or "").strip()
    series = str(getattr(market, "series_ticker", "") or "").strip().upper()
    if not series:
        series = event_news_series_prefix(ticker)
    open_rows = [str(raw or "").strip() for raw in open_tickers or () if str(raw or "").strip()]
    from utils.series_universe import (
        occupancy_family_series,
        open_family_lock_series,
        universe_failed,
    )

    if universe_failed():
        return EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN
    if (
        series in occupancy_family_series()
        and EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN in open_rows
    ):
        return EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN
    if series not in open_family_lock_series():
        return None
    for raw in open_tickers or ():
        occupied = str(raw or "").strip()
        if not occupied:
            continue
        if event_news_series_prefix(occupied) == series:
            return occupied
    return None


def event_news_clamp_execution_terms(
    terms: Any,
    *,
    ticker: str = "",
    config: Any = None,
) -> Any:
    """Clamp event-news contracts after Kelly. Leaves kelly_bet math unchanged."""
    contracts = int(getattr(terms, "contracts", 0) or 0)
    if contracts <= EVENT_NEWS_MAX_CONTRACTS:
        return terms
    from trading.execution_terms import FinalExecutionTerms

    price_cents = int(terms.price_cents)
    log.info(
        "[EVENT_NEWS_SIZE_CAP] ticker=%s contracts=%s cap=%s",
        ticker,
        contracts,
        EVENT_NEWS_MAX_CONTRACTS,
    )
    clamped = EVENT_NEWS_MAX_CONTRACTS
    return FinalExecutionTerms(
        price_cents=price_cents,
        contracts=clamped,
        cost_dollars=clamped * price_cents / 100.0,
    )


def event_news_settle_statuses(*, config: Any = None) -> tuple[str, ...]:
    """Paper auto-resolve statuses. Politics may write on determined+result."""
    return ("finalized", "settled", "determined")


def _ask_probability(market: Any, *names: str) -> float | None:
    for name in names:
        raw = getattr(market, name, None)
        if raw is None:
            continue
        value = float(raw)
        return value / 100.0 if value > 1.0 else value
    return None


def snapshot_ask_cents(market: Any) -> tuple[int | None, int | None]:
    """Return integer yes/no ask cents when that side is executable (1-99).

    Kalshi list quotes often use 0c/100c as an empty book on one side.
    Those boundary quotes are not executable and must not count as a
    missing-quad failure when the other side is a real take.
    """

    def _as_cents(name: str, alt: str) -> int | None:
        try:
            raw = getattr(market, name, None)
        except (AttributeError, ValueError, TypeError):
            raw = None
        if raw is None:
            try:
                raw = getattr(market, alt, None)
            except (AttributeError, ValueError, TypeError):
                raw = None
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if name.endswith("_cents") or value > 1.0:
            cents = int(round(value))
        else:
            cents = int(round(value * 100.0))
        if 1 <= cents <= 99:
            return cents
        return None

    return _as_cents("yes_ask_cents", "yes_ask"), _as_cents("no_ask_cents", "no_ask")


def event_news_missing_snapshot_ask(market: Any, *, config: Any = None) -> str | None:
    """Skip only when neither side has an executable ask."""
    yes_ask, no_ask = snapshot_ask_cents(market)
    if yes_ask is None and no_ask is None:
        return "missing_snapshot_ask"
    return None


def event_news_wide_spread(market: Any, *, config: Any = None) -> str | None:
    """Skip books whose round-trip cost is too wide to be a favorite take."""
    yes_ask, no_ask = snapshot_ask_cents(market)
    if yes_ask is None or no_ask is None:
        return None
    spread = yes_ask + no_ask - 100
    if spread < _WIDE_SPREAD_CENTS:
        return None
    log.info(
        "[EVENT_NEWS_RISK] wide_spread ticker=%s yes_ask=%s no_ask=%s spread=%s",
        getattr(market, "ticker", ""),
        yes_ask,
        no_ask,
        spread,
    )
    return "wide_spread"


def event_news_favorite_side(
    market: Any, *, config: Any = None
) -> tuple[str | None, int | None]:
    """Return the higher in-band executable ask, if any.

    This is a band/liquidity gate for which book to spend research on, not
    the trade side. YES is not preferred over NO. When both asks qualify,
    the higher ask is the favorite. Trade side comes from both-side edge
    after research produces a probability. Freeze never uses this.
    """
    yes_ask, no_ask = snapshot_ask_cents(market)
    active = config if config is not None else cfg
    entry = None if universe_failed() else match_market(market)
    if entry is not None:
        # The yaml band is the only favorite band. Excluded stays empty so
        # a 12c or 36c research band is not newly cut by the 0-35c take band.
        allowed = [entry.favorite_band]
        excluded: list[tuple[float, float]] = []
    else:
        allowed = list(getattr(active, "llm_allowed_price_bands", ()) or ())
        if not allowed:
            allowed = [(_FAVORITE_ASK_LOW, _FAVORITE_ASK_HIGH)]
        excluded = list(getattr(active, "llm_excluded_price_bands", ()) or ())

    def _in_favorite(cents: int | None) -> bool:
        if cents is None:
            return False
        price = cents / 100.0
        if any(_price_in_band(price, low, high) for low, high in excluded):
            return False
        return any(_price_in_band(price, low, high) for low, high in allowed)

    candidates: list[tuple[int, str]] = []
    if _in_favorite(yes_ask) and yes_ask < _FAVORITE_ASK_CERTAIN_CENTS:
        candidates.append((int(yes_ask), "yes"))
    if _in_favorite(no_ask) and no_ask < _FAVORITE_ASK_CERTAIN_CENTS:
        candidates.append((int(no_ask), "no"))
    if not candidates:
        return None, None
    # Higher ask wins. Equal asks prefer NO so a NO favorite is not dropped.
    cents, side = max(candidates, key=lambda item: (item[0], item[1] == "no"))
    return side, cents


def _price_in_band(price: float, low: float, high: float) -> bool:
    if high >= 1.0:
        return low <= price <= high
    return low <= price < high


def event_news_in_allowed_ask_band(market: Any, *, config: Any = None) -> bool:
    """True when an executable favorite-side ask is inside the allowed band."""
    side, _cents = event_news_favorite_side(market, config=config)
    return side is not None


def event_news_cents_in_excluded_band(
    cents: int | None,
    *,
    config: Any = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when this ask is inside the executed-side longshot band.

    The configured band is half-open at 35c, so 0c is inside and 35c is
    outside that interval. EVENT_NEWS_EXCLUDE_EXACT_35 defaults on and
    adds the 35c ask. Only the exact env value ``false`` leaves 35c out.
    """
    if cents is None:
        return False
    price = int(cents) / 100.0
    active = config if config is not None else cfg
    excluded = list(getattr(active, "llm_excluded_price_bands", ()) or ())
    if not excluded:
        excluded = [(0.00, 0.35)]
    if any(_price_in_band(price, low, high) for low, high in excluded):
        return True
    if int(cents) == 35 and event_news_exclude_exact_35_enabled(environ):
        return True
    return False


def event_news_fresh_ask_cents(best_price: Any) -> tuple[int | None, bool]:
    """Return ``(cents, invalid)`` for the fresh executable ask.

    ``best_price is None`` is absent, not invalid. A value that cannot be
    converted fails closed at the pre-POST guard.
    """
    if best_price is None:
        return None, False
    try:
        cents = int(
            (Decimal(best_price) * Decimal("100")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
    except (ArithmeticError, TypeError, ValueError):
        return None, True
    return cents, False


def event_news_blocked_final_ask(
    *,
    limit_cents: int | None,
    fresh_ask_cents: int | None,
    fresh_ask_invalid: bool,
    config: Any = None,
    environ: Mapping[str, str] | None = None,
) -> int | str | None:
    """Ask to log when the politics order must be skipped.

    Checks the fresh executable ask first, then the limit that would be
    posted. ``"invalid"`` means the fresh ask could not be read. None
    means this gate does not skip. Non-politics cohorts are ignored.
    The requote flag defaults on.
    """
    if not event_news_requote_band_check_enabled(environ):
        return None
    if fresh_ask_invalid:
        return "invalid"
    for cents in (fresh_ask_cents, limit_cents):
        if cents is None or isinstance(cents, bool):
            continue
        try:
            ask = int(cents)
        except (TypeError, ValueError):
            return "invalid"
        if event_news_cents_in_excluded_band(ask, config=config, environ=environ):
            return ask
    return None


def event_news_executed_side_skip_reason(
    market: Any,
    side: str | None,
    *,
    config: Any = None,
) -> str | None:
    """Skip when the fill-side snapshot ask is in the excluded longshot band.

    Favorite-side TRUMPACT 0.12-0.90 is a research-routing override only.
    The executed ask still uses llm_excluded_price_bands (0.00-0.35).
    Freeze never uses this.
    """
    executed = str(side or "").strip().lower()
    if executed not in {"yes", "no"}:
        return None
    yes_ask, no_ask = snapshot_ask_cents(market)
    cents = yes_ask if executed == "yes" else no_ask
    if not event_news_cents_in_excluded_band(cents, config=config):
        return None
    log.info(
        "[EVENT_NEWS_RISK] executed_ask_in_excluded_band ticker=%s "
        "side=%s ask=%s",
        getattr(market, "ticker", ""),
        executed,
        cents,
    )
    return "executed_ask_in_excluded_band"


def event_news_crossed_asks(market: Any, *, config: Any = None) -> str | None:
    """Skip locked/crossed books (YES ask + NO ask < 100c) on politics only."""
    yes_ask, no_ask = snapshot_ask_cents(market)
    if yes_ask is None or no_ask is None:
        return None
    if yes_ask + no_ask >= 100:
        return None
    log.info(
        "[EVENT_NEWS_RISK] crossed_asks ticker=%s yes_ask=%s no_ask=%s",
        getattr(market, "ticker", ""),
        yes_ask,
        no_ask,
    )
    return "crossed_asks"


def event_news_unified_edge_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Exact true or 1. Unset, false, and 0 keep the flat live edge check."""
    source = os.environ if environ is None else environ
    return str(source.get("EVENT_NEWS_UNIFIED_EDGE", "")).strip().lower() in {"true", "1"}


def event_news_fee_net_edge(
    probability: float | Decimal | str,
    ask_cents: int,
    contracts: int = 1,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Return (gross, fee, fee_net). None fails closed.

    fee_net is p(side) minus the ask minus quote_fee.net_fee / contracts.
    Kalshi ceils the raw taker fee to a centicent, then rounds the direct account.
    """
    if isinstance(ask_cents, bool) or type(ask_cents) is not int:
        return None
    if isinstance(contracts, bool) or type(contracts) is not int or contracts < 1:
        return None
    if ask_cents < 1 or ask_cents > 99:
        return None
    try:
        p_side = Decimal(str(probability))
    except Exception:
        return None
    if not p_side.is_finite() or p_side <= 0 or p_side >= 1:
        return None
    price = Decimal(ask_cents) / Decimal(100)
    quantity = Decimal(contracts)
    try:
        timestamp = datetime.now(timezone.utc)
        schedule = fee_schedule_at(venue=Venue.KALSHI, timestamp=timestamp)
        fee = quote_fee(
            FeeContext(
                schedule_id=schedule,
                role=FeeRole.TAKER,
                quantity=quantity,
                price=price,
                signed_revenue=-(quantity * price),
                order_id="paper-admission",
                accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
                multiplier=Decimal("1"),
                coefficient=fee_coefficient_for(schedule, FeeRole.TAKER),
                account_precision=DIRECT_ACCOUNT_PRECISION,
                timestamp=timestamp,
            )
        ).net_fee
    except Exception:
        return None
    if not isinstance(fee, Decimal) or not fee.is_finite() or fee < 0:
        return None
    gross = p_side - price
    return gross, fee, gross - (fee / quantity)


def event_news_fee_net_cents(
    probability: float | Decimal | str,
    ask_cents: int,
    contracts: int = 1,
) -> tuple[int, int] | None:
    """Whole cents (fee ceiled, fee-net floored) from the exact quote."""
    scored = event_news_fee_net_edge(probability, ask_cents, contracts)
    if scored is None:
        return None
    _gross, fee, fee_net = scored
    fee_cents = int((fee * Decimal(100)).to_integral_value(rounding=ROUND_CEILING))
    edge_cents = int((fee_net * Decimal(100)).to_integral_value(rounding=ROUND_FLOOR))
    return fee_cents, edge_cents


def event_news_score_edge(
    market: Any,
    *,
    yes_probability: float,
    side: str,
    ask_cents: int | None,
    contracts: int = 1,
    config: Any = None,
) -> dict[str, Any] | None:
    """Fee-net versus yaml edge_min. None means this cohort does not apply."""
    name = str(side or "").strip().lower()
    try:
        yes = Decimal(str(yes_probability))
    except Exception:
        yes = None
    p_side = None
    if yes is not None and yes.is_finite() and Decimal(0) < yes < Decimal(1):
        if name == "yes":
            p_side = yes
        elif name == "no":
            p_side = Decimal(1) - yes
    minimum = market_edge_min(market)
    scored = (
        event_news_fee_net_edge(p_side, ask_cents, contracts)
        if p_side is not None and type(ask_cents) is int
        else None
    )
    fee_net = None if scored is None else scored[2]
    return {
        "new_take": fee_net is not None and minimum is not None and fee_net >= minimum,
        "gross": None if scored is None else scored[0],
        "fee": None if scored is None else scored[1],
        "fee_net": fee_net,
        "minimum": minimum,
        "probability": p_side,
        "readable": fee_net is not None and minimum is not None,
    }


def event_news_shadow_edge(
    market: Any,
    side: str,
    ask: int | None,
    scored: Mapping[str, Any] | None,
    *,
    old_edge: float,
    old_take: bool,
) -> None:
    """Log one line when the flat live check and the fee-net check disagree."""
    new_take = False if scored is None else bool(scored.get("new_take"))
    if bool(old_take) == new_take:
        return

    def _fmt(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value, "f")
        return str(value)

    log.info(
        "[EVENT_NEWS_UNIFIED_EDGE] shadow ticker=%s side=%s ask=%s p=%s fee=%s "
        "old_edge=%s new_edge=%s old=%s new=%s",
        getattr(market, "ticker", ""),
        side,
        "" if ask is None else ask,
        "" if scored is None else _fmt(scored.get("probability")),
        "" if scored is None else _fmt(scored.get("fee")),
        old_edge,
        "" if scored is None else _fmt(scored.get("fee_net")),
        "take" if old_take else "skip",
        "take" if new_take else "skip",
    )


def event_news_series_ticker(market: Any) -> str:
    raw = getattr(market, "series_ticker", "")
    if isinstance(raw, str):
        series = raw.strip().upper()
        if series:
            return series
    ticker = str(getattr(market, "ticker", "") or "").strip().upper()
    return ticker.split("-", 1)[0] if ticker else ""


def event_news_non_politics_series(market: Any, *, config: Any = None) -> str | None:
    """The yaml mode is the desk gate. A prefix deny list does not skip."""
    del market, config
    return None


# Quote-dependent prewarm skips. Exponential backoff on these locks ATM
# favorites for hours after a stale illiquid/out-of-band print.
_QUOTE_DEPENDENT_PREWARM_REASONS = frozenset(
    {
        "missing_snapshot_ask",
        "ask_outside_favorite_band",
        "illiquid_top_size",
        "illiquid_open_interest",
        "wide_spread",
        "crossed_asks",
        "last_ask_divergence",
        "near_certain_ask",
    }
)
# Incomplete research on a live favorite. 19200s backoff here darks the only
# in-band books until after the useful window. official_data_pending / no_edge
# stay locked.
_IN_BAND_RETRYABLE_RESEARCH_REASONS = frozenset(
    {
        "missing_resolution_source",
        "insufficient_corroboration",
        "no_research_hits",
        "neutral_only_evidence",
        "ambiguous_direction",
        "official_data_pending",
    }
)


def event_news_quote_dependent_skip_reason(reason: str | None) -> bool:
    return str(reason or "").strip() in _QUOTE_DEPENDENT_PREWARM_REASONS


def event_news_bypass_quote_skip_cooldown(
    last_skip_reason: str | None,
    market: Any,
    *,
    config: Any = None,
) -> bool:
    """Retry when a prior skip is stale and the live book is still in-band.

    Politics only. Freeze keeps exponential backoff. Quote skips and
    incomplete official research (missing Roll Call fetch, neutral-only
    verifier) must not 5h-lock T240/B230 while they are 70¢+ favorites.
    """
    reason = str(last_skip_reason or "").strip()
    retryable = reason in _QUOTE_DEPENDENT_PREWARM_REASONS or (
        reason in _IN_BAND_RETRYABLE_RESEARCH_REASONS
    )
    if not retryable:
        return False
    return event_news_prewarm_skip_reason(market, config=config) is None


def event_news_prewarm_skip_reason(market: Any, *, config: Any = None) -> str | None:
    """Politics prewarm only spends research on favorite-band executable books."""
    return event_news_gate_chain_reason(market, config=config)


def event_news_gate_chain_reason(
    market: Any,
    *,
    side: str | None = None,
    edge: float | None = None,
    limit_cents: int | None = None,
    fresh_ask_cents: int | None = None,
    fresh_ask_invalid: bool = False,
    open_tickers: list[str] | tuple[str, ...] | None = None,
    config: Any = None,
    environ: Mapping[str, str] | None = None,
    live: bool = True,
    min_edge_base: float = 0.02,
    veto_reason: str | None = None,
    halt_reason: str | None = None,
    purpose: str = "order",
) -> str | None:
    denied = event_news_non_politics_series(market, config=config)
    if denied:
        return denied
    if universe_failed():
        return "universe_unavailable"
    if purpose == "score":
        entry = match_market(market)
        scored = {item.series for item in paper_score_entries()}
        if entry is not None and entry.mode == "off":
            return "series_off"
        if entry is None or entry.series not in scored:
            return "not_paper_series"
        if market_transcript_skip(market) and not entry.paper_score:
            return "mention_requires_transcript"
    elif not event_news_is_reserve_series(market, config=config):
        return "not_reserve_series"
    elif market_transcript_skip(market):
        if not _live_tradable_research_on():
            _shadow_research_drop(market, "not_researched")
        return "mention_requires_transcript"
    yes_ask, no_ask = snapshot_ask_cents(market)
    posted = type(limit_cents) is int or type(fresh_ask_cents) is int or fresh_ask_invalid is True
    if yes_ask is not None or no_ask is not None or not posted:
        for reason in (
            event_news_missing_snapshot_ask(market, config=config),
            event_news_illiquid(market, config=config),
            event_news_crossed_asks(market, config=config),
            event_news_wide_spread(market, config=config),
            event_news_spread_disagreement(market, config=config),
        ):
            if reason:
                return reason
        if not event_news_in_allowed_ask_band(market, config=config):
            favorite = yes_ask if yes_ask is not None and 55 <= yes_ask <= 99 else None
            if favorite is None and no_ask is not None and 55 <= no_ask <= 99:
                favorite = no_ask
            if favorite is not None and favorite >= _FAVORITE_ASK_CERTAIN_CENTS:
                return "near_certain_ask"
            return "ask_outside_favorite_band"
        if purpose != "score":
            blocked = _live_tradable_research_block(market, config=config)
            if blocked:
                if _live_tradable_research_on():
                    return blocked
                _shadow_research_drop(market, blocked)
    executed_side = str(side or "").strip().lower()
    if executed_side in {"yes", "no"}:
        from utils.event_news_desk_flags import event_news_yes_floor_skip_reason

        side_ask = limit_cents if type(limit_cents) is int else (
            yes_ask if executed_side == "yes" else no_ask
        )
        floor = event_news_yes_floor_skip_reason(
            executed_side, side_ask, live=live, environ=environ
        )
        if floor:
            return floor
        executed = event_news_executed_side_skip_reason(market, executed_side, config=config)
        if executed:
            return executed
    if fresh_ask_invalid or type(fresh_ask_cents) is int or type(limit_cents) is int:
        blocked_ask = event_news_blocked_final_ask(
            limit_cents=limit_cents if type(limit_cents) is int else None,
            fresh_ask_cents=fresh_ask_cents if type(fresh_ask_cents) is int else None,
            fresh_ask_invalid=fresh_ask_invalid is True,
            config=config,
            environ=environ,
        )
        if blocked_ask is not None:
            return "executed_ask_in_excluded_band"
    if open_tickers is not None:
        occupied = event_news_family_lock_ticker(market, open_tickers, config=config)
        if occupied:
            log.info(
                "[EVENT_NEWS_FAMILY_LOCK] open=%s blocked=%s series=%s",
                occupied,
                getattr(market, "ticker", ""),
                event_news_series_prefix(str(getattr(market, "ticker", "") or "")),
            )
            return "event_news_family_lock"
        from utils.event_news_desk_flags import event_news_family_skip_reason

        desk = event_news_family_skip_reason(
            str(getattr(market, "ticker", "") or ""),
            open_tickers,
            live=live,
            environ=environ,
        )
        if desk:
            return desk
    del edge, min_edge_base
    if veto_reason:
        return str(veto_reason)
    if halt_reason:
        return str(halt_reason)
    return None


def _live_tradable_research_on() -> bool:
    from utils.event_news_desk_flags import event_news_live_tradable_research_enabled

    return event_news_live_tradable_research_enabled()


def _live_tradable_research_block(market: Any, *, config: Any) -> str | None:
    """Skip prewarm when the favorite ask is not a live order.

    YES needs 55c through 90c. NO needs 36c through 90c. 35c and 91c stay out.
    """
    side, cents = event_news_favorite_side(market, config=config)
    if side is None or cents is None:
        return "not_live_tradable"
    price = int(cents)
    if price <= 35 or price >= _FAVORITE_ASK_CERTAIN_CENTS:
        return "not_live_tradable"
    if side == "yes" and price < 55:
        return "not_live_tradable"
    if side == "no" and price < 36:
        return "not_live_tradable"
    return None


def _shadow_research_drop(market: Any, new_reason: str) -> None:
    """Record a market the flag would stop researching. Never raises."""
    try:
        ticker = getattr(market, "ticker", "")
    except Exception:
        ticker = ""
    try:
        log.info(
            "[EVENT_NEWS_PREWARM] shadow ticker=%s old=research new=%s",
            ticker,
            new_reason,
        )
    except Exception:
        try:
            log.info("[EVENT_NEWS_PREWARM] shadow_unreadable")
        except Exception:
            return


def event_news_paper_score_decision(
    market: Any,
    *,
    probability: float | None = None,
    config: Any = None,
    environ: Mapping[str, str] | None = None,
    contracts: int = 1,
) -> dict[str, Any]:
    """Run one paper-score book through the quote gate. Never POST or call Grok.

    A quote-gate pass with no probability is pending and is not logged.
    A logged row has a local fee-net veto, or an explicit skip reason.
    """
    reasons: list[str] = []
    entry = None if universe_failed() else match_market(market)
    if entry is not None and entry.transcript_skip and entry.paper_score:
        reasons.append("transcript_skip_bypassed_for_paper_score")
    side, cents = event_news_favorite_side(market, config=config)
    chain_reason = event_news_gate_chain_reason(
        market,
        side=side,
        limit_cents=int(cents) if type(cents) is int else None,
        config=config,
        environ=environ,
        live=True,
        purpose="score",
    )
    if chain_reason:
        reasons.append(chain_reason)
    informational = {"transcript_skip_bypassed_for_paper_score"}
    structural = [reason for reason in reasons if reason not in informational]
    if probability is None and not structural:
        return _paper_score_row(entry, market, "pending", reasons, "pending")
    veto = "skipped"
    decision = "skip"
    shown = ""
    if not structural:
        if type(contracts) is not int or contracts < 1 or contracts > EVENT_NEWS_MAX_CONTRACTS:
            reasons.append("size_cap")
            veto = "skipped"
        else:
            decision, veto, shown = _paper_fee_veto(
                market, probability, contracts, config=config
            )
            if decision == "skip" and veto == "no":
                reasons.append(
                    "edge_not_evaluated"
                    if shown == ""
                    else "below_fee_net_min_edge"
                )
    _log_paper_score(entry, market, decision, reasons, veto, shown)
    return _paper_score_row(entry, market, decision, reasons, veto)


def _paper_score_row(
    entry: Any,
    market: Any,
    decision: str,
    reasons: list[str],
    veto: str,
) -> dict[str, Any]:
    return {
        "series": entry.series if entry is not None else "",
        "ticker": str(getattr(market, "ticker", "") or ""),
        "decision": decision,
        "reasons": tuple(reasons),
        "veto": veto,
        "contract_cap": EVENT_NEWS_MAX_CONTRACTS,
        "would_post": False,
    }


def _log_paper_score(
    entry: Any,
    market: Any,
    decision: str,
    reasons: list[str],
    veto: str,
    probability: str,
) -> None:
    log.info(
        "[EVENT_NEWS_PAPER_SCORE] series=%s ticker=%s decision=%s reasons=%s "
        "veto=%s probability=%s contract_cap=%s would_post=false",
        entry.series if entry is not None else "",
        getattr(market, "ticker", ""),
        decision,
        ",".join(reasons),
        veto,
        probability,
        EVENT_NEWS_MAX_CONTRACTS,
    )


def _paper_fee_veto(
    market: Any,
    probability: float | None,
    contracts: int,
    *,
    config: Any,
) -> tuple[str, str, str]:
    """Local yes/no from fee-net versus yaml edge_min. No Grok call."""
    try:
        estimated = Decimal(str(probability))
    except Exception:
        return "skip", "no", ""
    if not estimated.is_finite() or estimated <= 0 or estimated >= 1:
        return "skip", "no", ""
    yes_ask, no_ask = snapshot_ask_cents(market)
    best: Decimal | None = None
    for ask, p_side in (
        (yes_ask, estimated),
        (no_ask, Decimal(1) - estimated),
    ):
        if type(ask) is not int:
            continue
        scored = event_news_fee_net_edge(p_side, ask, contracts)
        if scored is None:
            continue
        fee_net = scored[2]
        best = fee_net if best is None else max(best, fee_net)
    minimum = market_edge_min(market)
    shown = format(estimated, "f")
    if best is None or minimum is None:
        return "skip", "no", ""
    if best < minimum:
        return "skip", "no", shown
    return "quote_pass", "yes", shown


def event_news_one_market_per_event(
    markets: list[Any],
    *,
    config: Any = None,
) -> list[Any]:
    """Keep one in-band book per event so a strike ladder cannot mill research.

    Do not prefer YES books over NO books. Pick the in-band ask closest to
    65c, then larger top size. Trade side is decided later from p vs both
    asks. Freeze is a no-op.
    """
    best: dict[str, tuple[tuple[int, float], Any]] = {}
    for market in markets:
        event = event_news_exposure_prefix(
            market,
            event_news_series_ticker(market) or str(getattr(market, "ticker", "")),
            config=config,
        )
        if not event:
            continue
        side, cents = event_news_favorite_side(market, config=config)
        if side is None or cents is None:
            continue
        size = _as_float(getattr(market, "yes_ask_size", None)) or 0.0
        if side == "no":
            size = _as_float(getattr(market, "no_ask_size", None)) or size
        score = (abs(int(cents) - 65), -size)
        prior = best.get(event)
        if prior is None or score < prior[0]:
            best[event] = (score, market)
    selected = [item[1] for item in best.values()]
    if len(selected) != len(markets):
        log.info(
            "[EVENT_NEWS_PREWARM] one_per_event before=%d after=%d tickers=%s",
            len(markets),
            len(selected),
            [str(getattr(market, "ticker", "") or "") for market in selected],
        )
    return selected


def event_news_finalize_prewarm_batch(
    markets: list[Any],
    *,
    config: Any = None,
) -> list[Any]:
    """Re-apply favorite-band skips, then one-per-event, on the final batch.

    Due-task refill can restore wide/illiquid siblings and macro leaks after
    the first filter. Freeze is a no-op.
    """
    eligible: list[Any] = []
    for market in markets:
        if event_news_prewarm_skip_reason(market, config=config) is None:
            eligible.append(market)
    return event_news_one_market_per_event(eligible, config=config)


def event_news_reorder_prewarm_batch(
    markets: list[Any],
    due_tickers: list[str] | tuple[str, ...] | None = None,
    *,
    config: Any = None,
) -> list[Any]:
    """Keep the live in-band set. Due-task order may sort inside it only.

    Out-of-band, denylisted, and non-reserve tickers never enter the batch.
    Freeze is a no-op.
    """
    in_band = event_news_finalize_prewarm_batch(markets, config=config)
    by_ticker: dict[str, Any] = {}
    for market in in_band:
        ticker = str(getattr(market, "ticker", "") or "").strip()
        if ticker and ticker not in by_ticker:
            by_ticker[ticker] = market
    ordered: list[Any] = []
    seen: set[str] = set()
    for raw in due_tickers or ():
        ticker = str(raw or "").strip()
        market = by_ticker.get(ticker)
        if market is None or ticker in seen:
            continue
        seen.add(ticker)
        ordered.append(market)
    for market in in_band:
        ticker = str(getattr(market, "ticker", "") or "").strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        ordered.append(market)
    return ordered


def event_news_news_may_refresh_research(
    market: Any,
    *,
    settlement_source_match: bool | None = None,
    config: Any = None,
) -> bool:
    """News may attach to an in-band book or force a settlement-source refresh.

    Matcher Jaccard must not add soccer/CPI/macro books to the research set.
    Settlement-source refresh is reserve-series only. Freeze is unchanged.
    """
    if event_news_prewarm_allows(market, config=config):
        return True
    if not settlement_source_match:
        return False
    if event_news_non_politics_series(market, config=config):
        return False
    return event_news_is_reserve_series(market, config=config)


def event_news_admission_outcome_retryable(outcome_reason: str | None) -> bool:
    reason = str(outcome_reason or "").strip().lower()
    if not reason:
        return False
    return any(
        fragment in reason
        for fragment in (
            "capped_dollars",
            "consumer_error",
            "attributeerror",
            "news_item",
        )
    )


def event_news_should_retry_admission(
    *,
    has_paper_exposure: bool,
    state: str | None = None,
    enqueued: bool | None = None,
    outcome_reason: str | None = None,
    allow_unfilled_enqueue: bool = False,
    config: Any = None,
) -> bool:
    """Retry DG admission on politics unless this ticker already has paper risk.

    Allow failed admits, G1/not-enqueued completes, crash/$0 outcomes, and an
    unfilled enqueue when the caller confirmed no open paper exposure.
    Never steal a live `claimed` row. Freeze keeps the one-claim invariant.
    """
    if has_paper_exposure:
        return False
    state_name = str(state or "").strip()
    if state_name == "failed":
        return True
    if state_name == "claimed":
        return False
    if event_news_admission_outcome_retryable(outcome_reason):
        return True
    if state_name == "completed" and not enqueued:
        return True
    if state_name == "completed" and enqueued and allow_unfilled_enqueue:
        return True
    return False


def event_news_prewarm_allows(market: Any, *, config: Any = None) -> bool:
    return event_news_prewarm_skip_reason(market, config=config) is None


# Keep aligned with analysis.research_gate._STRUCTURED_SIGNAL_METRICS.
_STRUCTURED_OFFICIAL_METRICS = frozenset(
    {
        "cpi_monthly_change_single_decimal",
        "getty_trump_distinct_photo_days",
        "gdpnow_real_gdp_growth_saar",
        "nws_daily_high_temp_f",
        "white_house_presidential_actions_count",
        "white_house_action_range_probability",
        "truth_social_weekly_post_count",
        "truth_social_range_probability",
        "truth_social_phrase_probability",
    }
)
_UNINFORMATIVE_P = 0.5
_UNINFORMATIVE_P_TOL = 1e-6


def event_news_uninformative_shrug_p(probability: float) -> bool:
    """True for the LLM/empty-packet 0.5 shrug, not clipped 0.02/0.98 counts."""
    try:
        value = float(probability)
    except (TypeError, ValueError):
        return False
    return abs(value - _UNINFORMATIVE_P) < _UNINFORMATIVE_P_TOL


def event_news_has_structured_official_metric(evidence: list[Any] | None) -> bool:
    """True when evidence carries a named official count/range metric with a value."""
    for item in evidence or ():
        metric = str(getattr(item, "metric_name", "") or "").strip()
        if metric not in _STRUCTURED_OFFICIAL_METRICS:
            continue
        if getattr(item, "metric_value", None) is None:
            continue
        return True
    return False


def event_news_google_counter_not_required(
    *,
    market: Any | None = None,
    ticker: str = "",
    yes_ask: float | None = None,
    no_ask: float | None = None,
    evidence: list[Any] | None = None,
    estimated_probability: float | None = None,
    force_side: str | None = None,
    require_favorite_band: bool = True,
    config: Any = None,
) -> bool:
    """Politics reserve favorites may keep a structured official p without Google.

    Fail closed on the uninformative 0.5 shrug, URL-only official pages, missing
    named count/range metric, executable 0.00-0.35 asks, or missing p+side.
    Freeze is unchanged. Persist/admission may skip the ask band; those callers
    re-check quotes later.
    """
    side = str(force_side or "").strip().lower()
    if side not in {"yes", "no"}:
        return False
    try:
        probability = float(estimated_probability)
    except (TypeError, ValueError):
        return False
    if not 0.0 < probability < 1.0:
        return False
    if event_news_uninformative_shrug_p(probability):
        return False
    if not event_news_has_structured_official_metric(evidence):
        return False
    if market is not None:
        if event_news_non_politics_series(market, config=config):
            return False
        if not event_news_is_reserve_series(market, config=config):
            return False
        if event_news_crossed_asks(market, config=config):
            return False
        if event_news_missing_snapshot_ask(market, config=config):
            return False
        ticker = str(getattr(market, "ticker", "") or ticker)
        yes_cents, no_cents = snapshot_ask_cents(market)
        yes_ask = None if yes_cents is None else yes_cents / 100.0
        no_ask = None if no_cents is None else no_cents / 100.0
    elif not event_news_is_reserve_series(
        SimpleNamespace(ticker=str(ticker or "")),
        config=config,
    ):
        return False
    if require_favorite_band or market is not None:
        if not _event_news_favorite_ask_in_trade_band(yes_ask, no_ask):
            return False
        yes_norm = _event_news_norm_ask(yes_ask)
        no_norm = _event_news_norm_ask(no_ask)
        if (
            yes_norm is not None
            and no_norm is not None
            and (yes_norm + no_norm) < 1.0 - 1e-12
        ):
            return False
    return True


def event_news_settlement_source_names(market: Any) -> tuple[str, ...]:
    """Non-empty settlement source strings. rules_primary is not a source."""
    raw = getattr(market, "settlement_sources", None)
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in raw if str(item or "").strip())


def event_news_prewarm_retries_terminal_missing_counter(
    market: Any | None,
    *,
    config: Any = None,
) -> bool:
    """Retry a terminal politics row only for an in-band reserve favorite.

    The market must already name an official settlement source. A
    rules_primary sentence alone does not retry, and this does not order.
    """
    if market is None:
        return False
    if not event_news_is_reserve_series(market, config=config):
        return False
    if not event_news_settlement_source_names(market):
        return False
    if event_news_non_politics_series(market, config=config):
        return False
    if event_news_crossed_asks(market, config=config):
        return False
    if event_news_missing_snapshot_ask(market, config=config):
        return False
    yes_cents, no_cents = snapshot_ask_cents(market)
    yes_ask = None if yes_cents is None else yes_cents / 100.0
    no_ask = None if no_cents is None else no_cents / 100.0
    return _event_news_favorite_ask_in_trade_band(yes_ask, no_ask)


def _event_news_norm_ask(raw: float | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not 0.0 < value < 100.0:
        return None
    return value / 100.0 if value > 1.0 else value


def _event_news_favorite_ask_in_trade_band(
    yes_ask: float | None,
    no_ask: float | None,
) -> bool:
    for price in (_event_news_norm_ask(yes_ask), _event_news_norm_ask(no_ask)):
        if price is None:
            continue
        if 0.00 <= price <= 0.35:
            continue
        if 0.55 <= price <= 0.99:
            return True
    return False


def event_news_official_p_ready(
    *,
    estimated_probability: float | None,
    force_side: str | None,
    market: Any,
    config: Any = None,
) -> bool:
    """True when research_gate already priced both sides on an in-band book."""
    side = str(force_side or "").strip().lower()
    if side not in {"yes", "no"}:
        return False
    try:
        probability = float(estimated_probability)
    except (TypeError, ValueError):
        return False
    if not 0.0 < probability < 1.0:
        return False
    if event_news_uninformative_shrug_p(probability):
        return False
    if event_news_prewarm_skip_reason(market, config=config) is not None:
        return False
    yes_ask, no_ask = snapshot_ask_cents(market)
    return yes_ask is not None and no_ask is not None


def event_news_admission_gate_reason(
    market: Any,
    *,
    edge: float | None = None,
    force_side: str | None = None,
    open_tickers: list[str] | tuple[str, ...] | None = None,
    config: Any = None,
    environ: Mapping[str, str] | None = None,
    live: bool = True,
    probability: float | None = None,
) -> str | None:
    """Admission is the gate chain. Family runs only when open tickers are passed.

    Flag off does not apply a second edge formula. Flag on requires fee-net
    at or above the yaml edge_min and shadow-logs a disagreement with flat 0.04.
    """
    reason = event_news_gate_chain_reason(
        market,
        side=force_side,
        edge=edge,
        open_tickers=open_tickers,
        config=config,
        environ=environ,
        live=live,
    )
    if reason or not event_news_unified_edge_enabled(environ):
        return reason
    yes_ask, no_ask = snapshot_ask_cents(market)
    side_name = str(force_side or "").strip().lower()
    ask = yes_ask if side_name == "yes" else no_ask if side_name == "no" else None
    scored = event_news_score_edge(
        market,
        yes_probability=float(probability) if probability is not None else 0.0,
        side=side_name,
        ask_cents=ask if type(ask) is int else None,
        contracts=1,
        config=config,
    )
    try:
        old_edge = float(edge) if edge is not None else 0.0
    except (TypeError, ValueError):
        old_edge = 0.0
    old_take = edge is not None and abs(old_edge) >= 0.04
    event_news_shadow_edge(
        market, side_name, ask if type(ask) is int else None, scored,
        old_edge=old_edge, old_take=old_take,
    )
    if scored is not None and scored["new_take"]:
        return None
    if scored is None or not scored["readable"]:
        return "unified_edge_unreadable"
    return "below_fee_net_min_edge"


def event_news_dossier_is_stale(
    researched_ts: str | None,
    *,
    now: datetime | None = None,
    max_age_seconds: float = 6 * 3600,
) -> bool:
    """Decision-grade older than max_age must be re-researched, not blend-killed."""
    text = str(researched_ts or "").strip()
    if not text:
        return True
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return (clock.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() > max_age_seconds


def event_news_official_research_kwargs(*, config: Any = None) -> dict[str, bool]:
    return {
        "allow_official_pdf_and_homepage": True,
        "prefer_official_sources": True,
    }


def event_news_omit_idle_runtime_tasks(*, config: Any = None) -> bool:
    """Politics does not start news-discovery or second-venue shells."""
    return True


def event_news_operator_live_allow(*, config: Any = None) -> bool:
    """Explicit politics live-allow. LIVE_TRADING_ENABLED remains the kill switch.

    Does not apply to freeze/DJI. Does not by itself place live orders.
    """
    active = config if config is not None else cfg
    return bool(getattr(active, "event_news_live_allow", False))


def event_news_forecast_refresh_series(
    configured: tuple[str, ...] | list[str] | None,
    *,
    config: Any = None,
) -> tuple[str, ...]:
    """Drop inherited freeze forecast-refresh series on the politics desk."""
    return ()


def event_news_matcher_reserve_series(*, config: Any = None) -> tuple[str, ...]:
    """Series that must stay in the politics matcher cache.

    Live rows come from the universe file. Paper rows stay out of this
    tuple so they do not take a prewarm slot. Env extra and the configured
    fallback are still prepended, then deduped, when the file loads.
    A failed file returns nothing.
    """
    if universe_failed():
        return ()
    live = live_series_tickers()
    allowed = set(live)
    extra = tuple(
        part.strip().upper()
        for part in str(os.getenv("EVENT_NEWS_SERIES_RESERVE", "")).split(",")
        if part.strip()
    )
    fallback = tuple(
        str(series).strip().upper()
        for series in (
            getattr(config if config is not None else cfg, "research_prewarm_sourceable_series_fallback", ())
            or ()
        )
        if str(series).strip()
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for series in (*extra, *fallback, *live):
        if series in allowed and series not in seen:
            seen.add(series)
            ordered.append(series)
    return tuple(ordered)


def event_news_is_reserve_series(market: Any, *, config: Any = None) -> bool:
    """True for freeze, and for politics only on pinned money-path series.

    A series matches the exact ticker or that ticker plus a hyphen.
    A glued sibling name does not match the shorter series.
    """
    if universe_failed():
        return False
    series = event_news_series_ticker(market)
    ticker = str(getattr(market, "ticker", "") or "").strip().upper()
    for prefix in event_news_matcher_reserve_series(config=config):
        if series_ticker_matches(series, prefix) or series_ticker_matches(ticker, prefix):
            return True
    return False


def event_news_prewarm_seed_markets(
    *,
    rest_client: Any,
    matcher_markets: list[Any] | None = None,
    config: Any = None,
) -> list[Any]:
    """Politics prewarm uses fresh pinned-series quotes, not the 30-minute cache.

    ATM politics books cross the 0.55 band inside one matcher TTL. Filtering
    favorite-band admission on stale cache asks drops the only takeable rungs.
    Freeze does not call this helper.
    """
    cache = list(matcher_markets or [])
    if universe_failed():
        return []
    series = event_news_matcher_reserve_series(config=config)
    reader = getattr(rest_client, "get_markets", None)
    if not callable(reader) or not series:
        if cache:
            log.info("[EVENT_NEWS_PREWARM] using_matcher_cache markets=%d", len(cache))
        return cache
    fresh: list[Any] = []
    for series_ticker in series:
        try:
            page, _cursor = reader(
                status="open",
                series_ticker=series_ticker,
                limit=200,
            )
        except Exception as exc:
            log.warning(
                "[EVENT_NEWS_PREWARM] pinned series fetch failed series=%s err=%s",
                series_ticker,
                type(exc).__name__,
            )
            continue
        fresh.extend(list(page or []))
    if fresh:
        log.info(
            "[EVENT_NEWS_PREWARM] using_pinned_series_quotes series=%d markets=%d",
            len(series),
            len(fresh),
        )
        return fresh
    if cache:
        log.info("[EVENT_NEWS_PREWARM] using_matcher_cache markets=%d", len(cache))
    return cache


def event_news_pin_matcher_series(
    geo_tickers: list[str],
    all_series: list[Any],
    *,
    limit: int | None = None,
    config: Any = None,
) -> list[str]:
    """Prepend reserved politics series so recency ranking cannot drop them."""
    reserve = event_news_matcher_reserve_series(config=config)
    if not reserve:
        return list(geo_tickers)
    catalog = [
        str(series.get("ticker", "") if isinstance(series, dict) else getattr(series, "ticker", "") or "")
        for series in all_series
    ]
    by_upper = {ticker.upper(): ticker for ticker in catalog if ticker}
    pinned: list[str] = []
    for prefix in reserve:
        if prefix in by_upper:
            pinned.append(by_upper[prefix])
            continue
        for ticker, original in by_upper.items():
            if ticker == prefix or ticker.startswith(prefix + "-"):
                pinned.append(original)
                break
    merged = list(dict.fromkeys([*pinned, *geo_tickers]))
    if limit is not None and limit > 0:
        merged = merged[:limit]
    if pinned:
        log.info(
            "[EVENT_NEWS_MATCHER] reserved_series=%s pinned=%d geo_tickers=%d",
            pinned,
            len(pinned),
            len(merged),
        )
    return merged


async def apply_event_news_live_research(
    *,
    news: Any,
    market: Any,
    estimated_prob: float,
    confidence: float,
    keywords: list[str],
    reasoning: str,
    llm_dir: str | None,
    llm_mag: str | None,
    llm_conf: float | None,
    dossier_store: Any = None,
    config: Any = None,
) -> tuple[float, float, list[str], str, str | None, str | None, float | None]:
    """Live-fetch research_gate and prefer its probability when present.

    Fail-closed corroboration/settlement checks stay inside run_research_gate.
    If the gate does not return an independent p, the LLM/keyword estimate is kept.
    """

    from utils.event_news_grok_source import event_news_grok_source_reader

    store = dossier_store if dossier_store is not None else default_research_dossier_store()
    active = config if config is not None else cfg
    log.info(
        "[EVENT_NEWS_RESEARCH] official_fetch_enabled=true ticker=%s",
        getattr(market, "ticker", ""),
    )
    verdict = await run_research_gate(
        news,
        market,
        model_direction=llm_dir,
        model_confidence=llm_conf,
        model_reason=reasoning,
        yes_ask=_ask_probability(market, "yes_ask_cents", "yes_ask", "yes_price"),
        no_ask=_ask_probability(market, "no_ask_cents", "no_ask", "no_price"),
        live_mode=not bool(getattr(active, "is_paper_trading", True)),
        max_queries=int(getattr(active, "real_web_research_max_queries", 6)),
        research_timeout_seconds=float(
            getattr(active, "real_web_research_timeout_seconds", 12.0)
        ),
        dossier_store=store,
        cache_only=False,
        require_decision_grade=True,
        allow_official_pdf_and_homepage=True,
        prefer_official_sources=True,
        grok_source_reader=event_news_grok_source_reader,
    )
    used = (
        verdict.status in _PRIMARY_STATUSES
        and verdict.force_side in {"yes", "no"}
        and verdict.estimated_probability is not None
    )
    log.info(
        "[EVENT_NEWS_RESEARCH] live_gate ticker=%s used=%s status=%s skip=%s p=%s cache_only=false",
        getattr(market, "ticker", ""),
        used,
        getattr(verdict.status, "value", verdict.status),
        verdict.skip_reason,
        verdict.estimated_probability,
    )
    if not used:
        return (
            estimated_prob,
            confidence,
            keywords,
            reasoning,
            llm_dir,
            llm_mag,
            llm_conf,
        )
    merged_keywords = list(keywords or [])
    if "research_evidence" not in merged_keywords:
        merged_keywords.append("research_evidence")
    return (
        float(verdict.estimated_probability),
        float(verdict.confidence or llm_conf or confidence),
        merged_keywords,
        verdict.summary or reasoning,
        str(verdict.force_side),
        "moderate",
        float(verdict.confidence or llm_conf or confidence),
    )
