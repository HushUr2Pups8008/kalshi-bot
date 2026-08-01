"""Immutable evidence for paper-side calibration quarantine decisions.

This module intentionally depends only on the standard library.  It stores
decision-time facts and never imports runtime, order, queue, executor, or
paper-trader code.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal


_SCHEMA_VERSION = 4
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_UTC_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"
)
_POLYMARKET_ID_PATTERN = re.compile(r"[0-9]+")
_POLYMARKET_SLUG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_VENUES = frozenset(("kalshi", "polymarket_us"))
_COHORT_KINDS = frozenset(("active", "legacy", "legacy_pending"))
_PROVENANCE_STATES = frozenset(("available", "unavailable", "not_applicable"))


class SideCalibrationQuarantineError(RuntimeError):
    """Raised when immutable quarantine evidence cannot be recorded safely."""


class SideCalibrationQuarantineSchemaError(SideCalibrationQuarantineError):
    """Raised when a pre-existing database differs from the exact schema contract."""


@dataclass(frozen=True)
class SideCalibrationProvenance:
    """One explicit available, unavailable, or not-applicable provenance fact."""

    state: str
    identifier: str | None = None
    payload_sha256: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class SideCalibrationSizingProvenance:
    """Frozen sizing inputs used for the quarantined paper decision."""

    state: str
    method: str | None = None
    requested_stake_dollars: Decimal | int | float | str | None = None
    payload_sha256: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class SideCalibrationFeeContext:
    """Decision-time fee role, schedule, and provenance fingerprints."""

    state: str
    fee_role: str | None = None
    fee_schedule_sha256: str | None = None
    provenance_sha256: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class SideCalibrationMarketContract:
    """Frozen market contract metadata needed for later authoritative settlement."""

    state: str
    canonical_contract: str | None = None
    question: str | None = None
    market_snapshot_sha256: str | None = None
    scheduled_close_at: datetime | str | None = None
    scheduled_settlement_at: datetime | str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class SideCalibrationPaperCohort:
    """Paper cohort boundary repeated on every evidence record."""

    cohort_id: str | None
    cohort_kind: str | None
    cohort_identity: str | None
    manifest_sha256: str | None


@dataclass(frozen=True)
class SideCalibrationPolicy:
    """Quarantine policy contract repeated on every evidence record."""

    policy_id: str | None
    policy_version: str | None
    schema_version: int | None
    payload_sha256: str | None


@dataclass(frozen=True)
class SideCalibrationCapture:
    """Frozen envelope for one immutable paper-side decision identity.

    Candidate-only fields may be unavailable or invalid.  The store preserves
    such an envelope as a typed unscorable attempt instead of filling facts in
    later or promoting it after the decision.
    """

    capture_id: str
    lifecycle_id: str | None
    decision_at: datetime | str | None
    captured_at: datetime | str | None
    venue: str | None
    ticker: str | None
    native_market_id: str | None
    settlement_alias: str | None
    side: str | None
    model_yes_probability: Decimal | int | float | str | None
    selected_side_probability: Decimal | int | float | str | None
    executed_price: Decimal | int | float | str | None
    derived_gross_edge: Decimal | int | float | str | None
    reported_gross_edge: Decimal | int | float | str | None
    sizing: SideCalibrationSizingProvenance
    book_observed_at: datetime | str | None
    book_payload_sha256: str | None
    evidence_ids: tuple[str, ...]
    research_provenance: SideCalibrationProvenance
    dossier_provenance: SideCalibrationProvenance
    run_provenance: SideCalibrationProvenance
    contract_provenance: SideCalibrationProvenance
    fee_context: SideCalibrationFeeContext
    market_contract: SideCalibrationMarketContract
    paper_cohort: SideCalibrationPaperCohort
    quarantine_policy: SideCalibrationPolicy
    software_provenance: SideCalibrationProvenance
    config_provenance: SideCalibrationProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.capture_id, str) or not self.capture_id.strip():
            raise SideCalibrationQuarantineError("capture_id must be a non-empty string")
        if not isinstance(self.evidence_ids, tuple):
            raise SideCalibrationQuarantineError("evidence_ids must be a tuple")
        expected_types = {
            "sizing": SideCalibrationSizingProvenance,
            "research_provenance": SideCalibrationProvenance,
            "dossier_provenance": SideCalibrationProvenance,
            "run_provenance": SideCalibrationProvenance,
            "contract_provenance": SideCalibrationProvenance,
            "fee_context": SideCalibrationFeeContext,
            "market_contract": SideCalibrationMarketContract,
            "paper_cohort": SideCalibrationPaperCohort,
            "quarantine_policy": SideCalibrationPolicy,
            "software_provenance": SideCalibrationProvenance,
            "config_provenance": SideCalibrationProvenance,
        }
        for name, expected_type in expected_types.items():
            if not isinstance(getattr(self, name), expected_type):
                raise SideCalibrationQuarantineError(f"{name} has an invalid envelope type")


@dataclass(frozen=True)
class SideCalibrationCaptureAttempt:
    """Persisted immutable attempt, including an unscorable disposition."""

    attempt_id: str
    capture_id: str
    payload_sha256: str
    disposition: Literal["candidate", "unscorable"]
    unscorable_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SideCalibrationCandidate:
    """Persisted frozen candidate promoted from one complete capture attempt."""

    candidate_id: str
    attempt_id: str
    capture_id: str
    payload_sha256: str


@dataclass(frozen=True)
class SideCalibrationCaptureResult:
    """Deterministic disposition for an append or replay request."""

    status: Literal["inserted", "unscorable", "duplicate", "conflict"]
    disposition: Literal["candidate", "unscorable", "conflict"]
    capture_id: str
    attempt_id: str
    candidate_id: str | None
    payload_sha256: str
    unscorable_reasons: tuple[str, ...]
    conflict_id: str | None
    attempt: SideCalibrationCaptureAttempt
    candidate: SideCalibrationCandidate | None


@dataclass(frozen=True)
class SideCalibrationQuarantineSnapshot:
    """Read-only count projection; no mutable status is maintained in the store."""

    attempt_count: int
    candidate_count: int
    lifecycle_event_count: int
    conflict_count: int


def canonical_json(value: object) -> str:
    """Return the only JSON representation accepted by this store."""
    return json.dumps(
        _normalize_json(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(namespace: str, *parts: str) -> str:
    return _sha256("\x1f".join((namespace, *parts)))


def _immutable_triggers(tables: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    statements: list[tuple[str, str]] = []
    for table in tables:
        for operation in ("update", "delete"):
            name = f"immutable_{table}_{operation}"
            statements.append(
                (
                    name,
                    f"""
                    CREATE TRIGGER {name}
                    BEFORE {operation.upper()} ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is append-only');
                    END
                    """,
                )
            )
    return tuple(statements)


_TABLE_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "side_calibration_schema_meta",
        """
        CREATE TABLE side_calibration_schema_meta (
            schema_version INTEGER PRIMARY KEY CHECK (schema_version = 4),
            ddl_sha256 TEXT NOT NULL CHECK (length(ddl_sha256) = 64),
            applied_at TEXT NOT NULL
        )
        """,
    ),
    (
        "side_calibration_capture_attempts",
        """
        CREATE TABLE side_calibration_capture_attempts (
            attempt_id TEXT PRIMARY KEY CHECK (length(attempt_id) = 64),
            capture_id TEXT NOT NULL UNIQUE,
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            payload_json TEXT NOT NULL,
            disposition TEXT NOT NULL CHECK (disposition IN ('candidate','unscorable')),
            unscorable_reasons_json TEXT NOT NULL,
            market_contract_json TEXT NOT NULL,
            cohort_json TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """,
    ),
    (
        "side_calibration_candidates",
        """
        CREATE TABLE side_calibration_candidates (
            candidate_id TEXT PRIMARY KEY CHECK (length(candidate_id) = 64),
            attempt_id TEXT NOT NULL UNIQUE REFERENCES side_calibration_capture_attempts(attempt_id),
            capture_id TEXT NOT NULL UNIQUE REFERENCES side_calibration_capture_attempts(capture_id),
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            candidate_json TEXT NOT NULL,
            market_contract_json TEXT NOT NULL,
            cohort_json TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            frozen_at TEXT NOT NULL
        )
        """,
    ),
    (
        "side_calibration_lifecycle_events",
        """
        CREATE TABLE side_calibration_lifecycle_events (
            event_id TEXT PRIMARY KEY CHECK (length(event_id) = 64),
            attempt_id TEXT NOT NULL REFERENCES side_calibration_capture_attempts(attempt_id),
            capture_id TEXT NOT NULL REFERENCES side_calibration_capture_attempts(capture_id),
            event_type TEXT NOT NULL CHECK (
                event_type IN (
                    'capture_recorded','candidate_frozen','capture_unscorable',
                    'identical_replay','conflict_recorded'
                )
            ),
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            details_json TEXT NOT NULL,
            cohort_json TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(capture_id, event_type, payload_sha256)
        )
        """,
    ),
    (
        "side_calibration_conflicts",
        """
        CREATE TABLE side_calibration_conflicts (
            conflict_id TEXT PRIMARY KEY CHECK (length(conflict_id) = 64),
            attempt_id TEXT NOT NULL REFERENCES side_calibration_capture_attempts(attempt_id),
            capture_id TEXT NOT NULL REFERENCES side_calibration_capture_attempts(capture_id),
            existing_payload_sha256 TEXT NOT NULL CHECK (length(existing_payload_sha256) = 64),
            incoming_payload_sha256 TEXT NOT NULL CHECK (length(incoming_payload_sha256) = 64),
            details_json TEXT NOT NULL,
            cohort_json TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(capture_id, existing_payload_sha256, incoming_payload_sha256)
        )
        """,
    ),
)
_INDEX_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "idx_side_calibration_lifecycle_capture",
        "CREATE INDEX idx_side_calibration_lifecycle_capture "
        "ON side_calibration_lifecycle_events(capture_id, recorded_at)",
    ),
    (
        "idx_side_calibration_conflicts_capture",
        "CREATE INDEX idx_side_calibration_conflicts_capture "
        "ON side_calibration_conflicts(capture_id, recorded_at)",
    ),
)
_TABLE_NAMES = tuple(name for name, _statement in _TABLE_STATEMENTS)
_TRIGGER_STATEMENTS = _immutable_triggers(_TABLE_NAMES)
_TARGET_STATEMENTS = _TABLE_STATEMENTS + _INDEX_STATEMENTS + _TRIGGER_STATEMENTS
_DDL_CONTRACT = "\n".join(
    f"-- {name}\n{statement.strip()};" for name, statement in _TARGET_STATEMENTS
)
_DDL_SHA256 = _sha256(_DDL_CONTRACT)


def _normalize_schema_sql(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().rstrip(";").lower().split())


_TARGET_OBJECT_TYPES = {
    **{name: "table" for name, _statement in _TABLE_STATEMENTS},
    **{name: "index" for name, _statement in _INDEX_STATEMENTS},
    **{name: "trigger" for name, _statement in _TRIGGER_STATEMENTS},
}
_TARGET_SQL = {
    name: _normalize_schema_sql(statement) for name, statement in _TARGET_STATEMENTS
}


class SideCalibrationQuarantineStore:
    """Dedicated SQLite append-only evidence store with exact schema validation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            self._initialize(connection)

    def append_capture(
        self, capture: SideCalibrationCapture
    ) -> SideCalibrationCaptureResult:
        """Persist a frozen candidate or typed unscorable attempt exactly once."""
        if not isinstance(capture, SideCalibrationCapture):
            raise TypeError("capture must be SideCalibrationCapture")
        payload = _capture_payload(capture)
        payload_json = canonical_json(payload)
        payload_sha256 = _sha256(payload_json)
        cohort_json = canonical_json(_cohort_payload(capture.paper_cohort))
        policy_json = canonical_json(_policy_payload(capture.quarantine_policy))
        market_contract_json = canonical_json(_market_contract_payload(capture.market_contract))
        reasons = _candidate_reasons(capture)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._validate_schema(connection)
            existing = self._attempt_for_capture_id(connection, capture.capture_id)
            if existing is not None:
                if existing.payload_sha256 == payload_sha256:
                    self._insert_lifecycle_event(
                        connection,
                        attempt=existing,
                        event_type="identical_replay",
                        payload_sha256=payload_sha256,
                        cohort_json=cohort_json,
                        policy_json=policy_json,
                        details={"disposition": existing.disposition},
                    )
                    return self._result_for_existing(connection, existing, status="duplicate")
                return self._record_conflict(
                    connection,
                    existing=existing,
                    incoming_payload_sha256=payload_sha256,
                    incoming_cohort_json=cohort_json,
                    incoming_policy_json=policy_json,
                )

            attempt = self._insert_attempt(
                connection,
                capture_id=capture.capture_id,
                payload_sha256=payload_sha256,
                payload_json=payload_json,
                reasons=reasons,
                market_contract_json=market_contract_json,
                cohort_json=cohort_json,
                policy_json=policy_json,
            )
            self._insert_lifecycle_event(
                connection,
                attempt=attempt,
                event_type="capture_recorded",
                payload_sha256=payload_sha256,
                cohort_json=cohort_json,
                policy_json=policy_json,
                details={"disposition": attempt.disposition},
            )
            if reasons:
                self._insert_lifecycle_event(
                    connection,
                    attempt=attempt,
                    event_type="capture_unscorable",
                    payload_sha256=payload_sha256,
                    cohort_json=cohort_json,
                    policy_json=policy_json,
                    details={"reasons": list(reasons)},
                )
                return SideCalibrationCaptureResult(
                    status="unscorable",
                    disposition="unscorable",
                    capture_id=capture.capture_id,
                    attempt_id=attempt.attempt_id,
                    candidate_id=None,
                    payload_sha256=payload_sha256,
                    unscorable_reasons=reasons,
                    conflict_id=None,
                    attempt=attempt,
                    candidate=None,
                )

            candidate = self._insert_candidate(
                connection,
                attempt=attempt,
                payload_json=payload_json,
                market_contract_json=market_contract_json,
                cohort_json=cohort_json,
                policy_json=policy_json,
            )
            self._insert_lifecycle_event(
                connection,
                attempt=attempt,
                event_type="candidate_frozen",
                payload_sha256=payload_sha256,
                cohort_json=cohort_json,
                policy_json=policy_json,
                details={"candidate_id": candidate.candidate_id},
            )
            return SideCalibrationCaptureResult(
                status="inserted",
                disposition="candidate",
                capture_id=capture.capture_id,
                attempt_id=attempt.attempt_id,
                candidate_id=candidate.candidate_id,
                payload_sha256=payload_sha256,
                unscorable_reasons=(),
                conflict_id=None,
                attempt=attempt,
                candidate=candidate,
            )

    def snapshot(self) -> SideCalibrationQuarantineSnapshot:
        """Return evidence counts after validating the immutable schema contract."""
        with self._connection() as connection:
            self._validate_schema(connection)
            return SideCalibrationQuarantineSnapshot(
                attempt_count=_row_count(connection, "side_calibration_capture_attempts"),
                candidate_count=_row_count(connection, "side_calibration_candidates"),
                lifecycle_event_count=_row_count(
                    connection, "side_calibration_lifecycle_events"
                ),
                conflict_count=_row_count(connection, "side_calibration_conflicts"),
            )

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = _user_schema_objects(connection)
            if not existing:
                for _name, statement in _TARGET_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO side_calibration_schema_meta(
                        schema_version, ddl_sha256, applied_at
                    ) VALUES (?, ?, ?)
                    """,
                    (_SCHEMA_VERSION, _DDL_SHA256, _timestamp(datetime.now(timezone.utc))),
                )
            self._validate_schema(connection)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        objects = _user_schema_objects(connection)
        actual_types = {name: kind for kind, name, _sql in objects}
        if actual_types != _TARGET_OBJECT_TYPES:
            raise SideCalibrationQuarantineSchemaError("side calibration schema drift")
        for kind, name, sql in objects:
            if kind != _TARGET_OBJECT_TYPES[name] or _normalize_schema_sql(sql) != _TARGET_SQL[name]:
                raise SideCalibrationQuarantineSchemaError("side calibration schema drift")
        metadata = connection.execute(
            "SELECT schema_version, ddl_sha256, applied_at FROM side_calibration_schema_meta"
        ).fetchall()
        if len(metadata) != 1 or metadata[0][0:2] != (_SCHEMA_VERSION, _DDL_SHA256):
            raise SideCalibrationQuarantineSchemaError("side calibration schema drift")
        if _utc_datetime_from_timestamp(str(metadata[0][2])) is None:
            raise SideCalibrationQuarantineSchemaError("side calibration schema drift")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SideCalibrationQuarantineSchemaError("side calibration schema drift")

    @staticmethod
    def _attempt_for_capture_id(
        connection: sqlite3.Connection, capture_id: str
    ) -> SideCalibrationCaptureAttempt | None:
        row = connection.execute(
            """
            SELECT attempt_id, capture_id, payload_sha256, disposition,
                   unscorable_reasons_json
            FROM side_calibration_capture_attempts
            WHERE capture_id = ?
            """,
            (capture_id,),
        ).fetchone()
        if row is None:
            return None
        return SideCalibrationCaptureAttempt(
            attempt_id=str(row[0]),
            capture_id=str(row[1]),
            payload_sha256=str(row[2]),
            disposition=str(row[3]),
            unscorable_reasons=tuple(json.loads(str(row[4]))),
        )

    @staticmethod
    def _candidate_for_attempt(
        connection: sqlite3.Connection, attempt_id: str
    ) -> SideCalibrationCandidate | None:
        row = connection.execute(
            """
            SELECT candidate_id, attempt_id, capture_id, payload_sha256
            FROM side_calibration_candidates WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if row is None:
            return None
        return SideCalibrationCandidate(
            candidate_id=str(row[0]),
            attempt_id=str(row[1]),
            capture_id=str(row[2]),
            payload_sha256=str(row[3]),
        )

    def _result_for_existing(
        self,
        connection: sqlite3.Connection,
        attempt: SideCalibrationCaptureAttempt,
        *,
        status: Literal["duplicate"],
    ) -> SideCalibrationCaptureResult:
        candidate = self._candidate_for_attempt(connection, attempt.attempt_id)
        if attempt.disposition == "candidate" and candidate is None:
            raise SideCalibrationQuarantineError("candidate attempt is missing frozen candidate")
        if attempt.disposition == "unscorable" and candidate is not None:
            raise SideCalibrationQuarantineError("unscorable attempt has frozen candidate")
        return SideCalibrationCaptureResult(
            status=status,
            disposition=attempt.disposition,
            capture_id=attempt.capture_id,
            attempt_id=attempt.attempt_id,
            candidate_id=None if candidate is None else candidate.candidate_id,
            payload_sha256=attempt.payload_sha256,
            unscorable_reasons=attempt.unscorable_reasons,
            conflict_id=None,
            attempt=attempt,
            candidate=candidate,
        )

    @staticmethod
    def _insert_attempt(
        connection: sqlite3.Connection,
        *,
        capture_id: str,
        payload_sha256: str,
        payload_json: str,
        reasons: tuple[str, ...],
        market_contract_json: str,
        cohort_json: str,
        policy_json: str,
    ) -> SideCalibrationCaptureAttempt:
        attempt_id = _stable_id("side-calibration-attempt-v1", capture_id, payload_sha256)
        disposition: Literal["candidate", "unscorable"] = (
            "unscorable" if reasons else "candidate"
        )
        connection.execute(
            """
            INSERT INTO side_calibration_capture_attempts(
                attempt_id, capture_id, payload_sha256, payload_json, disposition,
                unscorable_reasons_json, market_contract_json, cohort_json, policy_json,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                capture_id,
                payload_sha256,
                payload_json,
                disposition,
                canonical_json(list(reasons)),
                market_contract_json,
                cohort_json,
                policy_json,
                _timestamp(datetime.now(timezone.utc)),
            ),
        )
        return SideCalibrationCaptureAttempt(
            attempt_id=attempt_id,
            capture_id=capture_id,
            payload_sha256=payload_sha256,
            disposition=disposition,
            unscorable_reasons=reasons,
        )

    @staticmethod
    def _insert_candidate(
        connection: sqlite3.Connection,
        *,
        attempt: SideCalibrationCaptureAttempt,
        payload_json: str,
        market_contract_json: str,
        cohort_json: str,
        policy_json: str,
    ) -> SideCalibrationCandidate:
        candidate_id = _stable_id(
            "side-calibration-candidate-v1", attempt.capture_id, attempt.payload_sha256
        )
        connection.execute(
            """
            INSERT INTO side_calibration_candidates(
                candidate_id, attempt_id, capture_id, payload_sha256, candidate_json,
                market_contract_json, cohort_json, policy_json, frozen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                attempt.attempt_id,
                attempt.capture_id,
                attempt.payload_sha256,
                payload_json,
                market_contract_json,
                cohort_json,
                policy_json,
                _timestamp(datetime.now(timezone.utc)),
            ),
        )
        return SideCalibrationCandidate(
            candidate_id=candidate_id,
            attempt_id=attempt.attempt_id,
            capture_id=attempt.capture_id,
            payload_sha256=attempt.payload_sha256,
        )

    @staticmethod
    def _insert_lifecycle_event(
        connection: sqlite3.Connection,
        *,
        attempt: SideCalibrationCaptureAttempt,
        event_type: Literal[
            "capture_recorded",
            "candidate_frozen",
            "capture_unscorable",
            "identical_replay",
            "conflict_recorded",
        ],
        payload_sha256: str,
        cohort_json: str,
        policy_json: str,
        details: dict[str, object],
    ) -> str:
        event_id = _stable_id(
            "side-calibration-lifecycle-v1",
            attempt.capture_id,
            event_type,
            payload_sha256,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO side_calibration_lifecycle_events(
                event_id, attempt_id, capture_id, event_type, payload_sha256,
                details_json, cohort_json, policy_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                attempt.attempt_id,
                attempt.capture_id,
                event_type,
                payload_sha256,
                canonical_json(details),
                cohort_json,
                policy_json,
                _timestamp(datetime.now(timezone.utc)),
            ),
        )
        return event_id

    def _record_conflict(
        self,
        connection: sqlite3.Connection,
        *,
        existing: SideCalibrationCaptureAttempt,
        incoming_payload_sha256: str,
        incoming_cohort_json: str,
        incoming_policy_json: str,
    ) -> SideCalibrationCaptureResult:
        conflict_id = _stable_id(
            "side-calibration-conflict-v1",
            existing.capture_id,
            existing.payload_sha256,
            incoming_payload_sha256,
        )
        details = {
            "reason": "immutable_capture_id_collision",
            "existing_payload_sha256": existing.payload_sha256,
            "incoming_payload_sha256": incoming_payload_sha256,
        }
        connection.execute(
            """
            INSERT OR IGNORE INTO side_calibration_conflicts(
                conflict_id, attempt_id, capture_id, existing_payload_sha256,
                incoming_payload_sha256, details_json, cohort_json, policy_json,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conflict_id,
                existing.attempt_id,
                existing.capture_id,
                existing.payload_sha256,
                incoming_payload_sha256,
                canonical_json(details),
                incoming_cohort_json,
                incoming_policy_json,
                _timestamp(datetime.now(timezone.utc)),
            ),
        )
        self._insert_lifecycle_event(
            connection,
            attempt=existing,
            event_type="conflict_recorded",
            payload_sha256=incoming_payload_sha256,
            cohort_json=incoming_cohort_json,
            policy_json=incoming_policy_json,
            details={"conflict_id": conflict_id},
        )
        candidate = self._candidate_for_attempt(connection, existing.attempt_id)
        return SideCalibrationCaptureResult(
            status="conflict",
            disposition="conflict",
            capture_id=existing.capture_id,
            attempt_id=existing.attempt_id,
            candidate_id=None if candidate is None else candidate.candidate_id,
            payload_sha256=incoming_payload_sha256,
            unscorable_reasons=(),
            conflict_id=conflict_id,
            attempt=existing,
            candidate=candidate,
        )


def _capture_payload(capture: SideCalibrationCapture) -> dict[str, object]:
    return {
        "capture_id": capture.capture_id,
        "lifecycle_id": _raw_text(capture.lifecycle_id),
        "decision_at": _raw_timestamp(capture.decision_at),
        "captured_at": _raw_timestamp(capture.captured_at),
        "venue": _raw_text(capture.venue),
        "ticker": _raw_text(capture.ticker),
        "native_market_id": _raw_text(capture.native_market_id),
        "settlement_alias": _raw_text(capture.settlement_alias),
        "side": _raw_text(capture.side),
        "model_yes_probability": _raw_number(capture.model_yes_probability),
        "selected_side_probability": _raw_number(capture.selected_side_probability),
        "executed_price": _raw_number(capture.executed_price),
        "derived_gross_edge": _raw_number(capture.derived_gross_edge),
        "reported_gross_edge": _raw_number(capture.reported_gross_edge),
        "sizing": _sizing_payload(capture.sizing),
        "book_observed_at": _raw_timestamp(capture.book_observed_at),
        "book_payload_sha256": _raw_text(capture.book_payload_sha256),
        "evidence_ids": _evidence_ids_payload(capture.evidence_ids),
        "research_provenance": _provenance_payload(capture.research_provenance),
        "dossier_provenance": _provenance_payload(capture.dossier_provenance),
        "run_provenance": _provenance_payload(capture.run_provenance),
        "contract_provenance": _provenance_payload(capture.contract_provenance),
        "fee_context": _fee_context_payload(capture.fee_context),
        "market_contract": _market_contract_payload(capture.market_contract),
        "paper_cohort": _cohort_payload(capture.paper_cohort),
        "quarantine_policy": _policy_payload(capture.quarantine_policy),
        "software_provenance": _provenance_payload(capture.software_provenance),
        "config_provenance": _provenance_payload(capture.config_provenance),
    }


def _provenance_payload(value: SideCalibrationProvenance) -> dict[str, object]:
    return {
        "detail": _raw_text(value.detail),
        "identifier": _raw_text(value.identifier),
        "payload_sha256": _raw_text(value.payload_sha256),
        "state": _raw_text(value.state),
    }


def _evidence_ids_payload(evidence_ids: tuple[str, ...]) -> list[object]:
    if all(isinstance(identifier, str) for identifier in evidence_ids):
        return sorted(evidence_ids)
    return list(evidence_ids)


def _sizing_payload(value: SideCalibrationSizingProvenance) -> dict[str, object]:
    return {
        "detail": _raw_text(value.detail),
        "method": _raw_text(value.method),
        "payload_sha256": _raw_text(value.payload_sha256),
        "requested_stake_dollars": _raw_number(value.requested_stake_dollars),
        "state": _raw_text(value.state),
    }


def _fee_context_payload(value: SideCalibrationFeeContext) -> dict[str, object]:
    return {
        "detail": _raw_text(value.detail),
        "fee_role": _raw_text(value.fee_role),
        "fee_schedule_sha256": _raw_text(value.fee_schedule_sha256),
        "provenance_sha256": _raw_text(value.provenance_sha256),
        "state": _raw_text(value.state),
    }


def _market_contract_payload(value: SideCalibrationMarketContract) -> dict[str, object]:
    return {
        "canonical_contract": _raw_text(value.canonical_contract),
        "detail": _raw_text(value.detail),
        "market_snapshot_sha256": _raw_text(value.market_snapshot_sha256),
        "question": _raw_text(value.question),
        "scheduled_close_at": _raw_timestamp(value.scheduled_close_at),
        "scheduled_settlement_at": _raw_timestamp(value.scheduled_settlement_at),
        "state": _raw_text(value.state),
    }


def _cohort_payload(value: SideCalibrationPaperCohort) -> dict[str, object]:
    return {
        "cohort_id": _raw_text(value.cohort_id),
        "cohort_identity": _raw_text(value.cohort_identity),
        "cohort_kind": _raw_text(value.cohort_kind),
        "manifest_sha256": _raw_text(value.manifest_sha256),
    }


def _policy_payload(value: SideCalibrationPolicy) -> dict[str, object]:
    return {
        "payload_sha256": _raw_text(value.payload_sha256),
        "policy_id": _raw_text(value.policy_id),
        "policy_version": _raw_text(value.policy_version),
        "schema_version": value.schema_version,
    }


def _candidate_reasons(capture: SideCalibrationCapture) -> tuple[str, ...]:
    reasons: list[str] = []
    decision_at = _utc_datetime(capture.decision_at)
    captured_at = _utc_datetime(capture.captured_at)
    book_observed_at = _utc_datetime(capture.book_observed_at)
    _add_datetime_reason(reasons, "decision_at", capture.decision_at, decision_at)
    _add_datetime_reason(reasons, "captured_at", capture.captured_at, captured_at)
    _add_datetime_reason(
        reasons, "book_observed_at", capture.book_observed_at, book_observed_at
    )
    if decision_at is not None and captured_at is not None and captured_at < decision_at:
        reasons.append("captured_at_precedes_decision_at")
    if (
        decision_at is not None
        and book_observed_at is not None
        and book_observed_at > decision_at
    ):
        reasons.append("book_observed_after_decision_at")
    if capture.venue not in _VENUES:
        reasons.append("invalid_venue")
    for name in ("lifecycle_id", "ticker", "native_market_id"):
        if not _is_text(getattr(capture, name)):
            reasons.append(f"unavailable_{name}")
    if capture.venue == "polymarket_us":
        if (
            not isinstance(capture.native_market_id, str)
            or _POLYMARKET_ID_PATTERN.fullmatch(capture.native_market_id) is None
        ):
            reasons.append("invalid_polymarket_native_market_id")
        if (
            not isinstance(capture.settlement_alias, str)
            or _POLYMARKET_SLUG_PATTERN.fullmatch(capture.settlement_alias) is None
        ):
            reasons.append("invalid_polymarket_settlement_alias")
    if capture.side not in {"yes", "no"}:
        reasons.append("invalid_side")
    model_yes = _number_reason(reasons, "model_yes_probability", capture.model_yes_probability)
    selected = _number_reason(
        reasons, "selected_side_probability", capture.selected_side_probability
    )
    executed = _number_reason(reasons, "executed_price", capture.executed_price)
    derived = _number_reason(reasons, "derived_gross_edge", capture.derived_gross_edge)
    _number_reason(reasons, "reported_gross_edge", capture.reported_gross_edge)
    if model_yes is not None and not Decimal("0") <= model_yes <= Decimal("1"):
        reasons.append("invalid_model_yes_probability")
    if selected is not None and not Decimal("0") <= selected <= Decimal("1"):
        reasons.append("invalid_selected_side_probability")
    if executed is not None and not Decimal("0") <= executed <= Decimal("1"):
        reasons.append("invalid_executed_price")
    if capture.side in {"yes", "no"} and model_yes is not None and selected is not None:
        expected = model_yes if capture.side == "yes" else Decimal("1") - model_yes
        if selected != expected:
            reasons.append("selected_side_probability_mismatch")
    if selected is not None and executed is not None and derived is not None:
        if derived != selected - executed:
            reasons.append("derived_gross_edge_mismatch")
        elif derived <= Decimal("0"):
            reasons.append("nonpositive_derived_gross_edge")
    _add_sizing_reasons(reasons, capture.sizing)
    if not _is_sha256(capture.book_payload_sha256):
        reasons.append("invalid_book_payload_sha256")
    _add_evidence_reasons(reasons, capture.evidence_ids)
    _add_provenance_reasons(
        reasons, "research_provenance", capture.research_provenance, allow_not_applicable=True
    )
    _add_provenance_reasons(
        reasons, "dossier_provenance", capture.dossier_provenance, allow_not_applicable=True
    )
    _add_provenance_reasons(
        reasons, "run_provenance", capture.run_provenance, allow_not_applicable=False
    )
    _add_provenance_reasons(
        reasons,
        "contract_provenance",
        capture.contract_provenance,
        allow_not_applicable=False,
    )
    _add_fee_reasons(reasons, capture.fee_context)
    _add_market_contract_reasons(reasons, capture.market_contract)
    if not _valid_cohort(capture.paper_cohort):
        reasons.append("invalid_paper_cohort")
    if not _valid_policy(capture.quarantine_policy):
        reasons.append("invalid_quarantine_policy")
    _add_provenance_reasons(
        reasons, "software_provenance", capture.software_provenance, allow_not_applicable=False
    )
    _add_provenance_reasons(
        reasons, "config_provenance", capture.config_provenance, allow_not_applicable=False
    )
    return tuple(dict.fromkeys(reasons))


def _add_datetime_reason(
    reasons: list[str], name: str, raw_value: object, parsed: datetime | None
) -> None:
    if parsed is not None:
        return
    reasons.append(f"unavailable_{name}" if raw_value is None else f"invalid_{name}")


def _number_reason(
    reasons: list[str], name: str, value: object
) -> Decimal | None:
    if value is None:
        reasons.append(f"unavailable_{name}")
        return None
    number = _decimal_value(value)
    if number is None:
        reasons.append(f"invalid_{name}")
    return number


def _add_sizing_reasons(
    reasons: list[str], value: SideCalibrationSizingProvenance
) -> None:
    if value.state not in _PROVENANCE_STATES:
        reasons.append("invalid_sizing_provenance")
        return
    if value.state != "available":
        if not _is_text(value.detail):
            reasons.append("invalid_sizing_provenance")
        else:
            reasons.append("unavailable_sizing_provenance")
        return
    stake = _decimal_value(value.requested_stake_dollars)
    if (
        not _is_text(value.method)
        or not _is_sha256(value.payload_sha256)
        or stake is None
        or stake <= Decimal("0")
    ):
        reasons.append("invalid_sizing_provenance")


def _add_fee_reasons(reasons: list[str], value: SideCalibrationFeeContext) -> None:
    if value.state not in _PROVENANCE_STATES:
        reasons.append("invalid_fee_context")
        return
    if value.state != "available":
        if not _is_text(value.detail):
            reasons.append("invalid_fee_context")
        else:
            reasons.append("unavailable_fee_context")
        return
    if (
        value.fee_role not in {"maker", "taker"}
        or not _is_sha256(value.fee_schedule_sha256)
        or not _is_sha256(value.provenance_sha256)
    ):
        reasons.append("invalid_fee_context")


def _add_market_contract_reasons(
    reasons: list[str], value: SideCalibrationMarketContract
) -> None:
    if value.state not in _PROVENANCE_STATES:
        reasons.append("invalid_market_contract_metadata")
        return
    if value.state != "available":
        if (
            not _is_text(value.detail)
            or value.canonical_contract is not None
            or value.question is not None
            or value.market_snapshot_sha256 is not None
            or value.scheduled_close_at is not None
            or value.scheduled_settlement_at is not None
        ):
            reasons.append("invalid_market_contract_metadata")
        else:
            reasons.append("unavailable_market_contract_metadata")
        return
    close_at = _utc_datetime(value.scheduled_close_at)
    settlement_at = _utc_datetime(value.scheduled_settlement_at)
    if (
        not _is_text(value.canonical_contract)
        or not _is_text(value.question)
        or not _is_sha256(value.market_snapshot_sha256)
        or close_at is None
        or settlement_at is None
        or settlement_at < close_at
    ):
        reasons.append("invalid_market_contract_metadata")


def _add_provenance_reasons(
    reasons: list[str],
    name: str,
    value: SideCalibrationProvenance,
    *,
    allow_not_applicable: bool,
) -> None:
    if value.state not in _PROVENANCE_STATES:
        reasons.append(f"invalid_{name}")
        return
    if value.state == "available":
        if not _is_text(value.identifier) or not _is_sha256(value.payload_sha256):
            reasons.append(f"invalid_{name}")
        return
    if not _is_text(value.detail) or value.identifier is not None or value.payload_sha256 is not None:
        reasons.append(f"invalid_{name}")
    elif value.state == "unavailable" or not allow_not_applicable:
        reasons.append(f"unavailable_{name}")


def _add_evidence_reasons(reasons: list[str], evidence_ids: tuple[str, ...]) -> None:
    if not evidence_ids:
        reasons.append("unavailable_evidence_ids")
    elif (
        any(not _is_text(identifier) for identifier in evidence_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
    ):
        reasons.append("invalid_evidence_ids")


def _valid_cohort(value: SideCalibrationPaperCohort) -> bool:
    return (
        _is_text(value.cohort_id)
        and value.cohort_kind in _COHORT_KINDS
        and _is_text(value.cohort_identity)
        and _is_sha256(value.manifest_sha256)
    )


def _valid_policy(value: SideCalibrationPolicy) -> bool:
    return (
        _is_text(value.policy_id)
        and _is_text(value.policy_version)
        and isinstance(value.schema_version, int)
        and not isinstance(value.schema_version, bool)
        and value.schema_version > 0
        and _is_sha256(value.payload_sha256)
    )


def _normalize_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimal")
        return _decimal_text(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return _decimal_text(Decimal(str(value)))
    if isinstance(value, list) or isinstance(value, tuple):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _normalize_json(item) for key, item in value.items()}
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _decimal_value(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, Decimal):
            decimal = value
        elif isinstance(value, (int, float, str)):
            decimal = Decimal(str(value))
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    return decimal if decimal.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _raw_number(value: object) -> str | None:
    if value is None:
        return None
    decimal = _decimal_value(value)
    return _decimal_text(decimal) if decimal is not None else str(value)


def _raw_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _raw_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = _utc_datetime(value)
        return value if parsed is None else _timestamp(parsed)
    if isinstance(value, datetime):
        parsed = _utc_datetime(value)
        return value.isoformat() if parsed is None else _timestamp(parsed)
    raise SideCalibrationQuarantineError("timestamp fields must be datetime, string, or None")


def _utc_datetime(value: object) -> datetime | None:
    if isinstance(value, str):
        if _CANONICAL_UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
            return None
        try:
            value = datetime.fromisoformat(f"{value[:-1]}+00:00")
        except ValueError:
            return None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        return None
    return value


def _timestamp(value: datetime) -> str:
    if _utc_datetime(value) is None:
        raise SideCalibrationQuarantineError("store timestamp must be UTC")
    return value.isoformat().replace("+00:00", "Z")


def _utc_datetime_from_timestamp(value: str) -> datetime | None:
    return _utc_datetime(value)


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX_PATTERN.fullmatch(value) is not None


def _user_schema_objects(connection: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return [
        (str(kind), str(name), str(sql))
        for kind, name, sql in connection.execute(
            """
            SELECT type, name, sql FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    ]


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
