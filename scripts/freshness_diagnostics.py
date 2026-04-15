"""
Read-only source freshness / latency diagnostics for structured trade logs.

Uses logs/trades/trades.jsonl to summarize which sources are systematically
arriving too late to be useful. This report is intentionally conservative:

  - Total observed items are counted from source-attributed structured records.
  - Early stale drops come from EARLY_STALE_DROP.
  - Fresh passes come from EARLY_FRESH_PASS.
  - Publish-age statistics are only computed when age_seconds is present.
  - Historical windows may lack EARLY_FRESH_PASS, so some date ranges still have
    age coverage only for stale items.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from statistics import median
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "trades.jsonl"
FRESHNESS_THRESHOLD_SECONDS = 300
MIN_AGE_SAMPLES_FOR_STRONG_RANKING = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize source freshness and feed latency")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_LOG_PATH),
        help="Path to trades.jsonl (default: logs/trades/trades.jsonl)",
    )
    parser.add_argument(
        "--since",
        help="Inclusive start date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--until",
        help="Inclusive end date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Max rows to show in ranked sections (default: 10)",
    )
    parser.add_argument(
        "--min-observations",
        type=int,
        default=5,
        help="Minimum observed records before a source appears in ranked sections (default: 5)",
    )
    parser.add_argument(
        "--exclude-test",
        action="store_true",
        help="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show dead / insufficient-data long-tail rows in addition to the default operational view",
    )
    return parser.parse_args()


def parse_iso_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date_start(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return dt.replace(tzinfo=timezone.utc)


def parse_date_end(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return datetime.combine(dt.date(), time.max, tzinfo=timezone.utc)


def in_window(ts: datetime | None, since: datetime | None, until: datetime | None) -> bool:
    if ts is None:
        return since is None and until is None
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    return True


def is_test_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or record.get("signal_source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


def safe_age_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        if value < 0:
            return None
        return float(value)
    return None


def age_bucket(age_seconds: float) -> str:
    if age_seconds <= 60:
        return "<=60s"
    if age_seconds <= 300:
        return "<=300s"
    if age_seconds <= 900:
        return "301-900s"
    return ">900s"


def percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def default_source_metrics() -> dict[str, Any]:
    return {
        "observed_records": 0,
        "early_stale_drops": 0,
        "fresh_passes": 0,
        "age_samples": [],
        "within_60s": 0,
        "within_300s": 0,
        "over_300s": 0,
        "over_900s": 0,
    }


def interpret_source(row: dict[str, Any]) -> str:
    age_samples = row["age_samples_count"]
    stale_rate = row["stale_rate"]
    freshest = row["freshest_age_seconds"]
    median_age = row["median_age_seconds"]
    observed = row["observed_records"]

    if age_samples < MIN_AGE_SAMPLES_FOR_STRONG_RANKING:
        return "insufficient data"

    if (
        stale_rate is not None
        and stale_rate >= 0.98
        and observed >= 15
        and freshest is not None
        and freshest > 3 * FRESHNESS_THRESHOLD_SECONDS
        and median_age is not None
        and median_age > 10 * FRESHNESS_THRESHOLD_SECONDS
    ):
        return "chronically late"

    if freshest is not None and freshest <= 2 * FRESHNESS_THRESHOLD_SECONDS:
        return "near-threshold"

    if median_age is not None and median_age <= 20 * FRESHNESS_THRESHOLD_SECONDS:
        return "late but salvageable"

    return "chronically late"


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "age_bearing_early_stale_records": 0,
        "sources": {},
    }

    metrics: dict[str, dict[str, Any]] = defaultdict(default_source_metrics)

    if not path.exists():
        stats["sources"] = {}
        return stats

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            stats["lines_total"] += 1
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats["lines_malformed"] += 1
                continue
            if not isinstance(record, dict):
                stats["lines_malformed"] += 1
                continue

            ts = parse_iso_ts(record.get("ts"))
            if not in_window(ts, since, until):
                continue
            if exclude_test and is_test_record(record):
                continue

            stats["records_kept"] += 1

            source = str(record.get("source") or record.get("signal_source") or "").strip()
            if not source:
                continue

            row = metrics[source]
            row["observed_records"] += 1

            event_type = str(record.get("type") or "").strip()
            if event_type == "EARLY_STALE_DROP":
                row["early_stale_drops"] += 1
            elif event_type == "EARLY_FRESH_PASS":
                row["fresh_passes"] += 1
            else:
                continue

            age = safe_age_seconds(record.get("age_seconds"))
            if age is None:
                continue

            stats["age_bearing_early_stale_records"] += 1
            row["age_samples"].append(age)

            bucket = age_bucket(age)
            if bucket == "<=60s":
                row["within_60s"] += 1
                row["within_300s"] += 1
            elif bucket == "<=300s":
                row["within_300s"] += 1
            elif bucket == "301-900s":
                row["over_300s"] += 1
            else:
                row["over_300s"] += 1
                row["over_900s"] += 1

    rows: list[dict[str, Any]] = []
    for source, row in metrics.items():
        ages = sorted(row["age_samples"])
        observed = row["observed_records"]
        early_stale = row["early_stale_drops"]
        row_summary = {
            "source": source,
            "observed_records": observed,
            "early_stale_drops": early_stale,
            "fresh_passes": row["fresh_passes"],
            "stale_rate": (early_stale / observed) if observed else None,
            "age_samples_count": len(ages),
            "median_age_seconds": median(ages) if ages else None,
            "p90_age_seconds": percentile(ages, 0.90),
            "freshest_age_seconds": min(ages) if ages else None,
            "within_60s": row["within_60s"],
            "within_300s": row["within_300s"],
            "over_300s": row["over_300s"],
            "over_900s": row["over_900s"],
        }
        row_summary["interpretation"] = interpret_source(row_summary)
        rows.append(row_summary)

    stats["sources"] = {row["source"]: row for row in rows}
    return stats


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def fmt_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}s"


def format_compact_rows(rows: list[dict[str, Any]], top: int) -> list[str]:
    if not rows:
        return ["  (none)"]

    shown = rows[:top]
    source_width = max(len(row["source"]) for row in shown)
    obs_width = max(len(str(row["observed_records"])) for row in shown)
    fresh_width = max(len(str(row["fresh_passes"])) for row in shown)
    label_width = max(len(str(row["interpretation"])) for row in shown)

    lines = []
    for row in shown:
        lines.append(
            "  "
            f"{row['source']:<{source_width}}  "
            f"obs={row['observed_records']:>{obs_width}}  "
            f"fresh_pass={row['fresh_passes']:>{fresh_width}}  "
            f"stale_rate={fmt_pct(row['stale_rate'])}  "
            f"median_age={fmt_seconds(row['median_age_seconds'])}  "
            f"freshest={fmt_seconds(row['freshest_age_seconds'])}  "
            f"label={row['interpretation']:<{label_width}}"
        )
    return lines


def is_dead_filtered_source(row: dict[str, Any]) -> bool:
    stale_rate = row.get("stale_rate")
    return (
        stale_rate is not None
        and stale_rate >= 0.999
        and row.get("fresh_passes", 0) == 0
        and row.get("within_300s", 0) == 0
    )


def operational_candidate(row: dict[str, Any]) -> bool:
    return (
        row["interpretation"] in {"near-threshold", "late but salvageable"}
        and (row["fresh_passes"] > 0 or row["within_300s"] > 0)
    )


def bucket_sources(
    rows: list[dict[str, Any]],
    *,
    show_all: bool,
) -> dict[str, list[dict[str, Any]]]:
    insufficient = [row for row in rows if row["interpretation"] == "insufficient data"]
    dead = [row for row in rows if is_dead_filtered_source(row) and row["interpretation"] != "insufficient data"]
    active_rows = [
        row for row in rows
        if row["interpretation"] != "insufficient data" and not is_dead_filtered_source(row)
    ]

    used_sources: set[str] = set()

    fast_operational = sorted(
        [row for row in active_rows if operational_candidate(row)],
        key=freshest_rank,
    )
    used_sources.update(row["source"] for row in fast_operational)

    near_threshold = sorted(
        [
            row for row in active_rows
            if row["source"] not in used_sources and borderline_candidate(row)
        ],
        key=borderline_rank,
    )
    used_sources.update(row["source"] for row in near_threshold)

    chronically_late = sorted(
        [
            row for row in active_rows
            if row["source"] not in used_sources and strategy_misaligned_candidate(row)
        ],
        key=strategy_misaligned_rank,
    )

    buckets = {
        "fast_operational": fast_operational,
        "near_threshold": near_threshold,
        "chronically_late": chronically_late,
        "dead": sorted(dead, key=worst_offender_rank),
        "insufficient": sorted(insufficient, key=lambda row: (-row["observed_records"], row["source"].lower())),
    }
    if show_all:
        return buckets
    return {
        "fast_operational": fast_operational,
        "near_threshold": near_threshold,
        "chronically_late": chronically_late,
        "dead": [],
        "insufficient": [],
    }


def worst_offender_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    stale_rate = row["stale_rate"] if row["stale_rate"] is not None else -1.0
    median_age = row["median_age_seconds"] if row["median_age_seconds"] is not None else -1.0
    freshest = row["freshest_age_seconds"] if row["freshest_age_seconds"] is not None else -1.0
    return (
        -stale_rate,
        -row["observed_records"],
        -median_age,
        -freshest,
        row["source"].lower(),
    )


def freshest_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    stale_rate = row["stale_rate"] if row["stale_rate"] is not None else 2.0
    freshest = row["freshest_age_seconds"] if row["freshest_age_seconds"] is not None else float("inf")
    median_age = row["median_age_seconds"] if row["median_age_seconds"] is not None else float("inf")
    p90_age = row["p90_age_seconds"] if row["p90_age_seconds"] is not None else float("inf")
    strong_age_data_penalty = 0 if row["age_samples_count"] >= MIN_AGE_SAMPLES_FOR_STRONG_RANKING else 1
    return (
        strong_age_data_penalty,
        stale_rate,
        median_age,
        p90_age,
        freshest,
        -row["observed_records"],
        -row["age_samples_count"],
        abs(freshest - FRESHNESS_THRESHOLD_SECONDS) if freshest != float("inf") else float("inf"),
        row["source"].lower(),
    )


def borderline_candidate(row: dict[str, Any]) -> bool:
    if row["age_samples_count"] < MIN_AGE_SAMPLES_FOR_STRONG_RANKING:
        return False
    freshest = row["freshest_age_seconds"]
    median_age = row["median_age_seconds"]
    stale_rate = row["stale_rate"]
    if freshest is None or median_age is None or stale_rate is None:
        return False
    return (
        freshest <= 3 * FRESHNESS_THRESHOLD_SECONDS
        and median_age <= 20 * FRESHNESS_THRESHOLD_SECONDS
        and stale_rate < 0.99
    )


def strategy_misaligned_candidate(row: dict[str, Any]) -> bool:
    if row["observed_records"] < 15:
        return False
    if row["age_samples_count"] < MIN_AGE_SAMPLES_FOR_STRONG_RANKING:
        return False
    freshest = row["freshest_age_seconds"]
    median_age = row["median_age_seconds"]
    stale_rate = row["stale_rate"]
    if freshest is None or median_age is None or stale_rate is None:
        return False
    return (
        stale_rate >= 0.98
        and freshest > 3 * FRESHNESS_THRESHOLD_SECONDS
        and median_age > 10 * FRESHNESS_THRESHOLD_SECONDS
        and row["within_300s"] == 0
    )


def borderline_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    freshest = row["freshest_age_seconds"] if row["freshest_age_seconds"] is not None else float("inf")
    median_age = row["median_age_seconds"] if row["median_age_seconds"] is not None else float("inf")
    stale_rate = row["stale_rate"] if row["stale_rate"] is not None else 2.0
    return (
        abs(freshest - FRESHNESS_THRESHOLD_SECONDS),
        median_age,
        stale_rate,
        -row["observed_records"],
        row["source"].lower(),
    )


def strategy_misaligned_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    stale_rate = row["stale_rate"] if row["stale_rate"] is not None else -1.0
    median_age = row["median_age_seconds"] if row["median_age_seconds"] is not None else -1.0
    freshest = row["freshest_age_seconds"] if row["freshest_age_seconds"] is not None else -1.0
    return (
        -stale_rate,
        -row["observed_records"],
        -median_age,
        -freshest,
        row["source"].lower(),
    )


def print_summary(
    stats: dict[str, Any],
    top: int,
    min_observations: int,
    since: datetime | None,
    until: datetime | None,
    show_all: bool = False,
) -> None:
    print("FRESHNESS DIAGNOSTICS")
    print(f"Path: {stats['path']}")
    if since or until:
        since_text = since.date().isoformat() if since else "(beginning)"
        until_text = until.date().isoformat() if until else "(latest)"
        print(f"Date range: {since_text} -> {until_text}")
    print(f"Lines read: {stats['lines_total']}")
    print(f"Malformed lines skipped: {stats['lines_malformed']}")
    print(f"Records included: {stats['records_kept']}")

    if not stats["sources"]:
        print()
        print("No source-attributable freshness records found.")
        return

    print()
    print("Attribution Notes")
    print("  Publish-age metrics are based on age-bearing EARLY_STALE_DROP and EARLY_FRESH_PASS records.")
    print("  Historical windows may still have age coverage only for stale items.")
    print(f"  Age-bearing freshness records observed: {stats['age_bearing_early_stale_records']}")

    eligible_rows = [
        row
        for row in stats["sources"].values()
        if row["observed_records"] >= min_observations
    ]
    buckets = bucket_sources(eligible_rows, show_all=show_all)
    hidden_insufficient = sum(1 for row in eligible_rows if row["interpretation"] == "insufficient data")
    hidden_dead = sum(
        1
        for row in eligible_rows
        if is_dead_filtered_source(row) and row["interpretation"] != "insufficient data"
    )

    print()
    print("Operational Summary")
    print(f"  Eligible sources (min {min_observations} obs) : {len(eligible_rows)}")
    print(f"  Fast / Operational surfaced                    : {len(buckets['fast_operational'])}")
    print(f"  Near Threshold surfaced                        : {len(buckets['near_threshold'])}")
    print(f"  Chronically Late surfaced                      : {len(buckets['chronically_late'])}")
    if show_all:
        print(f"  Dead / Filtered Out surfaced                   : {len(buckets['dead'])}")
        print(f"  Insufficient Data surfaced                     : {len(buckets['insufficient'])}")
    else:
        print(f"  Hidden 100%-stale dead sources                 : {hidden_dead}")
        print(f"  Hidden insufficient-data sources               : {hidden_insufficient}")

    print()
    print(f"Fast / Operational (top {top})")
    print("  Sources with some timely arrivals and the best odds of affecting live decisions.")
    for line in format_compact_rows(buckets["fast_operational"], top):
        print(line)

    print()
    print(f"Near Threshold (top {top})")
    print("  Plausible candidates for future per-source policy review.")
    for line in format_compact_rows(buckets["near_threshold"], top):
        print(line)

    print()
    print(f"Chronically Late (top {top})")
    print("  High-confidence late sources that appear structurally incompatible with a 300s strategy.")
    for line in format_compact_rows(buckets["chronically_late"], top):
        print(line)

    if show_all:
        print()
        print(f"Dead / Filtered Out (top {top})")
        print("  Hidden by default: 100%-stale, zero-fresh-pass sources with no timely evidence.")
        for line in format_compact_rows(buckets["dead"], top):
            print(line)

        print()
        print(f"Insufficient Data (top {top})")
        print("  Hidden by default: rows without enough age-bearing evidence for operational ranking.")
        for line in format_compact_rows(buckets["insufficient"], top):
            print(line)
    else:
        print()
        print("Hidden Long Tail")
        print("  Use --show-all to inspect dead / filtered-out and insufficient-data sources.")


def main() -> int:
    args = parse_args()

    try:
        since = parse_date_start(args.since)
        until = parse_date_end(args.until)
    except ValueError as exc:
        raise SystemExit(f"Invalid date: {exc}") from exc

    if since and until and since > until:
        raise SystemExit("--since must be on or before --until")

    stats = summarize(
        Path(args.path),
        since,
        until,
        exclude_test=args.exclude_test,
    )
    print_summary(
        stats,
        top=max(1, args.top),
        min_observations=max(1, args.min_observations),
        since=since,
        until=until,
        show_all=args.show_all,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
