"""
ROADMAP P1.5.2 — Reddit source audit.

Purpose
-------
High-volume but unclear signal value: this script measures the ingestion
funnel per subreddit so P1.5.3 (disable confirmed-dead sources) can
act on evidence rather than anecdote.

Constraint
----------
**Diagnostic only; no changes until findings reviewed.** Script is
entirely read-only over the trade log + config. No runtime behavior
modified.

Funnel per subreddit
--------------------
Ingestion volume is measured as ``EARLY_STALE_DROP + EARLY_FRESH_PASS``
(the pair of events emitted by the pre-queue freshness gate). ``SIGNAL``
events fire *later* in the pipeline, only for items with a non-zero
``signal_strength`` (the old keyword-hit path), so they are not a
faithful ingestion proxy for high-freshness-threshold sources like
Reddit where nearly everything dies at the freshness gate.
::

    EARLY_STALE_DROP + EARLY_FRESH_PASS   (total items observed)
      → EARLY_FRESH_PASS                  (survived freshness; enqueued)
        → MATCH_DIAGNOSTIC                (scored above match threshold)
          → SIGNAL_ANALYSIS_DETAIL        (reached LLM / keyword-gate)
            → non-anchored                (|final_probability - market_price| >= 1e-3)
      → ANALYSIS_REJECTED reason=disabled_source   (dropped by config gate)
      → ANALYSIS_REJECTED reason=no_keywords       (dropped by keyword gate)
      → ANALYSIS_REJECTED reason=stale_news        (dropped by analysis-time freshness)

Classification bands
--------------------
- ``config_disabled``: source is listed in ``DISABLED_NEWS_SOURCES``; the
  audit tolerates events that slipped through before the config edit but
  does not penalize them.
- ``never_polled``: zero ingestion events (no stale or fresh drops).
  Either the subreddit wasn't polled during the window or its polls all
  failed upstream.
- ``all_stale``: ingestion ≥ ``--min-ingestion`` but ``early_fresh`` = 0 —
  every polled post was too old to survive the freshness gate.
- ``no_matches``: had fresh passes but zero MATCH_DIAGNOSTIC — nothing in
  the content overlaps any market.
- ``match_dead``: has matches but zero analysis rows (match-score floor or
  other pre-analysis gate eats everything).
- ``anchored_only``: analysis rows exist but 100% anchored (no signal value
  under the current LLM prompt).
- ``signaling``: at least one non-anchored analysis row (keeper territory).
- ``insufficient``: some events but below ``--min-ingestion`` total.

Output
------
- Per-subreddit funnel table, ordered by SIGNAL volume descending.
- Classification band counts.
- Recommendations: subs in ``never_ingested`` / ``all_stale`` /
  ``no_matches`` are candidates for ``DISABLED_NEWS_SOURCES`` addition.
- Cross-check against ``REDDIT_SUBREDDITS`` (active poll list) and
  ``DISABLED_NEWS_SOURCES`` (deny list): flag subs in the active list
  that should probably move to the deny list, and subs in the deny list
  that are no longer polled at all (cleanup candidates).

Usage
-----
::

    python scripts/reddit_source_audit.py --since 2026-04-20 --exclude-test
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import DISABLED_NEWS_SOURCES, REDDIT_SUBREDDITS
from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    add_path_arg,
    add_since_arg,
    add_until_arg,
    is_test_record_source_only as is_test_record,
    parse_date_end,
    parse_date_start,
)
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records

DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"
DEFAULT_MIN_INGESTION = 20
EST_EQ_MKT_TOL = 1e-3


@dataclass
class SubStats:
    subreddit: str  # canonicalized to the r/name form
    signals: int = 0  # post-keyword-match SIGNAL events (rare for Reddit)
    early_stale: int = 0
    early_fresh: int = 0
    rej_disabled: int = 0
    rej_no_keywords: int = 0
    rej_stale_news: int = 0
    rej_other: int = 0
    matches: int = 0
    analysis_rows: int = 0
    analysis_anchored: int = 0
    # track unique (ticker, headline) keys so we can compute
    # match-to-analysis conversion without double counting across
    # multiple matches for the same item
    analyzed_items: set[tuple[str, str]] = field(default_factory=set)

    @property
    def ingestion(self) -> int:
        """Total items observed at the pre-queue freshness gate."""
        return self.early_stale + self.early_fresh

    @property
    def early_stale_rate(self) -> float:
        return self.early_stale / self.ingestion if self.ingestion else 0.0

    @property
    def match_rate(self) -> float:
        return self.matches / self.early_fresh if self.early_fresh else 0.0

    @property
    def analysis_rate(self) -> float:
        # "items that reached analysis" / "items that had a match"
        return len(self.analyzed_items) / self.matches if self.matches else 0.0

    @property
    def anchor_rate(self) -> float:
        return self.analysis_anchored / self.analysis_rows if self.analysis_rows else 0.0


def _normalize_subreddit(source: str) -> str | None:
    """Return the ``r/<name>`` form for Reddit sources, else None."""
    if not source:
        return None
    s = source.strip()
    if not s.lower().startswith("r/"):
        return None
    # preserve case as logged (r/worldnews vs r/WarCollege, etc.)
    return s


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P1.5.2 — audit Reddit source freshness and match-to-analysis conversion",
    )
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text="Path to trade-log file or root (default: logs/trades/)",
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(parser, help_text="Inclusive end date in YYYY-MM-DD")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    parser.add_argument(
        "--min-ingestion", type=int, default=DEFAULT_MIN_INGESTION,
        help=(
            "Minimum ingestion events (EARLY_STALE_DROP + EARLY_FRESH_PASS) "
            f"to classify a sub as anything other than insufficient "
            f"(default: {DEFAULT_MIN_INGESTION})"
        ),
    )
    parser.add_argument(
        "--top", type=int, default=40,
        help="Max rows in the per-subreddit table (default: 40)",
    )
    parser.add_argument(
        "--only-active-or-polled", action="store_true",
        help=(
            "Skip subs that are neither in REDDIT_SUBREDDITS (active list) "
            "nor appear in the trade log. Useful to suppress the long tail "
            "of auto-discovered candidates that never produced events."
        ),
    )
    return parser.parse_args()


def collect(
    log_path: Path,
    since,
    until,
    exclude_test: bool,
) -> tuple[dict[str, SubStats], TradeLogReadStats]:
    stats: dict[str, SubStats] = {}
    read_stats = TradeLogReadStats()

    def _sub(record: dict[str, Any]) -> SubStats | None:
        raw = str(record.get("source") or "")
        norm = _normalize_subreddit(raw)
        if norm is None:
            return None
        s = stats.get(norm)
        if s is None:
            s = SubStats(subreddit=norm)
            stats[norm] = s
        return s

    for record in iter_trade_records(log_path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue
        event_type = str(record.get("type") or record.get("event") or "")

        if event_type == "SIGNAL":
            s = _sub(record)
            if s is not None:
                s.signals += 1
        elif event_type == "EARLY_STALE_DROP":
            s = _sub(record)
            if s is not None:
                s.early_stale += 1
        elif event_type == "EARLY_FRESH_PASS":
            s = _sub(record)
            if s is not None:
                s.early_fresh += 1
        elif event_type == "ANALYSIS_REJECTED":
            s = _sub(record)
            if s is None:
                continue
            reason = str(record.get("reason") or "")
            if reason == "disabled_source":
                s.rej_disabled += 1
            elif reason == "no_keywords":
                s.rej_no_keywords += 1
            elif reason == "stale_news":
                s.rej_stale_news += 1
            else:
                s.rej_other += 1
        elif event_type == "MATCH_DIAGNOSTIC":
            s = _sub(record)
            if s is not None:
                s.matches += 1
        elif event_type == "SIGNAL_ANALYSIS_DETAIL":
            if record.get("is_startup_probe") or record.get("is_synthetic_probe"):
                continue
            s = _sub(record)
            if s is None:
                continue
            s.analysis_rows += 1
            ticker = str(record.get("ticker") or "")
            headline = str(record.get("headline") or "")
            s.analyzed_items.add((ticker, headline))
            fp = record.get("final_probability")
            mp = record.get("market_price")
            if isinstance(fp, (int, float)) and isinstance(mp, (int, float)):
                if abs(float(fp) - float(mp)) < EST_EQ_MKT_TOL:
                    s.analysis_anchored += 1

    return stats, read_stats


def classify(s: SubStats, disabled: set[str], min_ingestion: int) -> str:
    if s.subreddit in disabled:
        return "config_disabled"
    if s.ingestion == 0:
        return "never_polled"
    if s.ingestion < min_ingestion:
        return "insufficient"
    if s.early_fresh == 0:
        return "all_stale"
    if s.matches == 0:
        return "no_matches"
    if s.analysis_rows == 0:
        return "match_dead"
    if s.anchor_rate >= 0.99:
        return "anchored_only"
    return "signaling"


_BAND_DESCRIPTIONS = {
    "config_disabled": "source listed in DISABLED_NEWS_SOURCES",
    "never_polled": "zero ingestion events in window (not polling, or dead upstream)",
    "insufficient": "below --min-ingestion threshold for classification",
    "all_stale": "had polls but 0 EARLY_FRESH_PASS — every post was too old",
    "no_matches": "survived freshness but zero MATCH_DIAGNOSTIC events",
    "match_dead": "has matches but zero analysis rows",
    "anchored_only": "analysis rows exist but 100% anchored (est == market_price)",
    "signaling": "at least one non-anchored analysis row",
}


def _fmt_row(s: SubStats, band: str) -> str:
    return (
        f"  {s.subreddit:<28} {band:<16} "
        f"ingest={s.ingestion:>5}  "
        f"stale={s.early_stale_rate * 100:>5.1f}%  "
        f"fresh={s.early_fresh:>4}  "
        f"match={s.matches:>4}  "
        f"rej(nk/sn)={s.rej_no_keywords}/{s.rej_stale_news}  "
        f"analysis={s.analysis_rows:>3}  "
        f"anchor={s.anchor_rate * 100 if s.analysis_rows else 0:>5.1f}%"
    )


def render(
    stats: dict[str, SubStats],
    *,
    min_ingestion: int,
    top: int,
    only_active_or_polled: bool,
) -> None:
    disabled = {d for d in DISABLED_NEWS_SOURCES if d.lower().startswith("r/")}
    active_configured = {f"r/{s}" for s in REDDIT_SUBREDDITS}

    classified: list[tuple[SubStats, str]] = [
        (s, classify(s, disabled, min_ingestion))
        for s in stats.values()
    ]

    # Add stubs for subs in the active config that weren't seen in the
    # trade log at all -- they classify as never_polled.
    seen_subs = {s.subreddit for s in stats.values()}
    for sub in sorted(active_configured - seen_subs):
        stub = SubStats(subreddit=sub)
        classified.append((stub, classify(stub, disabled, min_ingestion)))

    if only_active_or_polled:
        classified = [
            (s, band) for s, band in classified
            if s.subreddit in active_configured or s.ingestion > 0
        ]

    classified.sort(key=lambda pair: (-pair[0].ingestion, pair[0].subreddit.lower()))

    print("=" * 88)
    print("P1.5.2 — Reddit Source Audit")
    print("=" * 88)
    print(
        f"Subs observed: {len(stats)}   "
        f"Configured in REDDIT_SUBREDDITS: {len(active_configured)}   "
        f"Disabled (r/*): {len(disabled)}"
    )
    print(f"min_ingestion={min_ingestion} (classification floor)")

    # Band summary
    band_counts: dict[str, int] = defaultdict(int)
    for _, band in classified:
        band_counts[band] += 1
    print("\nClassification bands:")
    for band in (
        "signaling", "anchored_only", "match_dead", "no_matches",
        "all_stale", "insufficient", "never_polled", "config_disabled",
    ):
        if band in band_counts:
            print(f"  {band:<18} : {band_counts[band]:>3}   ({_BAND_DESCRIPTIONS[band]})")

    # Per-sub table
    print("\nPer-subreddit funnel (top {} by SIGNAL volume):".format(min(top, len(classified))))
    for s, band in classified[:top]:
        print(_fmt_row(s, band))

    # Cross-checks
    flagged_active_for_disable = sorted(
        s.subreddit for s, band in classified
        if band in {"all_stale", "no_matches", "anchored_only", "match_dead"}
           and s.subreddit in active_configured
    )
    if flagged_active_for_disable:
        print("\nCandidates for DISABLED_NEWS_SOURCES (active in config, low signal):")
        for sub in flagged_active_for_disable:
            print(f"  - {sub}")
    else:
        print("\nCandidates for DISABLED_NEWS_SOURCES: (none meet criteria in this window)")

    stale_disabled_entries = sorted(
        d for d in disabled
        if d not in seen_subs and d not in active_configured
    )
    if stale_disabled_entries:
        print("\nDISABLED_NEWS_SOURCES entries for r/* that have neither been seen")
        print("in the trade log nor appear in REDDIT_SUBREDDITS (cleanup candidates):")
        for sub in stale_disabled_entries:
            print(f"  - {sub}")

    signaling = [(s, band) for s, band in classified if band == "signaling"]
    if signaling:
        print("\nKeeper candidates (anchor_rate < 99% with real analysis volume):")
        for s, _ in signaling:
            print(_fmt_row(s, "signaling"))


def main() -> int:
    args = parse_args()
    log_path = Path(args.path)
    since = parse_date_start(getattr(args, "since", None))
    until = parse_date_end(getattr(args, "until", None))

    stats, read_stats = collect(
        log_path, since, until, args.exclude_test,
    )
    render(
        stats,
        min_ingestion=args.min_ingestion,
        top=args.top,
        only_active_or_polled=args.only_active_or_polled,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
