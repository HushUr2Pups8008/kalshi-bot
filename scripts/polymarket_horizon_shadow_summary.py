"""Summarize the non-routing Polymarket 0-14d versus 15-30d shadow study."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from typing import Any

from scripts.runtime_paper_cohort_scope import (
    record_has_runtime_paper_cohort_lineage,
    validate_runtime_paper_cohort_lineage,
)


_EVENT_TYPE = "POLYMARKET_HORIZON_SHADOW"
_VENUE = "polymarket_us"
_SHADOW_ANALYSIS_STATUS = "not_evaluated_shadow_only"
_EXPECTED_PRODUCTION_HORIZON_DAYS = 14.0
_EXPECTED_SHADOW_HORIZON_END_DAYS = 30.0
_REVIEW_MIN_VALID_SNAPSHOTS = 50
_REVIEW_MAX_COLLECTION_AGE = timedelta(days=7)


def _nonnegative_int(value: Any) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _finite_nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        return None
    return normalized


def _parse_timestamp(value: Any) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validated_horizon_shadow(record: Any) -> dict[str, Any] | None:
    if type(record) is not dict:
        return None
    if record.get("type") != _EVENT_TYPE or record.get("venue") != _VENUE:
        return None
    if record.get("shadow_analysis_status") != _SHADOW_ANALYSIS_STATUS:
        return None
    timestamp = _parse_timestamp(record.get("ts"))
    production_horizon_days = _finite_nonnegative_float(
        record.get("production_horizon_days")
    )
    shadow_horizon_start_days = _finite_nonnegative_float(
        record.get("shadow_horizon_start_days")
    )
    shadow_horizon_end_days = _finite_nonnegative_float(
        record.get("shadow_horizon_end_days")
    )
    if (
        timestamp is None
        or production_horizon_days is None
        or shadow_horizon_start_days is None
        or shadow_horizon_end_days is None
        or production_horizon_days <= 0.0
        or shadow_horizon_start_days != production_horizon_days
        or shadow_horizon_end_days <= production_horizon_days
        or shadow_horizon_end_days > 30.0
    ):
        return None

    counts: dict[str, int] = {}
    for field in (
        "production_candidate_count",
        "shadow_candidate_count",
        "production_qualifying_match_count",
        "shadow_qualifying_match_count",
        "production_no_token_overlap_count",
        "production_below_min_post_weight_score_count",
        "production_weight_demoted_below_min_score_count",
        "shadow_no_token_overlap_count",
        "shadow_below_min_post_weight_score_count",
        "shadow_weight_demoted_below_min_score_count",
    ):
        value = _nonnegative_int(record.get(field))
        if value is None:
            return None
        counts[field] = value

    for prefix in ("production", "shadow"):
        candidate_count = counts[f"{prefix}_candidate_count"]
        qualifying_count = counts[f"{prefix}_qualifying_match_count"]
        no_overlap_count = counts[f"{prefix}_no_token_overlap_count"]
        below_min_count = counts[f"{prefix}_below_min_post_weight_score_count"]
        weight_demoted_count = counts[
            f"{prefix}_weight_demoted_below_min_score_count"
        ]
        min_score = _finite_nonnegative_float(record.get(f"{prefix}_min_match_score"))
        if (
            min_score is None
            or qualifying_count + no_overlap_count + below_min_count != candidate_count
            or weight_demoted_count > below_min_count
        ):
            return None

    return {
        "timestamp": timestamp,
        "production_horizon_days": production_horizon_days,
        "shadow_horizon_end_days": shadow_horizon_end_days,
        **counts,
    }


def summarize_records(
    records: Iterable[dict[str, Any]],
    *,
    cohort_id: str,
    cohort_kind: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a fail-closed cohort-scoped review status for the shadow study."""
    cohort_id, cohort_kind = validate_runtime_paper_cohort_lineage(
        cohort_id,
        cohort_kind,
    )
    review_now = now or datetime.now(timezone.utc)
    if review_now.tzinfo is None or review_now.utcoffset() is None:
        raise ValueError("review clock must be timezone-aware")
    review_now = review_now.astimezone(timezone.utc)

    scoped_records = 0
    excluded_records = 0
    invalid_records = 0
    out_of_study_horizon_records = 0
    valid_rows: list[dict[str, Any]] = []
    for record in records:
        if type(record) is not dict or record.get("type") != _EVENT_TYPE:
            continue
        if not record_has_runtime_paper_cohort_lineage(
            record,
            cohort_id,
            cohort_kind,
        ):
            excluded_records += 1
            continue
        scoped_records += 1
        normalized = _validated_horizon_shadow(record)
        if normalized is None:
            invalid_records += 1
            continue
        if (
            normalized["production_horizon_days"]
            != _EXPECTED_PRODUCTION_HORIZON_DAYS
            or normalized["shadow_horizon_end_days"]
            != _EXPECTED_SHADOW_HORIZON_END_DAYS
        ):
            out_of_study_horizon_records += 1
            continue
        valid_rows.append(normalized)

    valid_rows.sort(key=lambda row: row["timestamp"])
    totals = {
        "production_candidate_count": sum(
            row["production_candidate_count"] for row in valid_rows
        ),
        "shadow_candidate_count": sum(
            row["shadow_candidate_count"] for row in valid_rows
        ),
        "production_qualifying_match_count": sum(
            row["production_qualifying_match_count"] for row in valid_rows
        ),
        "shadow_qualifying_match_count": sum(
            row["shadow_qualifying_match_count"] for row in valid_rows
        ),
    }
    first_timestamp = valid_rows[0]["timestamp"] if valid_rows else None
    last_timestamp = valid_rows[-1]["timestamp"] if valid_rows else None
    if len(valid_rows) >= _REVIEW_MIN_VALID_SNAPSHOTS:
        review_status = "manual_review_required"
        review_trigger = "50_valid_snapshots_reached"
    elif (
        first_timestamp is not None
        and review_now - first_timestamp >= _REVIEW_MAX_COLLECTION_AGE
    ):
        review_status = "manual_review_required"
        review_trigger = "7d_elapsed_before_50_valid_snapshots"
    else:
        review_status = "collecting"
        remaining = _REVIEW_MIN_VALID_SNAPSHOTS - len(valid_rows)
        review_trigger = f"need_{remaining}_more_valid_snapshots_or_7d_elapsed"
    return {
        "scoped_records": scoped_records,
        "excluded_records": excluded_records,
        "invalid_records": invalid_records,
        "out_of_study_horizon_records": out_of_study_horizon_records,
        "valid_snapshots": len(valid_rows),
        "first_snapshot_utc": first_timestamp.isoformat() if first_timestamp else None,
        "last_snapshot_utc": last_timestamp.isoformat() if last_timestamp else None,
        **totals,
        "review_status": review_status,
        "review_trigger": review_trigger,
        "automatic_promotion": False,
    }


