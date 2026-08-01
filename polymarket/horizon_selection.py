from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Sequence

from polymarket.models import PolymarketMarket
from trading.venue import Venue
from utils.market_horizon import evaluate_market_horizon


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ALLOWED_CATEGORIES = frozenset({"politics"})
_DISCOVERY_TOPIC_TOKENS = frozenset(
    {
        "election",
        "elections",
        "primary",
        "primaries",
        "senate",
        "house",
        "congress",
        "president",
        "presidential",
        "governor",
        "mayor",
        "parliament",
        "minister",
        "supreme",
        "court",
        "justice",
        "legislature",
        "ballot",
        "referendum",
        "vote",
        "voting",
        "policy",
        "politics",
        "political",
    }
)
_STOPWORDS = frozenset({"the", "and", "for", "that", "with"})


def select_polymarket_horizon_band(
    markets: Sequence[PolymarketMarket],
    *,
    now: datetime,
    lower_exclusive_days: float,
    upper_inclusive_days: float,
) -> list[PolymarketMarket]:
    """Return pre-admission-matchable markets in the exact `(lower, upper]` band."""

    lower = _finite_days(lower_exclusive_days, label="lower_exclusive_days")
    upper = _finite_days(upper_inclusive_days, label="upper_inclusive_days")
    if lower >= upper:
        raise ValueError("select_polymarket_horizon_band requires ordered finite bounds")

    selected: list[PolymarketMarket] = []
    for market in markets:
        if not _is_pre_admission_matchable_market(market):
            continue
        horizon = evaluate_market_horizon(
            market.close_time,
            now=now,
            max_days_to_close=upper,
        )
        if not horizon.eligible or horizon.days_to_close is None:
            continue
        if horizon.days_to_close <= lower:
            continue
        selected.append(market)
    return selected


def _finite_days(value: float, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        days = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(days):
        raise ValueError(f"{label} must be a finite number")
    return days


def _is_pre_admission_matchable_market(market: PolymarketMarket) -> bool:
    return (
        market.venue == Venue.POLYMARKET_US
        and market.is_tradeable()
        and not _is_suppressed_market(market)
    )


def _is_suppressed_market(market: PolymarketMarket) -> bool:
    category = market.category.strip().lower()
    if category in _ALLOWED_CATEGORIES:
        return False
    has_resolution_source = bool(market.resolution_source.strip())
    has_liquidity = market.volume_dollars > 0.0 or market.open_interest_dollars > 0.0
    context_text = " ".join(
        part
        for part in (
            category,
            market.event_title,
            market.series_title,
            *market.tags,
        )
        if part
    ).lower()
    topic_relevant = bool(_meaningful_tokens(context_text) & _DISCOVERY_TOPIC_TOKENS)
    return not (has_resolution_source and has_liquidity and topic_relevant)


def _meaningful_tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }
