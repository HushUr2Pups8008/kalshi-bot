"""Read-only reconciliation of ``paper_trades`` rows against ``PAPER_TRADE`` logs.

The control intentionally joins only on the durable ``trade_id``. It does not
attempt to recover sparse historical rows through ticker, price, or timestamp
heuristics because that would make an integrity alarm look resolved when it is
not. Journal reads go through :func:`utils.trade_log_reader.iter_trade_records`
so archive-plus-live behavior stays aligned with the daily-review reader.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.runtime_paper_cohort_scope import (
    RuntimePaperCohortScopeError,
    derive_runtime_paper_cohort_db_path,
    record_has_runtime_paper_cohort_lineage,
    validate_runtime_paper_cohort_lineage,
)
from utils.trade_log_reader import iter_trade_records


DEFAULT_TRADE_LOG_ROOT = REPO_ROOT / "logs" / "trades"
_IDENTITY_FIELDS = ("ticker", "venue", "side", "contracts", "price_cents")
_DB_COLUMNS = ("trade_id", "ts", *_IDENTITY_FIELDS)
_VERDICTS = frozenset(
    {
        "OK",
        "WARN_LEGACY_UNMATCHABLE",
        "ALARM_DB_ONLY",
        "ALARM_JOURNAL_ONLY",
        "ALARM_AMBIGUOUS",
    }
)

ImmutableIdentity = tuple[str, str, str, int, int]


class PaperTradeJournalReconciliationError(RuntimeError):
    """Raised when reconciliation cannot establish a trustworthy read scope."""


@dataclass(frozen=True)
class RuntimePaperCohortScope:
    """Validated runtime cohort and the only database that can represent it."""

    cohort_id: str
    cohort_kind: str
    canonical_db_path: Path


@dataclass(frozen=True)
class _Candidate:
    trade_id: str | None
    identity: ImmutableIdentity | None
    source: Literal["db", "journal"]
    reason: str | None = None


@dataclass(frozen=True)
class PaperTradeJournalParityResult:
    """Machine-readable strict DB/journal parity result."""

    verdict: str
    trade_log_root: Path
    paper_db_path: Path
    runtime_paper_cohort_id: str | None
    runtime_paper_cohort_kind: str | None
    db_trade_rows_scanned: int
    journal_rows_scanned: int
    journal_rows_excluded_outside_scope: int
    matched_count: int
    db_only_count: int
    journal_only_count: int
    ambiguous_count: int
    legacy_unmatchable_count: int
    db_only_examples: tuple[dict[str, Any], ...]
    journal_only_examples: tuple[dict[str, Any], ...]
    ambiguous_examples: tuple[dict[str, Any], ...]
    legacy_unmatchable_examples: tuple[dict[str, Any], ...]

    @property
    def missing_in_journal_count(self) -> int:
        """Compatibility name for DB rows absent from the journal."""

        return self.db_only_count

    @property
    def missing_in_db_count(self) -> int:
        """Compatibility name for journal rows absent from the database."""

        return self.journal_only_count

    @property
    def ambiguous_match_count(self) -> int:
        """Compatibility name for ambiguous or immutable-mismatch joins."""

        return self.ambiguous_count

    @property
    def excluded_legacy_unmatchable_count(self) -> int:
        """Compatibility name for sparse historical rows excluded from joins."""

        return self.legacy_unmatchable_count

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe report while retaining concise compatibility keys."""

        return {
            "verdict": self.verdict,
            "trade_log_root": str(self.trade_log_root),
            "db_path": str(self.paper_db_path),
            "runtime_paper_cohort_id": self.runtime_paper_cohort_id,
            "runtime_paper_cohort_kind": self.runtime_paper_cohort_kind,
            "db_trade_rows_scanned": self.db_trade_rows_scanned,
            "journal_rows_scanned": self.journal_rows_scanned,
            "journal_rows_excluded_outside_scope": self.journal_rows_excluded_outside_scope,
            "matched_count": self.matched_count,
            "db_only_count": self.db_only_count,
            "journal_only_count": self.journal_only_count,
            "ambiguous_count": self.ambiguous_count,
            "legacy_unmatchable_count": self.legacy_unmatchable_count,
            "missing_in_journal_count": self.missing_in_journal_count,
            "missing_in_db_count": self.missing_in_db_count,
            "ambiguous_match_count": self.ambiguous_match_count,
            "excluded_legacy_unmatchable_count": self.excluded_legacy_unmatchable_count,
            "examples": {
                "db_only": self.db_only_examples,
                "journal_only": self.journal_only_examples,
                "ambiguous": self.ambiguous_examples,
                "legacy_unmatchable": self.legacy_unmatchable_examples,
            },
        }


