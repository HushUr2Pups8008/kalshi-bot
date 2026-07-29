"""
Pipeline-shaped daily review report.

This is reporting-only: it reuses the existing read-only summary modules and
reorganizes their outputs into a single report that follows the observable
system flow from ingestion through execution.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, time as dt_time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import decision_funnel_summary
from scripts import counterfactual_llm_eval
from scripts import freshness_diagnostics
from scripts import keyword_feedback
from scripts import match_quality_diagnostics
from scripts import match_suppression_audit
from scripts import paper_performance_drilldown
from scripts import since_restart_money_path
from scripts import signal_edge_diagnostics
from scripts import source_scorecard
from scripts.throughput_operator_metrics import (
    format_operator_throughput_lines,
    summarize_operator_throughput_from_trade_log,
)
from tasks.stats import observability_checkpoint
from utils.reporting_helpers import (
    DEFAULT_CURRENT_STATE_WINDOW_HOURS,
    ProgressTracker,
    resolve_recent_window,
    stage_timer,
    _eprint,
)
from utils.output_paths import (
    DAILY_REPORTS_DIR,
    DERIVED_STATE_DIR,
    PAPER_TRADES_DB,
    RAW_TRADES_LIVE_DIR,
)
from utils.diagnostic_reporting_helpers import format_counter
from utils.diagnostics_script_helpers import is_test_record_source_only
from utils.trade_log_reader import iter_trade_records
from kalshi.rest_client import KalshiRestClient


REPORTS_DIR = DAILY_REPORTS_DIR
DEFAULT_REPORT_PATH = REPORTS_DIR / f"daily_review_{datetime.now().strftime('%Y%m%d')}.txt"
HEALTH_REPORTS_DIR = REPORTS_DIR.parent / "health"
# Persisted day-over-day tier baseline used by section 1 status-change diff.
# Two-deep history (current + previous) so same-day reruns don't blow away
# yesterday's reference.
SOURCE_TIER_STATE_PATH = DERIVED_STATE_DIR / "source_tier_state.json"
# Default to the active live file -- daily review is a current-state report.
# Pass --path logs/trades to include archive data for historical analysis.
DEFAULT_TRADES_LOG_PATH = RAW_TRADES_LIVE_DIR / "trades.jsonl"
DEFAULT_PAPER_DB_PATH = PAPER_TRADES_DB
VERSION_FILE = REPO_ROOT / "VERSION"
RECENT_MATCH_EXAMPLES = 5
RECENT_EDGE_AUDIT = 5

# Source-scorecard tiers we keep visible in section 1's per-source waterfall.
# Sources in any other tier (prune / remove immediately / disabled) are folded
# into a one-line summary referencing section 8.
_VISIBLE_TIERS: tuple[str, ...] = ("top performers", "keep", "watch / investigate", "incubating")
_HIDDEN_TIERS: tuple[str, ...] = (
    "prune",
    "remove immediately",
    "disabled by source",
    "disabled by family",
)
# Ordinal severity used to detect day-over-day tier degradation. Lower is better.
_TIER_ORDER: dict[str, int] = {
    "top performers": 0,
    "keep": 1,
    "watch / investigate": 2,
    "incubating": 2,
    "prune": 3,
    "remove immediately": 4,
    "disabled by source": 5,
    "disabled by family": 5,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the kalshi-bot daily review suite")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_TRADES_LOG_PATH),
        help=(
            "Trade log path -- default: logs/trades/live/trades.jsonl; pass logs/trades to include archive "
            f"(default window: last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours when dates omitted)"
        ),
    )
    parser.add_argument("--since", help="Inclusive start date in YYYY-MM-DD")
    parser.add_argument(
        "--until",
        help=f"Inclusive end date in YYYY-MM-DD (default: now when both dates omitted; last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours)",
    )
    parser.add_argument("--top", type=int, default=5, help="Max rows to show in top breakdowns")
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


def fmt_int(value: Any) -> str:
    if not isinstance(value, int):
        return "n/a"
    return str(value)


def fmt_pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(100.0 * numerator / denominator):.1f}%"


def fmt_prob(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):.4f}"


def fmt_money(value: Any) -> str:
    if not isinstance(value, (int, float, Decimal)):
        return "n/a"
    sign = "+" if float(value) >= 0 else ""
    return f"{sign}${float(value):.2f}"


def fmt_ts(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fmt_counter_line(counter: Counter[str], keys: list[tuple[str, str]]) -> str:
    if not counter:
        return "n/a"
    return ", ".join(f"{label}={counter.get(key, 0)}" for key, label in keys)


def _fresh_pass_assignment_shadow_path(trades_path: Path) -> Path:
    if trades_path.is_dir():
        trades_root = trades_path
    elif trades_path.name == "trades.jsonl" and trades_path.parent.name == "live":
        trades_root = trades_path.parent.parent
    else:
        trades_root = trades_path.parent
    return trades_root / "shadow" / "fresh_pass_assignment_shadow.jsonl"


def _summarize_fresh_pass_assignment_shadow(
    trades_path: Path,
    *,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool = False,
) -> dict[str, Any]:
    shadow_path = _fresh_pass_assignment_shadow_path(trades_path)
    stats: dict[str, Any] = {
        "path": shadow_path,
        "rows": 0,
        "assigned": 0,
        "unassigned": 0,
        "malformed": 0,
        "top_unassigned_sources": Counter(),
    }
    if not shadow_path.exists():
        return stats

    for record in iter_trade_records(
        shadow_path,
        since=since,
        until=until,
        event_types={"FRESH_PASS_ASSIGNMENT_SHADOW"},
    ):
        if exclude_test and is_test_record_source_only(record):
            continue
        stats["rows"] += 1
        if bool(record.get("malformed")):
            stats["malformed"] += 1
            continue
        if bool(record.get("assigned")):
            stats["assigned"] += 1
        else:
            stats["unassigned"] += 1
            source = str(record.get("source") or "").strip()
            if source:
                stats["top_unassigned_sources"][source] += 1
    return stats


def _format_fresh_pass_conversion_lines(
    *,
    fresh_passes: int,
    match_records: int,
    detail_rows: int,
    llm_attempted: int,
    opportunities: int,
    paper_trades: int,
) -> list[str]:
    signal_label = "signal row" if detail_rows == 1 else "signal rows"
    attempt_label = "LLM attempt" if llm_attempted == 1 else "LLM attempts"
    lines = [
        "  Fresh-pass observability          : "
        f"{fresh_passes} fresh; {match_records} match diagnostics; {detail_rows} {signal_label}; "
        f"{llm_attempted} {attempt_label}; {opportunities} opportunities; "
        f"{paper_trades} raw paper-trade events"
    ]
    lines.append("    attribution                    : raw stage counts are not lifecycle conversion")
    return lines


def _format_same_window_lifecycle_attribution_lines(
    attribution: dict[str, Any],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[str]:
    paper_trade_status = str(attribution.get("paper_trade_lifecycle_status") or "unavailable")
    paper_trade_event_rows = int(attribution.get("paper_trade_event_rows") or 0)
    paper_trade_linked_event_rows = int(attribution.get("paper_trade_linked_event_rows") or 0)
    live_submission_event_rows = int(attribution.get("live_submission_event_rows") or 0)
    live_submission_linked_event_rows = int(attribution.get("live_submission_linked_event_rows") or 0)
    unknown_live_submission_event_rows = int(attribution.get("unknown_live_submission_event_rows") or 0)
    unknown_live_submission_linked_event_rows = int(attribution.get("unknown_live_submission_linked_event_rows") or 0)
    paper_trade_opportunities = int(attribution.get("paper_trade_opportunity_lifecycle_count") or 0)
    live_submission_opportunities = int(attribution.get("live_submission_opportunity_lifecycle_count") or 0)
    unknown_live_submission_opportunities = int(
        attribution.get("unknown_live_submission_opportunity_lifecycle_count") or 0
    )
    unresolved_live_submission_intents = int(
        attribution.get("unresolved_live_submission_intent_opportunity_lifecycle_count") or 0
    )
    live_submission_intent_event_rows = int(attribution.get("live_submission_intent_event_rows") or 0)
    live_submission_intent_linked_event_rows = int(attribution.get("live_submission_intent_linked_event_rows") or 0)
    outcome_conflicts = int(attribution.get("outcome_conflict_lifecycle_count") or 0)
    terminal_evidence_conflicts = int(attribution.get("terminal_evidence_conflict_lifecycle_count") or 0)
    orphan_paper_trades = int(attribution.get("orphan_paper_trade_lifecycle_count") or 0)
    orphan_live_submissions = int(attribution.get("orphan_live_submission_lifecycle_count") or 0)
    orphan_unknown_live_submissions = int(attribution.get("orphan_unknown_live_submission_lifecycle_count") or 0)
    orphan_live_submission_intents = int(attribution.get("orphan_live_submission_intent_lifecycle_count") or 0)
    conflicted_lifecycles = int(attribution.get("conflicted_lifecycle_count") or 0)
    incomplete_lifecycles = int(attribution.get("identity_incomplete_lifecycle_count") or 0)
    reused_opportunities = int(attribution.get("reused_opportunity_lifecycle_count") or 0)
    unattributed = Counter(attribution.get("unattributed_event_counts") or {})
    lines = [
        "  Same-window linkable cohort      : "
        f"{int(attribution.get('opportunity_lifecycle_count') or 0)} opportunities",
        "    Window                         : "
        f"{since.isoformat() if since else '(beginning)'} -> {until.isoformat() if until else '(latest)'}",
        "    Terminal attribution           : "
        f"G7 skips={int(attribution.get('g7_skip_lifecycle_count') or 0)}, "
        f"zero-cap skips={int(attribution.get('zero_cap_skip_lifecycle_count') or 0)}, "
        f"other skips={int(attribution.get('other_skip_lifecycle_count') or 0)}, "
        f"paper trades={paper_trade_opportunities}, live submissions={live_submission_opportunities}, "
        f"unknown live submissions={unknown_live_submission_opportunities}, "
        f"intents without matching terminal journal={unresolved_live_submission_intents}, "
        f"conflicts={outcome_conflicts}, receipt conflicts={terminal_evidence_conflicts}, "
        f"pending={int(attribution.get('pending_opportunity_lifecycle_count') or 0)}, "
        f"orphan skips={int(attribution.get('orphan_skip_lifecycle_count') or 0)}",
    ]
    if paper_trade_status == "not_observed":
        lines.append("    Paper-trade lineage            : not observed in this window")
    else:
        lines.append(
            "    Paper-trade lineage            : "
            f"{paper_trade_status} ({paper_trade_linked_event_rows}/{paper_trade_event_rows} event rows linked)"
        )
    lines.append(
        "    Live submission lineage        : "
        f"{live_submission_linked_event_rows}/{live_submission_event_rows} event rows linked; "
        "not fill or P&L evidence"
    )
    lines.append(
        "    Live submission unknown       : "
        f"{unknown_live_submission_linked_event_rows}/{unknown_live_submission_event_rows} event rows linked; "
        "reconciliation required"
    )
    lines.append(
        "    Live submission intent lineage: "
        f"{live_submission_intent_linked_event_rows}/{live_submission_intent_event_rows} event rows linked; "
        f"{unresolved_live_submission_intents} without matching terminal journal; "
        "not fill or P&L evidence; reconciliation required"
    )
    lines.append(
        "    Linkage conflicts              : "
        f"lifecycle IDs={conflicted_lifecycles}, incomplete IDs={incomplete_lifecycles}, "
        f"reused opportunities={reused_opportunities}, terminal evidence conflicts={terminal_evidence_conflicts}, "
        f"orphan paper trades={orphan_paper_trades}, "
        f"orphan live submissions={orphan_live_submissions}, "
        f"orphan unknown submissions={orphan_unknown_live_submissions}, "
        f"orphan live intents={orphan_live_submission_intents}"
    )
    lines.append("    P&L basis                      : settlement and mark P&L excluded from lifecycle linkage")
    if unattributed:
        rendered = ", ".join(f"{event_type}={count}" for event_type, count in sorted(unattributed.items()))
        lines.append(f"    Unattributed lifecycle events  : {rendered}")
    return lines


def _format_match_attribution_lines(
    funnel_stats: dict[str, Any],
    *,
    top: int,
) -> list[str]:
    event_counts = funnel_stats.get("event_counts", {})
    match_diagnostics = int(
        funnel_stats.get("match_diagnostics_total", 0) or event_counts.get("MATCH_DIAGNOSTIC", 0) or 0
    )
    signal_detail_rows = int(
        funnel_stats.get("signal_analysis_detail_total", 0) or event_counts.get("SIGNAL_ANALYSIS_DETAIL", 0) or 0
    )
    match_detail_gap = int(
        funnel_stats.get(
            "match_to_signal_detail_gap",
            max(0, match_diagnostics - signal_detail_rows),
        )
        or 0
    )
    suppressions = int(
        event_counts.get("MATCH_SUPPRESSED", 0)
        or sum(Counter(funnel_stats.get("match_suppressed_reasons", {})).values())
        or 0
    )
    weight_applications = int(
        funnel_stats.get("match_weight_applied_total", 0) or event_counts.get("MATCH_WEIGHT_APPLIED", 0) or 0
    )
    weight_score_delta = float(funnel_stats.get("match_weight_score_delta_total", 0.0) or 0.0)
    suppression_coverage = Counter(funnel_stats.get("match_suppressed_column_coverage", {}))
    fresh_pass_linkage = funnel_stats.get("fresh_pass_route_log_linkage", {})
    if not isinstance(fresh_pass_linkage, dict):
        fresh_pass_linkage = {}
    fresh_pass_rows = int(fresh_pass_linkage.get("fresh_pass_rows", 0) or 0)
    fresh_pass_unique_keys = int(fresh_pass_linkage.get("fresh_pass_unique_keys", 0) or 0)
    fresh_pass_keys_with_candidate_diagnostic = int(
        fresh_pass_linkage.get("fresh_pass_keys_with_candidate_diagnostic", 0) or 0
    )
    fresh_pass_keys_with_explicit_no_match = int(
        fresh_pass_linkage.get("fresh_pass_keys_with_explicit_no_match", 0) or 0
    )
    fresh_pass_keys_with_market_availability_exit = int(
        fresh_pass_linkage.get("fresh_pass_keys_with_market_availability_exit", 0) or 0
    )
    fresh_pass_keys_with_unknown_route_exit = int(
        fresh_pass_linkage.get("fresh_pass_keys_with_unknown_route_exit", 0) or 0
    )
    fresh_pass_keys_with_multiple_route_signals = int(
        fresh_pass_linkage.get("fresh_pass_keys_with_multiple_route_signals", 0) or 0
    )
    fresh_pass_keys_without_tracked_route_signal = int(
        fresh_pass_linkage.get("fresh_pass_keys_without_tracked_route_signal", 0) or 0
    )
    fresh_pass_ambiguous_duplicate_keys = int(
        fresh_pass_linkage.get("fresh_pass_ambiguous_duplicate_keys", 0) or 0
    )
    fresh_pass_missing_identity_rows = int(
        fresh_pass_linkage.get("fresh_pass_missing_identity_rows", 0) or 0
    )
    match_diagnostic_missing_identity_rows = int(
        fresh_pass_linkage.get("match_diagnostic_missing_identity_rows", 0) or 0
    )
    match_no_candidate_missing_identity_rows = int(
        fresh_pass_linkage.get("match_no_candidate_missing_identity_rows", 0) or 0
    )
    candidate_diagnostic_keys_without_fresh_pass = int(
        fresh_pass_linkage.get("candidate_diagnostic_keys_without_fresh_pass", 0) or 0
    )
    explicit_no_match_keys_without_fresh_pass = int(
        fresh_pass_linkage.get("explicit_no_match_keys_without_fresh_pass", 0) or 0
    )
    market_availability_keys_without_fresh_pass = int(
        fresh_pass_linkage.get("market_availability_keys_without_fresh_pass", 0) or 0
    )
    unknown_route_exit_keys_without_fresh_pass = int(
        fresh_pass_linkage.get("unknown_route_exit_keys_without_fresh_pass", 0) or 0
    )
    match_no_candidate_total = int(funnel_stats.get("match_no_candidate_total", 0) or 0)
    no_candidate_pool_stages = Counter(
        funnel_stats.get("match_no_candidate_candidate_pool_stages", {})
    )
    no_candidate_missing_pool_stage = int(
        funnel_stats.get("match_no_candidate_missing_candidate_pool_stage", 0) or 0
    )
    post_admission_rejection_complete_rows = int(
        funnel_stats.get("match_no_candidate_post_admission_rejection_complete_rows", 0)
        or 0
    )
    post_admission_rejection_missing_breakdown_rows = int(
        funnel_stats.get("match_no_candidate_post_admission_rejection_missing_breakdown_rows", 0)
        or 0
    )
    post_admission_rejection_market_rows = int(
        funnel_stats.get("match_no_candidate_post_admission_rejection_within_horizon_markets", 0)
        or 0
    )
    post_admission_no_token_overlap = int(
        funnel_stats.get("match_no_candidate_post_admission_no_token_overlap", 0) or 0
    )
    post_admission_below_min = int(
        funnel_stats.get("match_no_candidate_post_admission_below_min", 0) or 0
    )
    post_admission_weight_demoted = int(
        funnel_stats.get("match_no_candidate_post_admission_weight_demoted", 0) or 0
    )
    post_admission_counterfactual_shadow_valid_rows = int(
        funnel_stats.get(
            "match_no_candidate_post_admission_counterfactual_shadow_valid_rows",
            0,
        )
        or 0
    )
    post_admission_counterfactual_shadow_legacy_rows = int(
        funnel_stats.get(
            "match_no_candidate_post_admission_counterfactual_shadow_legacy_rows",
            0,
        )
        or 0
    )
    post_admission_counterfactual_shadow_invalid_rows = int(
        funnel_stats.get(
            "match_no_candidate_post_admission_counterfactual_shadow_invalid_rows",
            0,
        )
        or 0
    )
    post_admission_counterfactual_shadow_truncated_rows = int(
        funnel_stats.get(
            "match_no_candidate_post_admission_counterfactual_shadow_truncated_rows",
            0,
        )
        or 0
    )
    drilldowns = [
        ("Drilldown: pre-LLM quality gate", Counter(funnel_stats.get("match_diagnostic_pre_llm_gate", {})), None),
        ("Drilldown: match diagnostic sources", Counter(funnel_stats.get("match_diagnostic_sources", {})), top),
        ("Drilldown: match diagnostic tickers", Counter(funnel_stats.get("match_diagnostic_tickers", {})), top),
        (
            "Drilldown: fresh-pass keys without tracked route signals",
            Counter(funnel_stats.get("fresh_pass_without_tracked_route_signal_sources", {})),
            top,
        ),
        ("Drilldown: explicit no-candidate reasons", Counter(funnel_stats.get("match_no_candidate_reasons", {})), None),
        ("Drilldown: no-candidate pool stages", no_candidate_pool_stages, top),
        ("Drilldown: explicit no-candidate venues", Counter(funnel_stats.get("match_no_candidate_venues", {})), top),
        ("Drilldown: match suppression reasons", Counter(funnel_stats.get("match_suppressed_reasons", {})), top),
        ("Drilldown: match suppression tokens", Counter(funnel_stats.get("match_suppressed_tokens", {})), top),
        ("Drilldown: match suppression venues", Counter(funnel_stats.get("match_suppressed_venues", {})), top),
        ("Drilldown: match weight tokens", Counter(funnel_stats.get("match_weight_tokens", {})), top),
        ("Drilldown: match weight prefixes", Counter(funnel_stats.get("match_weight_prefixes", {})), top),
        ("Drilldown: opportunity sources", Counter(funnel_stats.get("opportunity_sources", {})), top),
        ("Drilldown: opportunity source classes", Counter(funnel_stats.get("opportunity_source_classes", {})), top),
        ("Drilldown: opportunity retrieval modes", Counter(funnel_stats.get("opportunity_retrieval_modes", {})), top),
        (
            "Drilldown: opportunity settlement-source match",
            Counter(funnel_stats.get("opportunity_settlement_source_matches", {})),
            top,
        ),
        ("Drilldown: skip sources", Counter(funnel_stats.get("skip_sources", {})), top),
        ("Drilldown: skip source classes", Counter(funnel_stats.get("skip_source_classes", {})), top),
        ("Drilldown: skip retrieval modes", Counter(funnel_stats.get("skip_retrieval_modes", {})), top),
        ("Drilldown: skip evidence IDs", Counter(funnel_stats.get("skip_evidence_ids", {})), top),
        (
            "Drilldown: skip settlement-source match",
            Counter(funnel_stats.get("skip_settlement_source_matches", {})),
            top,
        ),
    ]

    if not any(
        [
            match_diagnostics,
            signal_detail_rows,
            match_detail_gap,
            suppressions,
            weight_applications,
            fresh_pass_rows,
            fresh_pass_unique_keys,
            match_no_candidate_total,
            no_candidate_missing_pool_stage,
            candidate_diagnostic_keys_without_fresh_pass,
            explicit_no_match_keys_without_fresh_pass,
            market_availability_keys_without_fresh_pass,
            unknown_route_exit_keys_without_fresh_pass,
            *[bool(counter) for _label, counter, _limit in drilldowns],
        ]
    ):
        return []

    lines = [
        f"  Match diagnostics                : {match_diagnostics}",
        f"  Signal analysis detail rows      : {signal_detail_rows}",
        f"  Match -> analysis detail gap     : {match_detail_gap}",
        f"  Match suppressions               : {suppressions}",
        f"  Match weight applications        : {weight_applications} (score_delta={weight_score_delta:.4f})",
    ]
    if (
        fresh_pass_rows
        or fresh_pass_unique_keys
        or candidate_diagnostic_keys_without_fresh_pass
        or explicit_no_match_keys_without_fresh_pass
        or market_availability_keys_without_fresh_pass
        or unknown_route_exit_keys_without_fresh_pass
    ):
        lines.append(
            "  Fresh-pass route linkage        : venue-agnostic, window-local log signals; "
            "not attempt, conversion, or per-venue coverage; signal rates overlap"
        )
        lines.append(
            "    candidate_diagnostic="
            f"{fresh_pass_keys_with_candidate_diagnostic}/{fresh_pass_unique_keys} "
            f"({fmt_pct(fresh_pass_keys_with_candidate_diagnostic, fresh_pass_unique_keys)}) "
            "logged_no_match="
            f"{fresh_pass_keys_with_explicit_no_match}/{fresh_pass_unique_keys} "
            f"({fmt_pct(fresh_pass_keys_with_explicit_no_match, fresh_pass_unique_keys)}) "
            "market_availability="
            f"{fresh_pass_keys_with_market_availability_exit}/{fresh_pass_unique_keys} "
            f"({fmt_pct(fresh_pass_keys_with_market_availability_exit, fresh_pass_unique_keys)}) "
            "unknown_or_other_exit="
            f"{fresh_pass_keys_with_unknown_route_exit}/{fresh_pass_unique_keys} "
            f"({fmt_pct(fresh_pass_keys_with_unknown_route_exit, fresh_pass_unique_keys)})"
        )
        lines.append(
            "    "
            f"multiple={fresh_pass_keys_with_multiple_route_signals} "
            f"no_signal={fresh_pass_keys_without_tracked_route_signal}"
        )
        lines.append(
            "    "
            f"ambiguous={fresh_pass_ambiguous_duplicate_keys} "
            f"missing_identity=fresh={fresh_pass_missing_identity_rows} "
            f"diagnostic={match_diagnostic_missing_identity_rows} "
            f"no_candidate={match_no_candidate_missing_identity_rows}"
        )
        lines.append(
            "    signal_without_fresh="
            f"candidate_diagnostic={candidate_diagnostic_keys_without_fresh_pass} "
            f"logged_no_match={explicit_no_match_keys_without_fresh_pass} "
            f"market_availability={market_availability_keys_without_fresh_pass} "
            f"unknown_or_other_exit={unknown_route_exit_keys_without_fresh_pass}"
        )
    if match_no_candidate_total:
        lines.append(f"  Explicit no-candidate rows      : {match_no_candidate_total}")
        lines.append(
            "  No-candidate records missing pool-stage: "
            f"{no_candidate_missing_pool_stage}"
        )
    if (
        post_admission_rejection_complete_rows
        or post_admission_rejection_missing_breakdown_rows
    ):
        lines.append(
            "  Post-admission rejection attribution: "
            f"complete={post_admission_rejection_complete_rows} "
            f"unavailable={post_admission_rejection_missing_breakdown_rows} "
            f"market_rows={post_admission_rejection_market_rows}"
        )
        overlap_percent = (
            100.0 * post_admission_no_token_overlap / post_admission_rejection_market_rows
            if post_admission_rejection_market_rows
            else 0.0
        )
        below_percent = (
            100.0 * post_admission_below_min / post_admission_rejection_market_rows
            if post_admission_rejection_market_rows
            else 0.0
        )
        lines.append(
            "    no_token_overlap="
            f"{post_admission_no_token_overlap} ({overlap_percent:.1f}%) "
            f"below_min_score={post_admission_below_min} ({below_percent:.1f}%) "
            f"weight_demoted={post_admission_weight_demoted}"
        )
    if any(
        (
            post_admission_counterfactual_shadow_valid_rows,
            post_admission_counterfactual_shadow_legacy_rows,
            post_admission_counterfactual_shadow_invalid_rows,
            post_admission_counterfactual_shadow_truncated_rows,
        )
    ):
        lines.append(
            "  Counterfactual snapshot coverage: "
            f"valid={post_admission_counterfactual_shadow_valid_rows} "
            f"legacy={post_admission_counterfactual_shadow_legacy_rows} "
            f"invalid={post_admission_counterfactual_shadow_invalid_rows} "
            f"truncated={post_admission_counterfactual_shadow_truncated_rows}"
        )
    if suppressions:
        coverage_parts = [
            f"{column}={suppression_coverage.get(column, 0)}/{suppressions}"
            for column in (
                "raw_score",
                "adjusted_score",
                "threshold",
                "token_weight_multiplier",
                "venue",
                "market_prefix",
            )
        ]
        lines.append("  Match suppression metadata       : " + ", ".join(coverage_parts))
    for label, counter, limit in drilldowns:
        if not counter:
            continue
        lines.append(f"  {label}")
        effective_limit = max(top, len(counter)) if limit is None else limit
        lines.extend(format_counter(counter, top=effective_limit))
    return lines


def _latest_restart_timestamp_from_health_reports(
    health_dir: Path = HEALTH_REPORTS_DIR,
) -> str | None:
    candidates = sorted(
        health_dir.glob("bothealth_*.md"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = re.search(r"bot_runtime\.started_utc=([^\s]+)", text)
        if match:
            return match.group(1)
    return None


def _latest_log_boot_timestamp(log_path: Path = REPO_ROOT / "logs" / "app" / "bot.log") -> str | None:
    try:
        from scripts.botcheck import read_sessions

        sessions = read_sessions(log_path)
    except Exception:
        return None
    if not sessions:
        return None
    return sessions[-1].boot_ts.isoformat()


def _matcher_weight_runtime_status() -> dict[str, Any]:
    """Mirror matcher admission verification for operator-facing reports."""
    from analysis.match_feedback import matcher_weights_status

    status = matcher_weights_status(REPO_ROOT / "data" / "matcher_token_weights.json")
    if status.get("status") == "unverified":
        return {
            "status": "fail_closed",
            "reason": status.get("reason") or "matcher weights unverified",
            "path": status.get("path"),
            "count": status.get("count", 0),
        }
    return {
        "status": "clean",
        "reason": None,
        "path": status.get("path"),
        "count": status.get("count", 0),
    }


def _format_since_restart_money_path_lines(report: dict[str, Any]) -> list[str]:
    summary = report.get("summary", {})
    no_keywords = report.get("no_keywords", {})
    terminal_counts = summary.get("terminal_counts", {}) or {}
    terminal_text = ", ".join(f"{key}={value}" for key, value in sorted(terminal_counts.items())) or "none"
    lines = [
        "SINCE-RESTART MONEY PATH  [source: scripts/since_restart_money_path.py]",
        "  Boundary source                  : health bot_runtime.started_utc (process start)",
        f"  Window                           : {report.get('window', {}).get('since')} -> {report.get('window', {}).get('until') or 'open'}",
        f"  Candidates                       : {summary.get('candidates', 0)}",
        f"  Terminal outcomes                : {terminal_text}",
        f"  Measurement gaps                 : {summary.get('measurement_gaps', 0)}",
        f"  No-keyword exits                 : {no_keywords.get('count', 0)}",
    ]
    boundaries = report.get("boundaries") or {}
    if boundaries.get("process_start_utc") or boundaries.get("log_boot_utc"):
        lines.append(f"  Process-start boundary           : {boundaries.get('process_start_utc') or 'unknown'}")
        lines.append(f"  Latest log-boot boundary         : {boundaries.get('log_boot_utc') or 'unknown'}")
    gap = report.get("legacy_resolutions_between_process_start_and_log_boot") or {}
    if gap.get("count", 0):
        lines.append(
            f"  Legacy resolutions before boot   : {gap.get('count', 0)} pnl={fmt_money(gap.get('pnl_total'))}"
        )
    pm_feedback = report.get("polymarket_settlement_feedback", {}) or {}
    if pm_feedback:
        lines.append(
            "  Polymarket settlement feedback   : "
            f"{pm_feedback.get('status', 'unknown')} "
            f"({pm_feedback.get('resolved_count', 0)}/"
            f"{pm_feedback.get('min_resolved_required', 0)} resolved)"
        )
        proof_rows = pm_feedback.get("proof_rows", []) or []
        if proof_rows:
            lines.append("  Polymarket settlement proof rows :")
            for row in proof_rows[:5]:
                lines.append(
                    "    "
                    f"{row.get('ticker') or 'n/a'} "
                    f"trade_id={row.get('trade_id') or 'n/a'} "
                    f"pnl={row.get('pnl_dollars')} "
                    f"feedback={row.get('feedback_ts') or 'none'} "
                    f"prefix={row.get('market_prefix') or 'none'}"
                )
    candidates = report.get("candidates", []) or []
    if candidates:
        lines.append("  Drilldown: recent since-restart candidates")
        for row in candidates[:5]:
            lines.append(
                "    "
                f"{row.get('ticker') or 'n/a'} terminal={row.get('terminal_type') or 'none'} "
                f"venue={row.get('terminal_venue') or row.get('blend_venue') or 'unknown'} "
                f"reason={row.get('terminal_reason') or 'none'}"
            )
    return lines


def _counterfactual_eval_ollama_models_from_env() -> list[str]:
    raw = os.getenv("DAILY_REVIEW_COUNTERFACTUAL_EVAL_OLLAMA_MODELS", "")
    return [model.strip() for model in raw.split(",") if model.strip()]


def _software_version() -> str:
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return f"v{version}" if version else "unknown"


def _counterfactual_eval_hydrate_markets_enabled() -> bool:
    return os.getenv("DAILY_REVIEW_COUNTERFACTUAL_HYDRATE_KALSHI_MARKETS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _build_counterfactual_llm_eval_report(
    trades_path: Path,
    since: datetime | None,
    until: datetime | None,
    *,
    exclude_test: bool,
) -> dict[str, Any]:
    market_detail_provider = None
    if _counterfactual_eval_hydrate_markets_enabled():
        market_detail_provider = KalshiRestClient().get_market
    report = counterfactual_llm_eval.build_eval_report(
        trades_path,
        since=since or (datetime.now(timezone.utc) - timedelta(days=1)),
        until=until,
        exclude_test=exclude_test,
        market_detail_provider=market_detail_provider,
    )
    models = _counterfactual_eval_ollama_models_from_env()
    if not models:
        return report
    evaluators = {
        model: counterfactual_llm_eval.make_ollama_evaluator(
            model,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            timeout_seconds=float(os.getenv("OLLAMA_REQUEST_TIMEOUT_SECONDS", "60")),
        )
        for model in models
    }
    return counterfactual_llm_eval.run_model_eval(report, evaluators)


def _format_counterfactual_llm_eval_lines(report: dict[str, Any]) -> list[str]:
    status = report.get("model_eval_status", "unknown")
    counts = report.get("target_counts", {}) or {}
    lines = [
        f"  Counterfactual LLM eval          : {status}",
        (
            "    "
            f"neutral_none_no_keywords={counts.get('neutral_none_no_keywords', 0)} "
            f"context_ready={counts.get('context_ready', 0)} "
            f"missing_contract_context={counts.get('missing_contract_context', 0)}"
        ),
    ]
    if "evaluated_context_ready" in counts or "skipped_missing_contract_context" in counts:
        lines.append(
            "    "
            f"evaluated_context_ready={counts.get('evaluated_context_ready', 0)} "
            f"skipped_missing_contract_context={counts.get('skipped_missing_contract_context', 0)}"
        )
    model_summary = report.get("model_summary") or {}
    if model_summary:
        lines.append("    Model comparison summary:")
        for model_name, summary in sorted(model_summary.items()):
            lines.append(
                f"      {model_name}: evaluated={summary.get('evaluated', 0)} "
                f"paper_candidate_positive={summary.get('paper_candidate_positive', 0)} "
                f"errors={summary.get('errors', 0)}"
            )
    if report.get("error"):
        lines.append(f"    error={report['error']}")
    return lines


def _fmt_probability(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_unsigned_money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _collect_kalshi_drift_state() -> tuple[dict[str, Any], str | None]:
    """Read the same P0 heartbeat sources `scripts/botcheck.py` consumes.

    Returns ``(halt_state, cohort_ts)`` for `_format_kalshi_drift_lines`
    to render. Reads two sources, both read-only:

    - ``data/runtime/kalshi_drift_halt.json`` — DriftCounter halt sentinel
      (LD-6/LD-6b: presence-equals-halt; manual clearance only).
    - ``data/paper_trades.db`` ``bot_state.p0_price_fix_deployed_ts`` —
      POST_FIX_NEW cohort boundary (LD-7/CR-F).

    If either source is missing or unreadable, that signal renders as
    ``null`` rather than crashing the daily review. The DriftCounter
    threshold is operator-locked to ``abs >= 1`` (LD-6 / OQ-A).

    Local mirror rather than re-import of ``scripts.botcheck.print_kalshi_drift_section``
    so the daily-review section can append into the ``lines`` list (the
    botcheck helper writes to stdout, which doesn't compose with the
    report-file output path used here).
    """
    import sqlite3

    halt_state: dict[str, Any] = {
        "cycle_count": 0,
        "halt": False,
        "last_halt_at": None,
        "threshold_abs": 1,
    }
    sentinel_path = REPO_ROOT / "data" / "runtime" / "kalshi_drift_halt.json"
    if sentinel_path.is_file():
        try:
            payload = json.loads(sentinel_path.read_text(encoding="utf-8"))
            halt_state["cycle_count"] = int(payload.get("cycle_drift_count") or 0)
            halt_state["halt"] = True
            halt_state["last_halt_at"] = payload.get("halt_ts")
        except (json.JSONDecodeError, OSError, ValueError):
            # Fail-closed if sentinel exists but is unreadable —
            # operator should treat as halted and investigate.
            halt_state["halt"] = True

    cohort_ts: str | None = None
    db_path = REPO_ROOT / "data" / "paper_trades.db"
    if db_path.is_file():
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT value FROM bot_state WHERE key = ?",
                    ("p0_price_fix_deployed_ts",),
                ).fetchone()
                if row is not None:
                    cohort_ts = row[0]
            finally:
                conn.close()
        except sqlite3.Error:
            cohort_ts = None

    return halt_state, cohort_ts


def _format_kalshi_drift_lines(halt_state: dict[str, Any], cohort_ts: str | None) -> list[str]:
    """Render the P0 heartbeat block for the daily-review report file.

    Output shape matches `scripts/botcheck.py::print_kalshi_drift_section`
    so a single operator visual model covers both surfaces.
    """
    return [
        "0. SYSTEM HEALTH  [source: data/runtime/kalshi_drift_halt.json + data/paper_trades.db]",
        f"  kalshi_drift: cycle_count={halt_state['cycle_count']} "
        f"halt={halt_state['halt']} "
        f"last_halt_at={halt_state['last_halt_at'] or 'null'} "
        f"threshold_abs={halt_state['threshold_abs']}",
        f"  p0_cohort   : deployed_ts={cohort_ts or 'null'} "
        f"(post-P0 replay rows: decision_ts >= deployed_ts; LD-7 / CR-F)",
        "",
    ]


def _format_segment_rate_lines(
    rows: list[dict[str, Any]],
    *,
    label_key: str,
    rate_key: str,
    top: int,
) -> list[str]:
    if not rows:
        return ["    (none)"]
    ordered = sorted(
        rows,
        key=lambda row: (
            -(row.get(rate_key) or 0.0),
            -(row.get("llm_rows") or 0),
            str(row.get(label_key) or "").lower(),
        ),
    )
    lines: list[str] = []
    for row in ordered[:top]:
        llm_rows = row.get("llm_rows", 0)
        lines.append(
            f"    {row.get(label_key) or 'n/a'}: rows={llm_rows} "
            f"meaningful={fmt_pct(row.get('meaningful_signals', 0), llm_rows)} "
            f"near_neutral={fmt_pct(row.get('near_neutral_outputs', 0), llm_rows)} "
            f"non_zero_edge={fmt_pct(row.get('non_zero_edge_outputs', 0), llm_rows)} "
            f"trade_candidate={fmt_pct(row.get('trade_candidates', 0), llm_rows)}"
        )
    return lines


def write_report_line(report_path: Path, text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))
        sys.stdout.buffer.flush()


def write_report(report_path: Path, lines: list[str]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_lines = [
        *lines,
        "",
        f"Daily review report saved to: {report_path}",
    ]
    tmp = report_path.with_suffix(report_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for line in output_lines:
            handle.write(line + "\n")
    os.replace(tmp, report_path)
    for line in output_lines:
        try:
            print(line)
        except UnicodeEncodeError:
            encoding = sys.stdout.encoding or "utf-8"
            sys.stdout.buffer.write((line + "\n").encode(encoding, errors="replace"))
            sys.stdout.buffer.flush()


def _top_source_rows(freshness_stats: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    rows = list(freshness_stats.get("sources", {}).values())
    rows.sort(
        key=lambda row: (
            -(row.get("early_stale_drops") or 0),
            -(row.get("observed_records") or 0),
            str(row.get("source") or "").lower(),
        )
    )
    return rows[:limit]


def _report_ollama_settings() -> tuple[str, str]:
    env_values: dict[str, str] = {}
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_values[key.strip()] = value.strip()
    model = str(os.getenv("OLLAMA_MODEL") or env_values.get("OLLAMA_MODEL") or "qwen2.5:7b").strip()
    base_url = str(
        os.getenv("OLLAMA_BASE_URL") or env_values.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
    ).strip()
    return model, base_url


def _ollama_tags_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/api/tags"


def _ollama_runtime_summary() -> str:
    configured, base_url = _report_ollama_settings()
    try:
        with urllib.request.urlopen(_ollama_tags_url(base_url), timeout=3) as response:
            payload = json.load(response)
        available = [item.get("name") for item in payload.get("models", []) if item.get("name")]
        if available:
            health = "ok" if configured in available else "mismatch"
            return f"configured={configured} health={health} available={available}"
        return f"configured={configured} health=unknown available=[]"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return f"configured={configured} health=unreachable detail={exc.__class__.__name__}"


def _build_tier_by_source(scorecard_stats: dict[str, Any]) -> dict[str, str]:
    """Flatten scorecard_stats['grouped'] into a {source: tier_key} map."""
    grouped = scorecard_stats.get("grouped") or {}
    tier_by_source: dict[str, str] = {}
    for tier_key, rows in grouped.items():
        if not rows:
            continue
        for row in rows:
            source = row.get("source") if isinstance(row, dict) else None
            if source:
                tier_by_source[source] = tier_key
    return tier_by_source


def _load_previous_tier_state(path: Path) -> dict[str, Any] | None:
    """Load the persisted tier-state JSON; return None if absent or corrupt."""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _save_current_tier_state(
    path: Path,
    tier_by_source: dict[str, str],
    now_utc: datetime,
) -> None:
    """Persist today's tier map with two-deep history (current + previous).

    Same-day reruns refresh `current` but preserve `previous` so the next
    distinct day still has yesterday's reference for the change diff.
    """
    today_str = now_utc.date().isoformat()
    current_block = {
        "date": today_str,
        "saved_at_utc": now_utc.isoformat(),
        "tiers": tier_by_source,
    }
    prior = _load_previous_tier_state(path)
    if prior is None or not isinstance(prior.get("current"), dict):
        new_state: dict[str, Any] = {"current": current_block, "previous": None}
    elif prior["current"].get("date") == today_str:
        new_state = {"current": current_block, "previous": prior.get("previous")}
    else:
        new_state = {"current": current_block, "previous": prior["current"]}

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(new_state, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _format_tier_change_lines(
    prior_tiers: dict[str, str] | None,
    current_tiers: dict[str, str],
    prior_date: str | None,
) -> list[str]:
    """Render the 'Status changes since previous report' sub-section.

    Reports two kinds of regressions on previously operationally-relevant sources:
      - Tier degraded (e.g., keep -> watch / investigate)
      - Source went silent (was tiered, today not in scorecard at all)

    Improvements and newly-active sources are intentionally not reported -- the
    section's purpose is operator triage of regressions.
    """
    if not prior_tiers:
        return ["  Status changes since previous report : (no prior baseline; first run or state reset)"]

    degraded: list[tuple[str, str, str]] = []
    silent: list[tuple[str, str]] = []
    for source, prior_tier in prior_tiers.items():
        if prior_tier not in _VISIBLE_TIERS:
            continue
        current_tier = current_tiers.get(source)
        if current_tier is None:
            silent.append((source, prior_tier))
            continue
        if _TIER_ORDER.get(current_tier, 99) > _TIER_ORDER.get(prior_tier, 99):
            degraded.append((source, prior_tier, current_tier))

    header = f"  Status changes since previous report (vs {prior_date or 'unknown'}):"
    if not degraded and not silent:
        return [header, "    (no tier regressions or new silences since previous report)"]

    degraded.sort(
        key=lambda triple: (
            -(_TIER_ORDER.get(triple[2], 99) - _TIER_ORDER.get(triple[1], 99)),
            triple[0].lower(),
        )
    )
    silent.sort(key=lambda pair: pair[0].lower())

    lines = [header]
    for source, prior_tier, current_tier in degraded:
        lines.append(f"    {source}: {prior_tier} -> {current_tier}")
    for source, prior_tier in silent:
        lines.append(f"    {source}: SILENT (was {prior_tier} in previous report, no records this window)")
    return lines


def build_daily_review(
    *,
    trades_path: Path,
    paper_db_path: Path,
    since: datetime | None,
    until: datetime | None,
    top: int,
    exclude_test: bool,
    show_progress: bool = False,
    show_profile: bool = False,
    tier_state_path: Path | None = None,
    mark_provider: paper_performance_drilldown.MarketProvider | None = None,
    mark_as_of: datetime | None = None,
) -> list[str]:
    _t0 = time.perf_counter()

    with stage_timer("freshness diagnostics", enabled=show_profile):
        freshness_stats = freshness_diagnostics.summarize(
            trades_path,
            since,
            until,
            exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("match quality diagnostics", enabled=show_profile):
        match_stats = match_quality_diagnostics.summarize(
            trades_path,
            since,
            until,
            exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("decision funnel summary", enabled=show_profile):
        funnel_stats = decision_funnel_summary.summarize(
            trades_path,
            since,
            until,
            exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("signal edge diagnostics", enabled=show_profile):
        edge_stats = signal_edge_diagnostics.summarize(
            trades_path,
            since,
            until,
            exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    throughput_until = until or datetime.now(timezone.utc)
    throughput_since = since or (throughput_until - timedelta(days=1))
    with stage_timer("operator throughput", enabled=show_profile):
        throughput_stats = summarize_operator_throughput_from_trade_log(
            trades_path,
            window_start=throughput_since,
            window_end=throughput_until,
            exclude_test=exclude_test,
        )
    with stage_timer("paper performance", enabled=show_profile):
        paper_stats = paper_performance_drilldown.summarize(
            paper_db_path,
            exclude_test=exclude_test,
            mark_provider=mark_provider,
            as_of=mark_as_of,
        )
    with stage_timer("matcher weight status", enabled=show_profile):
        matcher_weight_status = _matcher_weight_runtime_status()
    with stage_timer("match suppression audit", enabled=show_profile):
        suppression_stats = match_suppression_audit.summarize(
            trades_path,
            since,
            until,
            exclude_test=exclude_test,
        )
    with stage_timer("keyword feedback", enabled=show_profile):
        keyword_stats = keyword_feedback.summarize(
            trades_path,
            since,
            until,
            exclude_test=exclude_test,
        )
    with stage_timer("counterfactual LLM eval", enabled=show_profile):
        try:
            counterfactual_eval_stats = _build_counterfactual_llm_eval_report(
                trades_path,
                since,
                until,
                exclude_test=exclude_test,
            )
        except Exception as exc:  # reporting-only; never break daily review
            counterfactual_eval_stats = {
                "model_eval_status": "error",
                "target_counts": {},
                "error": f"{type(exc).__name__}: {exc}",
            }
    with stage_timer("source scorecard", enabled=show_profile):
        scorecard_stats = source_scorecard.summarize(
            trades_path,
            paper_db_path,
            since,
            until,
            exclude_test=exclude_test,
            # A(ii): judge tier recommendations over a wide horizon, not the 24h
            # display window -- "zero signals" is only meaningful over ~45d given
            # the bot emits ~1 SIGNAL/day across all sources. Lifetime-yield veto
            # (A(i)) is always on inside summarize.
            recommendation_window_days=source_scorecard.DEFAULT_RECOMMENDATION_WINDOW_DAYS,
        )
    with stage_timer("since-restart money path", enabled=show_profile):
        restart_ts = _latest_restart_timestamp_from_health_reports()
        log_boot_ts = _latest_log_boot_timestamp()
        since_restart_report = None
        if restart_ts:
            try:
                since_restart_report = since_restart_money_path.build_money_path_report(
                    trades_path,
                    since=restart_ts,
                    process_start=restart_ts,
                    log_boot=log_boot_ts,
                )
            except Exception as exc:  # reporting-only; never break daily review
                since_restart_report = {"error": f"{type(exc).__name__}: {exc}"}

    if show_profile:
        _eprint(f"[total] 11 stages completed in {time.perf_counter() - _t0:.1f}s")

    event_counts = funnel_stats.get("event_counts", {})
    analysis_rejections = Counter(funnel_stats.get("analysis_rejected_reasons", {}))
    assignment_shadow_stats = _summarize_fresh_pass_assignment_shadow(
        trades_path,
        since=since,
        until=until,
        exclude_test=exclude_test,
    )
    skip_breakdown = edge_stats.get("skip_breakdown", {})
    match_flags = Counter(match_stats.get("heuristic_flags", {}))
    llm_rows = sum(1 for row in edge_stats.get("audit_rows", []) if row.get("method") == "llm")
    keyword_rows = sum(1 for row in edge_stats.get("audit_rows", []) if row.get("method") == "keyword")
    keyword_gate_rows = sum(1 for row in edge_stats.get("audit_rows", []) if row.get("method") == "keyword_gate")
    suppressed = event_counts.get("MATCH_SUPPRESSED", 0)
    suppression_candidates = event_counts.get("MATCH_SUPPRESSION_CANDIDATE", 0)
    detail_rows = edge_stats.get("counts", {}).get("SIGNAL_ANALYSIS_DETAIL", 0)
    llm_attempted = edge_stats.get("llm_observability", {}).get("attempted", 0)
    opportunities = edge_stats.get("counts", {}).get("OPPORTUNITY", 0)
    executed = edge_stats.get("counts", {}).get("EXECUTED", 0)
    paper_trades = event_counts.get("PAPER_TRADE", 0)
    below_threshold = skip_breakdown.get("below_threshold", 0)
    zero_edge = skip_breakdown.get("zero_edge", 0)
    duplicate = skip_breakdown.get("duplicate", 0)
    liquidity_skip = skip_breakdown.get("liquidity", 0)
    risk_gate_skip = skip_breakdown.get("risk_gate", 0)
    other_skip = skip_breakdown.get("other", 0)

    lines: list[str] = []
    lines.append("PIPELINE REVIEW")
    lines.append(f"Software version                 : {_software_version()}")
    lines.append(f"Trades log: {trades_path}")
    lines.append(f"Paper DB : {paper_db_path}")
    if since or until:
        since_text = since.date().isoformat() if since else "(beginning)"
        until_text = until.date().isoformat() if until else "(latest)"
        lines.append(f"Date range: {since_text} -> {until_text}")
    lines.append(f"Records included: {fmt_int(funnel_stats.get('records_kept'))}")
    lines.append("")

    # P0 heartbeat — surface DriftCounter halt state + POST_FIX_NEW cohort
    # boundary at the top of the report so a fail-closed halt or a missing
    # cohort sentinel is visible before the operator wades through the
    # downstream analysis sections. Same data sources as
    # `scripts/botcheck.py::print_kalshi_drift_section` (P-9 / LD-14).
    halt_state, cohort_ts = _collect_kalshi_drift_state()
    lines.extend(_format_kalshi_drift_lines(halt_state, cohort_ts))

    # Gates & experiments: live-readiness (POST_FIX_NEW), the edge-prioritization
    # A/B verdict, and the rot-audit layer — the decision-relevant state added
    # since 2026-05 that the funnel sections below do not cover. Sourced from the
    # shared tasks/stats/observability_checkpoint (also used by pipeline_impact_audit).
    try:
        lines.extend(observability_checkpoint.summary_lines())
    except Exception as exc:  # observability must never break the daily report
        lines.append(f"GATES & EXPERIMENTS              : unavailable ({type(exc).__name__})")
    lines.append("")
    if since_restart_report:
        if "error" in since_restart_report:
            lines.append(f"SINCE-RESTART MONEY PATH          : unavailable ({since_restart_report['error']})")
        else:
            lines.extend(_format_since_restart_money_path_lines(since_restart_report))
        lines.append("")

    lines.extend(format_operator_throughput_lines(throughput_stats, top=top))
    lines.append("")

    lines.append("1. INGESTION  [source: scripts/freshness_diagnostics.py + scripts/decision_funnel_summary.py]")
    observed_records = sum(row.get("observed_records", 0) for row in freshness_stats.get("sources", {}).values())
    fresh_passes = sum(row.get("fresh_passes", 0) for row in freshness_stats.get("sources", {}).values())
    early_stale = sum(row.get("early_stale_drops", 0) for row in freshness_stats.get("sources", {}).values())
    lines.append(f"  Source-attributed items observed : {observed_records}")
    lines.append(f"  Fresh passes                     : {fresh_passes}")
    lines.append(f"  Dropped early stale              : {early_stale}")
    lines.append(f"  Rejected stale at analysis       : {analysis_rejections.get('stale_news', 0)}")
    lines.extend(
        _format_fresh_pass_conversion_lines(
            fresh_passes=fresh_passes,
            match_records=match_stats.get("match_records", 0),
            detail_rows=detail_rows,
            llm_attempted=llm_attempted,
            opportunities=opportunities,
            paper_trades=paper_trades,
        )
    )
    attribution = funnel_stats.get("same_window_lifecycle_attribution")
    if isinstance(attribution, dict):
        lines.extend(
            _format_same_window_lifecycle_attribution_lines(
                attribution,
                since=since,
                until=until,
            )
        )
    lines.extend(_format_match_attribution_lines(funnel_stats, top=top))
    if assignment_shadow_stats.get("rows", 0) > 0:
        lines.append(
            "  Fresh assignment shadow         : "
            f"{assignment_shadow_stats.get('assigned', 0)} assigned, "
            f"{assignment_shadow_stats.get('unassigned', 0)} unassigned, "
            f"{assignment_shadow_stats.get('malformed', 0)} malformed"
        )
        top_unassigned = assignment_shadow_stats.get("top_unassigned_sources", Counter())
        if top_unassigned:
            lines.append("  Drilldown: unassigned fresh-pass sources")
            for line in format_counter(top_unassigned, top=top):
                lines.append(line)
    top_stale_sources = _top_source_rows(freshness_stats, limit=top)
    if top_stale_sources:
        lines.append("  Drilldown: top stale sources")
        for row in top_stale_sources:
            lines.append(
                f"    {row['source']}: early_stale={row['early_stale_drops']} "
                f"fresh_pass={row['fresh_passes']} observed={row['observed_records']}"
            )
    tier_by_source = _build_tier_by_source(scorecard_stats)

    if tier_state_path is not None:
        prior_state = _load_previous_tier_state(tier_state_path) or {}
        prior_previous = prior_state.get("previous") or {}
        prior_tiers = prior_previous.get("tiers") if isinstance(prior_previous, dict) else None
        prior_date = prior_previous.get("date") if isinstance(prior_previous, dict) else None
        lines.extend(_format_tier_change_lines(prior_tiers, tier_by_source, prior_date))

    eligible_freshness_rows = [
        row for row in freshness_stats.get("sources", {}).values() if row.get("observed_records", 0) >= 1
    ]
    if eligible_freshness_rows:
        buckets = freshness_diagnostics.bucket_sources(eligible_freshness_rows, show_all=True)
        # Exclude `dead` and `insufficient` buckets from the waterfall up-front
        # -- a source with stale_rate >= 99.9% AND no fresh passes (or fewer
        # samples than the strong-ranking threshold) is operational noise.
        # The regression section + scorecard tiers cover the cases where a
        # previously-active source drops into one of these buckets.
        waterfall_rows = (
            buckets.get("fast_operational", [])
            + buckets.get("near_threshold", [])
            + buckets.get("chronically_late", [])
        )

        # Filter to operationally-relevant tiers; sources in {prune, remove
        # immediately, disabled} are folded into a one-line summary referencing
        # section 8. Sources not present in the scorecard at all are shown
        # (defensive: we'd rather over-report than silently drop unclassified
        # sources during edge-case data gaps).
        visible_rows: list[dict[str, Any]] = []
        hidden_counts: dict[str, int] = {tier: 0 for tier in _HIDDEN_TIERS}
        hidden_counts["dead"] = len(buckets.get("dead", []))
        hidden_counts["insufficient data"] = len(buckets.get("insufficient", []))
        for row in waterfall_rows:
            tier = tier_by_source.get(row.get("source", ""))
            if tier in _HIDDEN_TIERS:
                hidden_counts[tier] += 1
            else:
                visible_rows.append(row)

        if visible_rows or any(hidden_counts.values()):
            lines.append("  Drilldown: per-source freshness waterfall (operationally-relevant tiers only)")
            if visible_rows:
                for line in freshness_diagnostics.format_compact_rows(visible_rows, top=len(visible_rows)):
                    lines.append("  " + line)
            else:
                lines.append("    (no operationally-relevant sources in window)")
            total_hidden = sum(hidden_counts.values())
            if total_hidden > 0:
                breakdown = ", ".join(f"{tier}={count}" for tier, count in hidden_counts.items() if count > 0)
                lines.append(
                    f"    {total_hidden} lower-relevance sources hidden ({breakdown}; "
                    "see Section 8 Source Scorecard or run scripts/freshness_diagnostics.py for the full list)"
                )
    lines.append("")

    lines.append("2. MATCHING  [source: scripts/match_quality_diagnostics.py + scripts/match_suppression_audit.py]")
    match_records = match_stats.get("match_records", 0)
    low_quality = match_stats.get("low_quality_matches", 0)
    pre_llm_would_block = match_stats.get("pre_llm_would_block", 0)
    pre_llm_useful = edge_stats.get("llm_observability", {}).get("pre_llm_would_block_and_useful", 0)
    lines.append(f"  Match diagnostics emitted        : {match_records}")
    lines.append(f"  Low-quality flagged              : {low_quality} ({fmt_pct(low_quality, match_records)})")
    lines.append(
        f"  Pre-LLM gate would-block         : {pre_llm_would_block} ({fmt_pct(pre_llm_would_block, match_records)})"
    )
    lines.append(f"  Pre-LLM would-block useful rows : {pre_llm_useful}")
    lines.append(f"  Suppression candidates           : {suppression_candidates}")
    lines.append(f"  Suppressed                       : {suppressed}")
    if match_flags:
        lines.append("  Drilldown: top heuristic flags")
        for label, count in match_flags.most_common(top):
            lines.append(f"    {label}: {count}")
    pre_llm_by_source = match_stats.get("pre_llm_would_block_by_source", Counter())
    matcher_status_label = str(matcher_weight_status.get("status") or "unknown").upper()
    lines.append(f"  Matcher weights runtime status   : {matcher_status_label}")
    if matcher_weight_status.get("reason"):
        lines.append(f"    {matcher_weight_status['reason']}")
    if pre_llm_by_source:
        lines.append("  Drilldown: pre-LLM would-block by source (top)")
        for label, count in pre_llm_by_source.most_common(top):
            lines.append(f"    {label}: {count}")
    pre_llm_by_ticker = match_stats.get("pre_llm_would_block_by_ticker", Counter())
    if pre_llm_by_ticker:
        lines.append("  Drilldown: pre-LLM would-block by market (top)")
        for label, count in pre_llm_by_ticker.most_common(top):
            lines.append(f"    {label}: {count}")
    examples_bad = match_stats.get("examples_bad", [])[: min(top, RECENT_MATCH_EXAMPLES)]
    if examples_bad:
        lines.append("  Drilldown: recent low-quality examples")
        for row in examples_bad:
            lines.append(
                f"    {row['ticker']} score={fmt_prob(row.get('match_score'))} "
                f"flags={','.join(row.get('heuristic_flags') or []) or 'n/a'} "
                f"headline={row.get('headline', '')[:70]}"
            )
    suppression_total = suppression_stats.get("total_candidates", 0)
    suppression_safe = suppression_stats.get("safe_count", 0)
    suppression_risky = suppression_stats.get("risky_count", 0)
    lines.append(f"  Suppression candidates (audited) : {suppression_total}")
    lines.append(
        f"    Likely safe to suppress        : {suppression_safe} ({fmt_pct(suppression_safe, suppression_total)})"
    )
    lines.append(
        f"    Likely risky to suppress       : {suppression_risky} ({fmt_pct(suppression_risky, suppression_total)})"
    )
    if suppression_total:
        risky_by_source = sorted(
            (
                {"source": src, "risky": counts.get("risky", 0)}
                for src, counts in suppression_stats.get("by_source", {}).items()
                if counts.get("risky", 0) > 0
            ),
            key=lambda row: -row["risky"],
        )[:top]
        if risky_by_source:
            lines.append("  Drilldown: top risky-suppression sources")
            for row in risky_by_source:
                lines.append(f"    {row['source'] or 'n/a'}: risky={row['risky']}")
    lines.append("")

    lines.append(
        "3. ANALYSIS  [source: scripts/decision_funnel_summary.py + scripts/signal_edge_diagnostics.py + scripts/keyword_feedback.py]"
    )
    total_rejections = sum(analysis_rejections.values())
    lines.append(f"  Signal analysis detail rows      : {detail_rows}")
    lines.append(f"  Passed keyword gate              : {detail_rows - keyword_gate_rows}")
    lines.append(f"  Keyword-gate exits               : {keyword_gate_rows}")
    lines.append(f"  Keyword fallback rows            : {keyword_rows}")
    lines.append(f"  LLM rows                         : {llm_rows}")
    llm_obs = edge_stats.get("llm_observability", {})
    llm_attempted = llm_obs.get("attempted", 0)
    llm_result_used = llm_obs.get("result_used", 0)
    llm_fallback = llm_obs.get("fallback", 0)
    llm_status_counts = llm_obs.get("status_counts", Counter())
    if llm_attempted:
        success_rate = f"{llm_result_used / llm_attempted * 100:.0f}%"
    else:
        success_rate = "n/a"
    lines.append(f"  LLM attempted                    : {llm_attempted}")
    lines.append(f"  LLM result used                  : {llm_result_used}  (success rate: {success_rate})")
    lines.append(f"  LLM fallback to keyword          : {llm_fallback}")
    lines.append(f"  Ollama runtime                   : {_ollama_runtime_summary()}")
    latency_samples = llm_obs.get("latency_ms_samples", [])
    lines.append(f"  LLM latency                      : {signal_edge_diagnostics.fmt_latency_stats(latency_samples)}")
    lines.append(
        "  LLM total stage                  : "
        f"{signal_edge_diagnostics.fmt_latency_stats(llm_obs.get('total_stage_ms_samples', []))}"
    )
    lines.append(
        "  LLM queue wait                   : "
        f"{signal_edge_diagnostics.fmt_latency_stats(llm_obs.get('queue_wait_ms_samples', []))}"
    )
    lines.append(
        "  LLM HTTP round-trip              : "
        f"{signal_edge_diagnostics.fmt_latency_stats(llm_obs.get('http_round_trip_ms_samples', []))}"
    )
    lines.append(
        "  LLM parse time                   : "
        f"{signal_edge_diagnostics.fmt_latency_stats(llm_obs.get('parse_ms_samples', []))}"
    )
    lines.append(f"  LLM contention observed          : {llm_obs.get('contention_observed', 0)}")
    lines.append(f"  LLM max in-flight at entry       : {llm_obs.get('max_in_flight_at_entry', 0)}")
    if llm_status_counts:
        lines.append("  LLM status breakdown:")
        for status, count in llm_status_counts.most_common():
            lines.append(f"    {status}: {count}")
    lines.append(f"  Analysis rejections total        : {total_rejections}")
    for reason, count in analysis_rejections.most_common(top):
        lines.append(f"    rejected[{reason}] = {count}")
    no_keyword_misses = keyword_stats.get("no_keyword_misses", 0)
    corroborating_rows = keyword_stats.get("corroborating_keyword_gate_records", 0)
    unique_phrases = keyword_stats.get("unique_candidate_phrases", 0)
    lines.append(f"  No-keyword analysis exits       : {no_keyword_misses}")
    lines.append(f"  Keyword-gate corroborating rows  : {corroborating_rows}")
    lines.append(
        "  Empty-keyword LLM detail rows   : "
        f"directional={keyword_stats.get('empty_keyword_llm_directional_rows', 0)} "
        f"neutral={keyword_stats.get('empty_keyword_llm_neutral_rows', 0)}"
    )
    lines.append(f"  Unique candidate phrases         : {unique_phrases}")
    lines.extend(_format_counterfactual_llm_eval_lines(counterfactual_eval_stats))
    no_keyword_categories = keyword_stats.get("no_keyword_rejection_categories") or Counter()
    if no_keyword_categories:
        lines.append("  Drilldown: no-keyword rejection branches")
        for category, count in no_keyword_categories.most_common(top):
            lines.append(f"    {category}: {count}")
    top_no_keyword_sources = keyword_stats.get("top_no_keyword_sources") or []
    if top_no_keyword_sources:
        lines.append("  Drilldown: top no-keyword analysis-exit sources")
        for source, count in top_no_keyword_sources[:top]:
            lines.append(f"    {source}: {count}")
    top_no_keyword_tickers = keyword_stats.get("top_no_keyword_tickers") or []
    if top_no_keyword_tickers:
        lines.append("  Drilldown: top no-keyword analysis-exit tickers")
        for ticker, count in top_no_keyword_tickers[:top]:
            lines.append(f"    {ticker}: {count}")
    strongest = keyword_stats.get("grouped_phrases", {}).get("strongest specific candidates", [])
    if strongest:
        lines.append("  Drilldown: strongest keyword-gate miss candidates")
        for row in strongest[:top]:
            lines.append(
                f"    {row.get('phrase')}: count={row.get('count', 0)} "
                f"sources={len(row.get('sources') or [])} "
                f"tickers={len(row.get('tickers') or [])} "
                f"category={row.get('category') or 'n/a'}"
            )
    lines.append("")

    lines.append("4. EDGE FORMATION  [source: scripts/signal_edge_diagnostics.py]")
    lines.append(f"  Opportunities created            : {opportunities}")
    lines.append(f"  Below min edge                   : {below_threshold}")
    lines.append(f"  Zero edge                        : {zero_edge}")
    lines.append(f"  Above min edge / reached execute : {executed}")
    recent_edge_rows = edge_stats.get("audit_rows", [])[: min(top, RECENT_EDGE_AUDIT)]
    if recent_edge_rows:
        lines.append("  Drilldown: recent edge audit")
        for row in recent_edge_rows:
            lines.append(
                f"    {fmt_ts(row.get('ts'))} {row.get('ticker') or 'n/a'} "
                f"est={fmt_prob(row.get('estimated_probability'))} "
                f"market={fmt_prob(row.get('market_price'))} "
                f"edge={fmt_prob(row.get('edge'))} outcome={row.get('outcome') or 'n/a'}"
            )
    lines.append("")

    lines.append(
        "5. PAPER TRADES AND LIVE SUBMISSIONS  "
        "[source: scripts/decision_funnel_summary.py + scripts/paper_performance_drilldown.py]"
    )
    live_orders = event_counts.get("LIVE_ORDER", 0)
    lines.append(f"  Paper-trade records              : {paper_trades}")
    lines.append(f"  Live order submissions           : {live_orders}")
    lines.append("  Scope: live order submissions are not fill or P&L evidence.")
    lines.append(f"  Skipped duplicate position       : {duplicate}")
    lines.append(f"  Skipped liquidity/near-limit     : {liquidity_skip}")
    lines.append(f"  Skipped risk gate                : {risk_gate_skip}")
    lines.append(f"  Skipped below threshold          : {below_threshold}")
    lines.append(f"  Skipped other                    : {other_skip}")
    if paper_stats.get("total_trades", 0):
        lines.append("  Drilldown: paper performance")
        lines.append(f"    Total trades: {paper_stats['total_trades']}")
        lines.append(f"    Resolved: {paper_stats['resolved_trades']}")
        lines.append(f"    Open: {paper_stats['open_trades']}")
        lines.append(f"    Win rate: {paper_performance_drilldown.fmt_pct(paper_stats.get('win_rate'))}")
        lines.append(f"    Total P&L: {fmt_money(paper_stats.get('total_pnl'))}")
        open_mark = paper_stats.get("open_mark_summary") or {}
        liquidation = paper_stats.get("executable_liquidation") or {}
        if open_mark:
            lines.append(f"    Open cost                        : {fmt_money(open_mark.get('open_cost_dollars'))}")
            if liquidation:
                lines.append(f"    Gross executable bid value       : {fmt_money(liquidation.get('gross_bid_value'))}")
                lines.append(
                    f"    Estimated exit fees              : {fmt_money(liquidation.get('estimated_exit_fees'))}"
                )
                lines.append(
                    "    Fee-net liquidation value        : "
                    f"{fmt_money(liquidation.get('report_net_liquidation_value'))}"
                )
                lines.append(
                    f"    Unrealized fee-net P&L           : {fmt_money(liquidation.get('unrealized_fee_net_pnl'))}"
                )
                lines.append(f"    Unscorable liquidation cost      : {fmt_money(liquidation.get('unscorable_cost'))}")
                reasons = liquidation.get("unscorable_reasons") or {}
                if reasons:
                    lines.append(
                        "    Unscorable reasons                : "
                        + ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))
                    )
                lines.append(f"    Executable snapshot as of         : {liquidation.get('as_of') or 'n/a'}")
                for row in liquidation.get("by_venue") or []:
                    lines.append(
                        f"    {row.get('venue') or 'unknown'} "
                        f"gross={fmt_money(row.get('gross_bid_value'))} "
                        f"fees={fmt_money(row.get('estimated_exit_fees'))} "
                        f"net={fmt_money(row.get('report_net_liquidation_value'))} "
                        f"unscorable={fmt_money(row.get('unscorable_cost'))}"
                    )
                schedules = liquidation.get("fee_schedule_hashes") or {}
                for venue, schedule in sorted(schedules.items()):
                    lines.append(
                        f"    Fee schedule {venue}: "
                        f"{schedule.get('name') or 'unknown'} "
                        f"sha256={schedule.get('artifact_sha256') or 'unknown'}"
                    )
            else:
                lines.append(
                    "    Marked Kalshi bid value          : "
                    f"{fmt_money(open_mark.get('marked_kalshi_bid_value_dollars'))}"
                )
                lines.append(
                    "    Marked Kalshi unrealized P&L     : "
                    f"{fmt_money(open_mark.get('marked_kalshi_unrealized_pnl_dollars'))}"
                )
                lines.append(
                    f"    Unknown mark cost                : {fmt_money(open_mark.get('unknown_mark_cost_dollars'))}"
                )
        venue_rows = paper_stats.get("venues") or []
        if len(venue_rows) > 1 or any(str(row.get("name") or "") != "kalshi" for row in venue_rows):
            lines.append("  Drilldown: paper performance by venue")
            for row in venue_rows:
                resolved = row.get("resolved") or 0
                pnl_display = fmt_money(row.get("pnl")) if resolved else "n/a"
                lines.append(
                    f"    {row.get('name') or 'kalshi'}: "
                    f"trades={row.get('trades', 0)} "
                    f"resolved={resolved} "
                    f"win_rate={paper_performance_drilldown.fmt_pct(row.get('win_rate'))} "
                    f"pnl={pnl_display}"
                )
        bucket_rows = paper_stats.get("open_resolution_buckets") or []
        if bucket_rows:
            lines.append("  Drilldown: open exposure by resolution horizon")
            for row in bucket_rows[:top]:
                lines.append(
                    f"    {row.get('bucket') or 'unknown'} "
                    f"venue={row.get('venue') or 'kalshi'} "
                    f"trades={row.get('trades', 0)} "
                    f"exposure={paper_performance_drilldown.fmt_bucket_exposure(row.get('exposure'))}"
                )
        full_loss_rows = paper_stats.get("high_confidence_full_losses") or []
        lines.append(f"  High-confidence full-loss rows   : {len(full_loss_rows)}")
        for row in full_loss_rows[:top]:
            chosen_probability = row.get("chosen_side_probability", row.get("estimated_prob"))
            lines.append(
                "    "
                f"{row.get('ticker') or 'n/a'} "
                f"venue={row.get('venue') or 'kalshi'} "
                f"pnl={fmt_money(row.get('pnl_dollars'))} "
                f"cost={_fmt_unsigned_money(row.get('cost_dollars'))} "
                f"p={_fmt_probability(chosen_probability)} "
                f"entry={row.get('entry_price_cents') if row.get('entry_price_cents') is not None else 'n/a'}c "
                f"llm_conf={_fmt_probability(row.get('llm_confidence'))} "
                f"source={row.get('signal_source') or 'n/a'}"
            )
    lines.append("")

    llm_value = edge_stats.get("llm_value_add", {})
    llm_observability = edge_stats.get("llm_observability", {})
    llm_rows = llm_value.get("llm_rows", 0)
    lines.append("6. LLM VALUE-ADD ANALYSIS  [source: scripts/signal_edge_diagnostics.py]")
    lines.append(f"  Total LLM rows                    : {llm_rows}")
    lines.append(f"  LLM attempted (post-filter)       : {llm_observability.get('attempted', 0)}")
    lines.append(f"  LLM skipped (routing)             : {llm_observability.get('skipped_routing', 0)}")
    routing_skip_reasons = llm_observability.get("skipped_routing_reasons", Counter())
    if routing_skip_reasons:
        lines.append(
            "  Routing skip reasons              : "
            + ", ".join(f"{reason}={count}" for reason, count in routing_skip_reasons.most_common())
        )
    lines.append(
        f"  Near-neutral outputs              : {llm_value.get('near_neutral_outputs', 0)} ({fmt_pct(llm_value.get('near_neutral_outputs', 0), llm_rows)})"
    )
    lines.append(
        f"  Non-zero edge outputs             : {llm_value.get('non_zero_edge_outputs', 0)} ({fmt_pct(llm_value.get('non_zero_edge_outputs', 0), llm_rows)})"
    )
    lines.append(
        f"  Meaningful signals                : {llm_value.get('meaningful_signals', 0)} ({fmt_pct(llm_value.get('meaningful_signals', 0), llm_rows)})"
    )
    lines.append(
        f"  Model gross-edge candidates (fee unscored): {llm_value.get('trade_candidates', 0)} ({fmt_pct(llm_value.get('trade_candidates', 0), llm_rows)})"
    )
    lines.append(f"  Candidates admitted               : {llm_value.get('admitted_trade_candidates', 0)}")
    lines.append(f"  Candidates blocked by gates       : {llm_value.get('blocked_trade_candidates', 0)}")
    lines.append(f"  Candidates pending                : {llm_value.get('pending_trade_candidates', 0)}")
    lines.append(
        f"  LLM created edge @0.50 market     : {llm_value.get('llm_created_edge', 0)} ({fmt_pct(llm_value.get('llm_created_edge', 0), llm_rows)})"
    )
    lines.append(
        "  Probability movement buckets      : "
        + fmt_counter_line(
            llm_value.get("probability_movement_buckets", Counter()),
            [
                ("near_neutral", "near-neutral"),
                ("weak", "weak"),
                ("moderate", "moderate"),
                ("strong", "strong"),
            ],
        )
    )
    lines.append(
        "  Edge magnitude buckets            : "
        + fmt_counter_line(
            llm_value.get("edge_magnitude_buckets", Counter()),
            [
                ("zero_neutral", "zero/neutral"),
                ("weak", "weak"),
                ("moderate", "moderate"),
                ("strong", "strong"),
            ],
        )
    )
    meaningful_sources = llm_value.get("meaningful_sources", Counter())
    if meaningful_sources:
        lines.append("  Top meaningful sources")
        for label, count in meaningful_sources.most_common(top):
            lines.append(f"    {label}: {count}")
    meaningful_tickers = llm_value.get("meaningful_tickers", Counter())
    if meaningful_tickers:
        lines.append("  Top meaningful tickers")
        for label, count in meaningful_tickers.most_common(top):
            lines.append(f"    {label}: {count}")
    strong_examples = llm_value.get("strong_examples", [])[: min(top, RECENT_EDGE_AUDIT)]
    if strong_examples:
        lines.append("  Strong LLM signal examples")
        for row in strong_examples:
            lines.append(
                f"    {fmt_ts(row.get('ts'))} {row.get('ticker') or 'n/a'} "
                f"est={fmt_prob(row.get('estimated_probability'))} "
                f"market={fmt_prob(row.get('market_price'))} "
                f"edge={fmt_prob(row.get('edge'))} "
                f"impact={row.get('llm_decision_impact') or 'n/a'}"
            )
    neutral_examples = llm_value.get("neutral_examples", [])[: min(top, RECENT_EDGE_AUDIT)]
    if neutral_examples:
        lines.append("  Neutral confirmation examples")
        for row in neutral_examples:
            lines.append(
                f"    {fmt_ts(row.get('ts'))} {row.get('ticker') or 'n/a'} "
                f"est={fmt_prob(row.get('estimated_probability'))} "
                f"market={fmt_prob(row.get('market_price'))} "
                f"edge={fmt_prob(row.get('edge'))}"
            )
    lines.append("")

    llm_segmentation = llm_value.get("segmentation", {})
    lines.append("7. LLM VALUE-ADD SEGMENTATION  [source: scripts/signal_edge_diagnostics.py]")
    lines.append("  Top sources by meaningful signal rate")
    lines.extend(
        _format_segment_rate_lines(
            llm_segmentation.get("by_source", []),
            label_key="source",
            rate_key="meaningful_signal_rate",
            top=top,
        )
    )
    lines.append("  Top sources by neutral-confirmation rate")
    lines.extend(
        _format_segment_rate_lines(
            llm_segmentation.get("by_source", []),
            label_key="source",
            rate_key="neutral_confirmation_rate",
            top=top,
        )
    )
    lines.append("  Top tickers by meaningful signal rate")
    lines.extend(
        _format_segment_rate_lines(
            llm_segmentation.get("by_ticker", []),
            label_key="ticker",
            rate_key="meaningful_signal_rate",
            top=top,
        )
    )
    lines.append("  Market price bands by meaningful signal rate")
    lines.extend(
        _format_segment_rate_lines(
            llm_segmentation.get("by_price_band", []),
            label_key="price_band",
            rate_key="meaningful_signal_rate",
            top=max(1, top),
        )
    )
    timing_segmentation = llm_segmentation.get("timing", {})
    if timing_segmentation.get("available"):
        lines.append("  Freshness/timing segmentation      : available")
    else:
        lines.append(f"  Freshness/timing segmentation      : unavailable ({timing_segmentation.get('reason', 'n/a')})")
    category_segmentation = llm_segmentation.get("headline_category", {})
    if category_segmentation.get("available"):
        lines.append("  Headline/category segmentation     : available")
    else:
        lines.append(
            f"  Headline/category segmentation     : unavailable ({category_segmentation.get('reason', 'n/a')})"
        )
    rare_examples = llm_value.get("rare_non_neutral_examples", [])[: min(top, RECENT_EDGE_AUDIT)]
    if rare_examples:
        lines.append("  Rare non-neutral cases")
        for row in rare_examples:
            lines.append(
                f"    {fmt_ts(row.get('ts'))} {row.get('ticker') or 'n/a'} "
                f"source={row.get('source') or 'n/a'} "
                f"est={fmt_prob(row.get('estimated_probability'))} "
                f"market={fmt_prob(row.get('market_price'))} "
                f"edge={fmt_prob(row.get('edge'))} "
                f"impact={row.get('llm_decision_impact') or 'n/a'}"
            )
    lines.append("")

    lines.append("8. SOURCE SCORECARD  [source: scripts/source_scorecard.py]")
    grouped = scorecard_stats.get("grouped", {}) or {}
    log_meta = scorecard_stats.get("log_meta", {}) or {}
    scored_rows = scorecard_stats.get("rows", []) or []
    lines.append(f"  Sources scored (window)          : {len(scored_rows)}")
    lines.append(f"  Log records attributed           : {log_meta.get('records_kept', 0)}")
    lines.append(f"  Paper DB available               : {'yes' if scorecard_stats.get('db_exists') else 'no'}")
    for tier_label, tier_key in (
        ("Top performers", "top performers"),
        ("Keep", "keep"),
        ("Watch / investigate", "watch / investigate"),
        ("Incubating", "incubating"),
        ("Prune", "prune"),
        ("Remove immediately", "remove immediately"),
    ):
        rows = grouped.get(tier_key, []) or []
        lines.append(f"  {tier_label:<32} : {len(rows)}")
        if rows:
            for row in rows[:top]:
                resolved = row.get("resolved_paper_trades") or 0
                pnl_display = fmt_money(row.get("total_pnl")) if resolved else "n/a"
                lines.append(
                    f"    {row.get('source') or 'n/a'}: "
                    f"obs={row.get('observed_records', 0)} "
                    f"signals={row.get('signals', 0)} "
                    f"paper={row.get('paper_trades', 0)} "
                    f"resolved={resolved} "
                    f"win_rate={paper_performance_drilldown.fmt_pct(row.get('win_rate'))} "
                    f"pnl={pnl_display} "
                    # B: lifetime funnel beside each tier verdict, so a "remove
                    # immediately" recommendation is always shown with the source's
                    # real all-time yield (posts -> signals -> opportunities -> trades).
                    f"| life: posts={row.get('lifetime_posts', 0)} "
                    f"sig={row.get('lifetime_signals', 0)} "
                    f"opp={row.get('lifetime_opportunities', 0)} "
                    f"trade={row.get('lifetime_trades', 0)}"
                )
    disabled_source = grouped.get("disabled by source", []) or []
    disabled_family = grouped.get("disabled by family", []) or []
    lines.append(f"  Disabled by config               : source={len(disabled_source)} family={len(disabled_family)}")
    lines.append("")

    lines.append("Appendix")
    lines.append("  Sections above are sourced directly from the listed scripts; run any of them")
    lines.append("  standalone for the full, unfiltered output.")
    lines.append("")
    lines.append("  Integrated into this report (see [source: ...] tags above):")
    lines.append("    scripts/freshness_diagnostics.py           -- section 1")
    lines.append("    scripts/match_quality_diagnostics.py       -- section 2")
    lines.append("    scripts/match_suppression_audit.py         -- section 2")
    lines.append("    scripts/decision_funnel_summary.py         -- sections 1, 3, 5")
    lines.append("    scripts/signal_edge_diagnostics.py         -- sections 3, 4, 6, 7")
    lines.append("    scripts/keyword_feedback.py                -- section 3")
    lines.append("    scripts/paper_performance_drilldown.py     -- section 5")
    lines.append("    scripts/source_scorecard.py                -- section 8")
    lines.append("")
    lines.append("  Deep-dive scripts (run separately when investigating):")
    lines.append("    scripts/trade_log_summary.py               -- raw event/skip/rejection counts")
    lines.append("    scripts/performance_analysis.py            -- full trade-history P&L + calibration")
    lines.append("    scripts/pipeline_impact_audit.py           -- before/after pipeline change audit")
    lines.append("    scripts/replay_dossier.py                  -- dossier trajectory replay")
    lines.append("    scripts/regime_weight_validation.py        -- S4.4 regime weight back-check")
    lines.append("    scripts/observability_completeness_review.py -- observability gap audit")
    lines.append("    scripts/ollama_error_audit.py              -- Ollama HTTP error classification")
    lines.append("    scripts/keyword_shadow_eval.py             -- shadow-eval of candidate keywords")
    lines.append("    scripts/keyword_promotion_report.py        -- keyword promotion governance")
    lines.append("    scripts/botcheck.py                        -- macOS LaunchAgent status report")

    # Persist today's tier baseline so tomorrow's report can compute the diff.
    # Reporting-only feature: log to stderr on IO failure, never raise.
    if tier_state_path is not None:
        try:
            _save_current_tier_state(tier_state_path, tier_by_source, datetime.now(timezone.utc))
        except OSError as exc:
            _eprint(
                f"warning: failed to persist source tier state to {tier_state_path}: {exc.__class__.__name__}: {exc}"
            )

    return lines


def main() -> int:
    args = parse_args()
    try:
        since_input = parse_date_start(args.since)
        until_input = parse_date_end(args.until)
    except ValueError as exc:
        raise SystemExit(f"Invalid date: {exc}") from exc

    since, until, _ = resolve_recent_window(since_input, until_input)

    if since and until and since > until:
        raise SystemExit("--since must be on or before --until")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = DEFAULT_REPORT_PATH
    mark_as_of = datetime.now(timezone.utc)
    lines = build_daily_review(
        trades_path=Path(args.path),
        paper_db_path=DEFAULT_PAPER_DB_PATH,
        since=since,
        until=until,
        top=max(1, args.top),
        exclude_test=args.exclude_test,
        show_progress=args.progress,
        show_profile=args.profile,
        tier_state_path=SOURCE_TIER_STATE_PATH,
        mark_provider=paper_performance_drilldown.PublicMarketProvider(),
        mark_as_of=mark_as_of,
    )

    write_report(report_path, lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
