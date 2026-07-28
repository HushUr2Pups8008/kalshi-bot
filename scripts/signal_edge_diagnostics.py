"""
Read-only diagnostics for the signal -> opportunity -> execution boundary.

This report explains why recent signal-bearing items reached OPPORTUNITY but did
not become trades, focusing on estimated probability, market price, computed
edge, and the final recorded outcome.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from config import PAPER_MIN_EDGE, cfg
from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    add_path_arg,
    add_since_arg,
    add_top_arg,
    add_until_arg,
    is_test_record_source_or_signal_source as is_test_record,
    parse_date_end,
    parse_date_start,
    parse_iso_ts,
)
from utils.reporting_helpers import DEFAULT_CURRENT_STATE_WINDOW_HOURS, resolve_recent_window
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records


REPO_ROOT = Path(__file__).resolve().parent.parent
# Default to the active live file -- signal edge is a current-run diagnostic.
# Pass --path logs/trades to include archive data for multi-day analysis.
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl"
RECENT_AUDIT_DEFAULT = 20
LLM_NEAR_NEUTRAL_PROB_MAX = 0.02
LLM_WEAK_PROB_MAX = 0.10
LLM_MODERATE_PROB_MAX = 0.20
LLM_ZERO_EDGE_MAX = 0.01
LLM_WEAK_EDGE_MAX = 0.05
LLM_MODERATE_EDGE_MAX = 0.10
LLM_MARKET_NEUTRAL_MAX = 0.01
LLM_MEANINGFUL_EDGE_MIN = PAPER_MIN_EDGE
LLM_TRADE_CANDIDATE_EDGE_MIN = max(cfg.min_edge, PAPER_MIN_EDGE)
LLM_PRICE_BAND_LABELS = [
    "0.00-0.20",
    "0.20-0.40",
    "0.40-0.60",
    "0.60-0.80",
    "0.80-1.00",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize signal-to-edge execution diagnostics")
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
    add_top_arg(parser, default=10, help_text="Max rows to show in grouped sections (default: 10)")
    parser.add_argument(
        "--recent",
        type=int,
        default=RECENT_AUDIT_DEFAULT,
        help="Number of most recent signal-bearing rows in the audit table (default: 20)",
    )
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def event_key(record: dict[str, Any]) -> tuple[str, str, str]:
    ticker = str(record.get("ticker") or "").strip()
    source = str(record.get("source") or record.get("signal_source") or "").strip()
    headline = str(record.get("headline") or record.get("signal_headline") or "").strip()
    return ticker, source, headline


def classify_skip_reason(record: dict[str, Any]) -> str:
    reason = str(record.get("reason") or "").lower()
    edge = safe_float(record.get("edge"))
    min_edge = safe_float(record.get("min_edge_threshold"))
    gate_prefix = reason.split("_", 1)[0]
    if len(gate_prefix) == 2 and gate_prefix[0] == "g" and gate_prefix[1].isdigit():
        return "risk_gate"
    if "duplicate" in reason or "same-signal" in reason:
        return "duplicate"
    if "illiquid" in reason or "near limit" in reason or "price unavailable" in reason or "not tradeable" in reason:
        return "liquidity"
    if "below min_edge" in reason:
        if edge is not None and abs(edge) < 0.00005:
            return "zero_edge"
        if edge is not None and min_edge is not None and abs(edge) < min_edge:
            return "below_threshold"
        return "below_threshold"
    return "other"


def fmt_prob(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def fmt_ts(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fmt_avg(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"{sum(values) / len(values):.4f}"


def fmt_median(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"{median(values):.4f}"


def _p95(values: list[float]) -> float | None:
    """Return approximate 95th percentile; requires at least 2 samples."""
    if len(values) < 2:
        return None
    from statistics import quantiles
    # quantiles(n=20) returns 19 cut-points; index 18 (0-based) = p95
    return quantiles(sorted(values), n=20)[18]


def fmt_latency_stats(samples: list[float]) -> str:
    """Return 'avg=Xms  median=Xms  p95=Xms  max=Xms  n=N' or 'n/a'."""
    if not samples:
        return "n/a"
    avg = sum(samples) / len(samples)
    med = median(samples)
    p95_val = _p95(samples)
    mx = max(samples)
    p95_str = f"{p95_val:.0f}ms" if p95_val is not None else "n/a"
    return (
        f"avg={avg:.0f}ms  median={med:.0f}ms  p95={p95_str}  max={mx:.0f}ms  n={len(samples)}"
    )


def default_group_metrics() -> dict[str, Any]:
    return {
        "signals": 0,
        "opportunities": 0,
        "edges": [],
        "zero_edge": 0,
        "duplicate_skip": 0,
    }


def _llm_probability_movement_bucket(movement: float) -> str:
    if movement < LLM_NEAR_NEUTRAL_PROB_MAX:
        return "near_neutral"
    if movement < LLM_WEAK_PROB_MAX:
        return "weak"
    if movement <= LLM_MODERATE_PROB_MAX:
        return "moderate"
    return "strong"


def _llm_edge_bucket(abs_edge: float) -> str:
    if abs_edge < LLM_ZERO_EDGE_MAX:
        return "zero_neutral"
    if abs_edge < LLM_WEAK_EDGE_MAX:
        return "weak"
    if abs_edge <= LLM_MODERATE_EDGE_MAX:
        return "moderate"
    return "strong"


def _llm_decision_impact_classification(
    *,
    est_prob: float,
    market_price: float | None,
    executable_edge: float | None = None,
) -> str:
    """Classify model impact without mixing cents and probability units."""
    if executable_edge is not None:
        abs_edge = max(0.0, executable_edge)
    elif market_price is not None:
        abs_edge = abs(est_prob - market_price)
    else:
        return "weak_signal"
    market_for_neutral = market_price if market_price is not None else 0.5
    if abs(est_prob - 0.5) < LLM_NEAR_NEUTRAL_PROB_MAX and abs(market_for_neutral - 0.5) < LLM_MARKET_NEUTRAL_MAX and abs_edge < LLM_ZERO_EDGE_MAX:
        return "neutral_confirmation"
    if executable_edge is not None and abs_edge >= LLM_TRADE_CANDIDATE_EDGE_MIN:
        return "trade_candidate"
    if abs_edge >= LLM_MEANINGFUL_EDGE_MIN:
        return "meaningful_signal"
    return "weak_signal"


def _default_llm_value_add() -> dict[str, Any]:
    return {
        "llm_rows": 0,
        "near_neutral_outputs": 0,
        "meaningful_signals": 0,
        "non_zero_edge_outputs": 0,
        "trade_candidates": 0,
        "admitted_trade_candidates": 0,
        "blocked_trade_candidates": 0,
        "pending_trade_candidates": 0,
        "llm_created_edge": 0,
        "probability_movement_buckets": Counter(),
        "edge_magnitude_buckets": Counter(),
        "impact_classifications": Counter(),
        "meaningful_sources": Counter(),
        "meaningful_tickers": Counter(),
        "strong_examples": [],
        "neutral_examples": [],
        "rare_non_neutral_examples": [],
        "segmentation": {
            "by_source": [],
            "by_ticker": [],
            "by_price_band": [],
            "timing": {
                "available": False,
                "reason": "publish/event age is not present on SIGNAL_ANALYSIS_DETAIL rows",
            },
            "headline_category": {
                "available": False,
                "reason": "no safe built-in category derivation is present in reporting inputs",
            },
        },
    }


def _default_llm_segment_metrics() -> dict[str, int]:
    return {
        "llm_rows": 0,
        "near_neutral_outputs": 0,
        "non_zero_edge_outputs": 0,
        "meaningful_signals": 0,
        "trade_candidates": 0,
        "neutral_confirmations": 0,
    }


def _llm_market_price_band(market_price: float) -> str:
    if market_price < 0.20:
        return "0.00-0.20"
    if market_price < 0.40:
        return "0.20-0.40"
    if market_price < 0.60:
        return "0.40-0.60"
    if market_price < 0.80:
        return "0.60-0.80"
    return "0.80-1.00"


def _llm_timing_bucket(age_seconds: float) -> str:
    if age_seconds < 5 * 60:
        return "0-5m"
    if age_seconds < 30 * 60:
        return "5-30m"
    if age_seconds < 2 * 60 * 60:
        return "30m-2h"
    if age_seconds < 24 * 60 * 60:
        return "2h-24h"
    return "24h+"


def _update_llm_segment_metrics(
    metrics: dict[str, int],
    *,
    movement_bucket: str,
    abs_edge: float,
    impact: str,
) -> None:
    metrics["llm_rows"] += 1
    if movement_bucket == "near_neutral":
        metrics["near_neutral_outputs"] += 1
    if abs_edge >= LLM_ZERO_EDGE_MAX:
        metrics["non_zero_edge_outputs"] += 1
    if impact in {"meaningful_signal", "trade_candidate"}:
        metrics["meaningful_signals"] += 1
    if impact == "trade_candidate":
        metrics["trade_candidates"] += 1
    if impact == "neutral_confirmation":
        metrics["neutral_confirmations"] += 1


def _finalize_llm_segment_rows(
    groups: dict[str, dict[str, int]],
    *,
    key_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, metrics in groups.items():
        llm_rows = metrics["llm_rows"]
        rows.append(
            {
                key_name: label,
                "llm_rows": llm_rows,
                "near_neutral_outputs": metrics["near_neutral_outputs"],
                "near_neutral_rate": (metrics["near_neutral_outputs"] / llm_rows) if llm_rows else None,
                "non_zero_edge_outputs": metrics["non_zero_edge_outputs"],
                "non_zero_edge_rate": (metrics["non_zero_edge_outputs"] / llm_rows) if llm_rows else None,
                "meaningful_signals": metrics["meaningful_signals"],
                "meaningful_signal_rate": (metrics["meaningful_signals"] / llm_rows) if llm_rows else None,
                "trade_candidates": metrics["trade_candidates"],
                "trade_candidate_rate": (metrics["trade_candidates"] / llm_rows) if llm_rows else None,
                "neutral_confirmations": metrics["neutral_confirmations"],
                "neutral_confirmation_rate": (metrics["neutral_confirmations"] / llm_rows) if llm_rows else None,
            }
        )
    return rows


def _pct(count: int, total: int) -> str:
    if total <= 0:
        return "n/a"
    return f"{(100.0 * count / total):.1f}%"


def _format_bucket_counter(counter: Counter[str], labels: list[tuple[str, str]]) -> str:
    if not counter:
        return "n/a"
    return ", ".join(f"{label}={counter.get(key, 0)}" for key, label in labels)


def _sort_segment_rows(
    rows: list[dict[str, Any]],
    *,
    label_key: str,
    rate_key: str,
) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -(row.get(rate_key) or 0.0),
            -(row.get("llm_rows") or 0),
            str(row.get(label_key) or "").lower(),
        ),
    )


def format_llm_segmentation_rows(
    rows: list[dict[str, Any]],
    *,
    label_key: str,
    limit: int,
) -> list[str]:
    if not rows:
        return ["  (none)"]
    lines: list[str] = []
    for row in rows[:limit]:
        lines.append(
            "  "
            f"{row.get(label_key) or 'n/a'}  "
            f"rows={row.get('llm_rows', 0)}  "
            f"near-neutral={_pct(row.get('near_neutral_outputs', 0), row.get('llm_rows', 0))}  "
            f"non-zero-edge={_pct(row.get('non_zero_edge_outputs', 0), row.get('llm_rows', 0))}  "
            f"meaningful={_pct(row.get('meaningful_signals', 0), row.get('llm_rows', 0))}  "
            f"trade-candidate={_pct(row.get('trade_candidates', 0), row.get('llm_rows', 0))}"
        )
    return lines


def attach_opportunities(
    rows: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    queues: dict[tuple[str, str, str], deque[int]] = defaultdict(deque)
    ticker_only: dict[str, deque[int]] = defaultdict(deque)
    for idx, row in enumerate(rows):
        queues[event_key(row)].append(idx)
        if row["ticker"]:
            ticker_only[row["ticker"]].append(idx)

    unmatched_rows = [dict(row) for row in rows]
    for opp in opportunities:
        key = event_key(opp)
        target_idx = None
        while queues[key]:
            candidate_idx = queues[key][0]
            candidate = unmatched_rows[candidate_idx]
            if candidate["ts"] is not None and opp["ts"] is not None and candidate["ts"] > opp["ts"]:
                break
            if candidate.get("opportunity_ts") is None:
                target_idx = candidate_idx
                queues[key].popleft()
                break
            queues[key].popleft()

        if target_idx is None and opp["ticker"]:
            while ticker_only[opp["ticker"]]:
                candidate_idx = ticker_only[opp["ticker"]][0]
                candidate = unmatched_rows[candidate_idx]
                if candidate["ts"] is not None and opp["ts"] is not None and candidate["ts"] > opp["ts"]:
                    break
                if candidate.get("opportunity_ts") is None:
                    target_idx = candidate_idx
                    ticker_only[opp["ticker"]].popleft()
                    break
                ticker_only[opp["ticker"]].popleft()

        if target_idx is None:
            unmatched_rows.append({
                "ts": opp["ts"],
                "ticker": opp["ticker"],
                "source": opp["source"],
                "headline": opp["headline"],
                "method": opp["method"],
                "llm_direction": opp["llm_direction"],
                "llm_magnitude": opp["llm_magnitude"],
                "estimated_probability": opp["estimated_probability"],
                # The opportunity quote is selected-side cents, not a YES
                # probability. Do not fabricate a midpoint for an orphaned
                # OPPORTUNITY record.
                "market_price": None,
                "edge": opp["edge"],
                "opportunity_side": opp["side"],
                "executable_price_cents": opp["entry_price_cents"],
                "signal_ts": None,
                "opportunity_ts": opp["ts"],
                "outcome": "opportunity no recorded outcome",
            })
            continue

        row = unmatched_rows[target_idx]
        row["opportunity_ts"] = opp["ts"]
        if row["estimated_probability"] is None:
            row["estimated_probability"] = opp["estimated_probability"]
        if opp["edge"] is not None:
            row["edge"] = opp["edge"]
        row["opportunity_side"] = opp["side"]
        row["executable_price_cents"] = opp["entry_price_cents"]
        row["method"] = row["method"] or opp["method"]
        row["llm_direction"] = row["llm_direction"] or opp["llm_direction"]
        row["llm_magnitude"] = row["llm_magnitude"] or opp["llm_magnitude"]
        row["outcome"] = "opportunity no recorded outcome"
    return unmatched_rows


def attach_outcomes(
    rows: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    *,
    label_builder,
) -> None:
    queues: dict[tuple[str, str, str], deque[int]] = defaultdict(deque)
    ticker_only: dict[str, deque[int]] = defaultdict(deque)
    for idx, row in enumerate(rows):
        if row.get("opportunity_ts") is not None:
            queues[event_key(row)].append(idx)
            if row["ticker"]:
                ticker_only[row["ticker"]].append(idx)

    for event in outcomes:
        key = event_key(event)
        while queues[key]:
            idx = queues[key][0]
            row = rows[idx]
            if row.get("final_ts") is not None:
                queues[key].popleft()
                continue
            anchor_ts = row.get("opportunity_ts") or row.get("ts")
            if anchor_ts is not None and event["ts"] is not None and event["ts"] < anchor_ts:
                break
            row["final_ts"] = event["ts"]
            row["outcome"] = label_builder(event)
            row["skip_reason"] = event.get("reason")
            row["skip_type"] = event.get("skip_type")
            queues[key].popleft()
            break
        else:
            ticker = event.get("ticker")
            if not ticker:
                continue
            while ticker_only[ticker]:
                idx = ticker_only[ticker][0]
                row = rows[idx]
                if row.get("final_ts") is not None:
                    ticker_only[ticker].popleft()
                    continue
                anchor_ts = row.get("opportunity_ts") or row.get("ts")
                if anchor_ts is not None and event["ts"] is not None and event["ts"] < anchor_ts:
                    break
                row["final_ts"] = event["ts"]
                row["outcome"] = label_builder(event)
                row["skip_reason"] = event.get("reason")
                row["skip_type"] = event.get("skip_type")
                ticker_only[ticker].popleft()
                break


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False, *, progress_tracker=None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "counts": {
            "SIGNAL_ANALYSIS_DETAIL": 0,
            "SIGNAL": 0,
            "OPPORTUNITY": 0,
            "SKIPPED": 0,
            "EXECUTED": 0,
        },
        "skip_breakdown": {
            "zero_edge": 0,
            "below_threshold": 0,
            "duplicate": 0,
            "liquidity": 0,
            "risk_gate": 0,
            "other": 0,
        },
        "llm_observability": {
            "attempted": 0,
            "result_used": 0,
            "fallback": 0,
            "skipped_routing": 0,
            "skipped_routing_reasons": Counter(),
            "status_counts": Counter(),
            "probe_count": 0,
            "latency_ms_samples": [],
            "total_stage_ms_samples": [],
            "queue_wait_ms_samples": [],
            "http_round_trip_ms_samples": [],
            "parse_ms_samples": [],
            "contention_observed": 0,
            "max_in_flight_at_entry": 0,
            "pre_llm_would_block_and_useful": 0,
        },
        "llm_value_add": _default_llm_value_add(),
        "live_execution_attribution_limited": False,
        "audit_rows": [],
        "by_source": [],
        "by_ticker": [],
    }

    detail_rows: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    paper_trades: list[dict[str, Any]] = []
    source_groups: dict[str, dict[str, Any]] = defaultdict(default_group_metrics)
    ticker_groups: dict[str, dict[str, Any]] = defaultdict(default_group_metrics)
    llm_source_segments: dict[str, dict[str, int]] = defaultdict(_default_llm_segment_metrics)
    llm_ticker_segments: dict[str, dict[str, int]] = defaultdict(_default_llm_segment_metrics)
    llm_price_band_segments: dict[str, dict[str, int]] = defaultdict(_default_llm_segment_metrics)
    llm_timing_segments: dict[str, dict[str, int]] = defaultdict(_default_llm_segment_metrics)

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if progress_tracker is not None:
            progress_tracker.tick()
        if exclude_test and is_test_record(record):
            continue

        stats["records_kept"] += 1
        event_type = str(record.get("type") or "").strip()
        ts = parse_iso_ts(record.get("ts"))

        if event_type == "SIGNAL_ANALYSIS_DETAIL":
            stats["counts"]["SIGNAL_ANALYSIS_DETAIL"] += 1
            detail_rows.append({
                "ts": ts,
                "signal_ts": ts,
                "opportunity_ts": None,
                "final_ts": None,
                "ticker": str(record.get("ticker") or "").strip(),
                "source": str(record.get("source") or "").strip(),
                "headline": str(record.get("headline") or "").strip(),
                "method": str(record.get("method") or "").strip() or None,
                "llm_direction": str(record.get("llm_direction") or "").strip() or None,
                "llm_magnitude": str(record.get("llm_magnitude") or "").strip() or None,
                "llm_attempted": (bool(record.get("llm_attempted")) if "llm_attempted" in record else None),
                "llm_result_used": (bool(record.get("llm_result_used")) if "llm_result_used" in record else None),
                "llm_result_status": str(record.get("llm_result_status") or "").strip() or None,
                "llm_provider": str(record.get("llm_provider") or "").strip() or None,
                "llm_latency_ms": safe_float(record.get("llm_latency_ms")),
                "llm_total_stage_ms": safe_float(record.get("llm_total_stage_ms")),
                "llm_queue_wait_ms": safe_float(record.get("llm_queue_wait_ms")),
                "llm_http_round_trip_ms": safe_float(record.get("llm_http_round_trip_ms")),
                "llm_parse_ms": safe_float(record.get("llm_parse_ms")),
                "llm_http_status": safe_float(record.get("llm_http_status")),
                "llm_contention_observed": (
                    bool(record.get("llm_contention_observed"))
                    if "llm_contention_observed" in record
                    else None
                ),
                "llm_routing_passed": (
                    bool(record.get("llm_routing_passed"))
                    if "llm_routing_passed" in record
                    else None
                ),
                "llm_routing_reason": str(record.get("llm_routing_reason") or "").strip() or None,
                "llm_in_flight_at_entry": (
                    int(record.get("llm_in_flight_at_entry"))
                    if isinstance(record.get("llm_in_flight_at_entry"), int)
                    else None
                ),
                "is_startup_probe": (
                    bool(record.get("is_startup_probe"))
                    if "is_startup_probe" in record
                    else None
                ),
                "is_synthetic_probe": (
                    bool(record.get("is_synthetic_probe"))
                    if "is_synthetic_probe" in record
                    else None
                ),
                "pre_llm_quality_pass": (
                    bool(record.get("pre_llm_quality_pass"))
                    if "pre_llm_quality_pass" in record
                    else None
                ),
                "pre_llm_semantic_overlap_count": (
                    int(record.get("pre_llm_semantic_overlap_count"))
                    if isinstance(record.get("pre_llm_semantic_overlap_count"), int)
                    else None
                ),
                "pre_llm_semantic_overlap_ratio": safe_float(record.get("pre_llm_semantic_overlap_ratio")),
                "pre_llm_would_block": (
                    bool(record.get("pre_llm_would_block"))
                    if "pre_llm_would_block" in record
                    else None
                ),
                "pre_llm_keyword_override": (
                    bool(record.get("pre_llm_keyword_override"))
                    if "pre_llm_keyword_override" in record
                    else None
                ),
                "pre_llm_keyword_override_mode": str(record.get("pre_llm_keyword_override_mode") or "").strip() or None,
                "pre_llm_keyword_signal_strength": safe_float(record.get("pre_llm_keyword_signal_strength")),
                "pre_llm_gate_reason": str(record.get("pre_llm_gate_reason") or "").strip() or None,
                "pre_llm_gate_enforced": (
                    bool(record.get("pre_llm_gate_enforced"))
                    if "pre_llm_gate_enforced" in record
                    else None
                ),
                "pre_llm_filtered_stopword_count": (
                    int(record.get("pre_llm_filtered_stopword_count"))
                    if isinstance(record.get("pre_llm_filtered_stopword_count"), int)
                    else None
                ),
                "pre_llm_filtered_generic_count": (
                    int(record.get("pre_llm_filtered_generic_count"))
                    if isinstance(record.get("pre_llm_filtered_generic_count"), int)
                    else None
                ),
                "pre_llm_semantic_token_types": (
                    dict(record.get("pre_llm_semantic_token_types"))
                    if isinstance(record.get("pre_llm_semantic_token_types"), dict)
                    else None
                ),
                "pre_llm_would_block_and_useful": (
                    bool(record.get("pre_llm_would_block_and_useful"))
                    if "pre_llm_would_block_and_useful" in record
                    else None
                ),
                "llm_useful": (
                    bool(record.get("llm_useful"))
                    if "llm_useful" in record
                    else None
                ),
                "llm_probability_movement": safe_float(record.get("llm_probability_movement")),
                "publish_ts": str(record.get("publish_ts") or "").strip() or None,
                "age_at_analysis_seconds": safe_float(record.get("age_at_analysis_seconds")),
                "analysis_threshold_seconds": safe_float(record.get("analysis_threshold_seconds")),
                "estimated_probability": safe_float(record.get("final_probability")),
                "market_price": safe_float(record.get("market_price")),
                "edge": (
                    safe_float(record.get("final_probability")) - safe_float(record.get("market_price"))
                    if safe_float(record.get("final_probability")) is not None and safe_float(record.get("market_price")) is not None
                    else None
                ),
                "outcome": "signal only",
                "skip_reason": None,
            })
        elif event_type == "SIGNAL":
            stats["counts"]["SIGNAL"] += 1
        elif event_type == "OPPORTUNITY":
            stats["counts"]["OPPORTUNITY"] += 1
            opportunities.append({
                "ts": ts,
                "ticker": str(record.get("ticker") or "").strip(),
                "source": str(record.get("source") or "").strip(),
                "headline": str(record.get("headline") or "").strip(),
                "method": str(record.get("method") or "").strip() or None,
                "llm_direction": str(record.get("llm_direction") or "").strip() or None,
                "llm_magnitude": str(record.get("llm_magnitude") or "").strip() or None,
                "estimated_probability": safe_float(record.get("estimated_probability")),
                "side": str(record.get("side") or "").strip().lower() or None,
                # F-11 P1-C: read with fallback; emit under canonical key only
                "entry_price_cents": safe_float(
                    record.get("entry_price_cents") if record.get("entry_price_cents") is not None
                    else record.get("market_yes_price")
                ),
                "edge": safe_float(record.get("edge")),
            })
        elif event_type == "SKIPPED":
            stats["counts"]["SKIPPED"] += 1
            skip_type = classify_skip_reason(record)
            stats["skip_breakdown"][skip_type] += 1
            skipped.append({
                "ts": ts,
                "ticker": str(record.get("ticker") or "").strip(),
                "source": str(record.get("source") or "").strip(),
                "headline": str(record.get("headline") or "").strip(),
                "reason": str(record.get("reason") or "").strip(),
                "skip_type": skip_type,
            })
        elif event_type == "PAPER_TRADE":
            stats["counts"]["EXECUTED"] += 1
            paper_trades.append({
                "ts": ts,
                "ticker": str(record.get("ticker") or "").strip(),
                "source": str(record.get("signal_source") or "").strip(),
                "headline": str(record.get("signal_headline") or "").strip(),
            })
        elif event_type == "LIVE_ORDER":
            stats["counts"]["EXECUTED"] += 1
            stats["live_execution_attribution_limited"] = True

    stats["lines_total"] = read_stats.lines_total
    stats["lines_malformed"] = read_stats.lines_malformed

    rows = attach_opportunities(detail_rows, opportunities)
    attach_outcomes(
        rows,
        skipped,
        label_builder=lambda event: (
            "opportunity skipped: zero edge"
            if event["skip_type"] == "zero_edge"
            else "opportunity skipped: duplicate"
            if event["skip_type"] == "duplicate"
            else "opportunity skipped: below threshold"
            if event["skip_type"] == "below_threshold"
            else "opportunity skipped: liquidity"
            if event["skip_type"] == "liquidity"
            else "opportunity skipped: risk gate"
            if event["skip_type"] == "risk_gate"
            else "opportunity skipped: other"
        ),
    )
    attach_outcomes(
        rows,
        paper_trades,
        label_builder=lambda _event: "executed",
    )

    for row in rows:
        is_probe = row.get("is_synthetic_probe") is True or row.get("is_startup_probe") is True
        if is_probe:
            stats["llm_observability"]["probe_count"] += 1
        if not is_probe and row.get("llm_attempted") is True:
            stats["llm_observability"]["attempted"] += 1
        if not is_probe and row.get("llm_result_used") is True:
            stats["llm_observability"]["result_used"] += 1
        if not is_probe and row.get("llm_attempted") is True and not row.get("llm_result_used"):
            stats["llm_observability"]["fallback"] += 1
        if row.get("llm_result_status"):
            stats["llm_observability"]["status_counts"][row["llm_result_status"]] += 1
            if not is_probe and str(row["llm_result_status"]).startswith("llm_skipped_routing_"):
                stats["llm_observability"]["skipped_routing"] += 1
                stats["llm_observability"]["skipped_routing_reasons"][
                    str(row["llm_result_status"]).removeprefix("llm_skipped_routing_")
                ] += 1
        if not is_probe and row.get("llm_latency_ms") is not None:
            stats["llm_observability"]["latency_ms_samples"].append(row["llm_latency_ms"])
        if not is_probe and row.get("llm_total_stage_ms") is not None:
            stats["llm_observability"]["total_stage_ms_samples"].append(row["llm_total_stage_ms"])
        elif not is_probe and row.get("llm_latency_ms") is not None:
            stats["llm_observability"]["total_stage_ms_samples"].append(row["llm_latency_ms"])
        if not is_probe and row.get("llm_queue_wait_ms") is not None:
            stats["llm_observability"]["queue_wait_ms_samples"].append(row["llm_queue_wait_ms"])
        if not is_probe and row.get("llm_http_round_trip_ms") is not None:
            stats["llm_observability"]["http_round_trip_ms_samples"].append(row["llm_http_round_trip_ms"])
        if not is_probe and row.get("llm_parse_ms") is not None:
            stats["llm_observability"]["parse_ms_samples"].append(row["llm_parse_ms"])
        if not is_probe and row.get("llm_contention_observed") is True:
            stats["llm_observability"]["contention_observed"] += 1
        if not is_probe and row.get("llm_in_flight_at_entry") is not None:
            stats["llm_observability"]["max_in_flight_at_entry"] = max(
                stats["llm_observability"]["max_in_flight_at_entry"],
                int(row["llm_in_flight_at_entry"]),
            )
        if not is_probe and row.get("pre_llm_would_block_and_useful") is True:
            stats["llm_observability"]["pre_llm_would_block_and_useful"] += 1
        if not is_probe and row.get("method") == "llm" and row.get("estimated_probability") is not None:
            est_prob = float(row["estimated_probability"])
            market_price = safe_float(row.get("market_price"))
            opportunity_edge = (
                safe_float(row.get("edge"))
                if row.get("opportunity_ts") is not None
                else None
            )
            if market_price is None and opportunity_edge is None:
                continue
            abs_move = abs(est_prob - 0.5)
            edge = (
                opportunity_edge
                if opportunity_edge is not None
                else est_prob - float(market_price)
            )
            abs_edge = max(0.0, edge) if opportunity_edge is not None else abs(edge)
            movement_bucket = _llm_probability_movement_bucket(abs_move)
            edge_bucket = _llm_edge_bucket(abs_edge)
            impact = _llm_decision_impact_classification(
                est_prob=est_prob,
                market_price=market_price,
                executable_edge=opportunity_edge,
            )
            created_edge = (
                market_price is not None
                and abs(market_price - 0.5) < LLM_MARKET_NEUTRAL_MAX
                and abs_edge >= LLM_ZERO_EDGE_MAX
            )

            row["llm_probability_movement"] = abs_move
            row["llm_probability_movement_bucket"] = movement_bucket
            row["llm_abs_edge"] = abs_edge
            row["llm_edge_bucket"] = edge_bucket
            row["llm_created_edge"] = created_edge
            row["llm_decision_impact"] = impact
            row["llm_edge_basis"] = (
                "gross_executable"
                if opportunity_edge is not None
                else "gross_midpoint"
            )
            # OPPORTUNITY does not contain the pinned fee schedule, fill role,
            # or rounding state required for a fee-net claim.
            row["llm_fee_net_status"] = "unscored"
            candidate_outcome = None
            if impact == "trade_candidate":
                if row.get("outcome") == "executed":
                    candidate_outcome = "admitted"
                    stats["llm_value_add"]["admitted_trade_candidates"] += 1
                elif str(row.get("outcome") or "").startswith("opportunity skipped:"):
                    if row.get("skip_type") == "risk_gate":
                        candidate_outcome = "blocked_by_risk_gate"
                    else:
                        candidate_outcome = "blocked_by_execution_gate"
                    stats["llm_value_add"]["blocked_trade_candidates"] += 1
                else:
                    candidate_outcome = "pending"
                    stats["llm_value_add"]["pending_trade_candidates"] += 1
            row["llm_candidate_outcome"] = candidate_outcome

            stats["llm_value_add"]["llm_rows"] += 1
            stats["llm_value_add"]["probability_movement_buckets"][movement_bucket] += 1
            stats["llm_value_add"]["edge_magnitude_buckets"][edge_bucket] += 1
            stats["llm_value_add"]["impact_classifications"][impact] += 1
            if movement_bucket == "near_neutral":
                stats["llm_value_add"]["near_neutral_outputs"] += 1
            if abs_edge >= LLM_ZERO_EDGE_MAX:
                stats["llm_value_add"]["non_zero_edge_outputs"] += 1
            if created_edge:
                stats["llm_value_add"]["llm_created_edge"] += 1
            if impact in {"meaningful_signal", "trade_candidate"}:
                stats["llm_value_add"]["meaningful_signals"] += 1
                if row["source"]:
                    stats["llm_value_add"]["meaningful_sources"][row["source"]] += 1
                if row["ticker"]:
                    stats["llm_value_add"]["meaningful_tickers"][row["ticker"]] += 1
                stats["llm_value_add"]["strong_examples"].append(row)
            if impact == "trade_candidate":
                stats["llm_value_add"]["trade_candidates"] += 1
            if impact == "neutral_confirmation":
                stats["llm_value_add"]["neutral_examples"].append(row)
            if impact != "neutral_confirmation":
                stats["llm_value_add"]["rare_non_neutral_examples"].append(row)
            if row["source"]:
                _update_llm_segment_metrics(
                    llm_source_segments[row["source"]],
                    movement_bucket=movement_bucket,
                    abs_edge=abs_edge,
                    impact=impact,
                )
            if row["ticker"]:
                _update_llm_segment_metrics(
                    llm_ticker_segments[row["ticker"]],
                    movement_bucket=movement_bucket,
                    abs_edge=abs_edge,
                    impact=impact,
                )
            if market_price is not None:
                _update_llm_segment_metrics(
                    llm_price_band_segments[_llm_market_price_band(market_price)],
                    movement_bucket=movement_bucket,
                    abs_edge=abs_edge,
                    impact=impact,
                )
            if row.get("age_at_analysis_seconds") is not None:
                _update_llm_segment_metrics(
                    llm_timing_segments[_llm_timing_bucket(float(row["age_at_analysis_seconds"]))],
                    movement_bucket=movement_bucket,
                    abs_edge=abs_edge,
                    impact=impact,
                )
        source = row["source"]
        ticker = row["ticker"]
        if source:
            source_groups[source]["signals"] += 1
            if row.get("opportunity_ts") is not None:
                source_groups[source]["opportunities"] += 1
            if row.get("edge") is not None:
                source_groups[source]["edges"].append(row["edge"])
            if row["outcome"] == "opportunity skipped: zero edge":
                source_groups[source]["zero_edge"] += 1
            if row["outcome"] == "opportunity skipped: duplicate":
                source_groups[source]["duplicate_skip"] += 1
        if ticker:
            ticker_groups[ticker]["signals"] += 1
            if row.get("opportunity_ts") is not None:
                ticker_groups[ticker]["opportunities"] += 1
            if row.get("edge") is not None:
                ticker_groups[ticker]["edges"].append(row["edge"])
            if row["outcome"] == "opportunity skipped: zero edge":
                ticker_groups[ticker]["zero_edge"] += 1
            if row["outcome"] == "opportunity skipped: duplicate":
                ticker_groups[ticker]["duplicate_skip"] += 1

    def finalize_group(name: str, metrics: dict[str, Any], key_name: str) -> dict[str, Any]:
        return {
            key_name: name,
            "signals": metrics["signals"],
            "opportunities": metrics["opportunities"],
            "avg_edge": sum(metrics["edges"]) / len(metrics["edges"]) if metrics["edges"] else None,
            "median_edge": median(metrics["edges"]) if metrics["edges"] else None,
            "zero_edge": metrics["zero_edge"],
            "duplicate_skip": metrics["duplicate_skip"],
        }

    stats["audit_rows"] = sorted(
        rows,
        key=lambda row: row["ts"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    stats["llm_value_add"]["strong_examples"] = sorted(
        stats["llm_value_add"]["strong_examples"],
        key=lambda row: (-(row.get("llm_abs_edge") or 0.0), row["ts"] or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=False,
    )
    stats["llm_value_add"]["strong_examples"].reverse()
    stats["llm_value_add"]["neutral_examples"] = sorted(
        stats["llm_value_add"]["neutral_examples"],
        key=lambda row: row["ts"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    stats["llm_value_add"]["rare_non_neutral_examples"] = sorted(
        stats["llm_value_add"]["rare_non_neutral_examples"],
        key=lambda row: (
            -(row.get("llm_abs_edge") or 0.0),
            row["ts"] or datetime.min.replace(tzinfo=timezone.utc),
        ),
    )
    stats["llm_value_add"]["segmentation"]["by_source"] = _finalize_llm_segment_rows(
        llm_source_segments,
        key_name="source",
    )
    stats["llm_value_add"]["segmentation"]["by_ticker"] = _finalize_llm_segment_rows(
        llm_ticker_segments,
        key_name="ticker",
    )
    stats["llm_value_add"]["segmentation"]["by_price_band"] = _finalize_llm_segment_rows(
        llm_price_band_segments,
        key_name="price_band",
    )
    if llm_timing_segments:
        stats["llm_value_add"]["segmentation"]["timing"] = {
            "available": True,
            "by_age_bucket": _finalize_llm_segment_rows(
                llm_timing_segments,
                key_name="age_bucket",
            ),
        }
    stats["by_source"] = sorted(
        [finalize_group(name, metrics, "source") for name, metrics in source_groups.items()],
        key=lambda row: (-row["signals"], -(row["opportunities"]), row["source"].lower()),
    )
    stats["by_ticker"] = sorted(
        [finalize_group(name, metrics, "ticker") for name, metrics in ticker_groups.items()],
        key=lambda row: (-row["signals"], -(row["opportunities"]), row["ticker"].lower()),
    )
    return stats


def format_audit_rows(rows: list[dict[str, Any]], limit: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    lines = []
    for row in rows[:limit]:
        lines.append(
            "  "
            f"{fmt_ts(row['ts'])}  "
            f"source={row['source'] or 'n/a'}  "
            f"ticker={row['ticker'] or 'n/a'}  "
            f"market_prob={fmt_prob(row['market_price'])}  "
            f"est_prob={fmt_prob(row['estimated_probability'])}  "
            f"edge={fmt_prob(row['edge'])}  "
            f"method={row['method'] or 'n/a'}  "
            f"llm_dir={row['llm_direction'] or 'n/a'}  "
            f"llm_mag={row['llm_magnitude'] or 'n/a'}  "
            f"outcome={row['outcome']}  "
            f"headline={row['headline'][:80] if row['headline'] else 'n/a'}"
        )
    return lines


def format_group_rows(rows: list[dict[str, Any]], *, label: str, top: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    shown = rows[:top]
    name_width = max(len(str(row[label])) for row in shown)
    signal_width = max(len(str(row["signals"])) for row in shown)
    opp_width = max(len(str(row["opportunities"])) for row in shown)
    zero_width = max(len(str(row["zero_edge"])) for row in shown)
    dup_width = max(len(str(row["duplicate_skip"])) for row in shown)
    lines = []
    for row in shown:
        lines.append(
            "  "
            f"{row[label]:<{name_width}}  "
            f"signals={row['signals']:>{signal_width}}  "
            f"opportunities={row['opportunities']:>{opp_width}}  "
            f"avg_edge={fmt_prob(row['avg_edge'])}  "
            f"median_edge={fmt_prob(row['median_edge'])}  "
            f"zero_edge={row['zero_edge']:>{zero_width}}  "
            f"duplicate_skip={row['duplicate_skip']:>{dup_width}}"
        )
    return lines


def format_llm_value_rows(rows: list[dict[str, Any]], limit: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    lines = []
    for row in rows[:limit]:
        lines.append(
            "  "
            f"{fmt_ts(row['ts'])}  "
            f"ticker={row['ticker'] or 'n/a'}  "
            f"source={row['source'] or 'n/a'}  "
            f"est_prob={fmt_prob(row.get('estimated_probability'))}  "
            f"market_prob={fmt_prob(row.get('market_price'))}  "
            f"side={row.get('opportunity_side') or 'n/a'}  "
            f"exec_cents={fmt_prob(row.get('executable_price_cents'))}  "
            f"edge={fmt_prob(row.get('edge'))}  "
            f"basis={row.get('llm_edge_basis') or 'n/a'}  "
            f"fee_net={row.get('llm_fee_net_status') or 'n/a'}  "
            f"impact={row.get('llm_decision_impact') or 'n/a'}  "
            f"candidate={row.get('llm_candidate_outcome') or 'n/a'}  "
            f"headline={row['headline'][:80] if row['headline'] else 'n/a'}"
        )
    return lines


def print_summary(
    stats: dict[str, Any],
    *,
    top: int,
    recent: int,
    since: datetime | None,
    until: datetime | None,
) -> None:
    print("SIGNAL-TO-EDGE DIAGNOSTICS")
    print(f"Path: {stats['path']}")
    if since or until:
        since_text = since.date().isoformat() if since else "(beginning)"
        until_text = until.date().isoformat() if until else "(latest)"
        print(f"Date range: {since_text} -> {until_text}")
    print(f"Lines read: {stats['lines_total']}")
    print(f"Malformed lines skipped: {stats['lines_malformed']}")
    print(f"Records included: {stats['records_kept']}")

    print()
    print("Attribution Notes")
    print("  Signal-bearing rows are anchored on SIGNAL_ANALYSIS_DETAIL and stitched to OPPORTUNITY, SKIPPED, and PAPER_TRADE when structured keys line up.")
    print("  Live orders are counted in the cohort summary, but source/headline attribution for LIVE_ORDER remains limited in current logs.")
    if stats["live_execution_attribution_limited"]:
        print("  LIVE_ORDER records were present in this window; per-event executed attribution may be incomplete for live trades.")

    print()
    print("Recent Signal Cohort")
    for event_type in ("SIGNAL_ANALYSIS_DETAIL", "SIGNAL", "OPPORTUNITY", "SKIPPED", "EXECUTED"):
        print(f"  {event_type:22s}: {stats['counts'][event_type]}")

    print()
    print("Zero-Edge Breakdown")
    print(f"  Zero-edge skips           : {stats['skip_breakdown']['zero_edge']}")
    print(f"  Non-zero below-threshold  : {stats['skip_breakdown']['below_threshold']}")
    print(f"  Duplicate-position skips  : {stats['skip_breakdown']['duplicate']}")
    print(f"  Liquidity skips           : {stats['skip_breakdown']['liquidity']}")
    print(f"  Risk-gate skips           : {stats['skip_breakdown']['risk_gate']}")
    print(f"  Other skip reasons        : {stats['skip_breakdown']['other']}")

    print()
    print("LLM Path Observability")
    print(f"  LLM attempted             : {stats['llm_observability']['attempted']} (excludes startup probes)")
    print(f"  LLM result used           : {stats['llm_observability']['result_used']}")
    print(f"  LLM fallback              : {stats['llm_observability']['fallback']}")
    print(f"  LLM skipped (routing)     : {stats['llm_observability']['skipped_routing']}")
    print(f"  Startup probes (excluded) : {stats['llm_observability']['probe_count']}")
    for status, count in stats["llm_observability"]["status_counts"].most_common(5):
        print(f"  status[{status}]          : {count}")
    if stats["llm_observability"]["skipped_routing_reasons"]:
        print(
            "  Routing skip reasons      : "
            + ", ".join(
                f"{reason}={count}"
                for reason, count in stats["llm_observability"]["skipped_routing_reasons"].most_common()
            )
        )
    print(f"  LLM latency               : {fmt_latency_stats(stats['llm_observability']['latency_ms_samples'])}")
    print(f"  LLM total stage           : {fmt_latency_stats(stats['llm_observability']['total_stage_ms_samples'])}")
    print(f"  LLM queue wait            : {fmt_latency_stats(stats['llm_observability']['queue_wait_ms_samples'])}")
    print(f"  LLM HTTP round-trip       : {fmt_latency_stats(stats['llm_observability']['http_round_trip_ms_samples'])}")
    print(f"  LLM parse time            : {fmt_latency_stats(stats['llm_observability']['parse_ms_samples'])}")
    print(f"  LLM contention observed   : {stats['llm_observability']['contention_observed']}")
    print(f"  LLM max in-flight entry   : {stats['llm_observability']['max_in_flight_at_entry']}")

    print()
    print("LLM Value-Add Analysis")
    llm_value = stats["llm_value_add"]
    llm_rows = llm_value["llm_rows"]
    print(f"  LLM rows                  : {llm_rows}")
    print(f"  Near-neutral outputs      : {llm_value['near_neutral_outputs']} ({_pct(llm_value['near_neutral_outputs'], llm_rows)})")
    print(f"  Non-zero edge outputs     : {llm_value['non_zero_edge_outputs']} ({_pct(llm_value['non_zero_edge_outputs'], llm_rows)})")
    print(f"  Meaningful signals        : {llm_value['meaningful_signals']} ({_pct(llm_value['meaningful_signals'], llm_rows)})")
    print(f"  Model gross-edge candidates (fee unscored): {llm_value['trade_candidates']} ({_pct(llm_value['trade_candidates'], llm_rows)})")
    print(f"  Candidates admitted       : {llm_value['admitted_trade_candidates']}")
    print(f"  Candidates blocked        : {llm_value['blocked_trade_candidates']}")
    print(f"  Candidates pending        : {llm_value['pending_trade_candidates']}")
    print(
        f"  LLM created edge @0.50    : {llm_value['llm_created_edge']} "
        f"({_pct(llm_value['llm_created_edge'], llm_rows)})"
    )
    print(
        "  Probability movement      : "
        + _format_bucket_counter(
            llm_value["probability_movement_buckets"],
            [
                ("near_neutral", "near-neutral"),
                ("weak", "weak"),
                ("moderate", "moderate"),
                ("strong", "strong"),
            ],
        )
    )
    print(
        "  Edge magnitude            : "
        + _format_bucket_counter(
            llm_value["edge_magnitude_buckets"],
            [
                ("zero_neutral", "zero/neutral"),
                ("weak", "weak"),
                ("moderate", "moderate"),
                ("strong", "strong"),
            ],
        )
    )
    print(
        "  Decision impact           : "
        + _format_bucket_counter(
            llm_value["impact_classifications"],
            [
                ("neutral_confirmation", "neutral_confirmation"),
                ("weak_signal", "weak_signal"),
                ("meaningful_signal", "meaningful_signal"),
                ("trade_candidate", "trade_candidate"),
            ],
        )
    )
    if llm_value["meaningful_sources"]:
        print("  Top meaningful sources")
        for label, count in llm_value["meaningful_sources"].most_common(top):
            print(f"    {label}: {count}")
    if llm_value["meaningful_tickers"]:
        print("  Top meaningful tickers")
        for label, count in llm_value["meaningful_tickers"].most_common(top):
            print(f"    {label}: {count}")

    print()
    print("LLM Value-Add Segmentation")
    source_segments = llm_value["segmentation"].get("by_source", [])
    ticker_segments = llm_value["segmentation"].get("by_ticker", [])
    price_band_segments = llm_value["segmentation"].get("by_price_band", [])
    print("  Top sources by meaningful signal rate")
    for line in format_llm_segmentation_rows(
        _sort_segment_rows(source_segments, label_key="source", rate_key="meaningful_signal_rate"),
        label_key="source",
        limit=top,
    ):
        print(line)
    print("  Top sources by neutral-confirmation rate")
    for line in format_llm_segmentation_rows(
        _sort_segment_rows(source_segments, label_key="source", rate_key="neutral_confirmation_rate"),
        label_key="source",
        limit=top,
    ):
        print(line)
    print("  Top tickers by meaningful signal rate")
    for line in format_llm_segmentation_rows(
        _sort_segment_rows(ticker_segments, label_key="ticker", rate_key="meaningful_signal_rate"),
        label_key="ticker",
        limit=top,
    ):
        print(line)
    print("  Market price bands by meaningful signal rate")
    for line in format_llm_segmentation_rows(
        _sort_segment_rows(price_band_segments, label_key="price_band", rate_key="meaningful_signal_rate"),
        label_key="price_band",
        limit=len(LLM_PRICE_BAND_LABELS),
    ):
        print(line)
    timing_segmentation = llm_value["segmentation"].get("timing", {})
    print(
        "  Freshness/timing segmentation   : "
        + ("available" if timing_segmentation.get("available") else f"unavailable ({timing_segmentation.get('reason', 'n/a')})")
    )
    category_segmentation = llm_value["segmentation"].get("headline_category", {})
    print(
        "  Headline/category segmentation  : "
        + ("available" if category_segmentation.get("available") else f"unavailable ({category_segmentation.get('reason', 'n/a')})")
    )
    print("  Rare non-neutral examples")
    for line in format_llm_value_rows(llm_value["rare_non_neutral_examples"], recent):
        print(line)

    print()
    print("Strong LLM Signal Examples")
    for line in format_llm_value_rows(llm_value["strong_examples"], recent):
        print(line)

    print()
    print("Neutral Confirmation Examples")
    for line in format_llm_value_rows(llm_value["neutral_examples"], recent):
        print(line)

    print()
    print(f"Per-Event Edge Audit (most recent {recent})")
    for line in format_audit_rows(stats["audit_rows"], recent):
        print(line)

    print()
    print(f"Aggregate by Source (top {top})")
    for line in format_group_rows(stats["by_source"], label="source", top=top):
        print(line)

    print()
    print(f"Aggregate by Ticker (top {top})")
    for line in format_group_rows(stats["by_ticker"], label="ticker", top=top):
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

    stats = summarize(
        Path(args.path),
        since,
        until,
        exclude_test=args.exclude_test,
    )
    print_summary(
        stats,
        top=max(1, args.top),
        recent=max(1, args.recent),
        since=since,
        until=until,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