def _parse_timestamp(value: object) -> datetime | None:
    if type(value) is not str or not value or value.strip() != value:
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _in_window(
    timestamp: datetime | None,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    if timestamp is None:
        return since is None and until is None
    if since is not None and timestamp < since:
        return False
    if until is not None and timestamp > until:
        return False
    return True


def _durable_trade_id(value: object) -> str | None:
    if type(value) is not str or not value or value.strip() != value:
        return None
    return value


def _identity_from_values(
    values: Mapping[str, object],
    *,
    journal_record: bool,
) -> tuple[ImmutableIdentity | None, str | None]:
    ticker = values.get("ticker")
    side = values.get("side")
    venue = values.get("venue")
    if journal_record and "venue" not in values:
        # Only journal history predates the persisted venue column contract.
        venue = "kalshi"

    for name, value in (("ticker", ticker), ("venue", venue), ("side", side)):
        if type(value) is not str or not value or value.strip() != value:
            return None, f"missing_or_invalid_{name}"

    contracts = values.get("contracts")
    price_cents = values.get("price_cents")
    for name, value in (("contracts", contracts), ("price_cents", price_cents)):
        if type(value) is not int:
            return None, f"missing_or_invalid_{name}"

    return (ticker, venue, side, contracts, price_cents), None


def _candidate_from_db_row(row: Mapping[str, object]) -> _Candidate:
    trade_id = _durable_trade_id(row.get("trade_id"))
    if trade_id is None:
        return _Candidate(None, None, "db", "missing_or_invalid_trade_id")
    identity, reason = _identity_from_values(row, journal_record=False)
    return _Candidate(trade_id, identity, "db", reason)


def _candidate_from_journal_record(record: Mapping[str, object]) -> _Candidate:
    trade_id = _durable_trade_id(record.get("trade_id"))
    if trade_id is None:
        return _Candidate(None, None, "journal", "missing_or_invalid_trade_id")
    identity, reason = _identity_from_values(record, journal_record=True)
    return _Candidate(trade_id, identity, "journal", reason)


def _read_only_connection(path: Path) -> sqlite3.Connection:
    expanded = path.expanduser()
    if not expanded.is_file():
        raise PaperTradeJournalReconciliationError(f"paper database is not a regular file: {expanded}")
    try:
        connection = sqlite3.connect(f"{expanded.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise PaperTradeJournalReconciliationError(
            f"failed to open paper database read-only: {expanded}: {exc}"
        ) from exc
    try:
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        connection.close()
        raise PaperTradeJournalReconciliationError(
            f"failed to enforce read-only paper database access: {expanded}: {exc}"
        ) from exc
    return connection


def _read_db_candidates(
    paper_db_path: Path,
    *,
    since: datetime | None,
    until: datetime | None,
) -> tuple[list[_Candidate], int]:
    connection = _read_only_connection(paper_db_path)
    try:
        try:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(paper_trades)")
            }
        except sqlite3.Error as exc:
            raise PaperTradeJournalReconciliationError(
                f"failed to inspect paper_trades schema: {exc}"
            ) from exc
        if not columns:
            raise PaperTradeJournalReconciliationError("paper_trades table is missing or unreadable")

        selected_columns = [
            name if name in columns else f"NULL AS {name}"
            for name in _DB_COLUMNS
        ]
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                f"SELECT {', '.join(selected_columns)} FROM paper_trades ORDER BY trade_id"
            )
        except sqlite3.Error as exc:
            raise PaperTradeJournalReconciliationError(
                f"failed to read paper_trades rows: {exc}"
            ) from exc

        candidates: list[_Candidate] = []
        scanned = 0
        for row in rows:
            scanned += 1
            row_mapping = dict(row)
            timestamp = _parse_timestamp(row_mapping.get("ts"))
            if timestamp is None and (since is not None or until is not None):
                candidates.append(
                    _Candidate(
                        _durable_trade_id(row_mapping.get("trade_id")),
                        None,
                        "db",
                        "missing_or_invalid_ts_for_window",
                    )
                )
                continue
            if not _in_window(timestamp, since, until):
                continue
            candidates.append(_candidate_from_db_row(row_mapping))
        return candidates, scanned
    finally:
        connection.close()


