from __future__ import annotations

import json
from typing import Any

from polymarket.models import PolymarketMarket
from trading.venue import Venue


def _money_value(payload: dict[str, Any] | None, default: float = 0.0) -> float:
    if isinstance(payload, (str, int, float)) and not isinstance(payload, bool):
        try:
            return float(payload)
        except (TypeError, ValueError):
            return default
    if not isinstance(payload, dict):
        return default
    try:
        return float(payload.get("value", default))
    except (TypeError, ValueError):
        return default


def _price_cents(outcome: dict[str, Any]) -> int | None:
    value = _money_value(outcome.get("bestAsk"), default=-1.0)
    if value < 0:
        return None
    return max(1, min(99, int(round(value * 100))))


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _outcome_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = _parse_json_list(payload.get("outcomes"))
    if not outcomes:
        return []
    if all(isinstance(outcome, dict) for outcome in outcomes):
        return outcomes

    prices = _parse_json_list(payload.get("outcomePrices"))
    if len(prices) != len(outcomes):
        return []

    return [
        {"name": str(name), "bestAsk": {"value": price}}
        for name, price in zip(outcomes, prices)
    ]


def _status(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("status") or "").strip().lower()
    if explicit:
        return explicit
    if payload.get("closed") is True:
        return "closed"
    if payload.get("active") is True:
        return "open"
    return ""


def normalize_polymarket_market(payload: dict[str, Any]) -> PolymarketMarket:
    outcomes = _outcome_dicts(payload)
    if len(outcomes) != 2:
        raise ValueError("Polymarket integration supports binary markets only")

    by_name = {
        str(outcome.get("name", "")).strip().lower(): outcome
        for outcome in outcomes
        if isinstance(outcome, dict)
    }
    if "yes" not in by_name or "no" not in by_name:
        raise ValueError("binary Polymarket market must contain Yes and No outcomes")

    return PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id=str(payload.get("slug") or payload.get("id") or "").strip(),
        title=str(payload.get("title") or payload.get("question") or "").strip(),
        status=_status(payload),
        yes_ask_cents=_price_cents(by_name["yes"]),
        no_ask_cents=_price_cents(by_name["no"]),
        volume_dollars=_money_value(payload.get("volume")),
        open_interest_dollars=_money_value(payload.get("openInterest")),
        close_time=str(
            payload.get("closeTime")
            or payload.get("close_time")
            or payload.get("endDate")
            or ""
        ),
        is_binary=True,
        question=str(payload.get("question") or "").strip(),
        subtitle=str(payload.get("subtitle") or "").strip(),
        category=str(payload.get("category") or "").strip().lower(),
    )
