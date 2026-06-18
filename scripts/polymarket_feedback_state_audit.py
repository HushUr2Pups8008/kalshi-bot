#!/usr/bin/env python3
"""Audit legacy Polymarket matcher-feedback state without mutating runtime files."""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_COUNTERS = _REPO_ROOT / "data" / "match_token_fp_counters.db"
_DEFAULT_WEIGHTS = _REPO_ROOT / "data" / "matcher_token_weights.json"
_PM_PREFIX = "polymarket_us"
_PM_FAMILY_GLOB = _PM_PREFIX + ":*"


@dataclass(frozen=True)
class CounterAudit:
    path: Path
    exists: bool
    total_rows: int = 0
    bare_pm_rows: int = 0
    family_pm_rows: int = 0
    other_rows: int = 0
    bare_pm_observations: int = 0
    family_pm_observations: int = 0


@dataclass(frozen=True)
class WeightAudit:
    path: Path
    exists: bool
    total_keys: int = 0
    bare_pm_keys: int = 0
    family_pm_keys: int = 0
    other_keys: int = 0
    malformed: bool = False
    bare_pm_key_sample: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuarantinePlan:
    write_required: bool
    counter_sql: str
    weight_operation: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class FeedbackStateAudit:
    counters_path: Path
    weights_path: Path
    counters: CounterAudit
    weights: WeightAudit
    plan: QuarantinePlan


def _sum_columns(conn: sqlite3.Connection) -> str:
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(match_token_fp_counters)").fetchall()
    }
    parts = []
    if "fp_neutral_count" in columns:
        parts.append("COALESCE(fp_neutral_count, 0)")
    if "true_positive_count" in columns:
        parts.append("COALESCE(true_positive_count, 0)")
    return " + ".join(parts) if parts else "0"


def _count_counter_rows(conn: sqlite3.Connection, where: str, params: tuple[str, ...]) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM match_token_fp_counters WHERE {where}",
            params,
        ).fetchone()[0]
        or 0
    )


def _count_counter_observations(conn: sqlite3.Connection, where: str, params: tuple[str, ...]) -> int:
    total_expr = _sum_columns(conn)
    return int(
        conn.execute(
            f"SELECT SUM({total_expr}) FROM match_token_fp_counters WHERE {where}",
            params,
        ).fetchone()[0]
        or 0
    )


def audit_counters(counters_path: Path) -> CounterAudit:
    if not counters_path.exists():
        return CounterAudit(path=counters_path, exists=False)

    conn = sqlite3.connect(str(counters_path))
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'match_token_fp_counters'"
        ).fetchone()
        if table_exists is None:
            return CounterAudit(path=counters_path, exists=True)

        total_rows = int(conn.execute("SELECT COUNT(*) FROM match_token_fp_counters").fetchone()[0] or 0)
        bare_rows = _count_counter_rows(conn, "market_prefix = ?", (_PM_PREFIX,))
        family_rows = _count_counter_rows(conn, "market_prefix GLOB ?", (_PM_FAMILY_GLOB,))
        other_rows = _count_counter_rows(
            conn,
            "NOT (market_prefix = ? OR market_prefix GLOB ?)",
            (_PM_PREFIX, _PM_FAMILY_GLOB),
        )
        return CounterAudit(
            path=counters_path,
            exists=True,
            total_rows=total_rows,
            bare_pm_rows=bare_rows,
            family_pm_rows=family_rows,
            other_rows=other_rows,
            bare_pm_observations=_count_counter_observations(conn, "market_prefix = ?", (_PM_PREFIX,)),
            family_pm_observations=_count_counter_observations(conn, "market_prefix GLOB ?", (_PM_FAMILY_GLOB,)),
        )
    finally:
        conn.close()


def _is_bare_pm_weight_key(key: str) -> bool:
    if key == _PM_PREFIX:
        return True
    if not key.startswith(_PM_PREFIX + ":"):
        return False
    tail = key[len(_PM_PREFIX) + 1:]
    return bool(tail) and ":" not in tail


def _is_family_pm_weight_key(key: str) -> bool:
    if not key.startswith(_PM_PREFIX + ":"):
        return False
    tail = key[len(_PM_PREFIX) + 1:]
    return ":" in tail