def _read_journal_candidates(
    trade_log_root: Path,
    *,
    scope: RuntimePaperCohortScope | None,
    since: datetime | None,
    until: datetime | None,
) -> tuple[list[_Candidate], int, int]:
    expanded_trade_log_root = trade_log_root.expanduser()
    try:
        if expanded_trade_log_root.is_file():
            with expanded_trade_log_root.open("rb"):
                pass
        elif expanded_trade_log_root.is_dir():
            with os.scandir(expanded_trade_log_root) as entries:
                next(entries, None)
        else:
            raise PaperTradeJournalReconciliationError(
                f"trade log path is not a readable file or directory: {expanded_trade_log_root}"
            )
    except OSError as exc:
        raise PaperTradeJournalReconciliationError(
            f"trade log path is unreadable: {expanded_trade_log_root}: {exc}"
        ) from exc

    candidates: list[_Candidate] = []
    scanned = 0
    excluded_outside_scope = 0
    try:
        records = iter_trade_records(
            expanded_trade_log_root,
            since=since,
            until=until,
            event_types={"PAPER_TRADE"},
        )
        for record in records:
            if scope is not None and not record_has_runtime_paper_cohort_lineage(
                record,
                scope.cohort_id,
                scope.cohort_kind,
            ):
                excluded_outside_scope += 1
                continue
            scanned += 1
            candidates.append(_candidate_from_journal_record(record))
    except (OSError, ValueError) as exc:
        raise PaperTradeJournalReconciliationError(
            f"failed to read paper trade journal: {trade_log_root}: {exc}"
        ) from exc
    return candidates, scanned, excluded_outside_scope


def _resolve_scope(
    *,
    repo_root: Path,
    paper_db_path: Path,
    runtime_paper_cohort_id: str | None,
    runtime_paper_cohort_kind: str | None,
) -> RuntimePaperCohortScope | None:
    if (runtime_paper_cohort_id is None) != (runtime_paper_cohort_kind is None):
        raise PaperTradeJournalReconciliationError(
            "runtime paper cohort ID and kind must be supplied together"
        )
    if runtime_paper_cohort_id is None:
        return None
    try:
        cohort_id, cohort_kind = validate_runtime_paper_cohort_lineage(
            runtime_paper_cohort_id,
            runtime_paper_cohort_kind,
        )
    except RuntimePaperCohortScopeError as exc:
        raise PaperTradeJournalReconciliationError(str(exc)) from exc
    canonical_db_path = derive_runtime_paper_cohort_db_path(
        repo_root,
        cohort_id,
        cohort_kind,
    )
    if paper_db_path.expanduser().resolve() != canonical_db_path.expanduser().resolve():
        raise PaperTradeJournalReconciliationError(
            "paper database is not canonical for runtime paper cohort "
            f"{cohort_id} ({cohort_kind}): expected {canonical_db_path}, got {paper_db_path}"
        )
    return RuntimePaperCohortScope(cohort_id, cohort_kind, canonical_db_path)


