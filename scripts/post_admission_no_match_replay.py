"""
Read-only replay of persisted post-admission no-match counterfactual shadows.

The report is offline-only evidence. It reads trade-log records and never calls
network, changes logs, changes runtime state, or feeds matcher policy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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


DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl"
TARGET_EVENT_TYPE = "MATCH_NO_CANDIDATE"
TARGET_CANDIDATE_POOL_STAGE = "post_admission_no_match"
SHADOW_FIELD = "post_admission_counterfactual_shadow"
SHADOW_STATUS_FIELD = "post_admission_counterfactual_shadow_status"
MAX_CANDIDATES = 4
MAX_COUNT = 10_000
MAX_TICKER_CHARS = 128
MAX_TITLE_RAW_CHARS = 512
MAX_TITLE_CHARS = 160
MAX_SHADOW_BYTES = 4 * 1024
SCORE_HISTOGRAM_BUCKETS = (
    "[0.00,0.02)",
    "[0.02,0.05)",
    "[0.05,0.10)",
    "[0.10,1.00]",
)
NEAR_MISS_MINIMUM_RATIO = 0.95
SHADOW_FIELDS = frozenset(
    {
        "schema_version",
        "match_clock_utc",
        "news_headline_token_count",
        "news_match_token_count",
        "candidate_count_total",
        "captured_market_count",
        "omitted_market_count",
        "truncated",
        "candidates",
    }
)
CANDIDATE_FIELDS = frozenset(
    {
        "ticker",
        "market_title",
        "rejection_reason",
        "market_token_count",
        "matched_token_count",
        "pre_weight_score",
        "post_weight_score",
    }
)
REQUIRED_CANDIDATE_FIELDS = frozenset(
    {
        "ticker",
        "rejection_reason",
        "market_token_count",
        "matched_token_count",
    }
)
REJECTION_REASONS = frozenset(
    {
        "no_token_overlap",
        "below_min_post_weight_score",
        "weight_demoted_below_min_score",
        "market_without_match_tokens",
    }
)
NO_TOKEN_REASONS = frozenset({"market_without_match_tokens", "no_token_overlap"})
SCORED_REASONS = frozenset({"below_min_post_weight_score", "weight_demoted_below_min_score"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only counterfactual replay for MATCH_NO_CANDIDATE records from the "
            "post_admission_no_match stage"
        )
    )
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text="Path to a trade-log file or trade-log root (default: logs/trades/live/trades.jsonl)",
    )
    add_since_arg(parser, help_text="Inclusive UTC date filter (YYYY-MM-DD)")
    add_until_arg(parser, help_text="Inclusive UTC date filter (YYYY-MM-DD)")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit a machine-readable report to stdout",
    )
    return parser.parse_args()


def _nonnegative_int(value: Any) -> int | None:
    if type(value) is not int or value < 0 or value > MAX_COUNT:
        return None
    return value


def _score(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        score = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(score) or score < 0 or score > 1:
        return None
    return score


def _canonical_match_clock(value: Any) -> str | None:
    if type(value) is not str or not value or len(value) > 64:
        return None
    if any(not (" " <= char <= "~") for char in value):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical_ticker(value: Any) -> str | None:
    if type(value) is not str or not value or len(value) > MAX_TICKER_CHARS:
        return None
    if any(not ("!" <= char <= "~") for char in value):
        return None
    return value


def _valid_title(value: Any) -> bool:
    if type(value) is not str or len(value) > MAX_TITLE_RAW_CHARS:
        return False
    printable = "".join(char for char in value if " " <= char <= "~")
    canonical = " ".join(printable.split())[:MAX_TITLE_CHARS]
    return bool(canonical) and canonical == value


def _parse_candidate(
    value: Any,
    *,
    news_match_token_count: int,
) -> tuple[dict[str, Any], tuple[str, float, str]] | None:
    if type(value) is not dict:
        return None
    fields = set(value)
    if not REQUIRED_CANDIDATE_FIELDS <= fields or not fields <= CANDIDATE_FIELDS:
        return None

    ticker = _canonical_ticker(value.get("ticker"))
    reason = value.get("rejection_reason")
    market_token_count = _nonnegative_int(value.get("market_token_count"))
    matched_token_count = _nonnegative_int(value.get("matched_token_count"))
    if (
        ticker is None
        or type(reason) is not str
        or reason not in REJECTION_REASONS
        or market_token_count is None
        or matched_token_count is None
    ):
        return None
    if "market_title" in value and not _valid_title(value["market_title"]):
        return None

    has_pre_score = "pre_weight_score" in value
    has_post_score = "post_weight_score" in value
    if has_pre_score != has_post_score:
        return None

    observation: dict[str, Any] = {"rejection_reason": reason}
    post_weight_score = 0.0
    if reason == "market_without_match_tokens":
        if market_token_count != 0 or matched_token_count != 0 or has_pre_score:
            return None
    elif reason == "no_token_overlap":
        if market_token_count == 0 or matched_token_count != 0 or has_pre_score:
            return None
    else:
        pre_weight_score = _score(value.get("pre_weight_score"))
        parsed_post_weight_score = _score(value.get("post_weight_score"))
        if (
            market_token_count == 0
            or matched_token_count == 0
            or matched_token_count > market_token_count
            or matched_token_count > news_match_token_count
            or pre_weight_score is None
            or parsed_post_weight_score is None
            or parsed_post_weight_score > pre_weight_score
        ):
            return None
        if reason == "weight_demoted_below_min_score" and parsed_post_weight_score >= pre_weight_score:
            return None
        post_weight_score = parsed_post_weight_score
        observation["pre_weight_score"] = pre_weight_score
        observation["post_weight_score"] = post_weight_score

    return observation, (reason, -post_weight_score, ticker)


def _contradicts_frozen_threshold(candidate: dict[str, Any], frozen_threshold: float | None) -> bool:
    if frozen_threshold is None or candidate["rejection_reason"] not in SCORED_REASONS:
        return False
    pre_weight_score = candidate["pre_weight_score"]
    post_weight_score = candidate["post_weight_score"]
    if post_weight_score >= frozen_threshold:
        return True
    if candidate["rejection_reason"] == "weight_demoted_below_min_score":
        return pre_weight_score < frozen_threshold
    return pre_weight_score >= frozen_threshold


def _parse_shadow(record: dict[str, Any]) -> dict[str, Any] | None:
    """Validate persisted shape without inventing a missing frozen threshold."""

    if record.get("venue") != "polymarket_us" or record.get("reason") != "no_match":
        return None
    value = record.get(SHADOW_FIELD)
    if type(value) is not dict or set(value) != SHADOW_FIELDS:
        return None

    match_clock = _canonical_match_clock(value.get("match_clock_utc"))
    headline_token_count = _nonnegative_int(value.get("news_headline_token_count"))
    news_match_token_count = _nonnegative_int(value.get("news_match_token_count"))
    candidate_count_total = _nonnegative_int(value.get("candidate_count_total"))
    captured_market_count = _nonnegative_int(value.get("captured_market_count"))
    omitted_market_count = _nonnegative_int(value.get("omitted_market_count"))
    within_horizon_market_count = _nonnegative_int(record.get("within_admission_horizon_market_count"))
    no_token_overlap_count = _nonnegative_int(record.get("post_admission_no_token_overlap_count"))
    below_min_count = _nonnegative_int(record.get("post_admission_below_min_post_weight_score_count"))
    weight_demoted_count = _nonnegative_int(record.get("post_admission_weight_demoted_below_min_score_count"))
    candidates = value.get("candidates")
    truncated = value.get("truncated")
    frozen_threshold = _score(record.get("post_admission_min_match_score"))
    if (
        value.get("schema_version") != 1
        or type(value.get("schema_version")) is not int
        or match_clock != value.get("match_clock_utc")
        or headline_token_count is None
        or news_match_token_count is None
        or candidate_count_total is None
        or candidate_count_total <= 0
        or captured_market_count is None
        or omitted_market_count is None
        or within_horizon_market_count is None
        or within_horizon_market_count <= 0
        or no_token_overlap_count is None
        or below_min_count is None
        or weight_demoted_count is None
        or type(truncated) is not bool
        or type(candidates) is not list
        or len(candidates) > MAX_CANDIDATES
        or captured_market_count != len(candidates)
        or candidate_count_total != captured_market_count + omitted_market_count
        or candidate_count_total != within_horizon_market_count
        or no_token_overlap_count + below_min_count != within_horizon_market_count
        or weight_demoted_count > below_min_count
        or truncated != (omitted_market_count > 0)
    ):
        return None

    observations: list[dict[str, Any]] = []
    sort_keys: list[tuple[str, float, str]] = []
    seen_tickers: set[str] = set()
    captured_no_token_count = 0
    captured_below_min_count = 0
    captured_weight_demoted_count = 0
    for candidate in candidates:
        parsed = _parse_candidate(candidate, news_match_token_count=news_match_token_count)
        if parsed is None:
            return None
        observation, sort_key = parsed
        if _contradicts_frozen_threshold(observation, frozen_threshold):
            return None
        ticker = sort_key[2]
        if ticker in seen_tickers:
            return None
        seen_tickers.add(ticker)
        observations.append(observation)
        sort_keys.append(sort_key)
        reason = observation["rejection_reason"]
        if reason in NO_TOKEN_REASONS:
            captured_no_token_count += 1
        elif reason in SCORED_REASONS:
            captured_below_min_count += 1
            if reason == "weight_demoted_below_min_score":
                captured_weight_demoted_count += 1

    if (
        sort_keys != sorted(sort_keys)
        or captured_no_token_count > no_token_overlap_count
        or captured_below_min_count > below_min_count
        or captured_weight_demoted_count > weight_demoted_count
    ):
        return None
    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, TypeError, ValueError):
        return None
    if len(serialized) > MAX_SHADOW_BYTES:
        return None

    return {
        "candidate_count_total": candidate_count_total,
        "truncated": truncated,
        "candidates": observations,
    }


def _classify_shadow(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    if SHADOW_FIELD not in record:
        return ("malformed", None) if SHADOW_STATUS_FIELD in record else ("absent", None)
    if SHADOW_STATUS_FIELD in record:
        return "malformed", None
    parsed = _parse_shadow(record)
    if parsed is None:
        return "malformed", None
    return "valid", parsed


def _score_histogram_bucket(value: float) -> str:
    if value < 0.02:
        return "[0.00,0.02)"
    if value < 0.05:
        return "[0.02,0.05)"
    if value < 0.10:
        return "[0.05,0.10)"
    return "[0.10,1.00]"


def _new_summary(path: Path) -> dict[str, Any]:
    return {
        "path": path,
        "filters": {},
        "lines_total": 0,
        "lines_malformed": 0,
        "target_records": 0,
        "shadow_counts": {
            "absent": 0,
            "malformed": 0,
            "valid": 0,
            "truncated": 0,
            "untruncated": 0,
        },
        "candidate_rejection_reasons": Counter(),
        "best_rejected_post_weight_score": {
            "available": 0,
            "unavailable": 0,
            "tied_best": 0,
            "histogram": Counter({bucket: 0 for bucket in SCORE_HISTOGRAM_BUCKETS}),
        },
        "near_miss": {
            "minimum_ratio": NEAR_MISS_MINIMUM_RATIO,
            "descriptive_report_threshold_only": True,
            "frozen_threshold_available": 0,
            "frozen_threshold_unavailable": 0,
            "near_miss": 0,
            "not_near_miss": 0,
            "unavailable": 0,
            "at_or_above_frozen_threshold": 0,
        },
        "candidate_composition": {
            "single_candidate_total": 0,
            "complete_same_reason": 0,
            "truncated_excluded_from_full_pool_claims": 0,
        },
    }


def _add_valid_shadow_observation(summary: dict[str, Any], record: dict[str, Any], shadow: dict[str, Any]) -> None:
    shadow_counts = summary["shadow_counts"]
    shadow_counts["valid"] += 1
    if shadow["truncated"]:
        shadow_counts["truncated"] += 1
        summary["candidate_composition"]["truncated_excluded_from_full_pool_claims"] += 1
    else:
        shadow_counts["untruncated"] += 1

    candidates = shadow["candidates"]
    for candidate in candidates:
        summary["candidate_rejection_reasons"][candidate["rejection_reason"]] += 1

    if shadow["candidate_count_total"] == 1:
        summary["candidate_composition"]["single_candidate_total"] += 1
    if not shadow["truncated"] and candidates:
        reasons = {candidate["rejection_reason"] for candidate in candidates}
        if len(reasons) == 1:
            summary["candidate_composition"]["complete_same_reason"] += 1

    scores = [candidate["post_weight_score"] for candidate in candidates if "post_weight_score" in candidate]
    score_stats = summary["best_rejected_post_weight_score"]
    near_miss = summary["near_miss"]
    frozen_threshold = _score(record.get("post_admission_min_match_score"))
    if frozen_threshold is None or frozen_threshold <= 0:
        near_miss["frozen_threshold_unavailable"] += 1
    else:
        near_miss["frozen_threshold_available"] += 1

    if not scores:
        score_stats["unavailable"] += 1
        near_miss["unavailable"] += 1
        return

    best_score = max(scores)
    score_stats["available"] += 1
    score_stats["histogram"][_score_histogram_bucket(best_score)] += 1
    if sum(score == best_score for score in scores) > 1:
        score_stats["tied_best"] += 1
    if frozen_threshold is None or frozen_threshold <= 0:
        near_miss["unavailable"] += 1
    elif best_score >= frozen_threshold:
        near_miss["at_or_above_frozen_threshold"] += 1
    elif best_score / frozen_threshold >= NEAR_MISS_MINIMUM_RATIO:
        near_miss["near_miss"] += 1
    else:
        near_miss["not_near_miss"] += 1


def _plain_summary(summary: dict[str, Any]) -> dict[str, Any]:
    score_stats = summary["best_rejected_post_weight_score"]
    return {
        "path": str(summary["path"]),
        "filters": dict(summary["filters"]),
        "lines_total": summary["lines_total"],
        "lines_malformed": summary["lines_malformed"],
        "target_records": summary["target_records"],
        "shadow_counts": dict(summary["shadow_counts"]),
        "candidate_rejection_reasons": dict(sorted(summary["candidate_rejection_reasons"].items())),
        "best_rejected_post_weight_score": {
            "available": score_stats["available"],
            "unavailable": score_stats["unavailable"],
            "tied_best": score_stats["tied_best"],
            "histogram": {
                bucket: score_stats["histogram"][bucket] for bucket in SCORE_HISTOGRAM_BUCKETS
            },
        },
        "near_miss": dict(summary["near_miss"]),
        "candidate_composition": dict(summary["candidate_composition"]),
    }


def summarize(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool = False,
) -> dict[str, Any]:
    """Read only target records and return sanitized counterfactual evidence."""

    summary = _new_summary(path)
    summary["filters"] = {
        "since": since.isoformat() if since is not None else None,
        "until": until.isoformat() if until is not None else None,
        "exclude_test": exclude_test,
    }
    read_stats = TradeLogReadStats()
    for record in iter_trade_records(
        path,
        since=since,
        until=until,
        event_types={TARGET_EVENT_TYPE},
        stats=read_stats,
    ):
        if record.get("candidate_pool_stage") != TARGET_CANDIDATE_POOL_STAGE:
            continue
        if exclude_test and is_test_record(record):
            continue
        summary["target_records"] += 1
        status, shadow = _classify_shadow(record)
        if status != "valid":
            summary["shadow_counts"][status] += 1
            continue
        _add_valid_shadow_observation(summary, record, shadow)

    summary["lines_total"] = read_stats.lines_total
    summary["lines_malformed"] = read_stats.lines_malformed
    return _plain_summary(summary)


def format_json_summary(summary: dict[str, Any]) -> str:
    payload = {
        "schema_version": 1,
        "counterfactual_evidence_only": True,
        "not_matcher_policy_approval": True,
        "not_runtime_input": True,
        "path": summary["path"],
        "filters": summary["filters"],
        "counts": {
            "lines_total": summary["lines_total"],
            "lines_malformed": summary["lines_malformed"],
            "target_records": summary["target_records"],
            **summary["shadow_counts"],
        },
        "candidate_rejection_reasons": summary["candidate_rejection_reasons"],
        "best_rejected_post_weight_score": summary["best_rejected_post_weight_score"],
        "near_miss": summary["near_miss"],
        "candidate_composition": summary["candidate_composition"],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def format_text_summary(summary: dict[str, Any]) -> str:
    shadow_counts = summary["shadow_counts"]
    score_stats = summary["best_rejected_post_weight_score"]
    near_miss = summary["near_miss"]
    composition = summary["candidate_composition"]
    lines = [
        "Post-admission no-match counterfactual replay",
        "COUNTERFACTUAL EVIDENCE ONLY: read-only persisted observations, not match truth.",
        "This report does not infer a match and is not matcher-policy approval.",
        "No replay output is consumed by runtime admission, matching, routing, or trading.",
        f"Path: {summary['path']}",
        (
            "Filters: "
            f"since={summary['filters']['since'] or 'none'}, "
            f"until={summary['filters']['until'] or 'none'}, "
            f"exclude_test={summary['filters']['exclude_test']}"
        ),
        (
            "Lines: "
            f"{summary['lines_total']} total, {summary['lines_malformed']} malformed; "
            f"{summary['target_records']} target records"
        ),
        (
            "Shadows: "
            f"{shadow_counts['absent']} absent, {shadow_counts['malformed']} malformed, "
            f"{shadow_counts['valid']} valid, {shadow_counts['truncated']} truncated, "
            f"{shadow_counts['untruncated']} untruncated"
        ),
        "Captured candidate rejection reasons:",
    ]
    if summary["candidate_rejection_reasons"]:
        lines.extend(
            f"  {reason}: {count}"
            for reason, count in summary["candidate_rejection_reasons"].items()
        )
    else:
        lines.append("  none")
    lines.append(
        "Best captured rejected post-weight score: "
        f"{score_stats['available']} available, {score_stats['unavailable']} unavailable, "
        f"{score_stats['tied_best']} tied best"
    )
    lines.extend(
        f"  {bucket}: {count}" for bucket, count in score_stats["histogram"].items()
    )
    lines.append(
        "Near-miss against per-record frozen threshold "
        f"(descriptive report ratio {near_miss['minimum_ratio']:.2f}; not runtime policy): "
        f"{near_miss['near_miss']} near, {near_miss['not_near_miss']} not near, "
        f"{near_miss['unavailable']} unavailable, "
        f"{near_miss['at_or_above_frozen_threshold']} at-or-above (not a match inference)"
    )
    lines.append(
        "Frozen threshold: "
        f"{near_miss['frozen_threshold_available']} available, "
        f"{near_miss['frozen_threshold_unavailable']} unavailable"
    )
    lines.append(
        "Candidate composition: "
        f"{composition['single_candidate_total']} single-candidate totals, "
        f"{composition['complete_same_reason']} complete same-reason pools, "
        f"{composition['truncated_excluded_from_full_pool_claims']} truncated pools excluded "
        "from full-pool claims"
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        since = parse_date_start(args.since)
        until = parse_date_end(args.until)
    except ValueError as exc:
        raise SystemExit(f"Invalid date filter: {exc}") from exc
    summary = summarize(Path(args.path), since, until, exclude_test=args.exclude_test)
    output = format_json_summary(summary) if args.json_output else format_text_summary(summary)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
