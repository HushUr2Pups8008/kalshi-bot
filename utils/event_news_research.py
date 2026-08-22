"""Politics/event-news cohort research overrides.

Isolated to PAPER_COHORT_ID=kalshi-event-news-20260820. The DJI freeze
process uses a different cohort id and must not take these paths.
"""

from __future__ import annotations

import os
from typing import Any

from analysis.research_gate import ResearchStatus, run_research_gate
from config import cfg
from tasks.research_dossier import default_store as default_research_dossier_store
from utils.logger import get_logger

log = get_logger("event_news_research")

EVENT_NEWS_COHORT_ID = "kalshi-event-news-20260820"
_PRIMARY_STATUSES = {
    ResearchStatus.TRADE_CANDIDATE,
    ResearchStatus.DECISION_GRADE_CANDIDATE,
}
# Skip taking when last trade and YES ask disagree by this many cents.
_LAST_ASK_DIVERGENCE_CENTS = 15
# Top-of-book size below this is treated as untradeable on the politics desk.
_MIN_TOP_SIZE = 2.0
# Joint OI + 24h volume floor; missing fields do not skip.
_MIN_OPEN_INTEREST = 10.0
_MIN_VOLUME_24H = 5.0
# Early-close contracts get a compressed Kelly horizon (days).
_EARLY_CLOSE_HORIZON_DAYS = 2.0
# Fallback favorite band when config bands are missing.
_FAVORITE_ASK_LOW = 0.55
_FAVORITE_ASK_HIGH = 0.99
# Kalshi general taker fee is 0.07 * p * (1-p). Buffer keeps min-edge above fee.
_TAKER_FEE_COEFFICIENT = 0.07
_MIN_EDGE_FEE_BUFFER = 0.005
# Commodity / macro / weather ladders that steal favorite-band prewarm.
_NON_POLITICS_SERIES_PREFIXES = (
    "KXAAAGAS",
    "KXDJI",
    "KXNASDAQ",
    "KXGOLD",
    "KXSILVER",
    "KXHIGHNY",
    "KXHIGHLAX",
    "KXWEATHER",
    "KXFEAR",
    "KXCPI",
    "KXGDP",
    "KXFED",
    "KXBTC",
    "KXETH",
)



def is_event_news_paper_cohort(config: Any = None) -> bool:
    active = config if config is not None else cfg
    return str(getattr(active, "paper_cohort_id", "")).strip().lower() == EVENT_NEWS_COHORT_ID


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
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
    if not is_event_news_paper_cohort(config):
        return mid, "mid"
    ask = _cents_or_probability(
        getattr(market, "yes_ask_cents", None)
    ) or _cents_or_probability(getattr(market, "yes_ask", None))
    if ask is None:
        return mid, "mid"
    return ask, "yes_ask"


def event_news_spread_disagreement(market: Any, *, config: Any = None) -> str | None:
    """Do not take when last trade and ask disagree badly (politics only)."""
    if not is_event_news_paper_cohort(config):
        return None
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
    if not is_event_news_paper_cohort(config):
        return None
    sizes = [
        _as_float(getattr(market, name, None))
        for name in ("yes_ask_size", "no_ask_size")
    ]
    present_sizes = [size for size in sizes if size is not None]
    if present_sizes and max(present_sizes) < _MIN_TOP_SIZE:
        log.info(
            "[EVENT_NEWS_LIQUIDITY] skip ticker=%s reason=illiquid_top_size size=%s",
            getattr(market, "ticker", ""),
            max(present_sizes),
        )
        return "illiquid_top_size"
    oi = _as_float(getattr(market, "open_interest_fp", None))
    if oi is None:
        oi = _as_float(getattr(market, "open_interest", None))
    vol = _as_float(getattr(market, "volume_24h_fp", None))
    if vol is None:
        vol = _as_float(getattr(market, "volume_24h", None))
    if oi is not None and vol is not None and oi < _MIN_OPEN_INTEREST and vol < _MIN_VOLUME_24H:
        log.info(
            "[EVENT_NEWS_LIQUIDITY] skip ticker=%s reason=illiquid_open_interest oi=%s vol24h=%s",
            getattr(market, "ticker", ""),
            oi,
            vol,
        )
        return "illiquid_open_interest"
    return None


def event_news_horizon_days(market: Any, days: float, *, config: Any = None) -> float:
    """Compress Kelly horizon when Kalshi says the market can close early."""
    if not is_event_news_paper_cohort(config):
        return days
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
    if not is_event_news_paper_cohort(config):
        return default_cap
    return 1


def event_news_exposure_prefix(market: Any, fallback: str, *, config: Any = None) -> str:
    """Prefer event_ticker so one headline cannot spray a whole ladder."""
    if not is_event_news_paper_cohort(config):
        return fallback
    event = str(getattr(market, "event_ticker", "") or "").strip()
    if event:
        return event
    return fallback


def event_news_settle_statuses(*, config: Any = None) -> tuple[str, ...]:
    """Paper auto-resolve statuses. Politics may write on determined+result."""
    if is_event_news_paper_cohort(config):
        return ("finalized", "settled", "determined")
    return ("finalized", "settled")


def _ask_probability(market: Any, *names: str) -> float | None:
    for name in names:
        raw = getattr(market, name, None)
        if raw is None:
            continue
        value = float(raw)
        return value / 100.0 if value > 1.0 else value
    return None


