from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts import decision_funnel_summary
from scripts import freshness_diagnostics
from scripts import source_scorecard
from scripts import trade_log_summary
from utils.diagnostics_script_helpers import parse_date_end, parse_date_start
from utils.trade_log_reader import iter_trade_records


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEGACY_PATH = REPO_ROOT / "logs" / "trades" / "trades.jsonl"
DEFAULT_NEW_ROOT = REPO_ROOT / "logs" / "trades"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "paper_trades.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate analytics parity between legacy and partitioned trade-log layouts"
    )
    parser.add_argument(
        "--legacy-path",
        default=str(DEFAULT_LEGACY_PATH),
        help="Path to the legacy monolithic trade log (default: logs/trades/trades.jsonl)",
    )
    parser.add_argument(
        "--new-root",
        default=str(DEFAULT_NEW_ROOT),
        help="Path to the preferred partitioned trade-log root (default: logs/trades/)",
    )
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Path to paper_trades.db for source scorecard")
    parser.add_argument("--since", help="Inclusive start date in YYYY-MM-DD")
    parser.add_argument("--until", help="Inclusive end date in YYYY-MM-DD")
    parser.add_argument("--exclude-test", action="store_true", help="Exclude synthetic/test records")
    parser.add_argument("--report-path", help="Optional path to save the validation report")
    return parser.parse_args()


def _counter_to_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted((str(key), int(value)) for key, value in counter.items()))


