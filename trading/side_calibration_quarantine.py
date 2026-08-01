"""Append-only evidence storage for paper-side calibration candidates."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 1
_REQUIRED_DECISION_FACTS = (
    "market_ticker",
    "side",
    "market_price",
    "gross_edge",
)


class SideCalibrationQuarantineError(RuntimeError):
    """Raised when a capture would violate immutable quarantine evidence."""


@dataclass(frozen=True)
class SideCalibrationCapture:
    """Plain serializable facts captured at the paper admission decision."""

    capture_id: str
    decision_facts: Mapping[str, Any]
    evidence_facts: Mapping[str, Any]
    lifecycle_events: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class SideCalibrationCaptureAttempt:
    """One immutable capture attempt, whether or not it is scorable."""

    attempt_id: int
    capture_id: str
    payload_sha256: str
    payload_json: str
    unscorable_reason: str | None


@dataclass(frozen=True)
class SideCalibrationCandidate:
    """A frozen, complete candidate retained for future calibration."""

    candidate_id: int
    attempt_id: int
    capture_id: str
    payload_sha256: str
    payload_json: str


@dataclass(frozen=True)
class SideCalibrationCaptureResult:
    """Outcome of recording a capture."""

    status: str
    payload_sha256: str
    attempt: SideCalibrationCaptureAttempt | None
    candidate: SideCalibrationCandidate | None


@dataclass(frozen=True)
class SideCalibrationQuarantineSnapshot:
    """Small read-only summary for operational assertions."""

    attempt_count: int
    candidate_count: int
    lifecycle_event_count: int


class SideCalibrationQuarantineStore:
    """SQLite-backed append-only store isolated from runtime and order state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            self._initialize(connection)

    def append_capture(
        self, capture: SideCalibrationCapture
    ) -> SideCalibrationCaptureResult:
        """Append a canonical attempt and, if complete, a frozen candidate."""
        payload_json = _canonical_capture_json(capture)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        unscorable_reason = _unscorable_reason(capture)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = self._candidate_for_capture_id(connection, capture.capture_id)
            if candidate is not None:
                if candidate.payload_sha256 != payload_sha256:
                    raise SideCalibrationQuarantineError(
                        "conflicting capture for immutable capture_id "
                        f"{capture.capture_id!r}"
                    )
                attempt = self._attempt_for_id(connection, candidate.attempt_id)
                return SideCalibrationCaptureResult(
                    status="duplicate",
                    payload_sha256=payload_sha256,
                    attempt=attempt,
                    candidate=candidate,
                )

            existing_attempt = self._attempt_for_identity(
                connection, capture.capture_id, payload_sha256
            )
            if existing_attempt is not None:
                return SideCalibrationCaptureResult(
                    status="duplicate",
                    payload_sha256=payload_sha256,
                    attempt=existing_attempt,
                    candidate=None,
                )

            attempt_id = self._insert_attempt(
                connection,
                capture_id=capture.capture_id,
                payload_sha256=payload_sha256,
                payload_json=payload_json,
                unscorable_reason=unscorable_reason,
            )
            self._insert_lifecycle_events(connection, attempt_id, capture.lifecycle_events)
            attempt = SideCalibrationCaptureAttempt(
                attempt_id=attempt_id,
                capture_id=capture.capture_id,
                payload_sha256=payload_sha256,
                payload_json=payload_json,
                unscorable_reason=unscorable_reason,
            )
            if unscorable_reason is not None:
                return SideCalibrationCaptureResult(
                    status="unscorable",
                    payload_sha256=payload_sha256,
                    attempt=attempt,
                    candidate=None,
                )

            candidate_id = self._insert_candidate(
                connection,
                attempt_id=attempt_id,
                capture_id=capture.capture_id,
                payload_sha256=payload_sha256,
                payload_json=payload_json,
            )
            candidate = SideCalibrationCandidate(
                candidate_id=candidate_id,
                attempt_id=attempt_id,
                capture_id=capture.capture_id,
                payload_sha256=payload_sha256,
                payload_json=payload_json,
            )
            return SideCalibrationCaptureResult(
                status="inserted",
                payload_sha256=payload_sha256,
                attempt=attempt,
                candidate=candidate,
            )

    def snapshot(self) -> SideCalibrationQuarantineSnapshot:
        """Return record counts without exposing mutable SQLite connections."""
        with self._connection() as connection:
            attempt_count = _row_count(connection, "capture_attempts")
            candidate_count = _row_count(connection, "candidates")
            lifecycle_event_count = _row_count(connection, "lifecycle_events")
        return SideCalibrationQuarantineSnapshot(
            attempt_count=attempt_count,
            candidate_count=candidate_count,
            lifecycle_event_count=lifecycle_event_count,
        )

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_attempts (
                attempt_id INTEGER PRIMARY KEY,
                capture_id TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                unscorable_reason TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(capture_id, payload_sha256)
            );
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id INTEGER PRIMARY KEY,
                attempt_id INTEGER NOT NULL UNIQUE,
                capture_id TEXT NOT NULL UNIQUE,
                payload_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(attempt_id) REFERENCES capture_attempts(attempt_id)
            );
            CREATE TABLE IF NOT EXISTS lifecycle_events (
                lifecycle_event_id INTEGER PRIMARY KEY,
                attempt_id INTEGER NOT NULL,
                event_index INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(attempt_id, event_index),
                FOREIGN KEY(attempt_id) REFERENCES capture_attempts(attempt_id)
            );
            """
        )
        metadata = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
        if metadata is None:
            connection.execute(
                "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
        elif metadata[0] != str(_SCHEMA_VERSION):
            raise SideCalibrationQuarantineError("unsupported quarantine schema version")

        for table in (
            "schema_metadata",
            "capture_attempts",
            "candidates",
            "lifecycle_events",
        ):
            connection.executescript(
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_reject_update
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'append-only evidence');
                END;
                CREATE TRIGGER IF NOT EXISTS {table}_reject_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'append-only evidence');
                END;
                """
            )

    @staticmethod
    def _attempt_for_id(
        connection: sqlite3.Connection, attempt_id: int
    ) -> SideCalibrationCaptureAttempt:
        row = connection.execute(
            """
            SELECT attempt_id, capture_id, payload_sha256, payload_json, unscorable_reason
            FROM capture_attempts WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise SideCalibrationQuarantineError("candidate references missing capture attempt")
        return SideCalibrationCaptureAttempt(*row)

    @staticmethod
    def _attempt_for_identity(
        connection: sqlite3.Connection, capture_id: str, payload_sha256: str
    ) -> SideCalibrationCaptureAttempt | None:
        row = connection.execute(
            """
            SELECT attempt_id, capture_id, payload_sha256, payload_json, unscorable_reason
            FROM capture_attempts
            WHERE capture_id = ? AND payload_sha256 = ?
            """,
            (capture_id, payload_sha256),
        ).fetchone()
        return None if row is None else SideCalibrationCaptureAttempt(*row)

    @staticmethod
    def _candidate_for_capture_id(
        connection: sqlite3.Connection, capture_id: str
    ) -> SideCalibrationCandidate | None:
        row = connection.execute(
            """
            SELECT candidate_id, attempt_id, capture_id, payload_sha256, payload_json
            FROM candidates WHERE capture_id = ?
            """,
            (capture_id,),
        ).fetchone()
        return None if row is None else SideCalibrationCandidate(*row)

    @staticmethod
    def _insert_attempt(
        connection: sqlite3.Connection,
        *,
        capture_id: str,
        payload_sha256: str,
        payload_json: str,
        unscorable_reason: str | None,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO capture_attempts(
                capture_id, payload_sha256, payload_json, unscorable_reason
            ) VALUES (?, ?, ?, ?)
            """,
            (capture_id, payload_sha256, payload_json, unscorable_reason),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_lifecycle_events(
        connection: sqlite3.Connection,
        attempt_id: int,
        lifecycle_events: Sequence[Mapping[str, Any]],
    ) -> None:
        for event_index, event in enumerate(lifecycle_events):
            event_json = _canonical_json(event)
            connection.execute(
                """
                INSERT INTO lifecycle_events(
                    attempt_id, event_index, payload_json, payload_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    event_index,
                    event_json,
                    hashlib.sha256(event_json.encode("utf-8")).hexdigest(),
                ),
            )

    @staticmethod
    def _insert_candidate(
        connection: sqlite3.Connection,
        *,
        attempt_id: int,
        capture_id: str,
        payload_sha256: str,
        payload_json: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO candidates(
                attempt_id, capture_id, payload_sha256, payload_json
            ) VALUES (?, ?, ?, ?)
            """,
            (attempt_id, capture_id, payload_sha256, payload_json),
        )
        return int(cursor.lastrowid)


def _canonical_capture_json(capture: SideCalibrationCapture) -> str:
    if not isinstance(capture.capture_id, str) or not capture.capture_id.strip():
        raise SideCalibrationQuarantineError("capture_id must be a non-empty string")
    return _canonical_json(
        {
            "capture_id": capture.capture_id,
            "decision_facts": capture.decision_facts,
            "evidence_facts": capture.evidence_facts,
            "lifecycle_events": list(capture.lifecycle_events),
        }
    )


def _canonical_json(value: Any) -> str:
    try:
        normalized = _normalize_json(value)
    except (TypeError, ValueError) as error:
        raise SideCalibrationQuarantineError(
            "quarantine facts must be plain JSON-serializable values"
        ) from error
    return json.dumps(normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _unscorable_reason(capture: SideCalibrationCapture) -> str | None:
    for field in _REQUIRED_DECISION_FACTS:
        if _missing(capture.decision_facts.get(field)):
            return f"missing_{field}"
    if _missing(capture.evidence_facts.get("book_hash")):
        return "missing_book_hash"
    if str(capture.decision_facts["side"]).lower() not in {"yes", "no"}:
        return "invalid_side"
    if not _finite_number(capture.decision_facts["market_price"]):
        return "invalid_market_price"
    if not 0.0 <= float(capture.decision_facts["market_price"]) <= 1.0:
        return "invalid_market_price"
    if not _finite_number(capture.decision_facts["gross_edge"]):
        return "invalid_gross_edge"
    return None


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
