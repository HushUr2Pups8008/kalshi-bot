"""Fail-closed close-time admission policy shared by market venues."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class MarketHorizonDecision:
    eligible: bool
    reason: str | None
    close_time: datetime | None
    days_to_close: float | None


def evaluate_market_horizon(
    close_time: object,
    *,
    now: datetime,
    max_days_to_close: float,
) -> MarketHorizonDecision:
    """Evaluate one provider close time without silently accepting bad input."""

    observed_at = _aware_utc_now(now)
    max_days = _positive_finite_days(max_days_to_close)
    parsed_close_time = _parse_close_time(close_time)
    if parsed_close_time is None:
        return MarketHorizonDecision(
            eligible=False,
            reason="close_time_missing" if _is_missing(close_time) else "close_time_invalid",
            close_time=None,
            days_to_close=None,
        )

    days_to_close = (parsed_close_time - observed_at).total_seconds() / 86_400.0
    if parsed_close_time <= observed_at:
        return MarketHorizonDecision(
            eligible=False,
            reason="market_expired",
            close_time=parsed_close_time,
            days_to_close=days_to_close,
        )
    if parsed_close_time > observed_at + timedelta(days=max_days):
        return MarketHorizonDecision(
            eligible=False,
            reason="market_too_far",
            close_time=parsed_close_time,
            days_to_close=days_to_close,
        )
    return MarketHorizonDecision(
        eligible=True,
        reason=None,
        close_time=parsed_close_time,
        days_to_close=days_to_close,
    )


def _aware_utc_now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return now.astimezone(timezone.utc)


def _positive_finite_days(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("max_days_to_close must be a positive finite number")
    try:
        days = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_days_to_close must be a positive finite number") from exc
    if not math.isfinite(days) or days <= 0:
        raise ValueError("max_days_to_close must be a positive finite number")
    return days


def _parse_close_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