def _compute_per_day_counts(path: Path, since: datetime | None, until: datetime | None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in iter_trade_records(path, since=since, until=until):
        ts = trade_log_summary.parse_iso_ts(record.get("ts"))
        if ts is None:
            continue
        counts[ts.date().isoformat()] += 1
    return _counter_to_dict(counts)


def _normalize_trade_log_summary(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "records_kept": int(stats["records_kept"]),
        "lines_total": int(stats["lines_total"]),
        "lines_malformed": int(stats["lines_malformed"]),
        "event_counts": _counter_to_dict(stats["event_counts"]),
        "skip_reasons": _counter_to_dict(stats["skip_reasons"]),
        "analysis_rejected_reasons": _counter_to_dict(stats["analysis_rejected_reasons"]),
        "signal_types": _counter_to_dict(stats["signal_types"]),
    }


def _normalize_freshness(stats: dict[str, Any]) -> dict[str, Any]:
    sources = {}
    for source, row in sorted(stats["sources"].items()):
        sources[source] = {
            "observed_records": int(row["observed_records"]),
            "early_stale_drops": int(row["early_stale_drops"]),
            "fresh_passes": int(row["fresh_passes"]),
            "age_samples_count": int(row["age_samples_count"]),
            "within_300s": int(row["within_300s"]),
            "over_300s": int(row["over_300s"]),
        }
    return {
        "records_kept": int(stats["records_kept"]),
        "lines_total": int(stats["lines_total"]),
        "lines_malformed": int(stats["lines_malformed"]),
        "age_bearing_early_stale_records": int(stats["age_bearing_early_stale_records"]),
        "sources": sources,
    }


def _normalize_decision_funnel(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "records_kept": int(stats["records_kept"]),
        "lines_total": int(stats["lines_total"]),
        "lines_malformed": int(stats["lines_malformed"]),
        "event_counts": _counter_to_dict(stats["event_counts"]),
        "skip_reasons": _counter_to_dict(stats["skip_reasons"]),
        "analysis_rejected_reasons": _counter_to_dict(stats["analysis_rejected_reasons"]),
        "path_counts": _counter_to_dict(stats["path_counts"]),
        "execution_paths": _counter_to_dict(stats["execution_paths"]),
    }


def _normalize_source_scorecard(stats: dict[str, Any]) -> dict[str, Any]:
    rows = {}
    for row in sorted(stats["rows"], key=lambda item: str(item["source"]).lower()):
        rows[row["source"]] = {
            "observed_records": int(row["observed_records"]),
            "early_stale_drops": int(row["early_stale_drops"]),
            "analysis_stale_rejections": int(row["analysis_stale_rejections"]),
            "signals": int(row["signals"]),
            "paper_trades": int(row["paper_trades"]),
            "resolved_paper_trades": int(row["resolved_paper_trades"]),
            "wins": int(row["wins"]),
            "total_pnl": float(row["total_pnl"]),
            "source_family": str(row["source_family"]),
            "classification": str(row["classification"]),
        }
    return {
        "log_meta": {
            "lines_total": int(stats["log_meta"]["lines_total"]),
            "lines_malformed": int(stats["log_meta"]["lines_malformed"]),
            "records_kept": int(stats["log_meta"]["records_kept"]),
        },
        "db_exists": bool(stats["db_exists"]),
        "rows": rows,
    }


def _build_metric_bundle(
    trade_log_path: Path,
    db_path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool,
) -> dict[str, Any]:
    trade_stats = trade_log_summary.summarize(trade_log_path, since, until, exclude_test=exclude_test)
    freshness_stats = freshness_diagnostics.summarize(trade_log_path, since, until, exclude_test=exclude_test)
    funnel_stats = decision_funnel_summary.summarize(trade_log_path, since, until, exclude_test=exclude_test)
    scorecard_stats = source_scorecard.summarize(
        trade_log_path,
        db_path,
        since,
        until,
        exclude_test=exclude_test,
    )
    return {
        "records": {
            "total_records": int(trade_stats["records_kept"]),
            "lines_total": int(trade_stats["lines_total"]),
            "lines_malformed": int(trade_stats["lines_malformed"]),
            "per_day_counts": _compute_per_day_counts(trade_log_path, since, until),
            "per_event_type_counts": _counter_to_dict(trade_stats["event_counts"]),
        },
        "trade_log_summary": _normalize_trade_log_summary(trade_stats),
        "freshness_diagnostics": _normalize_freshness(freshness_stats),
        "decision_funnel_summary": _normalize_decision_funnel(funnel_stats),
        "source_scorecard": _normalize_source_scorecard(scorecard_stats),
    }


def _flatten(prefix: str, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key in sorted(value):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(child_prefix, value[key]))
        return flattened
    return {prefix: value}


def compare_metric_bundles(legacy_bundle: dict[str, Any], new_bundle: dict[str, Any]) -> dict[str, Any]:
    legacy_flat = _flatten("", legacy_bundle)
    new_flat = _flatten("", new_bundle)
    metric_names = sorted(set(legacy_flat) | set(new_flat))
    matched: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []

    for name in metric_names:
        legacy_value = legacy_flat.get(name)
        new_value = new_flat.get(name)
        if legacy_value == new_value:
            matched.append({"metric": name, "legacy": legacy_value, "new": new_value})
        else:
            delta = None
            if isinstance(legacy_value, (int, float)) and isinstance(new_value, (int, float)):
                delta = new_value - legacy_value
            mismatched.append(
                {
                    "metric": name,
                    "legacy": legacy_value,
                    "new": new_value,
                    "delta": delta,
                }
            )

    return {
        "ok": len(mismatched) == 0,
        "matched_count": len(matched),
        "mismatched_count": len(mismatched),
        "matched": matched,
        "mismatched": mismatched,
    }


def validate_cutover(
    legacy_path: Path,
    new_root: Path,
    db_path: Path,
    since: datetime | None,
    until: datetime | None,
    *,
    exclude_test: bool = False,
) -> dict[str, Any]:
    legacy_bundle = _build_metric_bundle(legacy_path, db_path, since, until, exclude_test)
    new_bundle = _build_metric_bundle(new_root, db_path, since, until, exclude_test)
    comparison = compare_metric_bundles(legacy_bundle, new_bundle)
    return {
        "legacy_path": str(legacy_path),
        "new_root": str(new_root),
        "db_path": str(db_path),
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "comparison": comparison,
        "legacy_bundle": legacy_bundle,
        "new_bundle": new_bundle,
    }


def render_report(result: dict[str, Any]) -> str:
    comparison = result["comparison"]
    lines = [
        "TRADE LOG CUTOVER VALIDATION",
        f"Legacy path : {result['legacy_path']}",
        f"New root    : {result['new_root']}",
        f"DB path     : {result['db_path']}",
        f"Window      : {result['since'] or '-inf'} .. {result['until'] or '+inf'}",
        "",
        "Summary",
        f"  Status            : {'PASS' if comparison['ok'] else 'FAIL'}",
        f"  Matched metrics   : {comparison['matched_count']}",
        f"  Mismatched metrics: {comparison['mismatched_count']}",
        "",
        "Mismatches",
    ]
    if comparison["mismatched"]:
        for row in comparison["mismatched"]:
            delta_text = f" delta={row['delta']}" if row["delta"] is not None else ""
            lines.append(
                f"  {row['metric']}: legacy={row['legacy']!r} new={row['new']!r}{delta_text}"
            )
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Matched Checks")
    if comparison["matched"]:
        for row in comparison["matched"][:20]:
            lines.append(f"  {row['metric']}: {row['legacy']!r}")
        if comparison["matched_count"] > 20:
            lines.append(f"  ... {comparison['matched_count'] - 20} more matched metrics")
    else:
        lines.append("  (none)")

    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    since = parse_date_start(args.since)
    until = parse_date_end(args.until)
    if since and until and since > until:
        raise SystemExit("--since must be on or before --until")

    result = validate_cutover(
        Path(args.legacy_path),
        Path(args.new_root),
        Path(args.db_path),
        since,
        until,
        exclude_test=args.exclude_test,
    )
    report = render_report(result)
    print(report, end="")
    if args.report_path:
        Path(args.report_path).write_text(report, encoding="utf-8")
    if not result["comparison"]["ok"]:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