def _identity_dict(identity: ImmutableIdentity) -> dict[str, object]:
    return dict(zip(_IDENTITY_FIELDS, identity, strict=True))


def _group_candidates(
    candidates: list[_Candidate],
) -> tuple[
    dict[str, list[_Candidate]],
    dict[str, list[_Candidate]],
    list[dict[str, object]],
]:
    valid: dict[str, list[_Candidate]] = defaultdict(list)
    incomplete_identity_by_trade_id: dict[str, list[_Candidate]] = defaultdict(list)
    legacy_examples: list[dict[str, object]] = []
    for candidate in candidates:
        if candidate.trade_id is None:
            example: dict[str, object] = {
                "source": candidate.source,
                "reason": candidate.reason or "legacy_unmatchable",
            }
            legacy_examples.append(example)
            continue
        if candidate.identity is None:
            incomplete_identity_by_trade_id[candidate.trade_id].append(candidate)
            continue
        valid[candidate.trade_id].append(candidate)
    return valid, incomplete_identity_by_trade_id, legacy_examples


def _cap_examples(
    examples: list[dict[str, object]],
    limit: int | None,
) -> tuple[dict[str, Any], ...]:
    if limit is not None:
        examples = examples[:limit]
    return tuple(examples)


def _verdict(
    *,
    db_only_count: int,
    journal_only_count: int,
    ambiguous_count: int,
    legacy_unmatchable_count: int,
) -> str:
    if ambiguous_count:
        return "ALARM_AMBIGUOUS"
    if db_only_count:
        return "ALARM_DB_ONLY"
    if journal_only_count:
        return "ALARM_JOURNAL_ONLY"
    if legacy_unmatchable_count:
        return "WARN_LEGACY_UNMATCHABLE"
    return "OK"


