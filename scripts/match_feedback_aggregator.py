"""PROFIT-MATCH-DYNAMIC commit 3/5 — CLI aggregator for matcher feedback.

Reads MATCH_LLM_REVIEW events from the trade-log archive + live file,
aggregates per-(token × market_prefix) FP-rate counters into the
SQLite table at ``data/match_token_fp_counters.db``, and writes the
runtime downweight map at ``data/matcher_token_weights.json``.

Designed to run as a daily launchd cron OR ad-hoc by operator:

    python -m scripts.match_feedback_aggregator
    python -m scripts.match_feedback_aggregator --window-days 7 --dry-run

Idempotent: rolling window is recomputed from the SQLite table on every
run. Re-ingesting the same events is a no-op for already-counted days
within a single calendar day (counters increment, but the rate stays the
same once a day is complete). Operator-pinned weights are NEVER
overwritten — see `analysis/match_feedback.update_weights_from_stats`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from analysis.match_feedback import (
    aggregate_window,
    ingest_review_events,
    load_weights,
    update_weights_from_stats,
    write_weights,
)


def _iter_review_events(trade_log_root: Path):
    """Yield MATCH_LLM_REVIEW dicts across live + archive."""
    paths = []
    live = trade_log_root / "live" / "trades.jsonl"
    if live.exists():
        paths.append(live)
    archive = trade_log_root / "archive"
    if archive.exists():
        paths.extend(sorted(archive.rglob("*.jsonl")))
    for p in paths:
        try:
            with p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or "MATCH_LLM_REVIEW" not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "MATCH_LLM_REVIEW":
                        yield rec
        except OSError:
            continue


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Aggregate MATCH_LLM_REVIEW events into per-token FP counters + downweights",
    )
    p.add_argument("--trade-log-root", type=Path, default=Path("logs/trades"))
    p.add_argument("--window-days", type=int, default=14)
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print top 20 (token, prefix) FP-rate stats but do not update the weights JSON",
    )
    args = p.parse_args(argv)

    print(f"Reading MATCH_LLM_REVIEW events from {args.trade_log_root}/", file=sys.stderr)
    events = list(_iter_review_events(args.trade_log_root))
    print(f"  found {len(events)} review events", file=sys.stderr)

    if events:
        ingested = ingest_review_events(events)
        print(f"  ingested {ingested} (token, event) increments into the FP counter table",
              file=sys.stderr)

    stats = aggregate_window(window_days=args.window_days)
    print(f"\nTop 20 (token, market_prefix) by FP rate (window={args.window_days}d, "
          f"min total displayed=5):", file=sys.stderr)
    print(f"{'token':<20} {'prefix':<25} {'fp':>5} {'tp':>5} {'fp_rate':>8}",
          file=sys.stderr)
    for s in sorted(stats, key=lambda x: (-x.fp_rate, -x.total))[:20]:
        if s.total < 5:
            continue
        print(f"{s.token:<20} {s.market_prefix:<25} {s.fp_neutral:>5} "
              f"{s.true_positive:>5} {s.fp_rate:>8.4f}", file=sys.stderr)

    if args.dry_run:
        print("\n[--dry-run] not updating weights JSON", file=sys.stderr)
        return 0

    weights = load_weights()
    pinned_before = sum(1 for v in weights.values() if v.get("pinned"))
    updated = update_weights_from_stats(stats, weights=weights)
    write_weights(updated)
    active = sum(1 for v in updated.values() if v.get("weight", 1.0) < 1.0)
    print(f"\nWrote {len(updated)} weight entries "
          f"({active} active downweights, {pinned_before} operator-pinned)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
