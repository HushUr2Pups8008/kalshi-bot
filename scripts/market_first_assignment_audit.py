#!/usr/bin/env python3
"""Audit market-first fresh-pass assignment shadow rows."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from utils.output_paths import RAW_TRADES_SHADOW_DIR


@dataclass(frozen=True)
class AssignmentAuditSummary:
    rows: int
    assigned: int
    malformed_rows: int
    rows_assigned_without_ticker: int
    rows_assigned_without_score: int

    @property
    def assignment_rate(self) -> float | None:
        if self.rows == 0:
            return None
        return self.assigned / self.rows


def _iter_shadow_rows(path: Path) -> Iterable[dict[str, Any]]:
    files = [path] if path.is_file() else sorted(path.glob("*.jsonl"))
    for file_path in files:
        with file_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("type") == "FRESH_PASS_ASSIGNMENT_SHADOW":
                    yield row


def summarize(path: Path) -> AssignmentAuditSummary:
    rows = assigned = malformed = without_ticker = without_score = 0
    for row in _iter_shadow_rows(path):
        rows += 1
        is_assigned = bool(row.get("assigned"))
        assigned += int(is_assigned)
        malformed += int(bool(row.get("malformed")))
        without_ticker += int(is_assigned and not row.get("top_ticker"))
        without_score += int(is_assigned and row.get("top_score") is None)
    return AssignmentAuditSummary(
        rows=rows,
        assigned=assigned,
        malformed_rows=malformed,
        rows_assigned_without_ticker=without_ticker,
        rows_assigned_without_score=without_score,
    )


def validate(summary: AssignmentAuditSummary, *, allow_malformed: bool = False) -> list[str]:
    errors: list[str] = []
    if summary.rows_assigned_without_ticker:
        errors.append("assigned rows without top_ticker")
    if summary.rows_assigned_without_score:
        errors.append("assigned rows without top_score")
    if summary.malformed_rows and not allow_malformed:
        errors.append("malformed rows present")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=RAW_TRADES_SHADOW_DIR)
    parser.add_argument("--allow-malformed", action="store_true")
    args = parser.parse_args(argv)

    summary = summarize(args.path)
    errors = validate(summary, allow_malformed=args.allow_malformed)
    print(json.dumps({**summary.__dict__, "assignment_rate": summary.assignment_rate}, sort_keys=True))
    if errors:
        print("; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
