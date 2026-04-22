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
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from typing import Any

from scripts import decision_funnel_summary
from scripts import freshness_diagnostics
from scripts import match_quality_diagnostics
from scripts import paper_performance_drilldown
from scripts import signal_edge_diagnostics
from utils.reporting_helpers import (
    DEFAULT_CURRENT_STATE_WINDOW_HOURS,
    ProgressTracker,
    resolve_recent_window,
    stage_timer,
    _eprint,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "logs" / "reports"
DEFAULT_REPORT_PATH = REPORTS_DIR / f"daily_review_{datetime.now().strftime('%Y%m%d')}.txt"
# Default to the active live file -- daily review is a current-state report.
# Pass --path logs/trades to include archive data for historical analysis.
DEFAULT_TRADES_LOG_PATH = REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl"
DEFAULT_PAPER_DB_PATH = REPO_ROOT / "data" / "paper_trades.db"
RECENT_MATCH_EXAMPLES = 5
RECENT_EDGE_AUDIT = 5


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
    if not isinstance(value, (int, float)):
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
    with report_path.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


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
    model = str(
        os.getenv("OLLAMA_MODEL")
        or env_values.get("OLLAMA_MODEL")
        or "qwen2.5:7b"
    ).strip()
    base_url = str(
        os.getenv("OLLAMA_BASE_URL")
        or env_values.get("OLLAMA_BASE_URL")
        or "http://localhost:11434/v1"
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
        available = [
            item.get("name")
            for item in payload.get("models", [])
            if item.get("name")
        ]
        if available:
            health = "ok" if configured in available else "mismatch"
            return f"configured={configured} health={health} available={available}"
        return f"configured={configured} health=unknown available=[]"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return f"configured={configured} health=unreachable detail={exc.__class__.__name__}"


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
) -> list[str]:
    _t0 = time.perf_counter()

    with stage_timer("freshness diagnostics", enabled=show_profile):
        freshness_stats = freshness_diagnostics.summarize(
            trades_path, since, until, exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("match quality diagnostics", enabled=show_profile):
        match_stats = match_quality_diagnostics.summarize(
            trades_path, since, until, exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("decision funnel summary", enabled=show_profile):
        funnel_stats = decision_funnel_summary.summarize(
            trades_path, since, until, exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("signal edge diagnostics", enabled=show_profile):
        edge_stats = signal_edge_diagnostics.summarize(
            trades_path, since, until, exclude_test=exclude_test,
            progress_tracker=ProgressTracker() if show_progress else None,
        )
    with stage_timer("paper performance", enabled=show_profile):
        paper_stats = paper_performance_drilldown.summarize(
            paper_db_path, exclude_test=exclude_test
        )

    if show_profile:
        _eprint(f"[total] 5 stages completed in {time.perf_counter() - _t0:.1f}s")

    event_counts = funnel_stats.get("event_counts", {})
    analysis_rejections = Counter(funnel_stats.get("analysis_rejected_reasons", {}))
    skip_breakdown = edge_stats.get("skip_breakdown", {})
    match_flags = Counter(match_stats.get("heuristic_flags", {}))
    llm_rows = sum(
        1 for row in edge_stats.get("audit_rows", [])
        if row.get("method") == "llm"
    )
    keyword_rows = sum(
        1 for row in edge_stats.get("audit_rows", [])
        if row.get("method") == "keyword"
    )
    keyword_gate_rows = sum(
        1 for row in edge_stats.get("audit_rows", [])
        if row.get("method") == "keyword_gate"
    )
    suppressed = event_counts.get("MATCH_SUPPRESSED", 0)
    suppression_candidates = event_counts.get("MATCH_SUPPRESSION_CANDIDATE", 0)
    opportunities = edge_stats.get("counts", {}).get("OPPORTUNITY", 0)
    executed = edge_stats.get("counts", {}).get("EXECUTED", 0)
    below_threshold = skip_breakdown.get("below_threshold", 0)
    zero_edge = skip_breakdown.get("zero_edge", 0)
    duplicate = skip_breakdown.get("duplicate", 0)
    other_skip = skip_breakdown.get("other", 0)

    lines: list[str] = []
    lines.append("PIPELINE REVIEW")
    lines.append(f"Trades log: {trades_path}")
    lines.append(f"Paper DB : {paper_db_path}")
    if since or until:
        since_text = since.date().isoformat() if since else "(beginning)"
        until_text = until.date().isoformat() if until else "(latest)"
        lines.append(f"Date range: {since_text} -> {until_text}")
    lines.append(f"Records included: {fmt_int(funnel_stats.get('records_kept'))}")
    lines.append("")

    lines.append("1. INGESTION")
    observed_records = sum(
        row.get("observed_records", 0)
        for row in freshness_stats.get("sources", {}).values()
    )
    fresh_passes = sum(
        row.get("fresh_passes", 0)
        for row in freshness_stats.get("sources", {}).values()
    )
    early_stale = sum(
        row.get("early_stale_drops", 0)
        for row in freshness_stats.get("sources", {}).values()
    )
    lines.append(f"  Source-attributed items observed : {observed_records}")
    lines.append(f"  Fresh passes                     : {fresh_passes}")
    lines.append(f"  Dropped early stale              : {early_stale}")
    lines.append("  Dropped disabled sources         : not directly observable in current logs")
    lines.append("  Dropped deduped                  : not directly observable in current logs")
    lines.append(f"  Rejected stale at analysis       : {analysis_rejections.get('stale_news', 0)}")
    top_stale_sources = _top_source_rows(freshness_stats, limit=top)
    if top_stale_sources:
        lines.append("  Drilldown: top stale sources")
        for row in top_stale_sources:
            lines.append(
                f"    {row['source']}: early_stale={row['early_stale_drops']} "
                f"fresh_pass={row['fresh_passes']} observed={row['observed_records']}"
            )
    eligible_freshness_rows = [
        row for row in freshness_stats.get("sources", {}).values()
        if row.get("observed_records", 0) >= 1
    ]
    if eligible_freshness_rows:
        buckets = freshness_diagnostics.bucket_sources(eligible_freshness_rows, show_all=True)
        waterfall_rows = (
            buckets.get("fast_operational", [])
            + buckets.get("near_threshold", [])
            + buckets.get("chronically_late", [])
            + buckets.get("dead", [])
            + buckets.get("insufficient", [])
        )
        if waterfall_rows:
            lines.append("  Drilldown: per-source freshness waterfall")
            for line in freshness_diagnostics.format_compact_rows(waterfall_rows, top=len(waterfall_rows)):
                lines.append("  " + line)
    lines.append("")

    lines.append("2. MATCHING")
    match_records = match_stats.get("match_records", 0)
    low_quality = match_stats.get("low_quality_matches", 0)
    pre_llm_would_block = match_stats.get("pre_llm_would_block", 0)
    lines.append(f"  Match diagnostics emitted        : {match_records}")
    lines.append(f"  Low-quality flagged              : {low_quality} ({fmt_pct(low_quality, match_records)})")
    lines.append(f"  Pre-LLM gate would-block         : {pre_llm_would_block} ({fmt_pct(pre_llm_would_block, match_records)})")
    lines.append(f"  Suppression candidates           : {suppression_candidates}")
    lines.append(f"  Suppressed                       : {suppressed}")
    if match_flags:
        lines.append("  Drilldown: top heuristic flags")
        for label, count in match_flags.most_common(top):
            lines.append(f"    {label}: {count}")
    pre_llm_by_source = match_stats.get("pre_llm_would_block_by_source", Counter())
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
    lines.append("")

    lines.append("3. ANALYSIS")
    detail_rows = edge_stats.get("counts", {}).get("SIGNAL_ANALYSIS_DETAIL", 0)
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
    lines.append(
        f"  LLM contention observed          : {llm_obs.get('contention_observed', 0)}"
    )
    lines.append(
        f"  LLM max in-flight at entry       : {llm_obs.get('max_in_flight_at_entry', 0)}"
    )
    if llm_status_counts:
        lines.append("  LLM status breakdown:")
        for status, count in llm_status_counts.most_common():
            lines.append(f"    {status}: {count}")
    lines.append(f"  Analysis rejections total        : {total_rejections}")
    for reason, count in analysis_rejections.most_common(top):
        lines.append(f"    rejected[{reason}] = {count}")
    lines.append("")

    lines.append("4. EDGE FORMATION")
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

    lines.append("5. EXECUTION")
    paper_trades = event_counts.get("PAPER_TRADE", 0)
    live_orders = event_counts.get("LIVE_ORDER", 0)
    lines.append(f"  Paper trades                     : {paper_trades}")
    lines.append(f"  Live orders                      : {live_orders}")
    lines.append(f"  Skipped duplicate position       : {duplicate}")
    lines.append(f"  Skipped below threshold          : {below_threshold}")
    lines.append(f"  Skipped other                    : {other_skip}")
    if paper_stats.get("total_trades", 0):
        lines.append("  Drilldown: paper performance")
        lines.append(f"    Total trades: {paper_stats['total_trades']}")
        lines.append(f"    Resolved: {paper_stats['resolved_trades']}")
        lines.append(f"    Open: {paper_stats['open_trades']}")
        lines.append(f"    Win rate: {paper_performance_drilldown.fmt_pct(paper_stats.get('win_rate'))}")
        lines.append(f"    Total P&L: {fmt_money(paper_stats.get('total_pnl'))}")
    lines.append("")

    llm_value = edge_stats.get("llm_value_add", {})
    llm_observability = edge_stats.get("llm_observability", {})
    llm_rows = llm_value.get("llm_rows", 0)
    lines.append("6. LLM VALUE-ADD ANALYSIS")
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
        f"  Trade candidates                  : {llm_value.get('trade_candidates', 0)} ({fmt_pct(llm_value.get('trade_candidates', 0), llm_rows)})"
    )
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
    lines.append("7. LLM VALUE-ADD SEGMENTATION")
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
        lines.append(
            "  Freshness/timing segmentation      : "
            f"unavailable ({timing_segmentation.get('reason', 'n/a')})"
        )
    category_segmentation = llm_segmentation.get("headline_category", {})
    if category_segmentation.get("available"):
        lines.append("  Headline/category segmentation     : available")
    else:
        lines.append(
            "  Headline/category segmentation     : "
            f"unavailable ({category_segmentation.get('reason', 'n/a')})"
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

    lines.append("Appendix")
    lines.append("  Detailed drilldowns remain available via:")
    lines.append("  scripts/freshness_diagnostics.py")
    lines.append("  scripts/match_quality_diagnostics.py")
    lines.append("  scripts/decision_funnel_summary.py")
    lines.append("  scripts/signal_edge_diagnostics.py")
    lines.append("  scripts/paper_performance_drilldown.py")
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
    lines = build_daily_review(
        trades_path=Path(args.path),
        paper_db_path=DEFAULT_PAPER_DB_PATH,
        since=since,
        until=until,
        top=max(1, args.top),
        exclude_test=args.exclude_test,
        show_progress=args.progress,
        show_profile=args.profile,
    )

    for line in lines:
        write_report_line(report_path, line)
    write_report_line(report_path)
    write_report_line(report_path, f"Daily review report saved to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
