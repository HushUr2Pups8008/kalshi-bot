"""Read-only observability completeness review for Stage 4.2."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.diagnostics_script_helpers import in_window, parse_date_end, parse_date_start, parse_iso_ts
from utils.logger import BLEND_DECISION_REQUIRED_FIELDS


DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"
COMPLETENESS_TARGET = 0.90


@dataclass(frozen=True)
class FieldCompleteness:
    field: str
    present_count: int
    non_null_count: int
    malformed_count: int
    total: int

    @property
    def non_null_rate(self) -> float | None:
        if self.total == 0:
            return None
        return self.non_null_count / self.total


def load_records(
    path: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for file_path in _iter_jsonl_files(path):
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if in_window(parse_iso_ts(record.get("ts")), since, until):
                    records.append(record)
    return records


def summarize(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    by_type = Counter(str(row.get("type") or "") for row in rows)
    blend_rows = [row for row in rows if row.get("type") == "BLEND_DECISION"]

    field_results = [
        _field_completeness(field, blend_rows)
        for field in BLEND_DECISION_REQUIRED_FIELDS
    ]
    below_threshold = [
        result.field
        for result in field_results
        if result.non_null_rate is None or result.non_null_rate < COMPLETENESS_TARGET
    ]

    traceability = _traceability(rows, blend_rows)
    return {
        "event_counts": by_type,
        "blend_decision_total": len(blend_rows),
        "required_fields": BLEND_DECISION_REQUIRED_FIELDS,
        "field_results": field_results,
        "below_threshold": below_threshold,
        "target_met": len(blend_rows) > 0 and not below_threshold,
        "traceability": traceability,
    }


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        "OBSERVABILITY COMPLETENESS REVIEW",
        "",
        "EVENT COUNTS",
    ]
    for event_type, count in sorted(summary["event_counts"].items()):
        if event_type:
            lines.append(f"  {event_type:<32} {count}")

    lines.extend(
        [
            "",
            "BLEND_DECISION REQUIRED FIELD COMPLETENESS",
            f"  Total BLEND_DECISION events: {summary['blend_decision_total']}",
            f"  Target: {COMPLETENESS_TARGET:.0%} non-null per required field",
        ]
    )
    for result in summary["field_results"]:
        rate = "n/a" if result.non_null_rate is None else f"{result.non_null_rate:.1%}"
        lines.append(
            f"  {result.field:<32} present={result.present_count:>4} "
            f"non_null={result.non_null_count:>4} malformed={result.malformed_count:>4} "
            f"rate={rate}"
        )
    below = summary["below_threshold"]
    lines.append(f"  Fields below threshold: {', '.join(below) if below else 'none'}")
    lines.append(f"  Target met: {summary['target_met']}")

    trace = summary["traceability"]
    lines.extend(
        [
            "",
            "TRACEABILITY",
            f"  Evidence ingestion events:       {trace['evidence_ingestion_count']}",
            f"  Dossier update events:           {trace['dossier_update_count']}",
            f"  Structural recompute events:     {trace['structural_prior_recompute_count']}",
            f"  Blend decisions:                 {trace['blend_decision_count']}",
            f"  Outcome events:                  {trace['outcome_event_count']}",
            f"  Dossier evidence link gaps:      {trace['dossier_missing_evidence_links']}",
            f"  Blend dossier link gaps:         {trace['blend_missing_dossier_links']}",
            f"  Blend structural link gaps:      {trace['blend_missing_structural_links']}",
            f"  Outcome blend link gaps:         {trace['outcome_missing_blend_links']}",
            f"  Blocked blend reason gaps:       {trace['blocked_reason_gaps']}",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--since", help="Inclusive start date in YYYY-MM-DD")
    parser.add_argument("--until", help="Inclusive end date in YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    summary = summarize(
        load_records(
            args.path,
            since=parse_date_start(args.since),
            until=parse_date_end(args.until),
        )
    )
    if args.json:
        print(_json_ready(summary))
    else:
        print(render_report(summary))
    return 0


def _field_completeness(
    field: str,
    rows: list[dict[str, Any]],
) -> FieldCompleteness:
    present = 0
    non_null = 0
    malformed = 0
    for row in rows:
        if field in row:
            present += 1
        value = row.get(field)
        if value is not None:
            non_null += 1
        if value is not None and _is_malformed(field, value):
            malformed += 1
    return FieldCompleteness(
        field=field,
        present_count=present,
        non_null_count=non_null,
        malformed_count=malformed,
        total=len(rows),
    )


def _is_malformed(field: str, value: Any) -> bool:
    if field == "regime_weights":
        return not isinstance(value, dict) or not value
    if field == "evidence_ids_contributing":
        return not isinstance(value, list)
    if field == "trade_considered":
        return not isinstance(value, bool)
    if field in {
        "fast_lane_p",
        "fast_lane_confidence",
        "accumulation_p",
        "accumulation_confidence",
        "structural_p",
        "structural_confidence",
        "regime_confidence",
        "blended_p",
        "blended_confidence",
        "disagreement_score",
    }:
        return isinstance(value, bool) or not isinstance(value, (int, float))
    return False


def _traceability(
    rows: list[dict[str, Any]],
    blend_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids_by_market: dict[str, set[str]] = defaultdict(set)
    dossier_ids_by_market: dict[str, set[str]] = defaultdict(set)
    structural_markets: set[str] = set()
    blend_markets: set[str] = set()

    dossier_missing_evidence_links = 0
    for row in rows:
        event_type = row.get("type")
        market = _market(row)
        if event_type == "EVIDENCE_INGESTION" and market and row.get("evidence_id"):
            evidence_ids_by_market[market].add(str(row["evidence_id"]))
        elif event_type == "DOSSIER_UPDATE":
            ids = row.get("evidence_ids_contributing")
            if market and isinstance(ids, list):
                for evidence_id in ids:
                    dossier_ids_by_market[market].add(str(evidence_id))
                    if str(evidence_id) not in evidence_ids_by_market[market]:
                        dossier_missing_evidence_links += 1
        elif event_type == "STRUCTURAL_PRIOR_RECOMPUTE" and market:
            structural_markets.add(market)
        elif event_type == "BLEND_DECISION" and market:
            blend_markets.add(market)

    blend_missing_dossier_links = 0
    blend_missing_structural_links = 0
    blocked_reason_gaps = 0
    for row in blend_rows:
        market = _market(row)
        ids = row.get("evidence_ids_contributing")
        if isinstance(ids, list) and ids:
            for evidence_id in ids:
                if market and str(evidence_id) not in dossier_ids_by_market[market]:
                    blend_missing_dossier_links += 1
        if row.get("structural_p") is not None and market not in structural_markets:
            blend_missing_structural_links += 1
        if row.get("trade_considered") is True and row.get("trade_blocked_reason") in ("", None):
            blocked_reason_gaps += 1

    outcome_rows = [
        row
        for row in rows
        if row.get("type") in {"PAPER_TRADE", "LIVE_ORDER", "SKIPPED"}
    ]
    outcome_missing_blend_links = sum(
        1
        for row in outcome_rows
        if _market(row) and _market(row) not in blend_markets
    )

    event_counts = Counter(str(row.get("type") or "") for row in rows)
    return {
        "evidence_ingestion_count": event_counts["EVIDENCE_INGESTION"],
        "dossier_update_count": event_counts["DOSSIER_UPDATE"],
        "structural_prior_recompute_count": event_counts["STRUCTURAL_PRIOR_RECOMPUTE"],
        "blend_decision_count": event_counts["BLEND_DECISION"],
        "outcome_event_count": len(outcome_rows),
        "dossier_missing_evidence_links": dossier_missing_evidence_links,
        "blend_missing_dossier_links": blend_missing_dossier_links,
        "blend_missing_structural_links": blend_missing_structural_links,
        "outcome_missing_blend_links": outcome_missing_blend_links,
        "blocked_reason_gaps": blocked_reason_gaps,
    }


def _market(row: dict[str, Any]) -> str | None:
    value = row.get("market_ticker") or row.get("ticker")
    return str(value) if value else None


def _iter_jsonl_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    if not path.exists():
        return
    yield from sorted(path.rglob("*.jsonl"))


def _json_ready(summary: dict[str, Any]) -> str:
    payload = {
        **summary,
        "event_counts": dict(summary["event_counts"]),
        "field_results": [
            {
                "field": result.field,
                "present_count": result.present_count,
                "non_null_count": result.non_null_count,
                "malformed_count": result.malformed_count,
                "total": result.total,
                "non_null_rate": result.non_null_rate,
            }
            for result in summary["field_results"]
        ],
    }
    return json.dumps(payload, sort_keys=True, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
