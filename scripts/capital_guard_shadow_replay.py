#!/usr/bin/env python3
"""Build a deterministic, read-only OOS prerequisite report.

The shadow ledger records decision-time entry evidence and current authoritative
settlement heads. Its G7 candidates are counterfactual, so market/account
settlement receipts cannot establish candidate-specific fee-net P&L without an
authenticated candidate-to-fill-to-settlement chain. It also lacks correction-
cashflow, executable-mark, baseline, and multi-fill fee state contracts. This
tool therefore never calculates P&L or a promotion decision.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trading.capital_guard_shadow import (
    CAPITAL_GUARD_SHADOW_DB,
    CapitalGuardShadowReplayCandidate,
    CapitalGuardShadowReplaySnapshot,
    canonical_json,
    read_capital_guard_shadow_replay_snapshot,
)


REPLAY_REPORT_VERSION = 4
_ZERO = Decimal("0")
_IMPLEMENTATION_FINANCIAL_PREREQUISITES = (
    "authenticated_candidate_fill_settlement_attribution_missing",
    "settlement_correction_cashflow_contract_missing",
    "post_entry_executable_mark_contract_missing",
    "preregistered_counterfactual_baseline_manifest_missing",
    "multi_fill_fee_state_contract_missing",
)
_FEE_RECEIPT_COVERAGE_PREREQUISITE = "authoritative_settlement_fee_receipt_coverage_missing"


def build_replay_report(
    snapshot: CapitalGuardShadowReplaySnapshot,
    *,
    oos_start: datetime,
    oos_end: datetime,
) -> dict[str, object]:
    """Summarize decision-time OOS coverage without evaluating economics."""
    _validate_report_inputs(snapshot=snapshot, oos_start=oos_start, oos_end=oos_end)
    oos_rows = tuple(candidate for candidate in snapshot.candidates if oos_start <= candidate.decision_at < oos_end)
    ordered_rows = tuple(sorted(oos_rows, key=lambda row: (row.decision_at, row.candidate_id)))
    eligible_rows = tuple(row for row in ordered_rows if row.replay_eligible)
    diagnostic_rows = tuple(row for row in ordered_rows if not row.replay_eligible)
    candidate_diagnostics = [_candidate_diagnostic(row) for row in ordered_rows]
    coverage = _coverage(ordered_rows)
    financial_prerequisites = _financial_prerequisites(coverage)
    failure_reasons = _promotion_failures(
        snapshot=snapshot,
        oos_rows=ordered_rows,
        eligible_rows=eligible_rows,
        financial_prerequisites=financial_prerequisites,
    )
    report: dict[str, object] = {
        "report_version": REPLAY_REPORT_VERSION,
        "mode": "read_only_oos_prerequisite_report",
        "snapshot": {
            "snapshot_version": snapshot.snapshot_version,
            "schema_version": snapshot.schema_version,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "conflict_count": snapshot.conflict_count,
            "settlement_quarantine_count": snapshot.settlement_quarantine_count,
        },
        "oos_window": {
            "decision_at_gte": _timestamp(oos_start),
            "decision_at_lt": _timestamp(oos_end),
        },
        "manifest": {
            "decision_time_filter": "candidate.decision_at",
            "financial_evaluation": "not_performed",
            "settlement_head_policy": "current_authoritative_head_only",
            "promotion_policy": "always_ineligible_until_financial_prerequisites_exist",
        },
        "coverage": {
            "oos_candidate_rows": len(ordered_rows),
            "sole_g7_candidate_rows": len(eligible_rows),
            "diagnostic_nonsole_g7_rows": len(diagnostic_rows),
            **coverage,
        },
        "candidate_diagnostics": candidate_diagnostics,
        "financial_prerequisites": financial_prerequisites,
        "promotion": {
            "eligible": False,
            "failure_reasons": failure_reasons,
        },
    }
    report["report_sha256"] = _sha256(canonical_json(report))
    return report


def _validate_report_inputs(
    *,
    snapshot: CapitalGuardShadowReplaySnapshot,
    oos_start: datetime,
    oos_end: datetime,
) -> None:
    if not isinstance(snapshot, CapitalGuardShadowReplaySnapshot):
        raise TypeError("snapshot must be CapitalGuardShadowReplaySnapshot")
    _require_utc("oos_start", oos_start)
    _require_utc("oos_end", oos_end)
    if oos_start >= oos_end:
        raise ValueError("oos_start must be before oos_end")


def _candidate_diagnostic(
    candidate: CapitalGuardShadowReplayCandidate,
) -> dict[str, object]:
    observation = candidate.current_observation
    result: dict[str, object] = {
        "candidate_id": candidate.candidate_id,
        "decision_at": _timestamp(candidate.decision_at),
        "venue": candidate.venue.value,
        "venue_market_id": candidate.venue_market_id,
        "market_family": candidate.market_family,
        "side": candidate.side,
        "scope": "sole_g7_candidate" if candidate.replay_eligible else "nonsole_g7_diagnostic",
        "recorded_entry_debit_dollars": _decimal_text(candidate.net_entry_debit),
        "financial_evaluation": "not_performed",
    }
    if observation is None:
        if candidate.latest_quarantine_reason is not None:
            result["current_head_status"] = "settlement_quarantined"
            result["blocker"] = "settlement_quarantined:" + candidate.latest_quarantine_reason
        elif candidate.latest_settlement_attempt_status is not None:
            result["current_head_status"] = "awaiting_current_authoritative_head"
            result["blocker"] = "no_current_authoritative_head:" + candidate.latest_settlement_attempt_status
        else:
            result["current_head_status"] = "missing_current_authoritative_head"
            result["blocker"] = "missing_current_authoritative_head"
        result["financial_settlement_present"] = False
        return result

    result["observation_outcome"] = observation.outcome
    result["observation_sha256"] = observation.observation_sha256
    result["observation_effective_at"] = _timestamp(observation.effective_at)
    if observation.outcome == "unresolved":
        result["current_head_status"] = "current_authoritative_head_unresolved"
        result["blocker"] = "current_authoritative_head_unresolved"
        result["financial_settlement_present"] = False
    elif candidate.current_settlement is None:
        result["current_head_status"] = "terminal_head_without_financial_settlement"
        result["blocker"] = "missing_financial_settlement_record"
        result["financial_settlement_present"] = False
    elif candidate.current_settlement.economics_contract_sha256 is None:
        result["current_head_status"] = "terminal_head_with_unscorable_financial_settlement"
        result["blocker"] = "settlement_economics_unscorable:" + str(
            candidate.current_settlement.economics_unscorable_reason
        )
        result["financial_settlement_present"] = True
        result["financial_settlement_economics"] = "unscorable"
    else:
        result["current_head_status"] = "terminal_head_with_typed_financial_settlement"
        result["blocker"] = "settlement_correction_cashflow_contract_missing"
        result["financial_settlement_present"] = True
        result["financial_settlement_economics"] = "typed"
    return result


def _coverage(
    candidates: tuple[CapitalGuardShadowReplayCandidate, ...],
) -> dict[str, object]:
    by_venue: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "candidate_rows": 0,
            "sole_g7_candidate_rows": 0,
            "diagnostic_nonsole_g7_rows": 0,
            "recorded_entry_debit_dollars": _ZERO,
            "current_authoritative_head_rows": 0,
            "terminal_authoritative_head_rows": 0,
            "typed_financial_settlement_rows": 0,
            "unscorable_financial_settlement_rows": 0,
            "sole_g7_terminal_authoritative_head_rows": 0,
            "sole_g7_typed_financial_settlement_rows": 0,
            "sole_g7_unscorable_financial_settlement_rows": 0,
        }
    )
    current_head_rows = 0
    terminal_head_rows = 0
    typed_financial_settlement_rows = 0
    unscorable_financial_settlement_rows = 0
    sole_g7_terminal_authoritative_head_rows = 0
    sole_g7_typed_financial_settlement_rows = 0
    sole_g7_unscorable_financial_settlement_rows = 0
    recorded_entry_debit = _ZERO
    for candidate in candidates:
        values = by_venue[candidate.venue.value]
        values["candidate_rows"] += 1
        values["recorded_entry_debit_dollars"] += candidate.net_entry_debit
        recorded_entry_debit += candidate.net_entry_debit
        if candidate.replay_eligible:
            values["sole_g7_candidate_rows"] += 1
        else:
            values["diagnostic_nonsole_g7_rows"] += 1
        observation = candidate.current_observation
        if observation is None:
            continue
        current_head_rows += 1
        values["current_authoritative_head_rows"] += 1
        if observation.outcome == "unresolved":
            continue
        terminal_head_rows += 1
        values["terminal_authoritative_head_rows"] += 1
        if candidate.replay_eligible:
            sole_g7_terminal_authoritative_head_rows += 1
            values["sole_g7_terminal_authoritative_head_rows"] += 1
        if candidate.current_settlement is not None:
            if candidate.current_settlement.economics_contract_sha256 is None:
                unscorable_financial_settlement_rows += 1
                values["unscorable_financial_settlement_rows"] += 1
                if candidate.replay_eligible:
                    sole_g7_unscorable_financial_settlement_rows += 1
                    values["sole_g7_unscorable_financial_settlement_rows"] += 1
            else:
                typed_financial_settlement_rows += 1
                values["typed_financial_settlement_rows"] += 1
                if candidate.replay_eligible:
                    sole_g7_typed_financial_settlement_rows += 1
                    values["sole_g7_typed_financial_settlement_rows"] += 1
    return {
        "recorded_entry_debit_dollars": _decimal_text(recorded_entry_debit),
        "current_authoritative_head_rows": current_head_rows,
        "terminal_authoritative_head_rows": terminal_head_rows,
        "typed_financial_settlement_rows": typed_financial_settlement_rows,
        "unscorable_financial_settlement_rows": unscorable_financial_settlement_rows,
        "sole_g7_terminal_authoritative_head_rows": sole_g7_terminal_authoritative_head_rows,
        "sole_g7_typed_financial_settlement_rows": sole_g7_typed_financial_settlement_rows,
        "sole_g7_unscorable_financial_settlement_rows": sole_g7_unscorable_financial_settlement_rows,
        "venues": {
            venue: {
                **values,
                "recorded_entry_debit_dollars": _decimal_text(values["recorded_entry_debit_dollars"]),
            }
            for venue, values in sorted(by_venue.items())
        },
    }


def _financial_prerequisites(coverage: dict[str, object]) -> list[str]:
    terminal_rows = coverage.get("sole_g7_terminal_authoritative_head_rows")
    typed_rows = coverage.get("sole_g7_typed_financial_settlement_rows")
    unscorable_rows = coverage.get("sole_g7_unscorable_financial_settlement_rows")
    if not all(isinstance(value, int) for value in (terminal_rows, typed_rows, unscorable_rows)):
        raise TypeError("sole-G7 financial settlement coverage values must be integers")
    prerequisites = list(_IMPLEMENTATION_FINANCIAL_PREREQUISITES)
    if terminal_rows == 0 or typed_rows != terminal_rows or unscorable_rows != 0:
        prerequisites.insert(0, _FEE_RECEIPT_COVERAGE_PREREQUISITE)
    return prerequisites


def _promotion_failures(
    *,
    snapshot: CapitalGuardShadowReplaySnapshot,
    oos_rows: tuple[CapitalGuardShadowReplayCandidate, ...],
    eligible_rows: tuple[CapitalGuardShadowReplayCandidate, ...],
    financial_prerequisites: list[str],
) -> list[str]:
    failures = list(financial_prerequisites)
    if not oos_rows:
        failures.append("no_oos_candidates")
    if not eligible_rows:
        failures.append("no_sole_g7_candidates")
    if snapshot.conflict_count:
        failures.append("shadow_conflicts_present")
    if snapshot.settlement_quarantine_count:
        failures.append("settlement_quarantines_present")
    return failures


def _protected_sqlite_paths(db_path: Path) -> tuple[Path, ...]:
    source = db_path.expanduser().resolve(strict=True)
    return (
        source,
        source.with_name(source.name + "-wal"),
        source.with_name(source.name + "-shm"),
        source.with_name(source.name + "-journal"),
    )


def _safe_output_path(db_path: Path, output_path: Path) -> Path:
    output = output_path.expanduser().resolve(strict=False)
    for protected in _protected_sqlite_paths(db_path):
        if output == protected:
            raise ValueError("output path must not name the shadow ledger or SQLite sidecar")
        if output.exists() and protected.exists() and os.path.samefile(output, protected):
            raise ValueError("output path must not alias the shadow ledger or SQLite sidecar")
    return output


def _write_report_atomically(
    *,
    db_path: Path,
    output_path: Path,
    report: dict[str, object],
) -> Path:
    output = _safe_output_path(db_path, output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output = _safe_output_path(db_path, output)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _safe_output_path(db_path, output)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _timestamp(value: datetime) -> str:
    _require_utc("timestamp", value)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{name} must be UTC")


def _decimal_text(value: Decimal) -> str:
    if value == _ZERO:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=CAPITAL_GUARD_SHADOW_DB)
    parser.add_argument("--oos-start", type=_parse_timestamp, required=True)
    parser.add_argument("--oos-end", type=_parse_timestamp, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    output = _safe_output_path(args.db, args.output)
    report = build_replay_report(
        read_capital_guard_shadow_replay_snapshot(args.db),
        oos_start=args.oos_start,
        oos_end=args.oos_end,
    )
    output = _write_report_atomically(
        db_path=args.db,
        output_path=output,
        report=report,
    )
    print(f"{output} promotion_eligible=False report_sha256={report['report_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
