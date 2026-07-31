"""Read-only classification of diagnostic G7 skip evidence receipts."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trading.g7_skip_evidence import (
    G7_SKIP_EVIDENCE_DB,
    G7SkipEvidenceRecord,
    canonical_json,
    read_g7_skip_evidence_records,
)


G7_SKIP_EVIDENCE_REPLAY_REPORT_VERSION = 1
_PROMOTION_FAILURE_REASONS = [
    "diagnostic_only_scope",
    "financial_evidence_not_evaluated_by_g7_receipt_report",
    "repeatable_profitability_not_established_by_g7_receipt_report",
]


def classify_record(record: G7SkipEvidenceRecord) -> str:
    """Classify only the immutable decision-time liquidity provenance."""
    if record.liquidity_evidence_status == "unavailable":
        return "unavailable_liquidity_evidence"
    if record.liquidity_evidence_status == "not_queried":
        return "not_queried_liquidity_evidence"

    executable_notional = float(record.execution_liquidity["executable_notional"])
    minimum_liquidity = float(record.g7_inputs["minimum_market_liquidity_dollars"])
    if executable_notional < minimum_liquidity:
        return "observed_insufficient_liquidity"
    return "observed_sufficient_liquidity"


def build_report(records: Sequence[G7SkipEvidenceRecord]) -> dict[str, Any]:
    """Build a deterministic report without evaluating returns or promotions."""
    ordered_records = tuple(sorted(records, key=lambda record: record.evidence_id))
    classification_counts = Counter(classify_record(record) for record in ordered_records)
    status_counts = Counter(
        record.liquidity_evidence_status for record in ordered_records
    )
    failure_counts = Counter(
        failure for record in ordered_records for failure in record.g7_failures
    )
    report: dict[str, Any] = {
        "report_version": G7_SKIP_EVIDENCE_REPLAY_REPORT_VERSION,
        "mode": "read_only_g7_skip_evidence_classification",
        "manifest": {
            "receipt_scope": "decision_time_existing_execution_liquidity_metadata",
            "financial_evaluation": "not_performed",
            "not_trade_or_pnl_evidence": True,
            "promotion_policy": "always_ineligible_without_separate_financial_evidence",
        },
        "coverage": {
            "receipt_rows": len(ordered_records),
            "liquidity_evidence_status_counts": dict(sorted(status_counts.items())),
            "classification_counts": dict(sorted(classification_counts.items())),
            "g7_failure_counts": dict(sorted(failure_counts.items())),
        },
        "promotion": {
            "eligible": False,
            "failure_reasons": list(_PROMOTION_FAILURE_REASONS),
        },
    }
    report["report_sha256"] = sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only G7 skip evidence classification report",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=G7_SKIP_EVIDENCE_DB,
        help="isolated G7 evidence SQLite database",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report output path",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_report(read_g7_skip_evidence_records(args.db))
    encoded = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
