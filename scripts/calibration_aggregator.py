"""PROFIT-ALIGN-002/-005 (2026-05-25) — calibration + EV-by-archetype aggregator.

Reads CALIBRATION_OBSERVATION events from the trade-log live + archive,
buckets by (market_prefix × magnitude_bucket × side), and emits per-bucket:

  - count_total
  - count_wins (realized_outcome == 1)
  - win_rate
  - mean_predicted_prob (bot's stated probability for its chosen side)
  - brier_score (mean (p̂ - realized)²)
  - mean_realized_pnl_dollars

The Brier score is the calibration headline metric: lower is better.
A perfectly calibrated bot has Brier ≈ p(1-p) for its stated p; a poorly
calibrated bot has Brier > p(1-p). Surface delta to operator.

Designed for periodic invocation (manual or via launchd). Produces two
artifacts:

  - data/calibration_summary.json     — machine-readable per-bucket stats
  - stderr table                       — operator-readable preview

No env mutation. No behavior change. Pure observability.

Example::

    python -m scripts.calibration_aggregator
    python -m scripts.calibration_aggregator --since 2026-05-01

This script complements `scripts/match_feedback_aggregator.py` (matcher
feedback loop, 6h cadence). Volume gating: buckets with <5 observations
are listed but flagged as `insufficient_evidence` in the JSON; they do
NOT block the run.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from utils.output_paths import DERIVED_STATE_DIR, RAW_TRADES_DIR

_DEFAULT_OUTPUT = DERIVED_STATE_DIR / "calibration_summary.json"
_MIN_BUCKET_EVIDENCE = 5
"""Below this count, mark bucket as `insufficient_evidence`. Operator
should not act on Brier-score from buckets below this floor — too noisy."""


def _iter_calibration_events(trade_log_root: Path):
    """Yield CALIBRATION_OBSERVATION dicts across live + archive."""
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
                    if not line or "CALIBRATION_OBSERVATION" not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "CALIBRATION_OBSERVATION":
                        yield rec
        except OSError:
            continue


def aggregate(events) -> dict[str, Any]:
    """Compute per-bucket calibration stats. Pure function — testable."""
    by_bucket: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "wins": 0, "sum_p": 0.0, "sum_sq_err": 0.0, "sum_pnl": 0.0}
    )
    for ev in events:
        prefix = ev.get("market_prefix") or "UNKNOWN"
        mag = ev.get("llm_magnitude") or "unknown"
        side = ev.get("side") or "unknown"
        try:
            p = float(ev.get("estimated_probability", 0.0))
            outcome = int(ev.get("realized_outcome", 0))
            pnl = float(ev.get("pnl_dollars", 0.0))
        except (TypeError, ValueError):
            continue
        b = by_bucket[(prefix, mag, side)]
        b["count"] += 1
        b["wins"] += outcome
        b["sum_p"] += p
        b["sum_sq_err"] += (p - outcome) ** 2
        b["sum_pnl"] += pnl

    buckets = []
    for (prefix, mag, side), b in sorted(by_bucket.items()):
        n = b["count"]
        if n == 0:
            continue
        buckets.append({
            "market_prefix": prefix,
            "llm_magnitude": mag,
            "side": side,
            "count_total": n,
            "count_wins": b["wins"],
            "win_rate": round(b["wins"] / n, 4),
            "mean_predicted_prob": round(b["sum_p"] / n, 4),
            "brier_score": round(b["sum_sq_err"] / n, 4),
            "mean_realized_pnl_dollars": round(b["sum_pnl"] / n, 4),
            "insufficient_evidence": n < _MIN_BUCKET_EVIDENCE,
        })

    total_obs = sum(b["count_total"] for b in buckets)
    total_wins = sum(b["count_wins"] for b in buckets)
    return {
        "total_observations": total_obs,
        "total_wins": total_wins,
        "overall_win_rate": round(total_wins / total_obs, 4) if total_obs else 0.0,
        "min_bucket_evidence_threshold": _MIN_BUCKET_EVIDENCE,
        "buckets": buckets,
    }


def _print_table(summary: dict[str, Any]) -> None:
    print(
        f"\nCalibration summary  total={summary['total_observations']}  "
        f"wins={summary['total_wins']}  win_rate={summary['overall_win_rate']:.2%}",
        file=sys.stderr,
    )
    print(
        f"{'prefix':<25} {'mag':<10} {'side':<5} "
        f"{'n':>4} {'wins':>5} {'win_rate':>9} {'pred_p':>7} {'brier':>7} {'pnl_mean':>10} {'flag':<25}",
        file=sys.stderr,
    )
    for b in summary["buckets"]:
        flag = "insufficient_evidence" if b["insufficient_evidence"] else ""
        print(
            f"{b['market_prefix']:<25} {b['llm_magnitude']:<10} {b['side']:<5} "
            f"{b['count_total']:>4} {b['count_wins']:>5} {b['win_rate']:>9.2%} "
            f"{b['mean_predicted_prob']:>7.4f} {b['brier_score']:>7.4f} "
            f"{b['mean_realized_pnl_dollars']:>10.2f} {flag:<25}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Aggregate CALIBRATION_OBSERVATION events into per-archetype Brier-score buckets",
    )
    p.add_argument("--trade-log-root", type=Path, default=RAW_TRADES_DIR)
    p.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print table to stderr but do not write the JSON output file",
    )
    args = p.parse_args(argv)

    events = list(_iter_calibration_events(args.trade_log_root))
    print(f"Found {len(events)} CALIBRATION_OBSERVATION events", file=sys.stderr)
    summary = aggregate(events)
    _print_table(summary)

    if args.dry_run:
        print("[--dry-run] not writing JSON", file=sys.stderr)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