def audit_weights(weights_path: Path) -> WeightAudit:
    if not weights_path.exists():
        return WeightAudit(path=weights_path, exists=False)

    try:
        data = json.loads(weights_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return WeightAudit(path=weights_path, exists=True, malformed=True)
    if not isinstance(data, dict):
        return WeightAudit(path=weights_path, exists=True, malformed=True)

    keys = [str(key) for key in data.keys()]
    bare_pm = sorted(key for key in keys if _is_bare_pm_weight_key(key))
    family_pm = [key for key in keys if _is_family_pm_weight_key(key)]
    return WeightAudit(
        path=weights_path,
        exists=True,
        total_keys=len(keys),
        bare_pm_keys=len(bare_pm),
        family_pm_keys=len(family_pm),
        other_keys=len(keys) - len(bare_pm) - len(family_pm),
        bare_pm_key_sample=tuple(bare_pm[:10]),
    )


def build_quarantine_plan(*, counters_path: Path, weights_path: Path) -> QuarantinePlan:
    counter_sql = f"""-- Review-only plan. Do not run until operator approval and backup exist.
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS match_token_fp_counters_pm_legacy_quarantine AS
    SELECT * FROM match_token_fp_counters WHERE 0;
INSERT INTO match_token_fp_counters_pm_legacy_quarantine
    SELECT * FROM match_token_fp_counters
    WHERE market_prefix = '{_PM_PREFIX}';
DELETE FROM match_token_fp_counters
    WHERE market_prefix = '{_PM_PREFIX}';
COMMIT;"""
    weight_operation = (
        "remove JSON keys matching bare runtime pattern polymarket_us:<token> "
        "or exact polymarket_us; keep polymarket_us:<family>:<token> and non-PM keys"
    )
    return QuarantinePlan(
        write_required=False,
        counter_sql=counter_sql,
        weight_operation=weight_operation,
        notes=(
            "Default audit is read-only; this script does not execute quarantine.",
            f"Counter path reviewed: {counters_path}",
            f"Weight path reviewed: {weights_path}",
            "Migration into family prefixes is not inferable from aggregated bare counters alone.",
        ),
    )


def audit_feedback_state(*, counters_path: Path, weights_path: Path) -> FeedbackStateAudit:
    return FeedbackStateAudit(
        counters_path=counters_path,
        weights_path=weights_path,
        counters=audit_counters(counters_path),
        weights=audit_weights(weights_path),
        plan=build_quarantine_plan(counters_path=counters_path, weights_path=weights_path),
    )


def render_audit(audit: FeedbackStateAudit) -> str:
    counters = audit.counters
    weights = audit.weights
    lines = [
        "Polymarket feedback state audit",
        "mode: read-only",
        "writes executed: no",
        "",
        f"counters: {counters.path}",
        f"  exists: {'yes' if counters.exists else 'no'}",
        f"  total rows: {counters.total_rows}",
        f"  bare DB rows market_prefix='polymarket_us': {counters.bare_pm_rows}",
        f"  family DB rows market_prefix GLOB 'polymarket_us:*': {counters.family_pm_rows}",
        f"  other DB rows: {counters.other_rows}",
        f"  bare DB observations fp+tp: {counters.bare_pm_observations}",
        f"  family DB observations fp+tp: {counters.family_pm_observations}",
        "",
        f"weights: {weights.path}",
        f"  exists: {'yes' if weights.exists else 'no'}",
        f"  malformed: {'yes' if weights.malformed else 'no'}",
        f"  total keys: {weights.total_keys}",
        f"  bare runtime weight keys polymarket_us:<token>: {weights.bare_pm_keys}",
        f"  family runtime weight keys polymarket_us:<family>:<token>: {weights.family_pm_keys}",
        f"  other runtime weight keys: {weights.other_keys}",
    ]
    if weights.bare_pm_key_sample:
        lines.append(f"  bare runtime sample: {', '.join(weights.bare_pm_key_sample)}")
    lines.extend(
        [
            "",
            "review-only quarantine plan",
            "  counters SQL:",
            *[f"    {line}" for line in audit.plan.counter_sql.splitlines()],
            f"  weights operation: {audit.plan.weight_operation}",
            "  notes:",
            *[f"    - {note}" for note in audit.plan.notes],
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counters", type=Path, default=_DEFAULT_COUNTERS)
    parser.add_argument("--weights", type=Path, default=_DEFAULT_WEIGHTS)
    args = parser.parse_args(argv)

    audit = audit_feedback_state(counters_path=args.counters, weights_path=args.weights)
    print(render_audit(audit), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