def _iter_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if type(record) is dict:
                yield record


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize the Polymarket 0-14d versus 15-30d shadow study."
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=Path("logs/trades/live/trades.jsonl"),
    )
    parser.add_argument("--runtime-paper-cohort-id", required=True)
    parser.add_argument("--runtime-paper-cohort-kind", required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = summarize_records(
        _iter_records(args.trades),
        cohort_id=args.runtime_paper_cohort_id,
        cohort_kind=args.runtime_paper_cohort_kind,
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
        return
    print("Polymarket horizon shadow (0-14d production vs 15-30d shadow)")
    print(
        "Snapshots: "
        f"valid={result['valid_snapshots']} scoped={result['scoped_records']} "
        f"excluded={result['excluded_records']} invalid={result['invalid_records']} "
        f"out_of_study_horizon={result['out_of_study_horizon_records']}"
    )
    print(
        "Candidates: "
        f"production={result['production_candidate_count']} "
        f"shadow={result['shadow_candidate_count']}"
    )
    print(
        "Qualified matches: "
        f"production={result['production_qualifying_match_count']} "
        f"shadow={result['shadow_qualifying_match_count']}"
    )
    print(f"Review: {result['review_status']} ({result['review_trigger']})")
    print("Automatic promotion: disabled")


if __name__ == "__main__":
    main()