def snapshot_ask_cents(market: Any) -> tuple[int | None, int | None]:
    """Return integer yes/no ask cents if both executable asks exist."""

    def _as_cents(name: str, alt: str) -> int | None:
        raw = getattr(market, name, None)
        if raw is None:
            raw = getattr(market, alt, None)
        if raw is None:
            return None
        value = float(raw)
        cents = int(round(value if value > 1.0 else value * 100.0))
        if 1 <= cents <= 99:
            return cents
        return None

    return _as_cents("yes_ask_cents", "yes_ask"), _as_cents("no_ask_cents", "no_ask")


def event_news_missing_snapshot_ask(market: Any, *, config: Any = None) -> str | None:
    if not is_event_news_paper_cohort(config):
        return None
    yes_ask, no_ask = snapshot_ask_cents(market)
    if yes_ask is None or no_ask is None:
        return "missing_snapshot_ask"
    return None


def _price_in_band(price: float, low: float, high: float) -> bool:
    if high >= 1.0:
        return low <= price <= high
    return low <= price < high


def event_news_in_allowed_ask_band(market: Any, *, config: Any = None) -> bool:
    """True when the executable YES ask is inside the politics favorite band."""
    if not is_event_news_paper_cohort(config):
        return True
    price, _source = routing_yes_probability(market, config=config)
    active = config if config is not None else cfg
    allowed = list(getattr(active, "llm_allowed_price_bands", ()) or ())
    if not allowed:
        allowed = [(_FAVORITE_ASK_LOW, _FAVORITE_ASK_HIGH)]
    excluded = list(getattr(active, "llm_excluded_price_bands", ()) or ())
    if any(_price_in_band(price, low, high) for low, high in excluded):
        return False
    return any(_price_in_band(price, low, high) for low, high in allowed)


def event_news_crossed_asks(market: Any, *, config: Any = None) -> str | None:
    """Skip locked/crossed books (YES ask + NO ask < 100c) on politics only."""
    if not is_event_news_paper_cohort(config):
        return None
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


def event_news_min_edge(base: float, market: Any, *, config: Any = None) -> float:
    """Raise paper min-edge to taker-fee + buffer so admission matches keep-rule."""
    if not is_event_news_paper_cohort(config):
        return base
    price, _source = routing_yes_probability(market, config=config)
    p = min(max(float(price), 0.01), 0.99)
    fee = _TAKER_FEE_COEFFICIENT * p * (1.0 - p)
    required = fee + _MIN_EDGE_FEE_BUFFER
    if required <= base:
        return base
    log.info(
        "[EVENT_NEWS_EDGE] ticker=%s p=%.3f fee=%.4f base=%.4f required=%.4f",
        getattr(market, "ticker", ""),
        p,
        fee,
        base,
        required,
    )
    return required


def event_news_series_ticker(market: Any) -> str:
    series = str(getattr(market, "series_ticker", "") or "").strip().upper()
    if series:
        return series
    ticker = str(getattr(market, "ticker", "") or "").strip().upper()
    return ticker.split("-", 1)[0] if ticker else ""


def event_news_non_politics_series(market: Any, *, config: Any = None) -> str | None:
    """Skip commodity/macro/weather ladders on the politics desk."""
    if not is_event_news_paper_cohort(config):
        return None
    series = event_news_series_ticker(market)
    ticker = str(getattr(market, "ticker", "") or "").strip().upper()
    extra = tuple(
        part.strip().upper()
        for part in str(os.getenv("EVENT_NEWS_SERIES_DENY_PREFIXES", "")).split(",")
        if part.strip()
    )
    for prefix in (*_NON_POLITICS_SERIES_PREFIXES, *extra):
        if series.startswith(prefix) or ticker.startswith(prefix):
            log.info(
                "[EVENT_NEWS_PREWARM] skip ticker=%s series=%s reason=non_politics_series",
                getattr(market, "ticker", ""),
                series or ticker,
            )
            return "non_politics_series"
    return None


def event_news_prewarm_skip_reason(market: Any, *, config: Any = None) -> str | None:
    """Politics prewarm only spends research on favorite-band executable books."""
    if not is_event_news_paper_cohort(config):
        return None
    denied = event_news_non_politics_series(market, config=config)
    if denied:
        return denied
    missing = event_news_missing_snapshot_ask(market, config=config)
    if missing:
        return missing
    illiquid = event_news_illiquid(market, config=config)
    if illiquid:
        return illiquid
    crossed = event_news_crossed_asks(market, config=config)
    if crossed:
        return crossed
    if not event_news_in_allowed_ask_band(market, config=config):
        return "ask_outside_favorite_band"
    return None


def event_news_prewarm_allows(market: Any, *, config: Any = None) -> bool:
    return event_news_prewarm_skip_reason(market, config=config) is None


def event_news_official_research_kwargs(*, config: Any = None) -> dict[str, bool]:
    if not is_event_news_paper_cohort(config):
        return {}
    return {
        "allow_official_pdf_and_homepage": True,
        "prefer_official_sources": True,
    }


def event_news_omit_idle_runtime_tasks(*, config: Any = None) -> bool:
    """Politics does not start Reddit/GDELT/Polymarket/subreddit shells."""
    return is_event_news_paper_cohort(config)


def event_news_forecast_refresh_series(
    configured: tuple[str, ...] | list[str] | None,
    *,
    config: Any = None,
) -> tuple[str, ...]:
    """Drop inherited freeze forecast-refresh series on the politics desk."""
    if is_event_news_paper_cohort(config):
        return ()
    return tuple(str(series).strip().upper() for series in (configured or ()) if str(series).strip())


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
    if not is_event_news_paper_cohort(config):
        return (
            estimated_prob,
            confidence,
            keywords,
            reasoning,
            llm_dir,
            llm_mag,
            llm_conf,
        )

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