def reconcile_paper_trade_journal(
    *,
    trade_log_root: Path = DEFAULT_TRADE_LOG_ROOT,
    paper_db_path: Path,
    repo_root: Path = REPO_ROOT,
    runtime_paper_cohort_id: str | None = None,
    runtime_paper_cohort_kind: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = 20,
) -> PaperTradeJournalParityResult:
    """Reconcile one read-only paper DB against archive-plus-live journal rows.

    ``limit`` caps only reported examples. It never limits source scanning or
    changes the verdict. When a runtime cohort is supplied, the DB path must
    match its canonical path and journal records must carry that exact lineage.
    """

    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative or None")
    if since is not None and until is not None and since > until:
        raise ValueError("since must be before or equal to until")

    normalized_db_path = paper_db_path.expanduser()
    scope = _resolve_scope(
        repo_root=repo_root.expanduser(),
        paper_db_path=normalized_db_path,
        runtime_paper_cohort_id=runtime_paper_cohort_id,
        runtime_paper_cohort_kind=runtime_paper_cohort_kind,
    )
    db_candidates, db_rows_scanned = _read_db_candidates(
        normalized_db_path,
        since=since,
        until=until,
    )
    journal_candidates, journal_rows_scanned, excluded_outside_scope = _read_journal_candidates(
        trade_log_root.expanduser(),
        scope=scope,
        since=since,
        until=until,
    )

    db_by_trade_id, db_incomplete_identity_by_trade_id, db_legacy_examples = _group_candidates(
        db_candidates
    )
    journal_by_trade_id, journal_incomplete_identity_by_trade_id, journal_legacy_examples = _group_candidates(
        journal_candidates
    )

    ambiguous_examples: list[dict[str, object]] = []
    ambiguous_ids: set[str] = set()
    for source, incomplete_identity_by_trade_id in (
        ("db", db_incomplete_identity_by_trade_id),
        ("journal", journal_incomplete_identity_by_trade_id),
    ):
        for trade_id, candidates in sorted(incomplete_identity_by_trade_id.items()):
            ambiguous_ids.add(trade_id)
            for candidate in candidates:
                ambiguous_examples.append(
                    {
                        "trade_id": trade_id,
                        "source": source,
                        "reason": candidate.reason or "missing_or_invalid_immutable_identity",
                    }
                )
    for source, candidates_by_id in (("db", db_by_trade_id), ("journal", journal_by_trade_id)):
        for trade_id, candidates in sorted(candidates_by_id.items()):
            if len(candidates) > 1:
                ambiguous_ids.add(trade_id)
                ambiguous_examples.append(
                    {
                        "trade_id": trade_id,
                        "reason": f"duplicate_{source}_trade_id",
                        "count": len(candidates),
                    }
                )

    matched_count = 0
    for trade_id in sorted(set(db_by_trade_id) & set(journal_by_trade_id)):
        if (
            trade_id in db_incomplete_identity_by_trade_id
            or trade_id in journal_incomplete_identity_by_trade_id
        ):
            continue
        db_candidates_for_id = db_by_trade_id[trade_id]
        journal_candidates_for_id = journal_by_trade_id[trade_id]
        if len(db_candidates_for_id) != 1 or len(journal_candidates_for_id) != 1:
            continue
        db_identity = db_candidates_for_id[0].identity
        journal_identity = journal_candidates_for_id[0].identity
        if db_identity != journal_identity:
            ambiguous_ids.add(trade_id)
            ambiguous_examples.append(
                {
                    "trade_id": trade_id,
                    "reason": "immutable_identity_mismatch",
                    "db_identity": _identity_dict(db_identity),
                    "journal_identity": _identity_dict(journal_identity),
                }
            )
            continue
        matched_count += 1

    db_only_examples = [
        {"trade_id": trade_id}
        for trade_id in sorted(set(db_by_trade_id) - set(journal_by_trade_id))
        if trade_id not in journal_incomplete_identity_by_trade_id
    ]
    journal_only_examples = [
        {"trade_id": trade_id}
        for trade_id in sorted(set(journal_by_trade_id) - set(db_by_trade_id))
        if trade_id not in db_incomplete_identity_by_trade_id
    ]
    legacy_examples = [*db_legacy_examples, *journal_legacy_examples]
    verdict = _verdict(
        db_only_count=len(db_only_examples),
        journal_only_count=len(journal_only_examples),
        ambiguous_count=len(ambiguous_ids),
        legacy_unmatchable_count=len(legacy_examples),
    )
    if verdict not in _VERDICTS:  # Defensive guard for future verdict changes.
        raise AssertionError(f"unexpected paper journal reconciliation verdict: {verdict}")
    return PaperTradeJournalParityResult(
        verdict=verdict,
        trade_log_root=trade_log_root.expanduser(),
        paper_db_path=normalized_db_path,
        runtime_paper_cohort_id=scope.cohort_id if scope is not None else None,
        runtime_paper_cohort_kind=scope.cohort_kind if scope is not None else None,
        db_trade_rows_scanned=db_rows_scanned,
        journal_rows_scanned=journal_rows_scanned,
        journal_rows_excluded_outside_scope=excluded_outside_scope,
        matched_count=matched_count,
        db_only_count=len(db_only_examples),
        journal_only_count=len(journal_only_examples),
        ambiguous_count=len(ambiguous_ids),
        legacy_unmatchable_count=len(legacy_examples),
        db_only_examples=_cap_examples(db_only_examples, limit),
        journal_only_examples=_cap_examples(journal_only_examples, limit),
        ambiguous_examples=_cap_examples(ambiguous_examples, limit),
        legacy_unmatchable_examples=_cap_examples(legacy_examples, limit),
    )


