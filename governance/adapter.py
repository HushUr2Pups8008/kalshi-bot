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
        # source_market_alignment_audit.aggregate returns a tuple
        # (pairs_by_(source,series), unpaired_count); reddit_source_audit.collect
        # returns (sub_stats_by_name, read_stats). Both must be normalized to
        # the dict shape governance/evidence.py and governance/prompts.py
        # consume. See plan Task 6 follow-up note for rationale.
        pairs_dict, _unpaired = source_market_alignment_audit.aggregate(
            match_index, analysis_rows,
        )
        sub_stats_dict, _r2 = reddit_source_audit.collect(
            self.trade_log_path, since, until, False,
        )
        keyword_summary = keyword_feedback.summarize(
            self.trade_log_path, since, until,
        )
        freshness_summary = freshness_diagnostics.summarize(
            self.trade_log_path, since, until,
        )

        return {
            "alignment": _normalize_alignment(pairs_dict),
            "reddit": _normalize_reddit(sub_stats_dict),
            "keywords": _normalize_keywords(keyword_summary),
            "freshness": _normalize_freshness(freshness_summary),
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
        # Use iter_trade_records — the project's canonical trade-log reader —
        # so this method handles both single-file logs (test fixtures) and
        # directory-layout logs (production logs/trades/{archive,live}/...).
        from utils.trade_log_reader import iter_trade_records
        seen: list[str] = []
        for rec in iter_trade_records(self.trade_log_path):
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
        from utils.trade_log_reader import iter_trade_records
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        counts: dict[str, int] = {}
        for rec in iter_trade_records(self.trade_log_path, since=cutoff):
            source = rec.get("source")
            if not source:
                continue
            counts[source] = counts.get(source, 0) + 1
        return [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


# Normalization helpers below.
#
# The four scripts/* helpers return their natural data structures
# (dataclass-keyed dicts, tuples, or partly-typed nested dicts). The
# governance agent's evidence builder and prompt renderer expect a
# bot-agnostic dict-of-lists shape. These helpers translate between them
# so the agent's downstream code can stay shape-stable across future
# bot implementations (Polymarket, Alpaca) — the contract is the
# normalized shape, not the script outputs themselves.

# Reddit-classification floor: reuses the same threshold the
# diagnostic CLI uses (DEFAULT_MIN_INGESTION = 20 in
# scripts/reddit_source_audit.py) and matches the >= 20 floor the
# evidence builder applies in governance/evidence.py.
_REDDIT_MIN_INGESTION_FOR_CLASSIFY = 20


def _normalize_alignment(pairs_dict: dict[Any, Any]) -> dict[str, Any]:
    """Convert {(source, series_ticker): PairStats} into the
    {"pairs": [...], "overall_anchor_rate": float, "overall_n": int}
    shape evidence.py consumes."""
    pairs_list: list[dict[str, Any]] = []
    total_n = 0
    total_anchor = 0
    for stat in pairs_dict.values():
        n = int(getattr(stat, "n", 0) or 0)
        anchor = int(getattr(stat, "anchor", 0) or 0)
        pairs_list.append({
            "source": getattr(stat, "source", ""),
            "series_ticker": getattr(stat, "series_ticker", ""),
            "n": n,
            "anchor": anchor,
            "anchor_rate": (anchor / n) if n else None,
        })
        total_n += n
        total_anchor += anchor
    return {
        "pairs": pairs_list,
        "overall_anchor_rate": (total_anchor / total_n) if total_n else 0.0,
        "overall_n": total_n,
    }


def _normalize_reddit(sub_stats_dict: dict[str, Any]) -> dict[str, Any]:
    """Convert {subreddit_name: SubStats} into the {"subs": [...]} shape."""
    from scripts.reddit_source_audit import classify as _classify_sub
    subs_list: list[dict[str, Any]] = []
    for sub_stat in sub_stats_dict.values():
        ingestion = int(getattr(sub_stat, "ingestion", 0) or 0)
        subs_list.append({
            "source": getattr(sub_stat, "subreddit", ""),
            "ingestion": ingestion,
            "fresh_passes": int(getattr(sub_stat, "early_fresh", 0) or 0),
            "matches": int(getattr(sub_stat, "matches", 0) or 0),
            "classification": _classify_sub(
                sub_stat, set(), _REDDIT_MIN_INGESTION_FOR_CLASSIFY,
            ),
        })
    return {"subs": subs_list}


def _normalize_keywords(kw: dict[str, Any]) -> dict[str, Any]:
    """Project keyword_feedback.summarize() output into the shape the
    evidence builder consumes. The diagnostic dict has many fields the
    agent does not need; we keep only the load-bearing two."""
    return {
        "no_keyword_misses": int(kw.get("no_keyword_misses", 0) or 0),
        "candidate_phrases": [
            {
                "phrase": str(p.get("phrase", "")),
                "count": int(p.get("count", 0) or 0),
                "category": str(p.get("category", "unknown")),
            }
            for p in kw.get("phrases", []) or []
            if isinstance(p, dict)
        ],
    }


def _normalize_freshness(fresh: dict[str, Any]) -> dict[str, Any]:
    """Project freshness_diagnostics.summarize() bucket-keyed output into
    {"sources": {name: {observed_records, stale_rate, interpretation}}}.
    Phase 2 evidence/prompt code does not access this dict directly, but
    the contract test verifies the shape so future Phase 4 self-review
    can rely on it."""
    sources: dict[str, dict[str, Any]] = {}
    for bucket_name, rows in fresh.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_name = row.get("source")
            if not source_name:
                continue
            stale_rate = row.get("stale_rate")
            sources[source_name] = {
                "observed_records": int(row.get("observed_records", 0) or 0),
                "stale_rate": float(stale_rate) if stale_rate is not None else 0.0,
                "interpretation": bucket_name,
            }
    return {"sources": sources}
