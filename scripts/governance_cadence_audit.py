#!/usr/bin/env python3
"""Audit governance cadence stability for the Phase 2 close gate."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


TARGET_HOURS = {
    "fast": 2.0,
    "deep": 24.0,
}


@dataclass(frozen=True)
class CycleStart:
    cycle_id: str
    cadence: str
    started_at: datetime
    run_source: str | None


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_cycle_starts(path: Path) -> Iterable[CycleStart]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") != "GOVERNANCE_CYCLE_START":
                continue
            cycle_id = record.get("cycle_id")
            cadence = record.get("cadence")
            started_at = record.get("started_at")
            if not cycle_id or not cadence or not started_at:
                continue
            yield CycleStart(
                cycle_id=str(cycle_id),
                cadence=str(cadence),
                started_at=_parse_ts(str(started_at)),
                run_source=record.get("run_source"),
            )


def _gap_hours(left: CycleStart, right: CycleStart) -> float:
    return (right.started_at - left.started_at).total_seconds() / 3600.0


def audit_cadence(
    *,
    log_path: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    manual_cycle_ids: set[str] | None = None,
    phase_reset_cycle_ids: set[str] | None = None,
) -> dict:
    manual_cycle_ids = manual_cycle_ids or set()
    phase_reset_cycle_ids = phase_reset_cycle_ids or set()
    all_cycles = sorted(_iter_cycle_starts(log_path), key=lambda row: row.started_at)
    window_cycles = [
        row
        for row in all_cycles
        if (since is None or row.started_at >= since)
        and (until is None or row.started_at <= until)
    ]

    scheduled_cycles = [
        row
        for row in window_cycles
        if row.cycle_id not in manual_cycle_ids
        and (row.run_source in (None, "launchd"))
    ]
    excluded_manual = [
        row
        for row in window_cycles
        if row.cycle_id in manual_cycle_ids
        or row.run_source in ("manual", "smoke")
    ]

    max_inter_cycle_gap_hours = 0.0
    inter_cycle_gap_violations = []
    for left, right in zip(scheduled_cycles, scheduled_cycles[1:]):
        gap = _gap_hours(left, right)
        max_inter_cycle_gap_hours = max(max_inter_cycle_gap_hours, gap)
        if gap > 3.0:
            inter_cycle_gap_violations.append({
                "left_cycle_id": left.cycle_id,
                "right_cycle_id": right.cycle_id,
                "left_started_at": left.started_at.isoformat(),
                "right_started_at": right.started_at.isoformat(),
                "gap_hours": round(gap, 6),
            })

    cadence_results = {}
    cadence_violations = []
    for cadence, target_hours in TARGET_HOURS.items():
        rows = [row for row in scheduled_cycles if row.cadence == cadence]
        lower = target_hours * 0.9
        upper = target_hours * 1.1
        ignored_transitions = []
        violations = []
        for left, right in zip(rows, rows[1:]):
            gap = _gap_hours(left, right)
            if right.cycle_id in phase_reset_cycle_ids:
                ignored_transitions.append({
                    "left_cycle_id": left.cycle_id,
                    "right_cycle_id": right.cycle_id,
                    "gap_hours": round(gap, 6),
                    "reason": "documented_phase_reset",
                })
                continue
            if gap < lower or gap > upper:
                violations.append({
                    "left_cycle_id": left.cycle_id,
                    "right_cycle_id": right.cycle_id,
                    "left_started_at": left.started_at.isoformat(),
                    "right_started_at": right.started_at.isoformat(),
                    "gap_hours": round(gap, 6),
                    "target_hours": target_hours,
                    "deviation_pct": round(abs(gap - target_hours) / target_hours * 100, 3),
                })
        cadence_results[cadence] = {
            "cycle_count": len(rows),
            "target_hours": target_hours,
            "tolerance_hours": [lower, upper],
            "violation_count": len(violations),
            "ignored_transition_count": len(ignored_transitions),
            "ignored_transitions": ignored_transitions,
            "violations": violations,
        }
        cadence_violations.extend(violations)

    return {
        "status": "pass" if not cadence_violations and not inter_cycle_gap_violations else "fail",
        "log_path": str(log_path),
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "scheduled_cycle_count": len(scheduled_cycles),
        "excluded_manual_cycle_count": len(excluded_manual),
        "excluded_manual_cycle_ids": [row.cycle_id for row in excluded_manual],
        "phase_reset_cycle_ids": sorted(phase_reset_cycle_ids),
        "max_inter_cycle_gap_hours": round(max_inter_cycle_gap_hours, 6),
        "inter_cycle_gap_violation_count": len(inter_cycle_gap_violations),
        "inter_cycle_gap_violations": inter_cycle_gap_violations,
        "cadence": cadence_results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit scheduled governance cadence for Gate 5.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("logs/governance/decisions.jsonl"),
        help="Governance decisions JSONL log.",
    )
    parser.add_argument("--since", help="Optional inclusive UTC/window start timestamp.")
    parser.add_argument("--until", help="Optional inclusive UTC/window end timestamp.")
    parser.add_argument(
        "--manual-cycle-id",
        action="append",
        default=[],
        help=(
            "Cycle id that was manually triggered and must not count as scheduled "
            "cadence evidence. Use this for documented launchctl kickstart cycles "
            "or legacy pre-run_source manual/smoke cycles."
        ),
    )
    parser.add_argument(
        "--legacy-manual-cycle-id",
        action="append",
        default=[],
        help=(
            "Deprecated alias for --manual-cycle-id, retained for the Phase 2 "
            "close reattempt command."
        ),
    )
    parser.add_argument(
        "--phase-reset-cycle-id",
        action="append",
        default=[],
        help=(
            "Scheduled cycle id that intentionally reset launchd phase; the "
            "transition into this cycle is ignored for cadence-deviation count."
        ),
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = parser.parse_args(argv)

    result = audit_cadence(
        log_path=args.log,
        since=_parse_ts(args.since) if args.since else None,
        until=_parse_ts(args.until) if args.until else None,
        manual_cycle_ids=set(args.manual_cycle_id + args.legacy_manual_cycle_id),
        phase_reset_cycle_ids=set(args.phase_reset_cycle_id),
    )
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
