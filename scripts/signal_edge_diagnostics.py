"""
Read-only diagnostics for the signal -> opportunity -> execution boundary.

This report explains why recent signal-bearing items reached OPPORTUNITY but did
not become trades, focusing on estimated probability, market price, computed
edge, and the final recorded outcome.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime, time, timezone
from pathlib import Path
from statistics import median
from typing import Any

from utils.trade_log_reader import TradeLogReadStats, iter_trade_records


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"
RECENT_AUDIT_DEFAULT = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize signal-to-edge execution diagnostics")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_LOG_PATH),
        help="Path to trade-log file or root (default: logs/trades/; legacy logs/trades/trades.jsonl still supported)",
    )
    parser.add_argument("--since", help="Inclusive start date in YYYY-MM-DD")
    parser.add_argument("--until", help="Inclusive end date in YYYY-MM-DD")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Max rows to show in grouped sections (default: 10)",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=RECENT_AUDIT_DEFAULT,
        help="Number of most recent signal-bearing rows in the audit table (default: 20)",
    )
    parser.add_argument(
        "--exclude-test",
        action="store_true",
        help="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
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
    if "duplicate" in reason or "same-signal" in reason:
        return "duplicate"
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
                "market_price": opp["market_yes_price"],
                "edge": opp["edge"],
                "signal_ts": None,
                "opportunity_ts": opp["ts"],
                "outcome": "opportunity no recorded outcome",
            })
            continue

        row = unmatched_rows[target_idx]
        row["opportunity_ts"] = opp["ts"]
        row["estimated_probability"] = row["estimated_probability"] or opp["estimated_probability"]
        row["market_price"] = row["market_price"] or opp["market_yes_price"]
        row["edge"] = row["edge"] if row["edge"] is not None else opp["edge"]
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
                ticker_only[ticker].popleft()
                break


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False) -> dict[str, Any]:
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
            "other": 0,
        },
        "llm_observability": {
            "attempted": 0,
            "result_used": 0,
            "fallback": 0,
            "status_counts": Counter(),
            "latency_ms_samples": [],
        },
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

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
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
                "estimated_probability": safe_float(record.get("final_probability")),
                "market_price": safe_float(record.get("market_price")),
                "edge": None,
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
                "market_yes_price": safe_float(record.get("market_yes_price")),
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
            else "opportunity skipped: other"
        ),
    )
    attach_outcomes(
        rows,
        paper_trades,
        label_builder=lambda _event: "executed",
    )

    for row in rows:
        if row.get("llm_attempted") is True:
            stats["llm_observability"]["attempted"] += 1
        if row.get("llm_result_used") is True:
            stats["llm_observability"]["result_used"] += 1
        if row.get("llm_attempted") is True and not row.get("llm_result_used"):
            stats["llm_observability"]["fallback"] += 1
        if row.get("llm_result_status"):
            stats["llm_observability"]["status_counts"][row["llm_result_status"]] += 1
        if row.get("llm_latency_ms") is not None:
            stats["llm_observability"]["latency_ms_samples"].append(row["llm_latency_ms"])
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
    print(f"  Other skip reasons        : {stats['skip_breakdown']['other']}")

    print()
    print("LLM Path Observability")
    print(f"  LLM attempted             : {stats['llm_observability']['attempted']}")
    print(f"  LLM result used           : {stats['llm_observability']['result_used']}")
    print(f"  LLM fallback              : {stats['llm_observability']['fallback']}")
    for status, count in stats["llm_observability"]["status_counts"].most_common(5):
        print(f"  status[{status}]          : {count}")
    print(f"  LLM latency               : {fmt_latency_stats(stats['llm_observability']['latency_ms_samples'])}")

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
        recent=max(1, args.recent),
        since=since,
        until=until,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