def format_daily_review_parity_lines(result: PaperTradeJournalParityResult) -> list[str]:
    """Render the compact daily-review trust signal without report-side logic."""

    lines = [
        "Paper-trade journal parity       : "
        f"{result.verdict} matched={result.matched_count} "
        f"db_only={result.db_only_count} journal_only={result.journal_only_count} "
        f"ambiguous={result.ambiguous_count} legacy_unmatchable={result.legacy_unmatchable_count}",
    ]
    if result.verdict != "OK":
        lines.append(
            "WARNING: paper-trade journal/DB parity is not trustworthy; paper-trade counts, "
            "lifecycle terminal counts, and profit attribution require reconciliation"
        )
    return lines


def format_daily_review_parity_error_lines(error: Exception) -> list[str]:
    """Render a fail-closed daily-review trust signal when the control cannot run."""

    return [
        f"Paper-trade journal parity       : ALARM_AMBIGUOUS error={error}",
        "WARNING: paper-trade journal/DB parity is unavailable; paper-trade counts, "
        "lifecycle terminal counts, and profit attribution are not trustworthy",
    ]


def format_botcheck_parity_line(result: PaperTradeJournalParityResult) -> str:
    """Render the one-line botcheck heartbeat for an attested runtime cohort."""

    return (
        "paper_trade_journal_parity: "
        f"verdict={result.verdict} matched={result.matched_count} "
        f"db_only={result.db_only_count} journal_only={result.journal_only_count} "
        f"ambiguous={result.ambiguous_count} legacy_unmatchable={result.legacy_unmatchable_count}"
    )


def format_human_report(result: PaperTradeJournalParityResult) -> str:
    """Render a standalone concise report for the observability CLI."""

    scope = "global"
    if result.runtime_paper_cohort_id is not None:
        scope = f"{result.runtime_paper_cohort_id} ({result.runtime_paper_cohort_kind})"
    return "\n".join(
        (
            format_botcheck_parity_line(result),
            f"scope: {scope}",
            f"paper_db: {result.paper_db_path}",
            f"trade_log_root: {result.trade_log_root}",
        )
    )


def _parse_datetime(value: str) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise argparse.ArgumentTypeError("expected an ISO-8601 timestamp")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("expected a non-negative integer")
    return parsed


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trade-log-root",
        type=Path,
        default=DEFAULT_TRADE_LOG_ROOT,
        help="trade-log root containing archive/ and live/trades.jsonl",
    )
    parser.add_argument(
        "--paper-db",
        type=Path,
        required=True,
        help="paper_trades SQLite database to read with mode=ro",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository root used to validate an optional runtime cohort database",
    )
    parser.add_argument("--runtime-paper-cohort-id")
    parser.add_argument("--runtime-paper-cohort-kind")
    parser.add_argument("--since", type=_parse_datetime, help="inclusive ISO-8601 start")
    parser.add_argument("--until", type=_parse_datetime, help="inclusive ISO-8601 end")
    parser.add_argument(
        "--limit",
        type=_non_negative_int,
        default=20,
        help="maximum examples per discrepancy class (default: 20)",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Return a report exit status, not a verdict exit status."""

    parser = _build_argparser()
    args = parser.parse_args(argv)
    try:
        result = reconcile_paper_trade_journal(
            trade_log_root=args.trade_log_root,
            paper_db_path=args.paper_db,
            repo_root=args.repo_root,
            runtime_paper_cohort_id=args.runtime_paper_cohort_id,
            runtime_paper_cohort_kind=args.runtime_paper_cohort_kind,
            since=args.since,
            until=args.until,
            limit=args.limit,
        )
    except (PaperTradeJournalReconciliationError, ValueError) as exc:
        if args.json:
            json.dump({"verdict": "ALARM_AMBIGUOUS", "error": str(exc)}, sys.stdout, sort_keys=True)
            sys.stdout.write("\n")
        else:
            print(f"paper_trade_journal_reconcile: ERROR {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(format_human_report(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
