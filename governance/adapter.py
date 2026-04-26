"""GovernanceAdapter — the bot-agnostic seam (spec decision 9).

The agent loop talks to a GovernanceAdapter; concrete bots (Kalshi today,
Polymarket / Alpaca tomorrow) provide their own implementations. The
Protocol surface is intentionally small: all bot-specific concerns are
behind these five method signatures.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
        from scripts import (
            freshness_diagnostics,
            keyword_feedback,
            reddit_source_audit,
            source_market_alignment_audit,
        )
        from scripts.flag_outcome_correlation import collect as _foc_collect

        until = datetime.now(timezone.utc)
        since = until - window

        # alignment requires collecting match_index + analysis_rows first,
        # then aggregating — source_market_alignment_audit.aggregate does not
        # accept a path directly; it wraps flag_outcome_correlation.collect.
        match_index, analysis_rows, _read_stats = _foc_collect(
            self.trade_log_path, since, until, False, verbose=False,
        )
        alignment_result = source_market_alignment_audit.aggregate(
            match_index, analysis_rows,
        )

        return {
            "alignment": alignment_result,
            "keywords": keyword_feedback.summarize(
                self.trade_log_path, since, until,
            ),
            "reddit": reddit_source_audit.collect(
                self.trade_log_path, since, until, False,
            ),
            "freshness": freshness_diagnostics.summarize(
                self.trade_log_path, since, until,
            ),
        }

    def get_active_market_titles(self) -> list[str]:
        if self.market_provider is None:
            return []
        return [
            getattr(m, "title", "")
            for m in self.market_provider()
            if getattr(m, "title", "")
        ]

    def get_recent_headline_samples(self, source: str, k: int = 5) -> list[str]:
        if not self.trade_log_path.exists():
            return []
        seen: list[str] = []
        with self.trade_log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("source") != source:
                    continue
                headline = rec.get("headline")
                if not headline:
                    continue
                seen.append(str(headline))
        # Return last k, preserving forward order.
        return seen[-k:]

    def get_active_source_count(self) -> int:
        return len(self.get_active_source_list())

    def get_active_source_list(self) -> list[str]:
        if not self.trade_log_path.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        counts: dict[str, int] = {}
        with self.trade_log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("ts")
                if not ts:
                    continue
                try:
                    rec_dt = datetime.fromisoformat(ts)
                except ValueError:
                    continue
                if rec_dt < cutoff:
                    continue
                source = rec.get("source")
                if not source:
                    continue
                counts[source] = counts.get(source, 0) + 1
        return [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
