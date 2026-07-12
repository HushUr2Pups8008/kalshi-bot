"""Shared research retry cadence and market-priority helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable


OFFICIAL_PENDING_UNKNOWN_DELAY_SECONDS = 1800.0


def extract_pending_event_at(
    evidence: Iterable[Any],
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the earliest future timestamp from structured pending evidence."""

    now = _as_utc(now or datetime.now(timezone.utc))
    candidates = []
    for item in evidence:
        metric_name = str(getattr(item, "metric_name", "") or "").lower()
        if not metric_name.endswith("_pending"):
            continue
        parsed = _pending_available_at(item)
        if parsed is not None and parsed > now:
            candidates.append(parsed)
    return min(candidates) if candidates else None


def _pending_available_at(item: Any) -> datetime | None:
    metric_name = str(getattr(item, "metric_name", "") or "").lower()
    explicit = _parse_utc(getattr(item, "available_at", None))
    if explicit is not None:
        return explicit
    if metric_name == "cpi_official_data_pending":
        text = " ".join(
            str(value or "")
            for value in (getattr(item, "title", ""), getattr(item, "snippet", ""))
        )
        match = re.search(
            r"\b(january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\s+(20\d{2})\b",
            text,
            re.IGNORECASE,
        )
        if match:
            month = {
                name: index
                for index, name in enumerate(
                    (
                        "january",
                        "february",
                        "march",
                        "april",
                        "may",
                        "june",
                        "july",
                        "august",
                        "september",
                        "october",
                        "november",
                        "december",
                    ),
                    start=1,
                )
            }[match.group(1).lower()]
            year = int(match.group(2))
            release_year, release_month = (
                (year + 1, 1) if month == 12 else (year, month + 1)
            )
            return datetime(release_year, release_month, 20, tzinfo=timezone.utc)
    if metric_name in {
        "bank_of_israel_decision_pending",
        "event_window_pending",
        "fed_decision_pending",
        "treasury_yield_data_pending",
        "truth_social_window_pending",
    }:
        return _parse_utc(getattr(item, "published_at", None))
    return None


def official_pending_retry_delay(
    *,
    now: datetime,
    event_at: datetime | None,
) -> float:
    """Choose coarse retry cadence from the known official release horizon."""

    now = _as_utc(now)
    if event_at is None:
        return OFFICIAL_PENDING_UNKNOWN_DELAY_SECONDS
    horizon = _as_utc(event_at) - now
    if horizon <= timedelta(days=1):
        return 1800.0
    if horizon <= timedelta(days=7):
        return 21600.0
    if horizon <= timedelta(days=30):
        return 86400.0
    return 604800.0


def research_market_priority_key(
    market: Any,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Rank capital-useful, near-term markets first without excluding tails."""

    prices = _market_ask_probabilities(market)
    quoted = [price for price in prices if price is not None and 0.0 < price < 1.0]
    tail_rank = (
        2
        if not quoted
        else int(all(price <= 0.03 or price >= 0.97 for price in quoted))
    )
    now = _as_utc(now or datetime.now(timezone.utc))
    close_at = _parse_utc(getattr(market, "close_time", None))
    if close_at is None:
        horizon_rank = 4
    else:
        horizon = close_at - now
        if horizon <= timedelta(days=2):
            horizon_rank = 0
        elif horizon <= timedelta(days=7):
            horizon_rank = 1
        elif horizon <= timedelta(days=30):
            horizon_rank = 2
        else:
            horizon_rank = 3
    return tail_rank, horizon_rank


def _market_ask_probabilities(market: Any) -> tuple[float | None, float | None]:
    return (
        _ask_probability(market, "yes_ask_cents", "yes_ask"),
        _ask_probability(market, "no_ask_cents", "no_ask"),
    )


def _ask_probability(market: Any, cents_attr: str, legacy_attr: str) -> float | None:
    cents = getattr(market, cents_attr, None)
    raw = cents if cents is not None else getattr(market, legacy_attr, None)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if cents is not None or value > 1.0:
        value /= 100.0
    return value


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _as_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
