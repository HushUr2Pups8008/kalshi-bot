#!/usr/bin/env python3
"""Audit Cycle-16D decision-time price coverage before replay scoring."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.edge_replay.build_replay_dataset import load_historical_prices, price_at_decision


DEFAULT_DATASET = Path("logs/edge_replay/cycle15b/replay_dataset.jsonl")
DEFAULT_PRICES = Path("logs/edge_replay/cycle16d/historical_prices_cycle16d.json")
DEFAULT_POST_FIX_DB = Path("data/dossier_updates_post_fix.db")
DEFAULT_OUTPUT = Path("logs/edge_replay/cycle16d/coverage_audit.json")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_reingestion_metadata(db_path: Path) -> dict[str, str]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "reingestion_metadata" not in tables:
            return {}
        return {
            str(key): str(value)
            for key, value in conn.execute("SELECT key, value FROM reingestion_metadata")
        }
    finally:
        conn.close()


def cohort_sentinel(db_path: Path | None) -> dict[str, Any]:
    if db_path is None:
        return {"status": "not_checked", "reason": "no_post_fix_db_supplied"}
    metadata = load_reingestion_metadata(db_path)
    expected_commit = "2222227"
    expected_ts = "2026-05-07T00:00:00+00:00"
    commit_ok = metadata.get("cycle_15b_c7_deploy_commit") == expected_commit
    ts_ok = metadata.get("cycle_15b_c7_deploy_ts") == expected_ts
    return {
        "status": "pass" if commit_ok and ts_ok else "fail",
        "cohort_flag": "post_fix_rebuilt" if commit_ok and ts_ok else "unknown",
        "db_path": str(db_path),
        "cycle_15b_c7_deploy_commit": metadata.get("cycle_15b_c7_deploy_commit"),
        "cycle_15b_c7_deploy_ts": metadata.get("cycle_15b_c7_deploy_ts"),
        "expected_cycle_15b_c7_deploy_commit": expected_commit,
        "expected_cycle_15b_c7_deploy_ts": expected_ts,
    }


def _coverage(count: int, total: int) -> float:
    return count / total if total else 0.0


def _missing_reason(row: dict[str, Any], price_tickers: set[str]) -> str:
    ticker = str(row.get("ticker") or "")
    if _as_float(row.get("market_yes_price")) is not None:
        return "covered_existing_market_yes_price"
    if not ticker:
        return "missing_ticker"
    if ticker not in price_tickers:
        return "no_price_series_for_ticker"
    if not row.get("decision_ts"):
        return "missing_decision_ts"
    return "no_price_at_or_before_decision_ts"


def audit_price_coverage(
    rows: list[dict[str, Any]],
    prices_path: Path,
    *,
    post_fix_db: Path | None = DEFAULT_POST_FIX_DB,
    overall_threshold: float = 0.90,
    anomaly_threshold: float = 0.80,
    escalation_threshold: float = 0.70,
) -> dict[str, Any]:
    prices = load_historical_prices(prices_path)
    price_tickers = set(prices)

    per_ticker_counts: dict[str, Counter[str]] = defaultdict(Counter)
    missing_examples: list[dict[str, Any]] = []
    covered_rows = 0

    for row in rows:
        ticker = str(row.get("ticker") or "")
        existing_price = _as_float(row.get("market_yes_price"))
        reconstructed_price = price_at_decision(prices, ticker, row.get("decision_ts"))
        covered = existing_price is not None or reconstructed_price is not None
        reason = "covered_reconstructed_price" if reconstructed_price is not None else _missing_reason(row, price_tickers)

        per_ticker_counts[ticker]["total"] += 1
        if covered:
            covered_rows += 1
            per_ticker_counts[ticker]["covered"] += 1
            if existing_price is not None:
                per_ticker_counts[ticker]["covered_existing"] += 1
            if reconstructed_price is not None:
                per_ticker_counts[ticker]["covered_reconstructed"] += 1
        else:
            per_ticker_counts[ticker]["missing"] += 1
            per_ticker_counts[ticker][reason] += 1
            if len(missing_examples) < 20:
                missing_examples.append(
                    {
                        "ticker": ticker,
                        "decision_ts": row.get("decision_ts"),
                        "decision_kind": row.get("decision_kind"),
                        "reason": reason,
                    }
                )

    total_rows = len(rows)
    overall_coverage = _coverage(covered_rows, total_rows)
    per_ticker = []
    anomalies = []
    escalations = []
    for ticker, counts in sorted(per_ticker_counts.items()):
        total = int(counts["total"])
        covered = int(counts["covered"])
        ticker_coverage = _coverage(covered, total)
        entry = {
            "ticker": ticker,
            "total_rows": total,
            "covered_rows": covered,
            "missing_rows": int(counts["missing"]),
            "coverage": round(ticker_coverage, 6),
            "covered_existing_rows": int(counts["covered_existing"]),
            "covered_reconstructed_rows": int(counts["covered_reconstructed"]),
            "missing_reasons": {
                key: int(value)
                for key, value in sorted(counts.items())
                if key
                not in {
                    "total",
                    "covered",
                    "missing",
                    "covered_existing",
                    "covered_reconstructed",
                }
            },
        }
        per_ticker.append(entry)
        if total > 0 and ticker_coverage < anomaly_threshold:
            anomalies.append(entry)
        if total > 0 and ticker_coverage < escalation_threshold:
            escalations.append(entry)

    status = "pass" if overall_coverage >= overall_threshold else "fail"
    if overall_coverage < escalation_threshold:
        status = "escalation_required"
    elif overall_coverage < overall_threshold:
        status = "extension_needed"

    return {
        "input": {
            "dataset_rows": total_rows,
            "historical_prices_path": str(prices_path),
            "post_fix_db": str(post_fix_db) if post_fix_db is not None else None,
            "price_ticker_count": len(price_tickers),
        },
        "thresholds": {
            "overall_pass": overall_threshold,
            "per_ticker_anomaly": anomaly_threshold,
            "escalation": escalation_threshold,
        },
        "overall": {
            "status": status,
            "coverage": round(overall_coverage, 6),
            "covered_rows": covered_rows,
            "missing_rows": total_rows - covered_rows,
            "passes_overall_threshold": overall_coverage >= overall_threshold,
        },
        "per_ticker": per_ticker,
        "anomalies": anomalies,
        "escalations": escalations,
        "cohort_sentinel": cohort_sentinel(post_fix_db),
        "missing_examples": missing_examples,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--historical-prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--post-fix-db", type=Path, default=DEFAULT_POST_FIX_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overall-threshold", type=float, default=0.90)
    parser.add_argument("--anomaly-threshold", type=float, default=0.80)
    parser.add_argument("--escalation-threshold", type=float, default=0.70)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = audit_price_coverage(
        load_jsonl(args.dataset),
        args.historical_prices,
        post_fix_db=args.post_fix_db,
        overall_threshold=args.overall_threshold,
        anomaly_threshold=args.anomaly_threshold,
        escalation_threshold=args.escalation_threshold,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["overall"]["status"],
                "coverage": report["overall"]["coverage"],
                "covered_rows": report["overall"]["covered_rows"],
                "missing_rows": report["overall"]["missing_rows"],
                "anomaly_count": len(report["anomalies"]),
                "escalation_count": len(report["escalations"]),
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["overall"]["passes_overall_threshold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
