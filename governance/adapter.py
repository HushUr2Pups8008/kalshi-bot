"""GovernanceAdapter — the bot-agnostic seam (spec decision 9).

The agent loop talks to a GovernanceAdapter; concrete bots (Kalshi today,
Polymarket / Alpaca tomorrow) provide their own implementations. The
Protocol surface is intentionally small: all bot-specific concerns are
behind these five method signatures.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GovernanceAdapter(Protocol):
    """Read-only seam between the agent and the trading bot's data sources."""

    def collect_audit_data(self, window: timedelta) -> dict[str, Any]:
        """Return aggregated diagnostic-script outputs over the given window.

        Keys (Kalshi-specific, but the *shape* — a flat dict of named
        aggregations — is the contract):
          - 'alignment'  -> source_market_alignment_audit.aggregate(...)
          - 'keywords'   -> keyword_feedback.summarize(...)
          - 'reddit'     -> reddit_source_audit.collect(...)
          - 'freshness'  -> freshness_diagnostics.summarize(...)
        """
        ...

    def get_active_market_titles(self) -> list[str]:
        """Return current active-market titles (used to build prompt context
        about what the bot is currently watching)."""
        ...

    def get_recent_headline_samples(self, source: str, k: int = 5) -> list[str]:
        """Return up to k recent headline strings for the given source. Used
        to ground the LLM in concrete examples when reasoning about
        disable_source decisions."""
        ...

    def get_active_source_count(self) -> int:
        """Number of distinct sources observed in the last 24h. Drives the
        blast-radius cap (Phase 3) but the agent reads it from here so the
        Protocol stays bot-agnostic."""
        ...

    def get_active_source_list(self) -> list[str]:
        """List of distinct source identifiers observed in the last 24h.
        Order: ingestion-volume desc."""
        ...


class KalshiGovernanceAdapter:
    """Kalshi-specific implementation. Phase 2: Task 6 fills in the bodies."""

    def __init__(
        self,
        *,
        trade_log_path: Path,
        paper_db_path: Path,
        market_provider=None,
    ) -> None:
        self.trade_log_path = trade_log_path
        self.paper_db_path = paper_db_path
        self.market_provider = market_provider

    def collect_audit_data(self, window: timedelta) -> dict[str, Any]:
        raise NotImplementedError("Implemented in Task 6")

    def get_active_market_titles(self) -> list[str]:
        raise NotImplementedError("Implemented in Task 6")

    def get_recent_headline_samples(self, source: str, k: int = 5) -> list[str]:
        raise NotImplementedError("Implemented in Task 6")

    def get_active_source_count(self) -> int:
        raise NotImplementedError("Implemented in Task 6")

    def get_active_source_list(self) -> list[str]:
        raise NotImplementedError("Implemented in Task 6")
