from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolymarketAccountStatus:
    enabled: bool
    live_trading: bool
    paper_execution: str
    account_balance: float | None = None
    positions_count: int | None = None
    account_error: str | None = None

    def log_line(self) -> str:
        enabled = str(self.enabled).lower()
        live_trading = str(self.live_trading).lower()
        parts = [
            "Polymarket status:",
            f"enabled={enabled}",
            f"live_trading={live_trading}",
            f"paper_execution={self.paper_execution}",
        ]
        if self.account_balance is not None:
            parts.append(f"account_balance=${self.account_balance:.2f}")
        else:
            parts.append("account_balance=unavailable")
        if self.positions_count is not None:
            parts.append(f"positions={self.positions_count}")
        if self.account_error:
            parts.append("account_error=true")
        return " ".join(parts)


def count_positions(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    positions = payload.get("positions", [])
    if isinstance(positions, list):
        return len(positions)
    if isinstance(positions, dict):
        return len(positions)
    return 0
