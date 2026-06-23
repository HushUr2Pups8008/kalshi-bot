"""
Concise read-only decision funnel summary for structured trade logs.

Uses the preferred trade-log root at logs/trades/ to show what is directly observable from the
structured event stream:
  - total records considered
  - signal / opportunity counts where present
  - executor skips by reason
  - executions (paper trades / live orders)
  - news vs fade path contributions where inferable

This tool does not modify runtime or trading behavior.
The preferred source is logs/trades/. The legacy monolithic
logs/trades/trades.jsonl path is still supported during the cutover
validation window.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    add_path_arg,
    add_since_arg,
    add_top_arg,
    add_until_arg,
    is_test_record_source_or_signal_source as is_test_record,
    parse_date_end,
    parse_date_start,
)
from utils.diagnostic_reporting_helpers import format_counter, print_standard_trade_log_header
from utils.reporting_helpers import DEFAULT_CURRENT_STATE_WINDOW_HOURS, resolve_recent_window
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records


# Default to the active live file -- the funnel summary is a current-run metric.
# Pass --path logs/trades to include archive data for multi-day analysis.
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl"
DECISION_EVENT_TYPES = {"ANALYSIS_REJECTED", "SIGNAL", "OPPORTUNITY", "SKIPPED", "PAPER_TRADE", "LIVE_ORDER"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize kalshi-bot decision funnel")
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text=(
            "Path to trade-log file or root "
            "(default: logs/trades/live/trades.jsonl; pass logs/trades/ for archive scans; "
            f"default window: last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours when dates omitted; "
            "legacy logs/trades/trades.jsonl still supported)"
        ),
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(
        parser,
        help_text=f"Inclusive end date in YYYY-MM-DD (default: now when both dates omitted; last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours)",
    )
    add_top_arg(parser, default=5, help_text="Max rows to show in top breakdowns (default: 5)")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def infer_signal_type(record: dict[str, Any]) -> str:
    explicit = record.get("signal_type")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    source = str(record.get("source") or record.get("signal_source") or "").strip().lower()
    url = str(record.get("url") or "").strip().lower()
    reasoning = str(record.get("reasoning") or "").strip().upper()
    headline = str(record.get("headline") or record.get("signal_headline") or "").strip().upper()

    if source == "price_fade" or url.startswith("kalshi://price_fade/") or "[PRICE_FADE]" in reasoning or headline.startswith("[PRICE_FADE]"):
        return "price_fade"
    if "[FADE/" in reasoning:
        return "fade_tweet"
    return "news"


def stale_age_bucket(age_seconds: Any) -> str | None:
    if not isinstance(age_seconds, (int, float)):
        return None
    if age_seconds < 0:
        return None
    if age_seconds < 5 * 60:
        return "0-5m"
    if age_seconds < 15 * 60:
        return "5-15m"
    if age_seconds < 30 * 60:
        return "15-30m"
    if age_seconds < 60 * 60:
        return "30-60m"
    return "60m+"


def pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(100.0 * numerator / denominator):.1f}%"


def _text(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or default


def _nested_text(record: dict[str, Any], *keys: str, default: str = "unknown") -> str:
    for key in keys:
        if "." in key:
            value: Any = record
            for part in key.split("."):
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
        else:
            value = record.get(key)
        text = _text(value, default="")
        if text:
            return text
    return default


def _reason_parts(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return ["unknown"]
    return [part for part in text.split("+") if part] or ["unknown"]


def _string_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False, *, progress_tracker=None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "event_counts": Counter(),
        "skip_reasons": Counter(),
        "skip_categories": Counter(),
        "analysis_rejected_reasons": Counter(),
        "analysis_rejected_categories": Counter(),
        "stale_news_age_buckets": Counter(),
        "stale_news_sources": Counter(),
        "stale_news_tickers": Counter(),
        "path_counts": Counter(),
        "execution_paths": Counter(),
        "match_diagnostics_total": 0,
        "signal_analysis_detail_total": 0,
        "match_to_signal_detail_gap": 0,
        "match_diagnostic_pre_llm_gate": Counter(),
        "match_diagnostic_sources": Counter(),
        "match_diagnostic_tickers": Counter(),
        "match_suppressed_reasons": Counter(),
        "match_suppressed_sources": Counter(),
        "match_suppressed_tickers": Counter(),
        "match_suppressed_tokens": Counter(),
        "match_suppressed_column_coverage": Counter(),
        "match_suppressed_venues": Counter(),
        "match_suppressed_examples": [],
        "match_weight_applied_total": 0,
        "match_weight_tokens": Counter(),
        "match_weight_prefixes": Counter(),
        "match_weight_sources": Counter(),
        "match_weight_score_delta_total": 0.0,
        "opportunity_sources": Counter(),
        "opportunity_source_classes": Counter(),
        "opportunity_retrieval_modes": Counter(),
        "opportunity_evidence_ids": Counter(),
        "opportunity_settlement_source_matches": Counter(),
        "skip_sources": Counter(),
        "skip_source_classes": Counter(),
        "skip_retrieval_modes": Counter(),
        "skip_evidence_ids": Counter(),
        "skip_settlement_source_matches": Counter(),
    }

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if progress_tracker is not None:
            progress_tracker.tick()
        if exclude_test and is_test_record(record):
            continue

        stats["records_kept"] += 1
        event_type = str(record.get("type") or "UNKNOWN").strip() or "UNKNOWN"
        stats["event_counts"][event_type] += 1

        signal_type = infer_signal_type(record)
        if event_type in DECISION_EVENT_TYPES:
            stats["path_counts"][signal_type] += 1

        if event_type == "MATCH_DIAGNOSTIC":
            stats["match_diagnostics_total"] += 1
            if record.get("would_fail_pre_llm_gate") is True:
                stats["match_diagnostic_pre_llm_gate"]["would_fail"] += 1
            elif record.get("would_fail_pre_llm_gate") is False:
                stats["match_diagnostic_pre_llm_gate"]["would_pass"] += 1
            else:
                stats["match_diagnostic_pre_llm_gate"]["unknown"] += 1
            stats["match_diagnostic_sources"][_text(record.get("source"))] += 1
            stats["match_diagnostic_tickers"][_text(record.get("ticker"))] += 1
        elif event_type == "SIGNAL_ANALYSIS_DETAIL":
            stats["signal_analysis_detail_total"] += 1
        elif event_type == "MATCH_SUPPRESSED":
            for reason in _reason_parts(record.get("reason")):
                stats["match_suppressed_reasons"][reason] += 1
            stats["match_suppressed_sources"][_text(record.get("source"))] += 1
            stats["match_suppressed_tickers"][_text(record.get("ticker"))] += 1
            for token in _string_values(record.get("matched_tokens")):
                stats["match_suppressed_tokens"][token] += 1
            for column in (
                "raw_score",
                "adjusted_score",
                "threshold",
                "token_weight_multiplier",
                "venue",
                "market_prefix",
            ):
                value = record.get(column)
                if value is not None and str(value).strip():
                    stats["match_suppressed_column_coverage"][column] += 1
            stats["match_suppressed_venues"][_text(record.get("venue"))] += 1
            if len(stats["match_suppressed_examples"]) < 5:
                stats["match_suppressed_examples"].append(
                    {
                        "ticker": _text(record.get("ticker")),
                        "venue": _text(record.get("venue")),
                        "raw_score": _float_or_none(record.get("raw_score")),
                        "adjusted_score": _float_or_none(record.get("adjusted_score")),
                        "threshold": _float_or_none(record.get("threshold")),
                        "token_weight_multiplier": _float_or_none(
                            record.get("token_weight_multiplier")
                        ),
                        "reason": _text(record.get("reason")),
                    }
                )
        elif event_type == "MATCH_WEIGHT_APPLIED":
            stats["match_weight_applied_total"] += 1
            stats["match_weight_prefixes"][_text(record.get("market_prefix"))] += 1
            stats["match_weight_sources"][_text(record.get("source"))] += 1
            tokens = _string_values(record.get("tokens"))
            token_weights = record.get("token_weights")
            if isinstance(token_weights, dict):
                tokens.extend(str(token).strip() for token in token_weights if str(token).strip())
            for token in sorted(set(tokens)):
                stats["match_weight_tokens"][token] += 1
            pre_weight_score = _float_or_none(record.get("pre_weight_score"))
            post_weight_score = _float_or_none(record.get("post_weight_score"))
            if pre_weight_score is not None and post_weight_score is not None:
                stats["match_weight_score_delta_total"] += post_weight_score - pre_weight_score

        if event_type == "SKIPPED":
            reason = str(record.get("reason") or "unknown").strip() or "unknown"
            stats["skip_reasons"][reason] += 1
            category = str(record.get("skip_category") or "unknown").strip() or "unknown"
            stats["skip_categories"][category] += 1
            stats["skip_sources"][
                _nested_text(record, "source", "signal_meta.trigger_evidence_source")
            ] += 1
            stats["skip_source_classes"][
                _nested_text(record, "source_class", "signal_meta.trigger_evidence_source_class")
            ] += 1
            stats["skip_retrieval_modes"][
                _nested_text(
                    record,
                    "retrieval_mode",
                    "signal_meta.retrieval_mode",
                    "signal_meta.source_lane",
                )
            ] += 1
            stats["skip_evidence_ids"][
                _nested_text(record, "evidence_id", "signal_meta.trigger_evidence_id")
            ] += 1
            stats["skip_settlement_source_matches"][
                _nested_text(
                    record,
                    "settlement_source_match",
                    "signal_meta.settlement_source_match",
                )
            ] += 1
        elif event_type == "ANALYSIS_REJECTED":
            reason = str(record.get("reason") or "unknown").strip() or "unknown"
            stats["analysis_rejected_reasons"][reason] += 1
            category = str(record.get("rejection_category") or "").strip()
            if category:
                stats["analysis_rejected_categories"][category] += 1
            if reason == "stale_news":
                bucket = stale_age_bucket(record.get("age_seconds"))
                if bucket:
                    stats["stale_news_age_buckets"][bucket] += 1
                source = str(record.get("source") or record.get("signal_source") or "").strip()
                if source:
                    stats["stale_news_sources"][source] += 1
                ticker = str(record.get("ticker") or "").strip()
                if ticker:
                    stats["stale_news_tickers"][ticker] += 1
        elif event_type == "OPPORTUNITY":
            stats["opportunity_sources"][_nested_text(record, "source")] += 1
            stats["opportunity_source_classes"][
                _nested_text(record, "source_class")
            ] += 1
            stats["opportunity_retrieval_modes"][
                _nested_text(
                    record,
                    "retrieval_mode",
                    "signal_meta.retrieval_mode",
                    "signal_meta.source_lane",
                )
            ] += 1
            stats["opportunity_evidence_ids"][
                _nested_text(record, "evidence_id", "signal_meta.trigger_evidence_id")
            ] += 1
            stats["opportunity_settlement_source_matches"][
                _nested_text(
                    record,
                    "settlement_source_match",
                    "signal_meta.settlement_source_match",
                )
            ] += 1
        if event_type in {"PAPER_TRADE", "LIVE_ORDER"}:
            stats["execution_paths"][signal_type] += 1

    stats["lines_total"] = read_stats.lines_total
    stats["lines_malformed"] = read_stats.lines_malformed
    stats["match_to_signal_detail_gap"] = max(
        0,
        stats["match_diagnostics_total"] - stats["signal_analysis_detail_total"],
    )

    return stats


def print_summary(stats: dict[str, Any], top: int, since: datetime | None, until: datetime | None) -> None:
    print_standard_trade_log_header(
        title="DECISION FUNNEL SUMMARY",
        path=stats["path"],
        since=since,
        until=until,
        lines_total=stats["lines_total"],
        lines_malformed=stats["lines_malformed"],
        records_kept=stats["records_kept"],
    )

    if stats["records_kept"] == 0:
        print()
        print("No matching log records found.")
        return

    signals = stats["event_counts"].get("SIGNAL", 0)
    analysis_rejected = stats["event_counts"].get("ANALYSIS_REJECTED", 0)
    opportunities = stats["event_counts"].get("OPPORTUNITY", 0)
    skipped = stats["event_counts"].get("SKIPPED", 0)
    paper_trades = stats["event_counts"].get("PAPER_TRADE", 0)
    live_orders = stats["event_counts"].get("LIVE_ORDER", 0)
    executions = paper_trades + live_orders
    comparable_window = opportunities > 0 and skipped <= opportunities and executions <= opportunities

    print()
    print("Funnel")
    print(f"  Structured records considered : {stats['records_kept']}")
    print(f"  Pre-signal rejections         : {analysis_rejected}")
    print(f"  Signals logged                : {signals}")
    print(f"  Opportunities logged          : {opportunities} ({pct(opportunities, signals)} of signals)")
    if analysis_rejected:
        print("  No-signal exits               : partially observable via ANALYSIS_REJECTED")
    else:
        print("  No-signal exits               : not directly observable in trades.jsonl")
    if comparable_window:
        print(f"  Executor skips               : {skipped} ({pct(skipped, opportunities)} of opportunities)")
        print(f"  Executions                   : {executions} ({pct(executions, opportunities)} of opportunities)")
    else:
        print(f"  Executor skips               : {skipped}")
        print(f"  Executions                   : {executions}")
        print("  Note                         : window mixes stages from different decision lifecycles")
    if paper_trades:
        print(f"    Paper trades               : {paper_trades}")
    if live_orders:
        print(f"    Live orders                : {live_orders}")

    print()
    print("Match Attribution")
    print(f"  Match diagnostics            : {stats['match_diagnostics_total']}")
    print(f"  Signal analysis detail rows  : {stats['signal_analysis_detail_total']}")
    print(f"  Match -> analysis detail gap : {stats['match_to_signal_detail_gap']}")
    print(f"  Match suppressions           : {stats['event_counts'].get('MATCH_SUPPRESSED', 0)}")
    print(f"  Match weight applications    : {stats['match_weight_applied_total']}")
    if stats["match_weight_applied_total"]:
        print(f"  Weight score delta total     : {stats['match_weight_score_delta_total']:.4f}")

    print()
    print("Pre-LLM quality gate")
    for line in format_counter(stats["match_diagnostic_pre_llm_gate"], top=max(top, len(stats["match_diagnostic_pre_llm_gate"]))):
        print(line)

    print()
    print(f"Match Diagnostic Sources (top {top})")
    for line in format_counter(stats["match_diagnostic_sources"], top=top):
        print(line)

    print()
    print(f"Match Diagnostic Tickers (top {top})")
    for line in format_counter(stats["match_diagnostic_tickers"], top=top):
        print(line)

    print()
    print("Match Suppression Reasons")
    for line in format_counter(stats["match_suppressed_reasons"], top=top):
        print(line)

    print()
    print(f"Match Suppression Tokens (top {top})")
    for line in format_counter(stats["match_suppressed_tokens"], top=top):
        print(line)

    print()
    print("Match Suppression Column Coverage")
    suppression_total = stats["event_counts"].get("MATCH_SUPPRESSED", 0)
    for column in (
        "raw_score",
        "adjusted_score",
        "threshold",
        "token_weight_multiplier",
        "venue",
        "market_prefix",
    ):
        covered = stats["match_suppressed_column_coverage"].get(column, 0)
        print(f"  {column:<24}: {covered}/{suppression_total}")

    print()
    print(f"Match Suppression Venues (top {top})")
    for line in format_counter(stats["match_suppressed_venues"], top=top):
        print(line)

    print()
    print("Match Suppression Examples")
    if stats["match_suppressed_examples"]:
        for row in stats["match_suppressed_examples"][:top]:
            print(
                "  "
                f"{row['ticker']} venue={row['venue']} "
                f"raw={row['raw_score']} adjusted={row['adjusted_score']} "
                f"threshold={row['threshold']} multiplier={row['token_weight_multiplier']} "
                f"reason={row['reason']}"
            )
    else:
        print("  (none)")

    print()
    print(f"Match Weight Tokens (top {top})")
    for line in format_counter(stats["match_weight_tokens"], top=top):
        print(line)

    print()
    print(f"Match Weight Prefixes (top {top})")
    for line in format_counter(stats["match_weight_prefixes"], top=top):
        print(line)

    print()
    print("Pre-Signal Rejection Reasons")
    for line in format_counter(stats["analysis_rejected_reasons"], top=top):
        print(line)

    print()
    print("Pre-Signal Rejection Branches")
    for line in format_counter(stats["analysis_rejected_categories"], top=top):
        print(line)

    print()
    print("Stale-News Age Buckets")
    for line in format_counter(stats["stale_news_age_buckets"], top=max(top, len(stats["stale_news_age_buckets"]))):
        print(line)

    print()
    print(f"Stale-News Sources (top {top})")
    for line in format_counter(stats["stale_news_sources"], top=top):
        print(line)

    print()
    print(f"Stale-News Tickers (top {top})")
    for line in format_counter(stats["stale_news_tickers"], top=top):
        print(line)

    print()
    print("Executor Skip Categories")
    for line in format_counter(stats["skip_categories"], top=top):
        print(line)

    print()
    print("Executor Skip Reasons")
    for line in format_counter(stats["skip_reasons"], top=top):
        print(line)

    print()
    print("Path Contribution (decision-stage records)")
    for line in format_counter(stats["path_counts"], top=max(top, len(stats["path_counts"]))):
        print(line)

    print()
    print("Execution Path Contribution")
    for line in format_counter(stats["execution_paths"], top=max(top, len(stats["execution_paths"]))):
        print(line)

    print()
    print("Opportunity Attribution: Sources")
    for line in format_counter(stats["opportunity_sources"], top=top):
        print(line)

    print()
    print("Opportunity Attribution: Source Classes")
    for line in format_counter(stats["opportunity_source_classes"], top=top):
        print(line)

    print()
    print("Opportunity Attribution: Retrieval Modes")
    for line in format_counter(stats["opportunity_retrieval_modes"], top=top):
        print(line)

    print()
    print("Opportunity Attribution: Evidence IDs")
    for line in format_counter(stats["opportunity_evidence_ids"], top=top):
        print(line)

    print()
    print("Opportunity Attribution: Settlement-Source Match")
    for line in format_counter(stats["opportunity_settlement_source_matches"], top=top):
        print(line)

    print()
    print("Skip Attribution: Sources")
    for line in format_counter(stats["skip_sources"], top=top):
        print(line)

    print()
    print("Skip Attribution: Source Classes")
    for line in format_counter(stats["skip_source_classes"], top=top):
        print(line)

    print()
    print("Skip Attribution: Retrieval Modes")
    for line in format_counter(stats["skip_retrieval_modes"], top=top):
        print(line)

    print()
    print("Skip Attribution: Evidence IDs")
    for line in format_counter(stats["skip_evidence_ids"], top=top):
        print(line)

    print()
    print("Skip Attribution: Settlement-Source Match")
    for line in format_counter(stats["skip_settlement_source_matches"], top=top):
        print(line)

    print()
    print("Observed Event Types")
    for line in format_counter(stats["event_counts"], top=max(top, len(stats["event_counts"]))):
        print(line)


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

    stats = summarize(Path(args.path), since, until, exclude_test=args.exclude_test)
    print_summary(stats, top=max(1, args.top), since=since, until=until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
