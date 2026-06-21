from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Any, Iterable
from datetime import datetime, timezone

from observer.events import ObserverEvent
from utils.app_log_reader import iter_app_log_records
from utils.trade_log_reader import iter_trade_records


_APP_SEVERITY_BY_LEVEL = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warning",
    "WARN": "warning",
    "ERROR": "error",
    "CRITICAL": "critical",
}


def _limited(events: Iterable[ObserverEvent], limit: int | None) -> list[ObserverEvent]:
    if limit is None:
        return list(events)
    if limit <= 0:
        return []
    return list(islice(events, limit))


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_simple_app_line(raw: str) -> dict[str, Any] | None:
    ts_text, separator, remainder = raw.partition(" ")
    if not separator:
        return None
    ts = _parse_iso_timestamp(ts_text)
    if ts is None:
        return None
    level, separator, message = remainder.partition(" ")
    if not separator or not level.isalpha():
        return None
    return {"ts": ts, "level": level.upper(), "task": None, "message": message, "raw": raw}


def _title_from_trade_record(record: dict[str, Any], event_type: str) -> str:
    normalized_event_type = event_type.upper()
    if normalized_event_type == "PAPER_TRADE":
        context = record.get("signal_headline") or record.get("market_title") or record.get("ticker")
        if context:
            return f"Paper trade opened: {context}"
        return "Paper trade opened"
    if normalized_event_type == "PAPER_RESOLUTION":
        context = record.get("market_title") or record.get("ticker")
        if context:
            return f"Paper trade resolved: {context}"
        return "Paper trade resolved"
    headline = record.get("headline") or record.get("title") or record.get("reason")
    if headline:
        return str(headline)
    return event_type.replace("_", " ").title()


def _trade_details(record: dict[str, Any]) -> dict[str, Any]:
    excluded = {"type", "ts", "ticker", "market_ticker", "headline", "title", "reason"}
    return {key: value for key, value in record.items() if key not in excluded}


def _paper_trade_details(record: dict[str, Any]) -> dict[str, Any]:
    """Return the observer-safe paper-trade lifecycle summary.

    Keep this deliberately narrower than the raw trade log: no account balances,
    positions, fills, order history, or full rationale payloads. Operators get a
    lifecycle notification and compact trade context; detailed accounting remains
    in reports / local logs.
    """

    event_type = str(record.get("type") or "").upper()
    if event_type == "PAPER_TRADE":
        allowed = (
            "trade_id",
            "side",
            "contracts",
            "price_cents",
            "estimated_probability",
            "edge",
            "signal_source",
            "signal_headline",
            "bankroll_delta_dollars",
        )
        details = {key: record[key] for key in allowed if key in record}
        if "bankroll_delta_dollars" in details:
            details["simulated_notional_delta_dollars"] = details.pop("bankroll_delta_dollars")
        details["status"] = "opened"
        details["trade_id_description"] = "paper source event identity only; not a live order or fill id"
        return details
    if event_type == "PAPER_RESOLUTION":
        allowed = (
            "trade_id",
            "resolved_yes",
            "pnl_dollars",
            "bankroll_delta_dollars",
        )
        details = {key: record[key] for key in allowed if key in record}
        if "bankroll_delta_dollars" in details:
            details["simulated_notional_delta_dollars"] = details.pop("bankroll_delta_dollars")
        details["status"] = "resolved"
        details["trade_id_description"] = "paper source event identity only; not a live order or fill id"
        return details
    return _trade_details(record)


def _paper_trade_action(record: dict[str, Any], event_type: str) -> str:
    normalized_event_type = event_type.upper()
    if normalized_event_type == "PAPER_TRADE":
        return "paper_trade_opened"
    if normalized_event_type == "PAPER_RESOLUTION":
        return "paper_trade_resolved"
    return str(record.get("action") or event_type.lower())


def events_from_app_log(path: Path, limit: int | None = None) -> list[ObserverEvent]:
    def generate() -> Iterable[ObserverEvent]:
        for record in iter_app_log_records(path):
            if record.get("level") is None:
                parsed = _parse_simple_app_line(str(record.get("raw") or ""))
                if parsed is not None:
                    record = parsed
            level = str(record.get("level") or "INFO").upper()
            severity = _APP_SEVERITY_BY_LEVEL.get(level, "info")
            message = str(record.get("message") or "Log event")
            yield ObserverEvent(
                source="app_log",
                event_type=severity,
                severity=severity,
                title=message,
                ts=record.get("ts"),
                details={
                    "task": record.get("task"),
                    "raw": record.get("raw") if record.get("level") is None else None,
                },
            )

    return _limited(generate(), limit)


def events_from_trade_log(path: Path, limit: int | None = None) -> list[ObserverEvent]:
    def generate() -> Iterable[ObserverEvent]:
        for record in iter_trade_records(path):
            event_type = str(record.get("type") or "TRADE_LOG").strip() or "TRADE_LOG"
            normalized_event_type = event_type.upper()
            ticker = record.get("ticker") or record.get("market_ticker")
            ts = _parse_iso_timestamp(record.get("ts")) or _parse_iso_timestamp(record.get("timestamp"))
            is_paper_lifecycle = normalized_event_type in {"PAPER_TRADE", "PAPER_RESOLUTION"}
            yield ObserverEvent(
                source="trade_log",
                event_type=event_type.lower(),
                severity="info",
                title=_title_from_trade_record(record, event_type),
                ts=ts,
                market_ticker=str(ticker) if ticker else None,
                environment="paper" if is_paper_lifecycle else None,
                action=_paper_trade_action(record, event_type),
                details=_paper_trade_details(record),
            )

    return _limited(generate(), limit)
