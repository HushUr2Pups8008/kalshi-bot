#!/usr/bin/env python3
"""Audit and optionally requeue invalid persisted decision-grade research tasks."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.research_profit_validation_loop import _candidate_evidence_quality
from tasks.research_dossier import (
    RESEARCH_TASK_INITIAL_BACKOFF_SECONDS,
    RESEARCH_TASK_OFFICIAL_PENDING_MAX_BACKOFF_SECONDS,
    RESEARCH_TASK_TERMINAL_AFTER_SAME_REASON,
)
from utils.output_paths import EVIDENCE_STORE_DB

_SOURCE_PATH_EXHAUSTED_REASONS = {
    "insufficient_corroboration",
    "missing_resolution_source",
    "no_reliable_source_path",
    "no_research_hits",
}
_COUNTER_EVIDENCE_EXHAUSTED_REASONS = {
    "missing_counter_evidence",
    "unresolved_contradiction",
}
_TIMEOUT_EXHAUSTED_REASON = "research_timeout_exhausted"
_OFFICIAL_DATA_PENDING_REASON = "official_data_pending"
_KXHIGHNY_DATE_RE = re.compile(
    r"\bKXHIGHNY-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})",
    flags=re.I,
)
_KXSATRADEBAL_DATE_RE = re.compile(
    r"\bKXSATRADEBAL-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})",
    flags=re.I,
)
_KXICECONF_DEADLINE_RE = re.compile(
    r"\bKXICECONF-(\d{2})(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}"
    r"-(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})",
    flags=re.I,
)
_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(frozen=True)
class InvalidDecisionGradeCandidate:
    market_ticker: str
    research_run_id: str
    force_side: str
    reason: str
    task_updated_ts: str | None = None
    dossier_updated_ts: str | None = None
    observed_last_skip_reason: str | None = None
    observed_terminal_reason: str | None = None
    applied: bool | None = None
    apply_error: str | None = None


@dataclass(frozen=True)
class RepairableResearchTask:
    market_ticker: str
    state: str
    reason: str
    next_state: str
    terminal_reason: str | None = None
    task_updated_ts: str | None = None
    observed_last_skip_reason: str | None = None
    observed_terminal_reason: str | None = None
    observed_same_reason_count: int | None = None
    applied: bool | None = None
    apply_error: str | None = None


def find_invalid_decision_grade_candidates(
    db_path: Path = EVIDENCE_STORE_DB,
) -> list[InvalidDecisionGradeCandidate]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if not _has_required_tables(conn):
            return []
        if not _has_table(conn, "research_dossiers"):
            return []
        dossier_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(research_dossiers)").fetchall()
        }
        task_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(research_tasks)").fetchall()
        }
        task_updated_expr = (
            "t.updated_ts" if "updated_ts" in task_columns else "NULL"
        )
        task_skip_expr = (
            "t.last_skip_reason" if "last_skip_reason" in task_columns else "NULL"
        )
        task_terminal_expr = (
            "t.terminal_reason" if "terminal_reason" in task_columns else "NULL"
        )
        dossier_updated_expr = (
            "d.updated_ts"
            if "updated_ts" in dossier_columns
            else "d.updated_at"
            if "updated_at" in dossier_columns
            else "NULL"
        )
        candidates: list[InvalidDecisionGradeCandidate] = []
        for row in conn.execute(
            f"""
            SELECT r.market_ticker, r.research_run_id, r.force_side,
                   {task_updated_expr} AS task_updated_ts,
                   {task_skip_expr} AS task_last_skip_reason,
                   {task_terminal_expr} AS task_terminal_reason,
                   {dossier_updated_expr} AS dossier_updated_ts
            FROM research_runs r
            JOIN research_tasks t
              ON t.market_ticker = r.market_ticker
            JOIN research_dossiers d
              ON d.market_ticker = r.market_ticker
             AND d.last_research_run_id = r.research_run_id
            WHERE r.verdict_status = 'decision_grade_candidate'
              AND t.state = 'decision_grade_candidate'
              AND d.last_verdict_status = 'decision_grade_candidate'
              AND r.force_side IN ('yes', 'no')
              AND r.estimated_probability IS NOT NULL
              AND r.confidence IS NOT NULL
              AND r.market_price IS NOT NULL
              AND r.estimated_edge IS NOT NULL
            ORDER BY r.created_ts DESC, r.market_ticker ASC
            """
        ):
            ticker = str(row["market_ticker"] or "").strip()
            run_id = str(row["research_run_id"] or "").strip()
            side = str(row["force_side"] or "").strip().lower()
            if not ticker or not run_id:
                continue
            quality = _candidate_evidence_quality(
                conn,
                ticker=ticker,
                research_run_id=run_id,
                force_side=side,
                fresh_since=datetime.min.replace(tzinfo=timezone.utc),
                as_of=datetime.now(timezone.utc),
            )
            if not quality.has_reliable_source_path:
                reason = "no_reliable_source_path"
            elif not quality.has_directional_evidence:
                reason = "neutral_only_evidence"
            elif not quality.has_counter_query or not quality.has_counter_evidence:
                reason = "missing_counter_evidence"
            else:
                continue
            candidates.append(
                InvalidDecisionGradeCandidate(
                    market_ticker=ticker,
                    research_run_id=run_id,
                    force_side=side,
                    reason=reason,
                    task_updated_ts=str(row["task_updated_ts"] or "") or None,
                    dossier_updated_ts=str(row["dossier_updated_ts"] or "") or None,
                    observed_last_skip_reason=row["task_last_skip_reason"],
                    observed_terminal_reason=row["task_terminal_reason"],
                )
            )
        return candidates


def find_repairable_research_tasks(
    db_path: Path = EVIDENCE_STORE_DB,
) -> list[RepairableResearchTask]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if not _has_required_tables(conn):
            return []
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_tasks)").fetchall()
        }
        snapshot_columns = {
            "terminal_reason",
            "same_reason_count",
            "updated_ts",
            "last_skip_reason",
        }
        candidates = []
        if snapshot_columns.issubset(columns):
            candidates = [
                RepairableResearchTask(
                    market_ticker=str(row["market_ticker"]),
                    state=str(row["state"]),
                    reason=str(
                        row["terminal_reason"] or row["last_skip_reason"] or ""
                    ),
                    next_state="continue_researching",
                    task_updated_ts=str(row["updated_ts"] or "") or None,
                    observed_last_skip_reason=row["last_skip_reason"],
                    observed_terminal_reason=row["terminal_reason"],
                    observed_same_reason_count=row["same_reason_count"],
                )
                for row in conn.execute(
                    """
                    SELECT market_ticker, state, terminal_reason, last_skip_reason,
                           same_reason_count, updated_ts
                    FROM research_tasks
                    WHERE state = 'untradeable'
                      AND terminal_reason = ?
                    ORDER BY updated_ts ASC, market_ticker ASC
                    """,
                    (_TIMEOUT_EXHAUSTED_REASON,),
                ).fetchall()
            ]
        if snapshot_columns.issubset(columns):
            pending_reason_placeholders = ",".join(
                "?"
                for _ in (
                    "ambiguous_direction",
                    "neutral_only_evidence",
                )
            )
            for row in conn.execute(
                f"""
                SELECT market_ticker, state, terminal_reason, last_skip_reason,
                       same_reason_count, updated_ts
                FROM research_tasks
                WHERE last_skip_reason IN ({pending_reason_placeholders})
                  AND (
                        (
                            state = 'untradeable'
                            AND terminal_reason = 'contradictory_evidence_unresolved'
                        )
                     OR state != 'untradeable'
                  )
                ORDER BY updated_ts ASC, market_ticker ASC
                """,
                ("ambiguous_direction", "neutral_only_evidence"),
            ).fetchall():
                ticker = str(row["market_ticker"] or "")
                if not _future_nyc_high_temp_target_pending(
                    ticker,
                    str(row["updated_ts"] or ""),
                ) and not _future_south_africa_trade_balance_pending(
                    ticker,
                    str(row["updated_ts"] or ""),
                ) and not _future_confirmation_deadline_pending(
                    ticker,
                    str(row["updated_ts"] or ""),
                ):
                    continue
                candidates.append(
                    RepairableResearchTask(
                        market_ticker=ticker,
                        state=str(row["state"]),
                        reason=_OFFICIAL_DATA_PENDING_REASON,
                        next_state="needs_research",
                        task_updated_ts=str(row["updated_ts"] or "") or None,
                        observed_last_skip_reason=row["last_skip_reason"],
                        observed_terminal_reason=row["terminal_reason"],
                        observed_same_reason_count=row["same_reason_count"],
                    )
                )
        if snapshot_columns.issubset(columns):
            exhausted_reasons = sorted(
                _SOURCE_PATH_EXHAUSTED_REASONS | _COUNTER_EVIDENCE_EXHAUSTED_REASONS
            )
            placeholders = ",".join("?" for _ in exhausted_reasons)
            for row in conn.execute(
                f"""
                SELECT market_ticker, state, terminal_reason, last_skip_reason,
                       same_reason_count, updated_ts
                FROM research_tasks
                WHERE state != 'untradeable'
                  AND last_skip_reason IN ({placeholders})
                  AND same_reason_count >= ?
                ORDER BY updated_ts ASC, market_ticker ASC
                """,
                (*exhausted_reasons, RESEARCH_TASK_TERMINAL_AFTER_SAME_REASON),
            ).fetchall():
                reason = str(row["last_skip_reason"] or "")
                terminal_reason = (
                    "no_reliable_source_path"
                    if reason in _SOURCE_PATH_EXHAUSTED_REASONS
                    else "contradictory_evidence_unresolved"
                )
                candidates.append(
                    RepairableResearchTask(
                        market_ticker=str(row["market_ticker"]),
                        state=str(row["state"]),
                        reason=reason,
                        next_state="untradeable",
                        terminal_reason=terminal_reason,
                        task_updated_ts=str(row["updated_ts"] or "") or None,
                        observed_last_skip_reason=row["last_skip_reason"],
                        observed_terminal_reason=row["terminal_reason"],
                        observed_same_reason_count=row["same_reason_count"],
                    )
                )
        return candidates


def repair_invalid_decision_grade_candidates(
    db_path: Path = EVIDENCE_STORE_DB,
) -> list[InvalidDecisionGradeCandidate]:
    db_path = Path(db_path)
    candidates = find_invalid_decision_grade_candidates(db_path)
    if not candidates:
        return []
    results: list[InvalidDecisionGradeCandidate] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not _has_required_tables(conn):
            return []
        run_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
        }
        dossier_columns = (
            {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(research_dossiers)"
                ).fetchall()
            }
            if _has_table(conn, "research_dossiers")
            else set()
        )
        dossier_updated_column = (
            "updated_ts" if "updated_ts" in dossier_columns else "updated_at"
        )
        for index, candidate in enumerate(candidates):
            savepoint = f"invalid_candidate_{index}"
            conn.execute(f"SAVEPOINT {savepoint}")
            next_state = (
                "needs_research"
                if candidate.reason == "no_reliable_source_path"
                else "needs_counter_evidence"
            )
            backoff_seconds = (
                RESEARCH_TASK_INITIAL_BACKOFF_SECONDS
                if next_state == "needs_research"
                else 0.0
            )
            cooldown_until_ts = (
                (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=backoff_seconds)
                )
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
                if backoff_seconds > 0.0
                else None
            )
            try:
                task_update = conn.execute(
                    f"""
                    UPDATE research_tasks
                    SET state = ?,
                        cooldown_until_ts = ?,
                        backoff_seconds = ?,
                        terminal_reason = NULL,
                        last_skip_reason = ?,
                        updated_ts = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE market_ticker = ?
                      AND state = 'decision_grade_candidate'
                      AND updated_ts IS ?
                      AND last_skip_reason IS ?
                      AND terminal_reason IS ?
                      AND EXISTS (
                            SELECT 1
                            FROM research_dossiers d
                            WHERE d.market_ticker = research_tasks.market_ticker
                              AND d.last_research_run_id = ?
                              AND d.last_verdict_status = 'decision_grade_candidate'
                              AND d.{dossier_updated_column} IS ?
                      )
                    """,
                    (
                        next_state,
                        cooldown_until_ts,
                        backoff_seconds,
                        candidate.reason,
                        candidate.market_ticker,
                        candidate.task_updated_ts,
                        candidate.observed_last_skip_reason,
                        candidate.observed_terminal_reason,
                        candidate.research_run_id,
                        candidate.dossier_updated_ts,
                    ),
                )
                if task_update.rowcount != 1:
                    _rollback_savepoint(conn, savepoint)
                    results.append(
                        replace(
                            candidate,
                            applied=False,
                            apply_error="stale_current_state",
                        )
                    )
                    continue
                if "skip_reason" in run_columns:
                    run_update = conn.execute(
                        """
                        UPDATE research_runs
                        SET verdict_status = ?,
                            skip_reason = ?
                        WHERE market_ticker = ?
                          AND research_run_id = ?
                          AND verdict_status = 'decision_grade_candidate'
                        """,
                        (
                            next_state,
                            candidate.reason,
                            candidate.market_ticker,
                            candidate.research_run_id,
                        ),
                    )
                else:
                    run_update = conn.execute(
                        """
                        UPDATE research_runs
                        SET verdict_status = ?
                        WHERE market_ticker = ?
                          AND research_run_id = ?
                          AND verdict_status = 'decision_grade_candidate'
                        """,
                        (
                            next_state,
                            candidate.market_ticker,
                            candidate.research_run_id,
                        ),
                    )
                timestamp_assignment = (
                    ", updated_ts = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                    if "updated_ts" in dossier_columns
                    else ", updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                )
                dossier_update = conn.execute(
                    f"""
                    UPDATE research_dossiers
                    SET last_verdict_status = ?,
                        last_skip_reason = ?,
                        last_decision_grade_status = ?
                        {timestamp_assignment}
                    WHERE market_ticker = ?
                      AND last_research_run_id = ?
                      AND last_verdict_status = 'decision_grade_candidate'
                      AND {dossier_updated_column} IS ?
                    """,
                    (
                        next_state,
                        candidate.reason,
                        next_state,
                        candidate.market_ticker,
                        candidate.research_run_id,
                        candidate.dossier_updated_ts,
                    ),
                )
                if run_update.rowcount != 1 or dossier_update.rowcount != 1:
                    _rollback_savepoint(conn, savepoint)
                    results.append(
                        replace(
                            candidate,
                            applied=False,
                            apply_error="inconsistent_current_state",
                        )
                    )
                    continue
            except sqlite3.Error as exc:
                _rollback_savepoint(conn, savepoint)
                results.append(
                    replace(
                        candidate,
                        applied=False,
                        apply_error=f"sqlite_error:{exc}",
                    )
                )
                continue
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            results.append(replace(candidate, applied=True, apply_error=None))
    return results


def repair_research_task_blockers(
    db_path: Path = EVIDENCE_STORE_DB,
) -> list[RepairableResearchTask]:
    db_path = Path(db_path)
    candidates = find_repairable_research_tasks(db_path)
    if not candidates:
        return []
    results: list[RepairableResearchTask] = []
    with sqlite3.connect(db_path) as conn:
        if not _has_required_tables(conn):
            return []
        for index, candidate in enumerate(candidates):
            savepoint = f"task_blocker_{index}"
            conn.execute(f"SAVEPOINT {savepoint}")
            backoff_seconds = (
                RESEARCH_TASK_OFFICIAL_PENDING_MAX_BACKOFF_SECONDS
                if candidate.reason == _OFFICIAL_DATA_PENDING_REASON
                else RESEARCH_TASK_INITIAL_BACKOFF_SECONDS
            )
            next_terminal_reason = None
            cooldown_until_ts = None
            if candidate.next_state == "untradeable":
                backoff_seconds = 0.0
                next_terminal_reason = candidate.terminal_reason or candidate.reason
            else:
                cooldown_until_ts = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=backoff_seconds)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            try:
                task_update = conn.execute(
                    """
                    UPDATE research_tasks
                    SET state = ?,
                        cooldown_until_ts = ?,
                        backoff_seconds = ?,
                        terminal_reason = ?,
                        last_skip_reason = ?,
                        updated_ts = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE market_ticker = ?
                      AND state IS ?
                      AND terminal_reason IS ?
                      AND last_skip_reason IS ?
                      AND same_reason_count IS ?
                      AND updated_ts IS ?
                    """,
                    (
                        candidate.next_state,
                        cooldown_until_ts,
                        backoff_seconds,
                        next_terminal_reason,
                        candidate.reason,
                        candidate.market_ticker,
                        candidate.state,
                        candidate.observed_terminal_reason,
                        candidate.observed_last_skip_reason,
                        candidate.observed_same_reason_count,
                        candidate.task_updated_ts,
                    ),
                )
            except sqlite3.Error as exc:
                _rollback_savepoint(conn, savepoint)
                results.append(
                    replace(
                        candidate,
                        applied=False,
                        apply_error=f"sqlite_error:{exc}",
                    )
                )
                continue
            if task_update.rowcount != 1:
                _rollback_savepoint(conn, savepoint)
                results.append(
                    replace(
                        candidate,
                        applied=False,
                        apply_error="stale_current_state",
                    )
                )
                continue
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            results.append(replace(candidate, applied=True, apply_error=None))
    return results


def _rollback_savepoint(conn: sqlite3.Connection, savepoint: str) -> None:
    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
    conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def _future_south_africa_trade_balance_pending(
    ticker: str,
    updated_ts: str,
) -> bool:
    target_date = _ticker_target_date(ticker, _KXSATRADEBAL_DATE_RE)
    if target_date is None:
        return False
    updated_date = _date_from_iso(updated_ts) or datetime.now(timezone.utc).date()
    return target_date >= updated_date


def _future_nyc_high_temp_target_pending(
    ticker: str,
    updated_ts: str,
) -> bool:
    target_date = _ticker_target_date(ticker, _KXHIGHNY_DATE_RE)
    if target_date is None:
        return False
    updated_date = _date_from_iso(updated_ts) or datetime.now(timezone.utc).date()
    return target_date >= updated_date


def _future_confirmation_deadline_pending(
    ticker: str,
    updated_ts: str,
) -> bool:
    target_date = _ticker_target_date(ticker, _KXICECONF_DEADLINE_RE)
    if target_date is None:
        return False
    updated_date = _date_from_iso(updated_ts) or datetime.now(timezone.utc).date()
    return target_date >= updated_date


def _ticker_target_date(ticker: str, pattern: re.Pattern[str]) -> date | None:
    match = pattern.search(ticker)
    if not match:
        return None
    try:
        return date(
            2000 + int(match.group(1)),
            _MONTHS[match.group(2).upper()],
            int(match.group(3)),
        )
    except ValueError:
        return None


def _date_from_iso(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _has_required_tables(conn: sqlite3.Connection) -> bool:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    return {"research_runs", "research_evidence", "research_tasks"} <= tables


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=EVIDENCE_STORE_DB,
        help="Path to research evidence SQLite DB.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate research_tasks to requeue invalid decision-grade candidates.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
    )
    args = parser.parse_args(argv)

    candidates = find_invalid_decision_grade_candidates(args.db)
    task_candidates = find_repairable_research_tasks(args.db)
    if args.apply:
        candidates = repair_invalid_decision_grade_candidates(args.db)
        task_candidates = repair_research_task_blockers(args.db)
    payload = {
        "applied": bool(args.apply),
        "invalid_decision_grade_candidates": len(candidates),
        "candidates": [asdict(candidate) for candidate in candidates],
        "repairable_research_tasks": len(task_candidates),
        "task_candidates": [asdict(candidate) for candidate in task_candidates],
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        action = "requeued" if args.apply else "would requeue"
        print(f"{action}: {len(candidates)} invalid decision-grade candidates")
        for candidate in candidates:
            print(
                f"- {candidate.market_ticker} {candidate.research_run_id} "
                f"side={candidate.force_side} reason={candidate.reason}"
            )
        print(f"{action}: {len(task_candidates)} repairable research tasks")
        for candidate in task_candidates:
            print(
                f"- {candidate.market_ticker} {candidate.state} "
                f"reason={candidate.reason} -> {candidate.next_state}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
