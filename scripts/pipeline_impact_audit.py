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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import decision_funnel_summary
from scripts import match_quality_diagnostics
from scripts import signal_edge_diagnostics
from tasks.stats import observability_checkpoint
from utils.diagnostics_script_helpers import parse_date_end, parse_date_start
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
    startup_probe_rows = [
        row
        for row in detail_rows
        if row.get("is_synthetic_probe") is True or row.get("is_startup_probe") is True
    ]
    non_probe_rows = [
        row
        for row in detail_rows
        if row.get("is_synthetic_probe") is not True and row.get("is_startup_probe") is not True
    ]
    instrumented_rows = [row for row in non_probe_rows if row.get("pre_llm_quality_pass") is not None]
    block_rows = [row for row in instrumented_rows if row.get("pre_llm_would_block") is True]
    enforced_rows = [row for row in instrumented_rows if row.get("pre_llm_gate_enforced") is True]
    dangerous_rows = [row for row in instrumented_rows if row.get("pre_llm_would_block_and_useful") is True]
    keyword_override_rows = [row for row in instrumented_rows if row.get("pre_llm_keyword_override") is True]
    llm_skipped_match_quality_gate = sum(
        1 for row in instrumented_rows if row.get("llm_result_status") == "llm_skipped_match_quality_gate"
    )

    def _counter(rows: list[dict[str, Any]], key: str) -> Counter:
        return Counter(str(row.get(key) or "").strip() for row in rows if str(row.get(key) or "").strip())

    def _recent_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda row: row.get("ts") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:limit]

    gate_reason_counts = _counter(block_rows, "pre_llm_gate_reason")
    blocked_with_stopwords = sum(
        1 for row in block_rows if (row.get("pre_llm_filtered_stopword_count") or 0) > 0
    )
    blocked_with_generic = sum(
        1 for row in block_rows if (row.get("pre_llm_filtered_generic_count") or 0) > 0
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
            "llm_total_stage_ms_samples": edge_stats.get("llm_observability", {}).get("total_stage_ms_samples", []),
            "llm_queue_wait_ms_samples": edge_stats.get("llm_observability", {}).get("queue_wait_ms_samples", []),
            "llm_http_round_trip_ms_samples": edge_stats.get("llm_observability", {}).get("http_round_trip_ms_samples", []),
            "llm_contention_observed": edge_stats.get("llm_observability", {}).get("contention_observed", 0),
            "llm_skipped_routing": edge_stats.get("llm_observability", {}).get("skipped_routing", 0),
            "llm_skipped_routing_reasons": edge_stats.get("llm_observability", {}).get("skipped_routing_reasons", Counter()),
            "llm_value_add": edge_stats.get("llm_value_add", {}),
            "pre_llm_gate": {
                "signal_analysis_detail_total": edge_stats.get("counts", {}).get("SIGNAL_ANALYSIS_DETAIL", 0),
                "startup_probe_excluded": len(startup_probe_rows),
                "instrumented_non_probe_records": len(instrumented_rows),
                "would_block": len(block_rows),
                "gate_enforced": len(enforced_rows),
                "llm_skipped_match_quality_gate": llm_skipped_match_quality_gate,
                "gate_status_consistent": len(enforced_rows) == llm_skipped_match_quality_gate,
                "gate_status_delta": len(enforced_rows) - llm_skipped_match_quality_gate,
                "would_block_and_useful": len(dangerous_rows),
                "suppression_false_positive_candidate_count": len(dangerous_rows),
                "suppression_false_positive_candidate_rate": safe_pct(len(dangerous_rows), len(instrumented_rows)),
                "block_candidate_rate": safe_pct(len(block_rows), len(instrumented_rows)),
                "enforced_suppression_rate": safe_pct(len(enforced_rows), len(instrumented_rows)),
                "dangerous_suppression_rate": safe_pct(len(dangerous_rows), len(instrumented_rows)),
                "keyword_override_count": len(keyword_override_rows),
                "top_gate_reasons": gate_reason_counts,
                "interpretation": (
                    "Blocked calls appear safe"
                    if len(dangerous_rows) == 0
                    else "Review dangerous suppressions before trusting gate"
                ),
                "top_block_tickers": _counter(block_rows, "ticker"),
                "top_enforced_tickers": _counter(enforced_rows, "ticker"),
                "top_block_sources": _counter(block_rows, "source"),
                "top_enforced_sources": _counter(enforced_rows, "source"),
                "recent_enforced": _recent_rows(enforced_rows),
                "recent_dangerous": _recent_rows(dangerous_rows),
                "phase3_plan": {
                    "gate_health": {
                        "enforced_suppression_rate": safe_pct(len(enforced_rows), len(instrumented_rows)),
                        "dangerous_suppression_rate": safe_pct(len(dangerous_rows), len(instrumented_rows)),
                        "top_suppressed_tickers": _counter(enforced_rows, "ticker"),
                        "top_suppressed_sources": _counter(enforced_rows, "source"),
                    },
                    "keyword_override_review": {
                        "prevented_suppressions": len(keyword_override_rows),
                        "assessment": (
                            "No keyword overrides observed"
                            if len(keyword_override_rows) == 0
                            else (
                                "Review keyword override precision"
                                if len(dangerous_rows) > 0
                                else "Keyword override appears acceptable under current evidence"
                            )
                        ),
                    },
                    "heuristic_tuning_candidates": [
                        candidate
                        for candidate in [
                            (
                                "review stopword/generic token filter candidates"
                                if blocked_with_stopwords > 0 or blocked_with_generic > 0
                                else None
                            ),
                            (
                                "review overlap/ratio refinement opportunities"
                                if len(dangerous_rows) > 0 or len(block_rows) > 0
                                else None
                            ),
                            (
                                "review keyword override precision"
                                if len(keyword_override_rows) > 0
                                else None
                            ),
                        ]
                        if candidate is not None
                    ]
                    or ["No clear tuning candidate from current window"],
                    "recommendation": _phase3_recommendation(
                        dangerous_count=len(dangerous_rows),
                        keyword_override_count=len(keyword_override_rows),
                        enforced_count=len(enforced_rows),
                    ),
                },
            },
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


def _fmt_counter(counter: Counter, *, limit: int = 5) -> str:
    if not counter:
        return "(none)"
    return ", ".join(f"{label}={count}" for label, count in counter.most_common(limit))


def _fmt_recent_gate_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> str:
    if not rows:
        return "(none)"
    parts: list[str] = []
    for row in rows[:limit]:
        parts.append(
            f"{fmt_ts(row.get('ts'))} | {row.get('source') or 'n/a'} | {row.get('ticker') or 'n/a'} | "
            f"override={row.get('pre_llm_keyword_override')} | useful={row.get('llm_useful')} | "
            f"reason={row.get('pre_llm_gate_reason') or 'n/a'} | {str(row.get('headline') or '')[:80]}"
        )
    return "\n".join(f"    {part}" for part in parts)


def _phase3_recommendation(
    *,
    dangerous_count: int,
    keyword_override_count: int,
    enforced_count: int,
) -> str:
    if dangerous_count > 0:
        return "investigate dangerous suppressions before tightening"
    if keyword_override_count > 0 and enforced_count == 0:
        return "review keyword override precision"
    if enforced_count > 0:
        return "leave gate as-is"
    return "collect more data before tuning"


def _segment_snapshot(
    rows: list[dict[str, Any]],
    *,
    label_key: str,
    rate_key: str,
    limit: int = 3,
) -> str:
    if not rows:
        return "(none)"
    ordered = sorted(
        rows,
        key=lambda row: (
            -(row.get(rate_key) or 0.0),
            -(row.get("llm_rows") or 0),
            str(row.get(label_key) or "").lower(),
        ),
    )
    parts: list[str] = []
    for row in ordered[:limit]:
        parts.append(
            f"{row.get(label_key) or 'n/a'}="
            f"{fmt_pct(row.get(rate_key))} rows={row.get('llm_rows', 0)}"
        )
    return "; ".join(parts) if parts else "(none)"


def render_report(current_stats: dict[str, Any], previous_stats: dict[str, Any]) -> list[str]:
    current_since, current_until = current_stats["window"]
    previous_since, previous_until = previous_stats["window"]

    lines: list[str] = []
    lines.append("PIPELINE IMPACT AUDIT")
    lines.append(f"Current window : {fmt_ts(current_since)} -> {fmt_ts(current_until)}")
    lines.append(f"Previous window: {fmt_ts(previous_since)} -> {fmt_ts(previous_until)}")
    lines.append("")

    # Gates & experiments at audit time: live-readiness, the edge-prioritization
    # A/B verdict (carries its own before/after), and the feed/regime/market-horizon
    # rot-audit layer. Point-in-time context shared with daily_review via
    # tasks/stats/observability_checkpoint — so a PR review sees whether it moved
    # the readiness gate or the experiment, not just the funnel.
    try:
        lines.extend(observability_checkpoint.summary_lines())
    except Exception as exc:  # observability must never break the audit
        lines.append(f"GATES & EXPERIMENTS              : unavailable ({type(exc).__name__})")
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
        "LLM skipped (routing)",
        current=current_stats["analysis"]["llm_skipped_routing"],
        previous=previous_stats["analysis"]["llm_skipped_routing"],
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
    current_routing = current_stats["analysis"]["llm_skipped_routing_reasons"]
    previous_routing = previous_stats["analysis"]["llm_skipped_routing_reasons"]
    lines.append(
        "  Current routing skips         : "
        + (", ".join(f"{k}={v}" for k, v in current_routing.most_common()) if current_routing else "(none)")
    )
    lines.append(
        "  Previous routing skips        : "
        + (", ".join(f"{k}={v}" for k, v in previous_routing.most_common()) if previous_routing else "(none)")
    )
    lines.append(
        "  Current LLM total latency      : "
        f"{signal_edge_diagnostics.fmt_latency_stats(current_stats['analysis']['llm_total_stage_ms_samples'])}"
    )
    lines.append(
        "  Previous LLM total latency     : "
        f"{signal_edge_diagnostics.fmt_latency_stats(previous_stats['analysis']['llm_total_stage_ms_samples'])}"
    )
    lines.append(
        "  Current LLM queue wait         : "
        f"{signal_edge_diagnostics.fmt_latency_stats(current_stats['analysis']['llm_queue_wait_ms_samples'])}"
    )
    lines.append(
        "  Previous LLM queue wait        : "
        f"{signal_edge_diagnostics.fmt_latency_stats(previous_stats['analysis']['llm_queue_wait_ms_samples'])}"
    )
    lines.append(
        "  Current LLM HTTP round-trip    : "
        f"{signal_edge_diagnostics.fmt_latency_stats(current_stats['analysis']['llm_http_round_trip_ms_samples'])}"
    )
    lines.append(
        "  Previous LLM HTTP round-trip   : "
        f"{signal_edge_diagnostics.fmt_latency_stats(previous_stats['analysis']['llm_http_round_trip_ms_samples'])}"
    )
    lines.append(
        f"  Current LLM contention         : {current_stats['analysis']['llm_contention_observed']}"
    )
    lines.append(
        f"  Previous LLM contention        : {previous_stats['analysis']['llm_contention_observed']}"
    )
    lines.append("")

    current_value = current_stats["analysis"].get("llm_value_add", {})
    previous_value = previous_stats["analysis"].get("llm_value_add", {})
    current_llm_rows = current_value.get("llm_rows", 0)
    previous_llm_rows = previous_value.get("llm_rows", 0)
    current_gate = current_stats["analysis"].get("pre_llm_gate", {})
    previous_gate = previous_stats["analysis"].get("pre_llm_gate", {})
    lines.append("3. Pre-LLM Match Gate")
    lines.append(metric_line(
        "SIGNAL_ANALYSIS_DETAIL total",
        current=current_gate.get("signal_analysis_detail_total"),
        previous=previous_gate.get("signal_analysis_detail_total"),
    ))
    lines.append(metric_line(
        "Instrumented non-probe",
        current=current_gate.get("instrumented_non_probe_records"),
        previous=previous_gate.get("instrumented_non_probe_records"),
    ))
    lines.append(metric_line(
        "Startup probes excluded",
        current=current_gate.get("startup_probe_excluded"),
        previous=previous_gate.get("startup_probe_excluded"),
    ))
    lines.append(metric_line(
        "Would block",
        current=current_gate.get("would_block"),
        previous=previous_gate.get("would_block"),
    ))
    lines.append(metric_line(
        "Gate enforced",
        current=current_gate.get("gate_enforced"),
        previous=previous_gate.get("gate_enforced"),
    ))
    lines.append(metric_line(
        "Skipped by gate status",
        current=current_gate.get("llm_skipped_match_quality_gate"),
        previous=previous_gate.get("llm_skipped_match_quality_gate"),
    ))
    lines.append(metric_line(
        "Would block and useful",
        current=current_gate.get("would_block_and_useful"),
        previous=previous_gate.get("would_block_and_useful"),
    ))
    lines.append(metric_line(
        "False-positive candidates",
        current=current_gate.get("suppression_false_positive_candidate_count"),
        previous=previous_gate.get("suppression_false_positive_candidate_count"),
    ))
    lines.append(metric_line(
        "False-positive candidate rate",
        current=current_gate.get("suppression_false_positive_candidate_rate"),
        previous=previous_gate.get("suppression_false_positive_candidate_rate"),
        is_pct=True,
    ))
    lines.append(metric_line(
        "Block candidate rate",
        current=current_gate.get("block_candidate_rate"),
        previous=previous_gate.get("block_candidate_rate"),
        is_pct=True,
    ))
    lines.append(metric_line(
        "Enforced suppression rate",
        current=current_gate.get("enforced_suppression_rate"),
        previous=previous_gate.get("enforced_suppression_rate"),
        is_pct=True,
    ))
    lines.append(metric_line(
        "Dangerous suppression rate",
        current=current_gate.get("dangerous_suppression_rate"),
        previous=previous_gate.get("dangerous_suppression_rate"),
        is_pct=True,
    ))
    current_gate_consistency = (
        "OK"
        if current_gate.get("gate_status_consistent", True)
        else f"WARNING (delta={current_gate.get('gate_status_delta', 0):+d})"
    )
    previous_gate_consistency = (
        "OK"
        if previous_gate.get("gate_status_consistent", True)
        else f"WARNING (delta={previous_gate.get('gate_status_delta', 0):+d})"
    )
    lines.append(f"  Gate/status consistency      : {current_gate_consistency}")
    lines.append(f"  Previous gate consistency    : {previous_gate_consistency}")
    lines.append(f"  Current interpretation         : {current_gate.get('interpretation', 'n/a')}")
    lines.append(f"  Previous interpretation        : {previous_gate.get('interpretation', 'n/a')}")
    lines.append(f"  Current keyword overrides      : {fmt_count(current_gate.get('keyword_override_count'))}")
    lines.append(f"  Previous keyword overrides     : {fmt_count(previous_gate.get('keyword_override_count'))}")
    lines.append(f"  Current top gate reasons       : {_fmt_counter(current_gate.get('top_gate_reasons', Counter()))}")
    lines.append(f"  Previous top gate reasons      : {_fmt_counter(previous_gate.get('top_gate_reasons', Counter()))}")
    lines.append(f"  Current top block tickers      : {_fmt_counter(current_gate.get('top_block_tickers', Counter()))}")
    lines.append(f"  Current top enforced tickers   : {_fmt_counter(current_gate.get('top_enforced_tickers', Counter()))}")
    lines.append(f"  Current top block sources      : {_fmt_counter(current_gate.get('top_block_sources', Counter()))}")
    lines.append(f"  Current top enforced sources   : {_fmt_counter(current_gate.get('top_enforced_sources', Counter()))}")
    lines.append("  Recent enforced suppressions   :")
    lines.append(_fmt_recent_gate_rows(current_gate.get("recent_enforced", [])))
    lines.append("  Recent dangerous suppressions  :")
    lines.append(_fmt_recent_gate_rows(current_gate.get("recent_dangerous", [])))
    lines.append("")

    lines.append("4. LLM Value-Add Analysis")
    lines.append(metric_line(
        "LLM rows",
        current=current_llm_rows,
        previous=previous_llm_rows,
    ))
    lines.append(metric_line(
        "Near-neutral outputs",
        current=safe_pct(current_value.get("near_neutral_outputs", 0), current_llm_rows),
        previous=safe_pct(previous_value.get("near_neutral_outputs", 0), previous_llm_rows),
        is_pct=True,
    ))
    lines.append(metric_line(
        "Non-zero edge outputs",
        current=safe_pct(current_value.get("non_zero_edge_outputs", 0), current_llm_rows),
        previous=safe_pct(previous_value.get("non_zero_edge_outputs", 0), previous_llm_rows),
        is_pct=True,
    ))
    lines.append(metric_line(
        "Meaningful signals",
        current=safe_pct(current_value.get("meaningful_signals", 0), current_llm_rows),
        previous=safe_pct(previous_value.get("meaningful_signals", 0), previous_llm_rows),
        is_pct=True,
    ))
    lines.append(metric_line(
        "Trade candidates",
        current=safe_pct(current_value.get("trade_candidates", 0), current_llm_rows),
        previous=safe_pct(previous_value.get("trade_candidates", 0), previous_llm_rows),
        is_pct=True,
    ))
    lines.append(metric_line(
        "Created edge @0.50 market",
        current=safe_pct(current_value.get("llm_created_edge", 0), current_llm_rows),
        previous=safe_pct(previous_value.get("llm_created_edge", 0), previous_llm_rows),
        is_pct=True,
    ))
    current_prob_buckets = current_value.get("probability_movement_buckets", Counter())
    previous_prob_buckets = previous_value.get("probability_movement_buckets", Counter())
    lines.append(
        "  Current prob movement         : "
        + ", ".join(
            f"{label}={current_prob_buckets.get(key, 0)}"
            for key, label in (
                ("near_neutral", "near-neutral"),
                ("weak", "weak"),
                ("moderate", "moderate"),
                ("strong", "strong"),
            )
        )
    )
    lines.append(
        "  Previous prob movement        : "
        + ", ".join(
            f"{label}={previous_prob_buckets.get(key, 0)}"
            for key, label in (
                ("near_neutral", "near-neutral"),
                ("weak", "weak"),
                ("moderate", "moderate"),
                ("strong", "strong"),
            )
        )
    )
    current_edge_buckets = current_value.get("edge_magnitude_buckets", Counter())
    previous_edge_buckets = previous_value.get("edge_magnitude_buckets", Counter())
    lines.append(
        "  Current edge buckets          : "
        + ", ".join(
            f"{label}={current_edge_buckets.get(key, 0)}"
            for key, label in (
                ("zero_neutral", "zero/neutral"),
                ("weak", "weak"),
                ("moderate", "moderate"),
                ("strong", "strong"),
            )
        )
    )
    lines.append(
        "  Previous edge buckets         : "
        + ", ".join(
            f"{label}={previous_edge_buckets.get(key, 0)}"
            for key, label in (
                ("zero_neutral", "zero/neutral"),
                ("weak", "weak"),
                ("moderate", "moderate"),
                ("strong", "strong"),
            )
        )
    )
    lines.append("")

    current_segmentation = current_value.get("segmentation", {})
    previous_segmentation = previous_value.get("segmentation", {})
    lines.append("5. LLM Value-Add Segmentation")
    lines.append(
        "  Current top sources (meaningful): "
        + _segment_snapshot(
            current_segmentation.get("by_source", []),
            label_key="source",
            rate_key="meaningful_signal_rate",
        )
    )
    lines.append(
        "  Previous top sources (meaningful): "
        + _segment_snapshot(
            previous_segmentation.get("by_source", []),
            label_key="source",
            rate_key="meaningful_signal_rate",
        )
    )
    lines.append(
        "  Current top sources (neutral): "
        + _segment_snapshot(
            current_segmentation.get("by_source", []),
            label_key="source",
            rate_key="neutral_confirmation_rate",
        )
    )
    lines.append(
        "  Previous top sources (neutral): "
        + _segment_snapshot(
            previous_segmentation.get("by_source", []),
            label_key="source",
            rate_key="neutral_confirmation_rate",
        )
    )
    lines.append(
        "  Current top price bands       : "
        + _segment_snapshot(
            current_segmentation.get("by_price_band", []),
            label_key="price_band",
            rate_key="meaningful_signal_rate",
            limit=5,
        )
    )
    lines.append(
        "  Previous top price bands      : "
        + _segment_snapshot(
            previous_segmentation.get("by_price_band", []),
            label_key="price_band",
            rate_key="meaningful_signal_rate",
            limit=5,
        )
    )
    current_timing = current_segmentation.get("timing", {})
    previous_timing = previous_segmentation.get("timing", {})
    lines.append(
        "  Timing segmentation           : "
        f"current={'available' if current_timing.get('available') else 'unavailable'} "
        f"previous={'available' if previous_timing.get('available') else 'unavailable'}"
    )
    lines.append("")

    lines.append("6. Edge Formation")
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

    lines.append("7. Execution Quality")
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
    lines.append("")

    current_plan = current_gate.get("phase3_plan", {})
    lines.append("8. Phase 3 Planning Summary")
    lines.append(
        "  Gate health                  : "
        f"enforced={fmt_pct(current_plan.get('gate_health', {}).get('enforced_suppression_rate'))} "
        f"dangerous={fmt_pct(current_plan.get('gate_health', {}).get('dangerous_suppression_rate'))}"
    )
    lines.append(
        "  Top suppressed tickers       : "
        f"{_fmt_counter(current_plan.get('gate_health', {}).get('top_suppressed_tickers', Counter()))}"
    )
    lines.append(
        "  Top suppressed sources       : "
        f"{_fmt_counter(current_plan.get('gate_health', {}).get('top_suppressed_sources', Counter()))}"
    )
    lines.append(
        "  Keyword override review      : "
        f"prevented={fmt_count(current_plan.get('keyword_override_review', {}).get('prevented_suppressions'))} "
        f"| {current_plan.get('keyword_override_review', {}).get('assessment', 'n/a')}"
    )
    lines.append(
        "  Heuristic tuning candidates  : "
        + "; ".join(current_plan.get("heuristic_tuning_candidates", ["n/a"]))
    )
    lines.append(
        "  Recommendation               : "
        f"{current_plan.get('recommendation', 'n/a')}"
    )
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
