from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ObserverEvent:
    """Sanitized, transport-agnostic event for operator notifications."""

    source: str
    event_type: str
    severity: str
    title: str
    details: dict[str, Any] = field(default_factory=dict)
    ts: datetime | None = None
    market_ticker: str | None = None
    environment: str | None = None
    action: str | None = None

    def with_details(self, details: dict[str, Any]) -> ObserverEvent:
        return replace(self, details=details)


def is_externally_sendable_event(event: ObserverEvent) -> bool:
    """Return True only for approved paper/simulated lifecycle notifications."""

    event_type = event.event_type.lower()
    action = (event.action or "").lower()
    environment = (event.environment or "").lower()
    return (
        event.source == "trade_log"
        and environment == "paper"
        and event_type in {"paper_trade", "paper_resolution"}
        and action in {"paper_trade_opened", "paper_trade_resolved"}
    )
