"""
Read-only before/after audit for recent pipeline-quality changes.

Compares a current window against the immediately preceding equal-length window
using existing reporting summaries as the source of truth.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import decision_funnel_summary
from scripts import match_quality_diagnostics
from scripts import signal_edge_diagnostics
from utils.reporting_helpers import ProgressTracker, stage_timer, _eprint


DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"
DEFAULT_HOURS = 24


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare recent pipeline quality against the previous equal-length window")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_LOG_PATH),
        help="Path to trade-log file or root (default: logs/trades/; legacy logs/trades/trades.jsonl still supported)",
    )
    parser.add_argument("--since", help="Inclusive start date in YYYY-MM-DD")
    parser.add_argument("--until", help="Inclusive end date in YYYY-MM-DD")
    parser.add_argument(
        "--hours",
        type=int,
        default=DEFAULT_HOURS,
        help=f"If --since/--until are omitted, compare the last N hours vs the previous N hours (default: {DEFAULT_HOURS})",
    )
    parser.add_argument(
        "--exclude-test",
        action="store_true",
        help="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print record-scan progress to stderr every 10k records",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Print per-stage elapsed time to stderr",
    )
    return parser.parse_args()


def parse_date_start(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return dt.replace(tzinfo=timezone.utc)


def parse_date_end(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return datetime.combine(dt.date(), dt_time.max, tzinfo=timezone.utc)


def resolve_windows(
    *,
    since: datetime | None,
    until: datetime | None,
    hours: int,
    now: datetime | None = None,
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    if since and until:
        if since > until:
            raise ValueError("--since must be on or before --until")
        duration = until - since
        previous_until = since - timedelta(microseconds=1)
        previous_since = previous_until - duration
        return (since, until), (previous_since, previous_until)

    if hours <= 0:
        raise ValueError("--hours must be positive")
    ref = now or datetime.now(timezone.utc)
    current_until = ref
    current_since = ref - timedelta(hours=hours)
    previous_until = current_since - timedelta(microseconds=1)
    previous_since = previous_until - timedelta(hours=hours)
    return (current_since, current_until), (previous_since, previous_until)


def safe_pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def collect_window_metrics(
    *,
    path: Path,
    since: datetime,
    until: datetime,
    exclude_test: bool,
    show_progress: bool = False,
    show_profile: bool = False,
) -> dict[str, Any]:
    with stage_timer("match quality diagnostics", enabled=show_profile):
        match_stats = match_quality_diagnostics.summarize(
            path, since, until, exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("decision funnel summary", enabled=show_profile):
        funnel_stats = decision_funnel_summary.summarize(
            path, since, until, exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("signal edge diagnostics", enabled=show_profile):
        edge_stats = signal_edge_diagnostics.summarize(
            path, since, until, exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )

    detail_rows = edge_stats.get("audit_rows", [])
    llm_rows = sum(1 for row in detail_rows if row.get("method") == "llm")
    keyword_rows = sum(1 for row in detail_rows if row.get("method") == "keyword")
    keyword_gate_rows = sum(1 for row in detail_rows if row.get("method") == "keyword_gate")
    llm_attempted = sum(1 for row in detail_rows if row.get("llm_attempted") is True)
    llm_result_used = sum(1 for row in detail_rows if row.get("llm_result_used") is True)
    llm_status_counts = Counter(
        row.get("llm_result_status")
        for row in detail_rows
        if row.get("llm_result_status")
    )

    opportunities = edge_stats.get("counts", {}).get("OPPORTUNITY", 0)
    zero_edge = edge_stats.get("skip_breakdown", {}).get("zero_edge", 0)
    below_threshold = edge_stats.get("skip_breakdown", {}).get("below_threshold", 0)
    derived_non_zero = max(0, opportunities - zero_edge - below_threshold)

    return {
        "window": (since, until),
        "matching": {
            "matched_candidates": match_stats.get("match_records", 0),
            "low_quality_flagged": match_stats.get("low_quality_matches", 0),
            "low_quality_pct": safe_pct(
                match_stats.get("low_quality_matches", 0),
                match_stats.get("match_records", 0),
            ),
            "suppression_candidates": funnel_stats.get("event_counts", {}).get("MATCH_SUPPRESSION_CANDIDATE", 0),
            "suppressed": funnel_stats.get("event_counts", {}).get("MATCH_SUPPRESSED", 0),
        },
        "analysis": {
            "signal_analysis_detail": edge_stats.get("counts", {}).get("SIGNAL_ANALYSIS_DETAIL", 0),
            "method_llm": llm_rows,
            "method_keyword": keyword_rows,
            "method_keyword_gate": keyword_gate_rows,
            "keyword_gate_exit_pct": safe_pct(
                keyword_gate_rows,
                edge_stats.get("counts", {}).get("SIGNAL_ANALYSIS_DETAIL", 0),
            ),
            "llm_attempted": llm_attempted,
            "llm_result_used": llm_result_used,
            "llm_fallback": max(0, llm_attempted - llm_result_used),
            "llm_status_counts": llm_status_counts,
        },
        "edge": {
            "opportunities": opportunities,
            "below_min_edge": below_threshold,
            "zero_edge": zero_edge,
            "derived_non_zero_above_threshold": derived_non_zero,
        },
        "execution": {
            "executed": edge_stats.get("counts", {}).get("EXECUTED", 0),
            "skipped_total": edge_stats.get("counts", {}).get("SKIPPED", 0),
            "skipped_duplicate": edge_stats.get("skip_breakdown", {}).get("duplicate", 0),
            "skipped_below_threshold": below_threshold,
            "skipped_zero_edge": zero_edge,
            "skipped_other": edge_stats.get("skip_breakdown", {}).get("other", 0),
        },
    }


def fmt_count(value: Any) -> str:
    return str(value) if isinstance(value, int) else "n/a"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def fmt_delta(current: int | float | None, previous: int | float | None, *, is_pct: bool = False) -> str:
    if current is None or previous is None:
        return "n/a"
    diff = current - previous
    if is_pct:
        return f"{diff * 100:+.1f}pp"
    return f"{diff:+.0f}"


def fmt_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def metric_line(
    label: str,
    *,
    current: int | float | None,
    previous: int | float | None,
    is_pct: bool = False,
) -> str:
    current_text = fmt_pct(current) if is_pct else fmt_count(current)
    previous_text = fmt_pct(previous) if is_pct else fmt_count(previous)
    delta_text = fmt_delta(current, previous, is_pct=is_pct)
    return f"  {label:<32} current={current_text:<8} previous={previous_text:<8} delta={delta_text}"


def render_report(current_stats: dict[str, Any], previous_stats: dict[str, Any]) -> list[str]:
    current_since, current_until = current_stats["window"]
    previous_since, previous_until = previous_stats["window"]

    lines: list[str] = []
    lines.append("PIPELINE IMPACT AUDIT")
    lines.append(f"Current window : {fmt_ts(current_since)} -> {fmt_ts(current_until)}")
    lines.append(f"Previous window: {fmt_ts(previous_since)} -> {fmt_ts(previous_until)}")
    lines.append("")

    lines.append("1. Matching Quality")
    lines.append(metric_line(
        "Matched candidates",
        current=current_stats["matching"]["matched_candidates"],
        previous=previous_stats["matching"]["matched_candidates"],
    ))
    lines.append(metric_line(
        "Low-quality flagged",
        current=current_stats["matching"]["low_quality_flagged"],
        previous=previous_stats["matching"]["low_quality_flagged"],
    ))
    lines.append(metric_line(
        "Low-quality percentage",
        current=current_stats["matching"]["low_quality_pct"],
        previous=previous_stats["matching"]["low_quality_pct"],
        is_pct=True,
    ))
    lines.append(metric_line(
        "Suppression candidates",
        current=current_stats["matching"]["suppression_candidates"],
        previous=previous_stats["matching"]["suppression_candidates"],
    ))
    lines.append(metric_line(
        "Suppressed count",
        current=current_stats["matching"]["suppressed"],
        previous=previous_stats["matching"]["suppressed"],
    ))
    lines.append("")

    lines.append("2. Analysis Quality")
    lines.append(metric_line(
        "SIGNAL_ANALYSIS_DETAIL",
        current=current_stats["analysis"]["signal_analysis_detail"],
        previous=previous_stats["analysis"]["signal_analysis_detail"],
    ))
    lines.append(metric_line(
        "method=llm",
        current=current_stats["analysis"]["method_llm"],
        previous=previous_stats["analysis"]["method_llm"],
    ))
    lines.append(metric_line(
        "method=keyword",
        current=current_stats["analysis"]["method_keyword"],
        previous=previous_stats["analysis"]["method_keyword"],
    ))
    lines.append(metric_line(
        "method=keyword_gate",
        current=current_stats["analysis"]["method_keyword_gate"],
        previous=previous_stats["analysis"]["method_keyword_gate"],
    ))
    lines.append(metric_line(
        "Keyword-gate exit fraction",
        current=current_stats["analysis"]["keyword_gate_exit_pct"],
        previous=previous_stats["analysis"]["keyword_gate_exit_pct"],
        is_pct=True,
    ))
    lines.append(metric_line(
        "LLM attempted",
        current=current_stats["analysis"]["llm_attempted"],
        previous=previous_stats["analysis"]["llm_attempted"],
    ))
    lines.append(metric_line(
        "LLM result used",
        current=current_stats["analysis"]["llm_result_used"],
        previous=previous_stats["analysis"]["llm_result_used"],
    ))
    lines.append(metric_line(
        "LLM fallback",
        current=current_stats["analysis"]["llm_fallback"],
        previous=previous_stats["analysis"]["llm_fallback"],
    ))
    current_status = current_stats["analysis"]["llm_status_counts"].most_common(3)
    previous_status = previous_stats["analysis"]["llm_status_counts"].most_common(3)
    lines.append(f"  Current LLM statuses            : {', '.join(f'{k}={v}' for k, v in current_status) or 'n/a'}")
    lines.append(f"  Previous LLM statuses           : {', '.join(f'{k}={v}' for k, v in previous_status) or 'n/a'}")
    lines.append("")

    lines.append("3. Edge Formation")
    lines.append(metric_line(
        "OPPORTUNITY count",
        current=current_stats["edge"]["opportunities"],
        previous=previous_stats["edge"]["opportunities"],
    ))
    lines.append(metric_line(
        "Below min edge",
        current=current_stats["edge"]["below_min_edge"],
        previous=previous_stats["edge"]["below_min_edge"],
    ))
    lines.append(metric_line(
        "Zero edge",
        current=current_stats["edge"]["zero_edge"],
        previous=previous_stats["edge"]["zero_edge"],
    ))
    lines.append(metric_line(
        "Derived non-zero above-thr",
        current=current_stats["edge"]["derived_non_zero_above_threshold"],
        previous=previous_stats["edge"]["derived_non_zero_above_threshold"],
    ))
    lines.append("")

    lines.append("4. Execution Quality")
    lines.append(metric_line(
        "Executed count",
        current=current_stats["execution"]["executed"],
        previous=previous_stats["execution"]["executed"],
    ))
    lines.append(metric_line(
        "Skipped total",
        current=current_stats["execution"]["skipped_total"],
        previous=previous_stats["execution"]["skipped_total"],
    ))
    lines.append(metric_line(
        "Skipped duplicate",
        current=current_stats["execution"]["skipped_duplicate"],
        previous=previous_stats["execution"]["skipped_duplicate"],
    ))
    lines.append(metric_line(
        "Skipped below threshold",
        current=current_stats["execution"]["skipped_below_threshold"],
        previous=previous_stats["execution"]["skipped_below_threshold"],
    ))
    lines.append(metric_line(
        "Skipped zero edge",
        current=current_stats["execution"]["skipped_zero_edge"],
        previous=previous_stats["execution"]["skipped_zero_edge"],
    ))
    lines.append(metric_line(
        "Skipped other",
        current=current_stats["execution"]["skipped_other"],
        previous=previous_stats["execution"]["skipped_other"],
    ))
    return lines


def main() -> int:
    args = parse_args()
    try:
        since = parse_date_start(args.since)
        until = parse_date_end(args.until)
        current_window, previous_window = resolve_windows(
            since=since,
            until=until,
            hours=args.hours,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    path = Path(args.path)
    _t0 = time.perf_counter()
    current_stats = collect_window_metrics(
        path=path,
        since=current_window[0],
        until=current_window[1],
        exclude_test=args.exclude_test,
        show_progress=args.progress,
        show_profile=args.profile,
    )
    previous_stats = collect_window_metrics(
        path=path,
        since=previous_window[0],
        until=previous_window[1],
        exclude_test=args.exclude_test,
        show_progress=args.progress,
        show_profile=args.profile,
    )
    if args.profile:
        _eprint(f"[total] 6 stages completed in {time.perf_counter() - _t0:.1f}s")
    for line in render_report(current_stats, previous_stats):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
