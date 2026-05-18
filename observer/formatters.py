from __future__ import annotations

from datetime import timezone
from html import escape
from typing import Any

from observer.events import ObserverEvent
from observer.sanitizer import sanitize_event


_SEVERITY_LABELS = {
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARN",
    "warn": "WARN",
    "error": "ERROR",
    "critical": "CRITICAL",
}


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_format_value(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={_format_value(nested)}" for key, nested in value.items())
    return str(value)


def format_telegram_html(event: ObserverEvent) -> str:
    """Format an observer event for Telegram HTML parse mode."""

    event = sanitize_event(event)
    severity = _SEVERITY_LABELS.get(event.severity.lower(), event.severity.upper())
    if event.environment == "paper" and event.event_type in {"paper_trade", "paper_resolution"}:
        lines = [
            "<b>PAPER / SIMULATED lifecycle update — not a live Kalshi order, fill, position, balance, or trading instruction.</b>",
            f"<b>{escape(severity)}</b> - {escape(event.title)}",
        ]
    else:
        lines = [
            "<b>Kalshi-bot Observer</b>",
            f"<b>{escape(severity)}</b> - {escape(event.title)}",
        ]

    meta: list[str] = []
    if event.environment:
        meta.append(f"env={event.environment}")
    if event.market_ticker:
        meta.append(f"market={event.market_ticker}")
    if event.action:
        meta.append(f"action={event.action}")
    meta.append(f"source={event.source}")
    meta.append(f"type={event.event_type}")
    if event.ts:
        meta.append(f"ts={event.ts.astimezone(timezone.utc).isoformat()}")
    lines.append("<code>" + escape(" | ".join(meta)) + "</code>")

    for key in sorted(event.details):
        value = event.details[key]
        if value is None or value == "":
            continue
        lines.append(f"- <b>{escape(str(key))}</b>: {escape(_format_value(value))}")

    if event.environment == "paper" and event.event_type in {"paper_trade", "paper_resolution"}:
        lines.append(
            "<i>Paper/simulated archive/status only. Not live P&amp;L, not account balance, "
            "not execution approval, and not trading advice.</i>"
        )
    else:
        lines.append("<i>Human-readable archive/status only. Not execution approval.</i>")
    return "\n".join(lines)
