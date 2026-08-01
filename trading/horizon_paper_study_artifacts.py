"""Canonical artifact store for the isolated horizon paper study."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol, TypeVar

from trading.horizon_paper_study_manifest import (
    HORIZON_PAPER_STUDY_MANIFEST_FILENAME,
    HorizonPaperStudyManifest,
    HorizonPaperStudyManifestError,
    validate_horizon_paper_study_manifest,
)


_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_RE = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
_UTC_SUFFIX = "+00:00"
_INPUT_RECORD_TYPE = "POLYMARKET_HORIZON_STUDY_INPUT"
_ADMISSION_RECORD_TYPE = "POLYMARKET_HORIZON_STUDY_SHADOW_ADMISSION"
_DECISION_RECORD_TYPE = "POLYMARKET_HORIZON_STUDY_DECISION"
_EXECUTION_RECORD_TYPE = "POLYMARKET_HORIZON_STUDY_EXECUTION"
_SETTLEMENT_RECORD_TYPE = "POLYMARKET_HORIZON_STUDY_SETTLEMENT"
_ABORT_RECORD_TYPE = "POLYMARKET_HORIZON_STUDY_ABORT"
_ADMISSION_STATUSES = {
    "qualified",
    "no_token_overlap",
    "below_min_post_weight_score",
    "weight_demoted_below_min_score",
    "invalid_market",
}
_DECISION_STATUSES = {"execute", "reject"}
_BOOTSTRAP_TABLE = "horizon_study_bootstrap"
_STATE_APPLICATION_ID = 0x48505354
_BOOTSTRAP_COLUMNS = (
    "singleton",
    "bootstrap_schema_version",
    "database_role",
    "study_id",
    "study_kind",
    "database_identity",
    "manifest_preimage_sha256",
)
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")
_MIRROR_SPECS = {
    _INPUT_RECORD_TYPE: ("input_id", "artifacts/inputs.jsonl", "input"),
    _ADMISSION_RECORD_TYPE: (
        "admission_id",
        "artifacts/shadow_admissions.jsonl",
        "admission",
    ),
    _DECISION_RECORD_TYPE: ("decision_id", "artifacts/decisions.jsonl", None),
    _EXECUTION_RECORD_TYPE: (
        "execution_id",
        "artifacts/executions.jsonl",
        "execution",
    ),
    _SETTLEMENT_RECORD_TYPE: (
        "settlement_id",
        "artifacts/settlements.jsonl",
        "settlement",
    ),
    _ABORT_RECORD_TYPE: ("abort_id", "artifacts/aborts.jsonl", None),
}
_ARTIFACT_TABLE_COLUMNS = {
    "artifact_payload_journal": (
        "journal_sequence",
        "record_type",
        "record_id",
        "manifest_sha256",
        "record_sha256",
        "mirror_relative_path",
        "canonical_payload_json",
        "committed_at_utc",
    ),
    "input_receipts": (
        "input_id",
        "journal_sequence",
        "record_sha256",
        "observed_at_utc",
    ),
    "shadow_admissions": (
        "admission_id",
        "input_id",
        "market_id",
        "policy_snapshot_sha256",
        "journal_sequence",
        "record_sha256",
    ),
    "execution_claims": (
        "admission_id",
        "state",
        "study_trade_id",
        "execution_journal_sequence",
        "claimed_at_utc",
        "updated_at_utc",
    ),
    "settlement_receipts": (
        "study_trade_id",
        "settlement_observation_sha256",
        "journal_sequence",
        "record_sha256",
    ),
}


@dataclass(frozen=True)
class StudyLedgerExecutionLink:
    """One read-only study-ledger linkage returned during recovery."""

    study_trade_id: str
    study_id: str
    admission_id: str


class StudyLedgerExecutionLookup(Protocol):
    """Read-only boundary owned by the study-ledger module."""

    def lookup_study_trade_links(
        self,
        *,
        study_id: str,
        admission_id: str,
    ) -> tuple[StudyLedgerExecutionLink, ...]: ...


_TransactionResult = TypeVar("_TransactionResult")


class HorizonStudyArtifactStoreError(ValueError):
    """Artifact store or recovery state is invalid."""


class HorizonStudyArtifactStore(AbstractContextManager["HorizonStudyArtifactStore"]):
    """Write study-local artifacts with state-first persistence and recovery."""

    def __init__(
        self,
        manifest_path: Path | str,
        *,
        study_ledger_execution_lookup: StudyLedgerExecutionLookup | None = None,
        execution_lookup: StudyLedgerExecutionLookup | None = None,
    ) -> None:
        if (
            study_ledger_execution_lookup is not None
            and execution_lookup is not None
        ):
            raise HorizonStudyArtifactStoreError(
                "study ledger execution lookup is ambiguous"
            )
        self.manifest_path = _absolute_path(manifest_path)
        if self.manifest_path.name != HORIZON_PAPER_STUDY_MANIFEST_FILENAME:
            raise HorizonStudyArtifactStoreError("study manifest path is invalid")
        self.study_root = self.manifest_path.parent
        self.artifacts_root = self.study_root / "artifacts"
        self.locks_root = self.study_root / "locks"
        self.inputs_path = self.artifacts_root / "inputs.jsonl"
        self.shadow_admissions_path = self.artifacts_root / "shadow_admissions.jsonl"
        self.decisions_path = self.artifacts_root / "decisions.jsonl"
        self.executions_path = self.artifacts_root / "executions.jsonl"
        self.settlements_path = self.artifacts_root / "settlements.jsonl"
        self.aborts_path = self.artifacts_root / "aborts.jsonl"
        self.lock_path = self.locks_root / "runtime.lock"
        self._study_ledger_execution_lookup = (
            study_ledger_execution_lookup
            if study_ledger_execution_lookup is not None
            else execution_lookup
        )
        self._lock_fd: int | None = None
        self._lock_identity: tuple[int, int] | None = None
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        self._write_blocked = False
        self._state_file_identity: tuple[int, int, int] | None = None
        self._bootstrap_snapshot: tuple[object, ...] | None = None
        try:
            self.manifest = self._load_manifest()
            self.state_db_path = _absolute_path(self.study_root / self.manifest.state_db_path)
            _assert_below(self.state_db_path, self.study_root, "study state path")
            self._assert_state_database_file()
            self._reject_state_sidecars()
            self._ensure_directory(self.artifacts_root)
            self._ensure_directory(self.locks_root)
            self._acquire_runtime_lock()
            self._connection = sqlite3.connect(self.state_db_path, timeout=30.0)
            self._connection.row_factory = sqlite3.Row
            self._bootstrap_snapshot = self._capture_bootstrap_snapshot()
            journal_mode = self._connection.execute("PRAGMA journal_mode=DELETE").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "delete":
                raise HorizonStudyArtifactStoreError("SQLite DELETE journaling is required")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._assert_bootstrap_unchanged()
            self._initialize_schema()
            self._recover_and_validate()
        except Exception:
            self._shutdown(revalidate=False)
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def close(self) -> None:
        self._shutdown(revalidate=True)

    def record_input(self, record: Mapping[str, object]) -> dict[str, object]:
        self._require_open()
        normalized = self._normalize_input_record(record)
        self._validate_input_source_identity(normalized)
        return self._persist_payload(normalized)

    def record_shadow_admission(self, record: Mapping[str, object]) -> dict[str, object]:
        self._require_open()
        normalized = self._normalize_shadow_admission_record(record)
        return self._persist_payload(normalized)

    def record_decision(self, record: Mapping[str, object]) -> dict[str, object]:
        self._require_open()
        normalized = self._normalize_decision_record(record)
        if self._fetch_admission(str(normalized["admission_id"])) is None:
            raise HorizonStudyArtifactStoreError("decision admission is missing")
        return self._persist_payload(normalized)

    def claim_execution(
        self,
        *,
        manifest_sha256: str,
        admission_id: str,
        claimed_at_utc: str,
    ) -> dict[str, object]:
        self._require_open()
        self._require_manifest_hash(manifest_sha256)
        _require_sha256(admission_id, "admission_id")
        _require_utc_datetime(claimed_at_utc, "claimed_at_utc")
        if self._fetch_admission(admission_id) is None:
            raise HorizonStudyArtifactStoreError("execution admission is missing")

        def claim(cursor: sqlite3.Cursor) -> dict[str, object]:
            existing = cursor.execute(
                """
                SELECT admission_id, state, study_trade_id, claimed_at_utc, updated_at_utc
                FROM execution_claims WHERE admission_id = ?
                """,
                (admission_id,),
            ).fetchone()
            if existing is not None:
                return _claim_row_dict(existing)
            cursor.execute(
                """
                INSERT INTO execution_claims (
                    admission_id, state, study_trade_id, execution_journal_sequence,
                    claimed_at_utc, updated_at_utc
                ) VALUES (?, 'claimed', NULL, NULL, ?, ?)
                """,
                (admission_id, claimed_at_utc, claimed_at_utc),
            )
            return {
                "admission_id": admission_id,
                "state": "claimed",
                "study_trade_id": None,
                "claimed_at_utc": claimed_at_utc,
                "updated_at_utc": claimed_at_utc,
            }

        return self._run_state_transaction(claim)

    def record_execution(self, record: Mapping[str, object]) -> dict[str, object]:
        self._require_open()
        normalized = self._normalize_execution_record(record)
        decision = self._fetch_generic_record(
            _DECISION_RECORD_TYPE,
            str(normalized["decision_id"]),
        )
        if decision is None or decision.get("admission_id") != normalized["admission_id"]:
            raise HorizonStudyArtifactStoreError("execution decision is invalid")
        return self._persist_payload(normalized)

    def record_settlement(self, record: Mapping[str, object]) -> dict[str, object]:
        self._require_open()
        normalized = self._normalize_settlement_record(record)
        return self._persist_payload(normalized)

    def abort(self, record: Mapping[str, object]) -> dict[str, object]:
        self._require_open()
        normalized = self._normalize_abort_record(record)
        return self._persist_payload(normalized)

    def _load_manifest(self) -> HorizonPaperStudyManifest:
        try:
            data_root = self.manifest_path.parents[2]
        except IndexError as exc:
            raise HorizonStudyArtifactStoreError("study manifest path is invalid") from exc
        try:
            return validate_horizon_paper_study_manifest(
                self.manifest_path,
                data_root=data_root,
                # Task 3 validates only its own study_state.db. The study ledger
                # remains behind the injected read-only recovery protocol.
                verify_runtime_targets=False,
            )
        except HorizonPaperStudyManifestError as exc:
            raise HorizonStudyArtifactStoreError(str(exc)) from exc

    def _initialize_schema(self) -> None:
        connection = self._require_connection()
        expected_tables = set(_ARTIFACT_TABLE_COLUMNS)
        existing_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if str(row[0]) in expected_tables
        }
        if existing_tables:
            if existing_tables != expected_tables:
                raise HorizonStudyArtifactStoreError("artifact state schema is invalid")
            self._validate_artifact_schema()
            return
        statements = (
            """
            CREATE TABLE IF NOT EXISTS artifact_payload_journal (
                journal_sequence INTEGER PRIMARY KEY,
                record_type TEXT NOT NULL CHECK (record_type IN (
                    'POLYMARKET_HORIZON_STUDY_INPUT',
                    'POLYMARKET_HORIZON_STUDY_SHADOW_ADMISSION',
                    'POLYMARKET_HORIZON_STUDY_DECISION',
                    'POLYMARKET_HORIZON_STUDY_EXECUTION',
                    'POLYMARKET_HORIZON_STUDY_SETTLEMENT',
                    'POLYMARKET_HORIZON_STUDY_ABORT'
                )),
                record_id TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                record_sha256 TEXT NOT NULL,
                mirror_relative_path TEXT NOT NULL CHECK (mirror_relative_path IN (
                    'artifacts/inputs.jsonl',
                    'artifacts/shadow_admissions.jsonl',
                    'artifacts/decisions.jsonl',
                    'artifacts/executions.jsonl',
                    'artifacts/settlements.jsonl',
                    'artifacts/aborts.jsonl'
                )),
                canonical_payload_json TEXT NOT NULL,
                committed_at_utc TEXT NOT NULL,
                UNIQUE(record_type, record_id),
                UNIQUE(record_sha256)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS input_receipts (
                input_id TEXT PRIMARY KEY,
                journal_sequence INTEGER NOT NULL UNIQUE,
                record_sha256 TEXT NOT NULL,
                observed_at_utc TEXT NOT NULL,
                FOREIGN KEY (journal_sequence)
                    REFERENCES artifact_payload_journal(journal_sequence)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS shadow_admissions (
                admission_id TEXT PRIMARY KEY,
                input_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                policy_snapshot_sha256 TEXT NOT NULL,
                journal_sequence INTEGER NOT NULL UNIQUE,
                record_sha256 TEXT NOT NULL,
                UNIQUE(input_id, market_id, policy_snapshot_sha256),
                FOREIGN KEY (journal_sequence)
                    REFERENCES artifact_payload_journal(journal_sequence)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS execution_claims (
                admission_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('claimed', 'executed')),
                study_trade_id TEXT UNIQUE,
                execution_journal_sequence INTEGER UNIQUE,
                claimed_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                FOREIGN KEY (execution_journal_sequence)
                    REFERENCES artifact_payload_journal(journal_sequence)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settlement_receipts (
                study_trade_id TEXT PRIMARY KEY,
                settlement_observation_sha256 TEXT NOT NULL,
                journal_sequence INTEGER NOT NULL UNIQUE,
                record_sha256 TEXT NOT NULL,
                FOREIGN KEY (journal_sequence)
                    REFERENCES artifact_payload_journal(journal_sequence)
            )
            """,
        )

        def initialize(cursor: sqlite3.Cursor) -> None:
            for statement in statements:
                cursor.execute(statement)

        self._run_state_transaction(initialize, require_schema=False)
        self._validate_artifact_schema()

    def _recover_and_validate(self) -> None:
        self._validate_artifact_schema()
        self._reconcile_generic_and_typed()
        self._guard_database_boundary(require_schema=True)
        self._recover_mirrors_from_journal()
        self._guard_database_boundary(require_schema=True)
        self._validate_execution_claim_recovery()

    def _validate_execution_claim_recovery(self) -> None:
        connection = self._require_connection()
        rows = connection.execute(
            """
            SELECT admission_id, state, study_trade_id, execution_journal_sequence
            FROM execution_claims
            ORDER BY admission_id
            """
        ).fetchall()
        for row in rows:
            state = str(row["state"])
            if state == "executed":
                continue
            if state != "claimed" or row["study_trade_id"] is not None:
                raise HorizonStudyArtifactStoreError("orphaned execution claim is invalid")
            admission_id = str(row["admission_id"])
            if self._study_ledger_execution_lookup is None:
                raise HorizonStudyArtifactStoreError(
                    "study ledger execution lookup is unavailable"
                )
            try:
                links = self._study_ledger_execution_lookup.lookup_study_trade_links(
                    study_id=self.manifest.study_id,
                    admission_id=admission_id,
                )
            except Exception as exc:
                raise HorizonStudyArtifactStoreError(
                    "study ledger execution lookup failed"
                ) from exc
            if not isinstance(links, tuple):
                raise HorizonStudyArtifactStoreError(
                    "study ledger execution lookup is unavailable"
                )
            if len(links) != 1:
                raise HorizonStudyArtifactStoreError(
                    "ambiguous execution recovery is invalid"
                )
            link = links[0]
            try:
                study_trade_id = _require_exact_string(
                    getattr(link, "study_trade_id"),
                    "study_trade_id",
                )
                link_study_id = _require_exact_string(
                    getattr(link, "study_id"),
                    "study_id",
                )
                link_admission_id = _require_sha256(
                    getattr(link, "admission_id"),
                    "admission_id",
                )
            except (AttributeError, HorizonStudyArtifactStoreError) as exc:
                raise HorizonStudyArtifactStoreError(
                    "study ledger execution lookup is invalid"
                ) from exc
            if (
                link_study_id != self.manifest.study_id
                or link_admission_id != admission_id
                or not study_trade_id
            ):
                raise HorizonStudyArtifactStoreError(
                    "study ledger execution lookup is mismatched"
                )

    def _normalize_record_for_type(
        self,
        record: Mapping[str, object],
        *,
        expected_type: str,
    ) -> dict[str, object]:
        if expected_type == _INPUT_RECORD_TYPE:
            return self._normalize_input_record(record)
        if expected_type == _ADMISSION_RECORD_TYPE:
            return self._normalize_shadow_admission_record(record)
        if expected_type == _DECISION_RECORD_TYPE:
            return self._normalize_decision_record(record)
        if expected_type == _EXECUTION_RECORD_TYPE:
            return self._normalize_execution_record(record)
        if expected_type == _SETTLEMENT_RECORD_TYPE:
            return self._normalize_settlement_record(record)
        if expected_type == _ABORT_RECORD_TYPE:
            return self._normalize_abort_record(record)
        raise HorizonStudyArtifactStoreError("record type is invalid")

    def _fetch_input(self, input_id: str) -> dict[str, object] | None:
        return self._fetch_generic_record(_INPUT_RECORD_TYPE, input_id)

    def _fetch_admission(self, admission_id: str) -> dict[str, object] | None:
        return self._fetch_generic_record(_ADMISSION_RECORD_TYPE, admission_id)

    def _acquire_runtime_lock(self) -> None:
        _assert_below(self.lock_path, self.study_root, "runtime lock path")
        if _path_exists_or_symlink(self.lock_path):
            raise HorizonStudyArtifactStoreError(
                f"runtime lock is held at {self.lock_path}"
            )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            self._lock_fd = os.open(self.lock_path, flags, 0o600)
        except FileExistsError as exc:
            raise HorizonStudyArtifactStoreError(
                f"runtime lock is held at {self.lock_path}"
            ) from exc
        except OSError as exc:
            raise HorizonStudyArtifactStoreError("runtime lock is invalid") from exc
        lock_stat = os.fstat(self._lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise HorizonStudyArtifactStoreError("runtime lock is invalid")
        self._lock_identity = (lock_stat.st_dev, lock_stat.st_ino)
        os.write(
            self._lock_fd,
            _canonical_json(
                {
                    "pid": os.getpid(),
                    "operation": "horizon_paper_study_artifacts",
                    "study_id": self.manifest.study_id,
                }
            ).encode("utf-8"),
        )
        os.fsync(self._lock_fd)

    def _ensure_directory(self, path: Path) -> None:
        _assert_below(path, self.study_root, "study path")
        if path.exists():
            file_stat = path.lstat()
            if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISDIR(file_stat.st_mode):
                raise HorizonStudyArtifactStoreError("study path is invalid")
            return
        path.mkdir(parents=True, exist_ok=True)

    def _require_open(self) -> None:
        if self._closed:
            raise HorizonStudyArtifactStoreError("artifact store is closed")
        if self._write_blocked:
            raise HorizonStudyArtifactStoreError("artifact store is fail-closed")

    def _require_manifest_hash(self, value: object) -> str:
        manifest_sha256 = _require_sha256(value, "manifest_sha256")
        if manifest_sha256 != self.manifest.manifest_sha256:
            raise HorizonStudyArtifactStoreError("manifest hash is invalid")
        return manifest_sha256

    def _shutdown(self, *, revalidate: bool) -> None:
        if self._closed:
            return
        self._closed = True
        error: Exception | None = None
        connection = self._connection
        if connection is not None:
            try:
                if revalidate:
                    self._guard_database_boundary(require_schema=True)
            except Exception as exc:  # The lock still must be released below.
                error = exc
            try:
                connection.close()
            except sqlite3.Error as exc:
                if error is None:
                    error = HorizonStudyArtifactStoreError("study state close failed")
                    error.__cause__ = exc
            finally:
                self._connection = None
        if revalidate and error is None and hasattr(self, "manifest"):
            try:
                self._validate_manifest_boundary()
            except Exception as exc:
                error = exc
        try:
            self._release_runtime_lock()
        except Exception as exc:
            if error is None:
                error = exc
        if error is not None:
            if isinstance(error, HorizonStudyArtifactStoreError):
                raise error
            raise HorizonStudyArtifactStoreError("artifact store close validation failed") from error

    def _release_runtime_lock(self) -> None:
        lock_fd = self._lock_fd
        lock_identity = self._lock_identity
        self._lock_fd = None
        self._lock_identity = None
        if lock_fd is not None:
            os.close(lock_fd)
        if lock_identity is None or not _path_exists_or_symlink(self.lock_path):
            return
        try:
            current = self.lock_path.lstat()
        except OSError as exc:
            raise HorizonStudyArtifactStoreError("runtime lock is invalid") from exc
        if (current.st_dev, current.st_ino) != lock_identity:
            raise HorizonStudyArtifactStoreError("runtime lock identity changed")
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise HorizonStudyArtifactStoreError("runtime lock is invalid")
        try:
            self.lock_path.unlink()
        except OSError as exc:
            raise HorizonStudyArtifactStoreError("runtime lock release failed") from exc

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise HorizonStudyArtifactStoreError("study state connection is closed")
        return self._connection

    def _assert_state_database_file(self) -> None:
        try:
            state = self.state_db_path.lstat()
        except OSError as exc:
            raise HorizonStudyArtifactStoreError("study state database is invalid") from exc
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISREG(state.st_mode)
            or state.st_nlink != 1
        ):
            raise HorizonStudyArtifactStoreError("study state database is invalid")
        identity = (state.st_dev, state.st_ino, state.st_nlink)
        if self._state_file_identity is None:
            self._state_file_identity = identity
        elif identity != self._state_file_identity:
            raise HorizonStudyArtifactStoreError("study state database identity changed")

    def _reject_state_sidecars(self) -> None:
        for suffix in _SQLITE_SIDECAR_SUFFIXES:
            if _path_exists_or_symlink(Path(f"{self.state_db_path}{suffix}")):
                raise HorizonStudyArtifactStoreError("study state SQLite sidecar is invalid")

    def _capture_bootstrap_snapshot(self) -> tuple[object, ...]:
        self._assert_state_database_file()
        self._reject_state_sidecars()
        connection = self._require_connection()
        application_id = connection.execute("PRAGMA application_id").fetchone()
        if application_id is None or int(application_id[0]) != _STATE_APPLICATION_ID:
            raise HorizonStudyArtifactStoreError("study state application ID is invalid")
        columns = tuple(
            row[1]
            for row in connection.execute(f"PRAGMA table_info({_BOOTSTRAP_TABLE})")
        )
        if columns != _BOOTSTRAP_COLUMNS:
            raise HorizonStudyArtifactStoreError("study state bootstrap metadata is invalid")
        rows = tuple(
            tuple(row)
            for row in connection.execute(
                f"SELECT {', '.join(_BOOTSTRAP_COLUMNS)} "
                f"FROM {_BOOTSTRAP_TABLE} ORDER BY singleton"
            )
        )
        if len(rows) != 1:
            raise HorizonStudyArtifactStoreError("study state bootstrap metadata is invalid")
        expected_row = (
            1,
            1,
            "state",
            self.manifest.study_id,
            self.manifest.study_kind,
            self.manifest.database_identity,
            self.manifest.manifest_sha256,
        )
        if rows != (expected_row,):
            raise HorizonStudyArtifactStoreError("study state bootstrap metadata is invalid")
        return (int(application_id[0]), columns, rows, self._state_file_identity)

    def _assert_bootstrap_unchanged(self) -> None:
        snapshot = self._capture_bootstrap_snapshot()
        if self._bootstrap_snapshot is None:
            self._bootstrap_snapshot = snapshot
        elif snapshot != self._bootstrap_snapshot:
            raise HorizonStudyArtifactStoreError("study state bootstrap binding changed")

    def _validate_manifest_boundary(self) -> None:
        self._reject_state_sidecars()
        self._assert_state_database_file()
        manifest = self._load_manifest()
        if manifest.manifest_sha256 != self.manifest.manifest_sha256:
            raise HorizonStudyArtifactStoreError("study manifest changed")

    def _guard_database_boundary(self, *, require_schema: bool) -> None:
        self._validate_manifest_boundary()
        self._assert_bootstrap_unchanged()
        journal_mode = self._require_connection().execute("PRAGMA journal_mode").fetchone()
        if journal_mode is None or str(journal_mode[0]).lower() != "delete":
            raise HorizonStudyArtifactStoreError("SQLite DELETE journaling is required")
        if require_schema:
            self._validate_artifact_schema()
            self._reconcile_generic_and_typed()

    def _run_state_transaction(
        self,
        action: Callable[[sqlite3.Cursor], _TransactionResult],
        *,
        require_schema: bool = True,
    ) -> _TransactionResult:
        self._guard_database_boundary(require_schema=require_schema)
        connection = self._require_connection()
        cursor = connection.cursor()
        committed = False
        try:
            cursor.execute("BEGIN IMMEDIATE")
            result = action(cursor)
            connection.commit()
            committed = True
        except HorizonStudyArtifactStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise HorizonStudyArtifactStoreError("artifact state transaction is invalid") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
        if not committed:
            raise HorizonStudyArtifactStoreError("artifact state transaction did not commit")
        self._guard_database_boundary(require_schema=True)
        return result

    def _validate_artifact_schema(self) -> None:
        connection = self._require_connection()
        for table, expected_columns in _ARTIFACT_TABLE_COLUMNS.items():
            columns = tuple(
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if columns != expected_columns:
                raise HorizonStudyArtifactStoreError("artifact state schema is invalid")

    def _persist_payload(self, record: Mapping[str, object]) -> dict[str, object]:
        normalized = dict(record)
        record_type = _require_exact_string(normalized.get("record_type"), "record_type")
        try:
            id_field, mirror_relative_path, typed_kind = _MIRROR_SPECS[record_type]
        except KeyError as exc:
            raise HorizonStudyArtifactStoreError("record type is invalid") from exc
        record_id = _require_exact_string(normalized.get(id_field), id_field)
        canonical_payload_json = _canonical_json(normalized)
        record_sha256 = _require_sha256(normalized.get("record_sha256"), "record_sha256")

        def persist(cursor: sqlite3.Cursor) -> bool:
            existing = cursor.execute(
                """
                SELECT journal_sequence, record_type, record_id, manifest_sha256,
                       record_sha256, mirror_relative_path, canonical_payload_json,
                       committed_at_utc
                FROM artifact_payload_journal
                WHERE record_type = ? AND record_id = ?
                """,
                (record_type, record_id),
            ).fetchone()
            if existing is not None:
                entry = self._validated_journal_row(existing)
                if (
                    entry["record_sha256"] != record_sha256
                    or entry["canonical_payload_json"] != canonical_payload_json
                    or entry["record"] != normalized
                ):
                    raise HorizonStudyArtifactStoreError("artifact duplicate hash is invalid")
                self._assert_typed_index_for_entry(cursor, entry)
                return False
            duplicate_hash = cursor.execute(
                "SELECT record_type, record_id FROM artifact_payload_journal "
                "WHERE record_sha256 = ?",
                (record_sha256,),
            ).fetchone()
            if duplicate_hash is not None:
                raise HorizonStudyArtifactStoreError("artifact hash is already bound to another ID")
            cursor.execute(
                """
                INSERT INTO artifact_payload_journal (
                    record_type, record_id, manifest_sha256, record_sha256,
                    mirror_relative_path, canonical_payload_json, committed_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_type,
                    record_id,
                    self.manifest.manifest_sha256,
                    record_sha256,
                    mirror_relative_path,
                    canonical_payload_json,
                    _utc_now(),
                ),
            )
            journal_sequence = int(cursor.lastrowid)
            entry = {
                "journal_sequence": journal_sequence,
                "record_type": record_type,
                "record_id": record_id,
                "record_sha256": record_sha256,
                "canonical_payload_json": canonical_payload_json,
                "record": normalized,
                "typed_kind": typed_kind,
            }
            self._insert_typed_index(cursor, entry)
            return True

        try:
            created = self._run_state_transaction(persist)
        except HorizonStudyArtifactStoreError:
            self._write_blocked = True
            raise
        if created:
            path = self._mirror_path(mirror_relative_path)
            try:
                self._append_audit_payload(path, canonical_payload_json)
            except OSError as exc:
                self._write_blocked = True
                raise HorizonStudyArtifactStoreError("artifact mirror write failed") from exc
            try:
                self._guard_database_boundary(require_schema=True)
            except HorizonStudyArtifactStoreError:
                self._write_blocked = True
                raise
        return normalized

    def _insert_typed_index(
        self,
        cursor: sqlite3.Cursor,
        entry: Mapping[str, object],
    ) -> None:
        record = entry["record"]
        if not isinstance(record, Mapping):
            raise HorizonStudyArtifactStoreError("artifact payload is invalid")
        kind = entry["typed_kind"]
        journal_sequence = _require_int(entry["journal_sequence"], "journal_sequence")
        if kind == "input":
            cursor.execute(
                """
                INSERT INTO input_receipts (
                    input_id, journal_sequence, record_sha256, observed_at_utc
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    record["input_id"],
                    journal_sequence,
                    record["record_sha256"],
                    record["observed_at_utc"],
                ),
            )
            return
        if kind == "admission":
            cursor.execute(
                """
                INSERT INTO shadow_admissions (
                    admission_id, input_id, market_id, policy_snapshot_sha256,
                    journal_sequence, record_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record["admission_id"],
                    record["input_id"],
                    record["market_id"],
                    record["policy_snapshot_sha256"],
                    journal_sequence,
                    record["record_sha256"],
                ),
            )
            return
        if kind == "execution":
            claim = cursor.execute(
                """
                SELECT state, study_trade_id, execution_journal_sequence
                FROM execution_claims WHERE admission_id = ?
                """,
                (record["admission_id"],),
            ).fetchone()
            if claim is None or claim["state"] != "claimed":
                raise HorizonStudyArtifactStoreError("execution claim is missing")
            cursor.execute(
                """
                UPDATE execution_claims
                SET state = 'executed', study_trade_id = ?,
                    execution_journal_sequence = ?, updated_at_utc = ?
                WHERE admission_id = ?
                """,
                (
                    record["study_trade_id"],
                    journal_sequence,
                    record["executed_at_utc"],
                    record["admission_id"],
                ),
            )
            return
        if kind == "settlement":
            claim = cursor.execute(
                """
                SELECT state, execution_journal_sequence FROM execution_claims
                WHERE study_trade_id = ?
                """,
                (record["study_trade_id"],),
            ).fetchone()
            if (
                claim is None
                or claim["state"] != "executed"
                or claim["execution_journal_sequence"] is None
            ):
                raise HorizonStudyArtifactStoreError("settlement execution is missing")
            cursor.execute(
                """
                INSERT INTO settlement_receipts (
                    study_trade_id, settlement_observation_sha256,
                    journal_sequence, record_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    record["study_trade_id"],
                    record["settlement_observation_sha256"],
                    journal_sequence,
                    record["record_sha256"],
                ),
            )
            return
        if kind is not None:
            raise HorizonStudyArtifactStoreError("artifact typed index is invalid")

    def _assert_typed_index_for_entry(
        self,
        cursor: sqlite3.Cursor,
        entry: Mapping[str, object],
    ) -> None:
        record = entry["record"]
        if not isinstance(record, Mapping):
            raise HorizonStudyArtifactStoreError("artifact payload is invalid")
        kind = entry["typed_kind"]
        sequence = _require_int(entry["journal_sequence"], "journal_sequence")
        if kind == "input":
            row = cursor.execute(
                "SELECT journal_sequence, record_sha256, observed_at_utc "
                "FROM input_receipts WHERE input_id = ?",
                (record["input_id"],),
            ).fetchone()
            if row is None or tuple(row) != (
                sequence,
                record["record_sha256"],
                record["observed_at_utc"],
            ):
                raise HorizonStudyArtifactStoreError("typed input index is invalid")
            return
        if kind == "admission":
            row = cursor.execute(
                """
                SELECT input_id, market_id, policy_snapshot_sha256, journal_sequence,
                       record_sha256
                FROM shadow_admissions WHERE admission_id = ?
                """,
                (record["admission_id"],),
            ).fetchone()
            if row is None or tuple(row) != (
                record["input_id"],
                record["market_id"],
                record["policy_snapshot_sha256"],
                sequence,
                record["record_sha256"],
            ):
                raise HorizonStudyArtifactStoreError("typed admission index is invalid")
            return
        if kind == "execution":
            row = cursor.execute(
                """
                SELECT state, study_trade_id, execution_journal_sequence
                FROM execution_claims WHERE admission_id = ?
                """,
                (record["admission_id"],),
            ).fetchone()
            if row is None or tuple(row) != (
                "executed",
                record["study_trade_id"],
                sequence,
            ):
                raise HorizonStudyArtifactStoreError("typed execution claim is invalid")
            return
        if kind == "settlement":
            row = cursor.execute(
                """
                SELECT settlement_observation_sha256, journal_sequence, record_sha256
                FROM settlement_receipts WHERE study_trade_id = ?
                """,
                (record["study_trade_id"],),
            ).fetchone()
            if row is None or tuple(row) != (
                record["settlement_observation_sha256"],
                sequence,
                record["record_sha256"],
            ):
                raise HorizonStudyArtifactStoreError("typed settlement index is invalid")
            return

    def _fetch_generic_record(
        self,
        record_type: str,
        record_id: str,
    ) -> dict[str, object] | None:
        row = self._require_connection().execute(
            """
            SELECT journal_sequence, record_type, record_id, manifest_sha256,
                   record_sha256, mirror_relative_path, canonical_payload_json,
                   committed_at_utc
            FROM artifact_payload_journal
            WHERE record_type = ? AND record_id = ?
            """,
            (record_type, record_id),
        ).fetchone()
        if row is None:
            return None
        return self._validated_journal_row(row)["record"]

    def _validated_journal_row(self, row: sqlite3.Row) -> dict[str, object]:
        record_type = _require_exact_string(row["record_type"], "journal record_type")
        try:
            id_field, expected_path, typed_kind = _MIRROR_SPECS[record_type]
        except KeyError as exc:
            raise HorizonStudyArtifactStoreError("journal record type is invalid") from exc
        journal_sequence = _require_int(row["journal_sequence"], "journal_sequence")
        if journal_sequence <= 0:
            raise HorizonStudyArtifactStoreError("journal sequence is invalid")
        record_id = _require_exact_string(row["record_id"], "journal record_id")
        manifest_sha256 = self._require_manifest_hash(row["manifest_sha256"])
        record_sha256 = _require_sha256(row["record_sha256"], "journal record_sha256")
        mirror_relative_path = _require_exact_string(
            row["mirror_relative_path"], "journal mirror path"
        )
        if mirror_relative_path != expected_path:
            raise HorizonStudyArtifactStoreError("journal mirror path is invalid")
        canonical_payload_json = str(row["canonical_payload_json"])
        parsed = _parse_canonical_json(canonical_payload_json, "journal payload")
        if not isinstance(parsed, dict):
            raise HorizonStudyArtifactStoreError("journal payload is invalid")
        normalized = self._normalize_record_for_type(parsed, expected_type=record_type)
        if (
            normalized.get(id_field) != record_id
            or normalized.get("record_sha256") != record_sha256
            or _canonical_json(normalized) != canonical_payload_json
        ):
            raise HorizonStudyArtifactStoreError("journal payload hash is invalid")
        _require_utc_datetime(row["committed_at_utc"], "journal committed_at_utc")
        return {
            "journal_sequence": journal_sequence,
            "record_type": record_type,
            "record_id": record_id,
            "manifest_sha256": manifest_sha256,
            "record_sha256": record_sha256,
            "mirror_relative_path": mirror_relative_path,
            "canonical_payload_json": canonical_payload_json,
            "record": normalized,
            "typed_kind": typed_kind,
        }

    def _reconcile_generic_and_typed(self) -> dict[int, dict[str, object]]:
        connection = self._require_connection()
        rows = connection.execute(
            """
            SELECT journal_sequence, record_type, record_id, manifest_sha256,
                   record_sha256, mirror_relative_path, canonical_payload_json,
                   committed_at_utc
            FROM artifact_payload_journal ORDER BY journal_sequence
            """
        ).fetchall()
        entries: dict[int, dict[str, object]] = {}
        entries_by_key: dict[tuple[str, str], dict[str, object]] = {}
        for row in rows:
            entry = self._validated_journal_row(row)
            sequence = int(entry["journal_sequence"])
            if sequence in entries:
                raise HorizonStudyArtifactStoreError("duplicate journal sequence is invalid")
            entries[sequence] = entry
            key = (str(entry["record_type"]), str(entry["record_id"]))
            if key in entries_by_key:
                raise HorizonStudyArtifactStoreError("duplicate journal record is invalid")
            entries_by_key[key] = entry
        for entry in entries.values():
            record = entry["record"]
            if not isinstance(record, Mapping):
                raise HorizonStudyArtifactStoreError("journal payload is invalid")
            if entry["record_type"] == _DECISION_RECORD_TYPE:
                admission = entries_by_key.get(
                    (_ADMISSION_RECORD_TYPE, str(record["admission_id"]))
                )
                if admission is None:
                    raise HorizonStudyArtifactStoreError("decision admission is missing")
            if entry["record_type"] == _EXECUTION_RECORD_TYPE:
                decision = entries_by_key.get(
                    (_DECISION_RECORD_TYPE, str(record["decision_id"]))
                )
                if (
                    decision is None
                    or not isinstance(decision["record"], Mapping)
                    or decision["record"]["admission_id"] != record["admission_id"]
                ):
                    raise HorizonStudyArtifactStoreError("execution decision is invalid")

        seen_inputs: set[int] = set()
        for row in connection.execute(
            "SELECT input_id, journal_sequence, record_sha256, observed_at_utc FROM input_receipts"
        ):
            entry = self._required_typed_entry(entries, row["journal_sequence"], _INPUT_RECORD_TYPE)
            record = entry["record"]
            if not isinstance(record, Mapping) or (
                record["input_id"], record["record_sha256"], record["observed_at_utc"]
            ) != (row["input_id"], row["record_sha256"], row["observed_at_utc"]):
                raise HorizonStudyArtifactStoreError("typed input index is mismatched")
            seen_inputs.add(int(row["journal_sequence"]))
        self._require_all_typed_entries(entries, _INPUT_RECORD_TYPE, seen_inputs)

        seen_admissions: set[int] = set()
        for row in connection.execute(
            """
            SELECT admission_id, input_id, market_id, policy_snapshot_sha256,
                   journal_sequence, record_sha256 FROM shadow_admissions
            """
        ):
            entry = self._required_typed_entry(
                entries, row["journal_sequence"], _ADMISSION_RECORD_TYPE
            )
            record = entry["record"]
            if not isinstance(record, Mapping) or (
                record["admission_id"],
                record["input_id"],
                record["market_id"],
                record["policy_snapshot_sha256"],
                record["record_sha256"],
            ) != (
                row["admission_id"],
                row["input_id"],
                row["market_id"],
                row["policy_snapshot_sha256"],
                row["record_sha256"],
            ):
                raise HorizonStudyArtifactStoreError("typed admission index is mismatched")
            seen_admissions.add(int(row["journal_sequence"]))
        self._require_all_typed_entries(entries, _ADMISSION_RECORD_TYPE, seen_admissions)

        seen_executions: set[int] = set()
        for row in connection.execute(
            """
            SELECT admission_id, state, study_trade_id, execution_journal_sequence,
                   claimed_at_utc, updated_at_utc FROM execution_claims
            """
        ):
            state = str(row["state"])
            _require_utc_datetime(row["claimed_at_utc"], "claimed_at_utc")
            _require_utc_datetime(row["updated_at_utc"], "updated_at_utc")
            sequence = row["execution_journal_sequence"]
            if state == "claimed":
                if sequence is not None or row["study_trade_id"] is not None:
                    raise HorizonStudyArtifactStoreError("orphaned execution claim is invalid")
                continue
            if state != "executed" or sequence is None:
                raise HorizonStudyArtifactStoreError("orphaned execution claim is invalid")
            entry = self._required_typed_entry(entries, sequence, _EXECUTION_RECORD_TYPE)
            record = entry["record"]
            if not isinstance(record, Mapping) or (
                record["admission_id"], record["study_trade_id"]
            ) != (row["admission_id"], row["study_trade_id"]):
                raise HorizonStudyArtifactStoreError("typed execution claim is mismatched")
            seen_executions.add(int(sequence))
        self._require_all_typed_entries(entries, _EXECUTION_RECORD_TYPE, seen_executions)

        seen_settlements: set[int] = set()
        for row in connection.execute(
            """
            SELECT study_trade_id, settlement_observation_sha256, journal_sequence,
                   record_sha256 FROM settlement_receipts
            """
        ):
            entry = self._required_typed_entry(entries, row["journal_sequence"], _SETTLEMENT_RECORD_TYPE)
            record = entry["record"]
            if not isinstance(record, Mapping) or (
                record["study_trade_id"],
                record["settlement_observation_sha256"],
                record["record_sha256"],
            ) != (
                row["study_trade_id"],
                row["settlement_observation_sha256"],
                row["record_sha256"],
            ):
                raise HorizonStudyArtifactStoreError("typed settlement index is mismatched")
            seen_settlements.add(int(row["journal_sequence"]))
        self._require_all_typed_entries(entries, _SETTLEMENT_RECORD_TYPE, seen_settlements)
        return entries

    def _required_typed_entry(
        self,
        entries: Mapping[int, Mapping[str, object]],
        sequence_value: object,
        expected_type: str,
    ) -> Mapping[str, object]:
        sequence = _require_int(sequence_value, "journal_sequence")
        entry = entries.get(sequence)
        if entry is None:
            raise HorizonStudyArtifactStoreError("typed journal index orphan is invalid")
        if entry["record_type"] != expected_type:
            raise HorizonStudyArtifactStoreError("typed journal index is mismatched")
        return entry

    def _require_all_typed_entries(
        self,
        entries: Mapping[int, Mapping[str, object]],
        record_type: str,
        seen_sequences: set[int],
    ) -> None:
        missing = [
            sequence
            for sequence, entry in entries.items()
            if entry["record_type"] == record_type and sequence not in seen_sequences
        ]
        if missing:
            raise HorizonStudyArtifactStoreError("journal-to-typed orphan is invalid")

    def _recover_mirrors_from_journal(self) -> None:
        entries = self._reconcile_generic_and_typed()
        for record_type, (_id_field, relative_path, _typed_kind) in _MIRROR_SPECS.items():
            expected = [
                entry
                for _sequence, entry in sorted(entries.items())
                if entry["record_type"] == record_type
            ]
            self._recover_mirror_path(self._mirror_path(relative_path), expected)

    def _mirror_path(self, relative_path: str) -> Path:
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise HorizonStudyArtifactStoreError("artifact mirror path is invalid")
        path = _absolute_path(self.study_root / Path(*relative.parts))
        _assert_below(path, self.study_root, "artifact mirror path")
        if path.parent != self.artifacts_root:
            raise HorizonStudyArtifactStoreError("artifact mirror path is invalid")
        return path

    def _recover_mirror_path(
        self,
        path: Path,
        expected_entries: list[dict[str, object]],
    ) -> None:
        self._ensure_directory(self.artifacts_root)
        expected_lines = [
            (str(entry["canonical_payload_json"]) + "\n").encode("utf-8")
            for entry in expected_entries
        ]
        actual_lines = self._read_mirror_lines(path)
        if len(actual_lines) > len(expected_lines):
            raise HorizonStudyArtifactStoreError("artifact mirror contains extra line")
        for actual, expected in zip(actual_lines, expected_lines):
            if actual != expected:
                try:
                    text = actual.decode("utf-8")
                    _parse_canonical_json(text.removesuffix("\n"), "artifact mirror line")
                except (UnicodeDecodeError, HorizonStudyArtifactStoreError) as exc:
                    raise HorizonStudyArtifactStoreError("artifact mirror line is invalid") from exc
                raise HorizonStudyArtifactStoreError("artifact mirror hash or payload is invalid")
        for expected in expected_lines[len(actual_lines) :]:
            self._append_audit_payload(path, expected[:-1].decode("utf-8"))

    def _read_mirror_lines(self, path: Path) -> list[bytes]:
        if not _path_exists_or_symlink(path):
            return []
        try:
            mirror_stat = path.lstat()
        except OSError as exc:
            raise HorizonStudyArtifactStoreError("artifact mirror path is invalid") from exc
        if (
            stat.S_ISLNK(mirror_stat.st_mode)
            or not stat.S_ISREG(mirror_stat.st_mode)
            or mirror_stat.st_nlink != 1
        ):
            raise HorizonStudyArtifactStoreError("artifact mirror path is invalid")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise HorizonStudyArtifactStoreError("artifact mirror read failed") from exc
        if not payload:
            return []
        if not payload.endswith(b"\n"):
            raise HorizonStudyArtifactStoreError("artifact mirror line is invalid")
        lines = payload.splitlines(keepends=True)
        if any(not line or line == b"\n" or not line.endswith(b"\n") for line in lines):
            raise HorizonStudyArtifactStoreError("artifact mirror line is invalid")
        return lines

    def _append_audit_payload(self, path: Path, canonical_payload_json: str) -> None:
        self._ensure_directory(self.artifacts_root)
        _assert_below(path, self.study_root, "artifact mirror path")
        payload = (canonical_payload_json + "\n").encode("utf-8")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            mirror_stat = os.fstat(fd)
            if not stat.S_ISREG(mirror_stat.st_mode) or mirror_stat.st_nlink != 1:
                raise HorizonStudyArtifactStoreError("artifact mirror path is invalid")
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)

    def _validate_input_source_identity(self, record: Mapping[str, object]) -> None:
        identity = _input_source_identity(record)
        for row in self._require_connection().execute(
            """
            SELECT journal_sequence, record_type, record_id, manifest_sha256,
                   record_sha256, mirror_relative_path, canonical_payload_json,
                   committed_at_utc
            FROM artifact_payload_journal WHERE record_type = ?
            """,
            (_INPUT_RECORD_TYPE,),
        ):
            existing = self._validated_journal_row(row)["record"]
            if not isinstance(existing, Mapping):
                raise HorizonStudyArtifactStoreError("input payload is invalid")
            if (
                existing["input_id"] != record["input_id"]
                and _input_source_identity(existing) == identity
            ):
                raise HorizonStudyArtifactStoreError("input source identity is not unique")

    def _normalize_input_record(self, record: Mapping[str, object]) -> dict[str, object]:
        expected = {
            "schema_version",
            "record_type",
            "study_id",
            "manifest_sha256",
            "input_id",
            "observed_at_utc",
            "source",
            "source_url",
            "source_published_at_utc",
            "headline_sha256",
            "body_sha256",
            "market_snapshot_id",
            "market_snapshot_sha256",
            "policy_snapshot_sha256",
            "routing_prohibited",
            "record_sha256",
        }
        normalized = self._normalize_common_record(
            record,
            expected_fields=expected,
            record_type=_INPUT_RECORD_TYPE,
            allow_existing_hash=True,
        )
        _require_sha256(normalized["input_id"], "input_id")
        _require_utc_datetime(normalized["observed_at_utc"], "observed_at_utc")
        _require_exact_string(normalized["source"], "source")
        _require_optional_url(normalized["source_url"], "source_url")
        _require_optional_utc_datetime(
            normalized["source_published_at_utc"],
            "source_published_at_utc",
        )
        _require_sha256(normalized["headline_sha256"], "headline_sha256")
        _require_sha256(normalized["body_sha256"], "body_sha256")
        _require_exact_string(normalized["market_snapshot_id"], "market_snapshot_id")
        _require_sha256(normalized["market_snapshot_sha256"], "market_snapshot_sha256")
        _require_sha256(normalized["policy_snapshot_sha256"], "policy_snapshot_sha256")
        if normalized["policy_snapshot_sha256"] != self.manifest.policy_snapshot_sha256:
            raise HorizonStudyArtifactStoreError("policy_snapshot_sha256 is invalid")
        expected_input_id = hashlib.sha256(
            _canonical_json(
                {
                    "source": normalized["source"],
                    "source_url": normalized["source_url"],
                    "source_published_at_utc": normalized["source_published_at_utc"],
                    "headline_sha256": normalized["headline_sha256"],
                    "body_sha256": normalized["body_sha256"],
                    "market_snapshot_id": normalized["market_snapshot_id"],
                    "market_snapshot_sha256": normalized["market_snapshot_sha256"],
                }
            ).encode("utf-8")
        ).hexdigest()
        if normalized["input_id"] != expected_input_id:
            raise HorizonStudyArtifactStoreError("input_id is invalid")
        if normalized["routing_prohibited"] is not True:
            raise HorizonStudyArtifactStoreError("input routing_prohibited is invalid")
        return normalized

    def _normalize_shadow_admission_record(
        self,
        record: Mapping[str, object],
    ) -> dict[str, object]:
        expected = {
            "schema_version",
            "record_type",
            "study_id",
            "manifest_sha256",
            "admission_id",
            "input_id",
            "venue",
            "market_id",
            "market_close_time_utc",
            "days_to_close",
            "market_snapshot_sha256",
            "match_score",
            "min_match_score",
            "selection_status",
            "policy_snapshot_sha256",
            "routing_prohibited",
            "primary_route_called",
            "record_sha256",
        }
        normalized = self._normalize_common_record(
            record,
            expected_fields=expected,
            record_type=_ADMISSION_RECORD_TYPE,
            allow_existing_hash=True,
        )
        _require_sha256(normalized["input_id"], "input_id")
        input_record = self._fetch_input(str(normalized["input_id"]))
        if input_record is None:
            raise HorizonStudyArtifactStoreError("admission input is missing")
        if input_record.get("market_snapshot_sha256") != normalized["market_snapshot_sha256"]:
            raise HorizonStudyArtifactStoreError("admission snapshot hash is invalid")
        if input_record.get("policy_snapshot_sha256") != normalized["policy_snapshot_sha256"]:
            raise HorizonStudyArtifactStoreError("admission policy snapshot is invalid")
        actual_market_snapshot_id = _require_exact_string(
            input_record.get("market_snapshot_id"),
            "market_snapshot_id",
        )
        expected_admission_id = hashlib.sha256(
            _canonical_json(
                {
                    "study_id": self.manifest.study_id,
                    "input_id": normalized["input_id"],
                    "market_id": normalized["market_id"],
                    "market_snapshot_id": actual_market_snapshot_id,
                    "policy_snapshot_sha256": normalized["policy_snapshot_sha256"],
                }
            ).encode("utf-8")
        ).hexdigest()
        if normalized["admission_id"] != expected_admission_id:
            raise HorizonStudyArtifactStoreError("admission_id is invalid")
        if normalized["venue"] != "polymarket_us":
            raise HorizonStudyArtifactStoreError("venue is invalid")
        _require_exact_string(normalized["market_id"], "market_id")
        _require_utc_datetime(
            normalized["market_close_time_utc"], "market_close_time_utc"
        )
        _require_decimal_in_range(
            normalized["days_to_close"],
            "days_to_close",
            lower_exclusive=Decimal("14.0"),
            upper_inclusive=Decimal("30.0"),
        )
        _require_sha256(normalized["market_snapshot_sha256"], "market_snapshot_sha256")
        _require_decimal_string(normalized["match_score"], "match_score")
        _require_decimal_string(normalized["min_match_score"], "min_match_score")
        if normalized["selection_status"] not in _ADMISSION_STATUSES:
            raise HorizonStudyArtifactStoreError("selection_status is invalid")
        _require_sha256(normalized["policy_snapshot_sha256"], "policy_snapshot_sha256")
        if normalized["policy_snapshot_sha256"] != self.manifest.policy_snapshot_sha256:
            raise HorizonStudyArtifactStoreError("policy_snapshot_sha256 is invalid")
        if normalized["routing_prohibited"] is not True:
            raise HorizonStudyArtifactStoreError("routing_prohibited is invalid")
        if normalized["primary_route_called"] is not False:
            raise HorizonStudyArtifactStoreError("primary_route_called is invalid")
        return normalized

    def _normalize_decision_record(self, record: Mapping[str, object]) -> dict[str, object]:
        expected = {
            "schema_version",
            "record_type",
            "study_id",
            "manifest_sha256",
            "decision_id",
            "admission_id",
            "analysis_input_sha256",
            "research_snapshot_sha256",
            "counter_evidence_status",
            "market_price_snapshot",
            "estimated_edge",
            "decision_status",
            "routing_prohibited",
            "record_sha256",
        }
        normalized = self._normalize_common_record(
            record,
            expected_fields=expected,
            record_type=_DECISION_RECORD_TYPE,
            allow_existing_hash=True,
        )
        _require_sha256(normalized["decision_id"], "decision_id")
        _require_sha256(normalized["admission_id"], "admission_id")
        _require_sha256(normalized["analysis_input_sha256"], "analysis_input_sha256")
        _require_sha256(
            normalized["research_snapshot_sha256"],
            "research_snapshot_sha256",
        )
        _require_exact_string(
            normalized["counter_evidence_status"],
            "counter_evidence_status",
        )
        _require_decimal_string(
            normalized["market_price_snapshot"],
            "market_price_snapshot",
        )
        _require_decimal_string(normalized["estimated_edge"], "estimated_edge")
        if normalized["decision_status"] not in _DECISION_STATUSES:
            raise HorizonStudyArtifactStoreError("decision_status is invalid")
        if normalized["routing_prohibited"] is not True:
            raise HorizonStudyArtifactStoreError("routing_prohibited is invalid")
        return normalized

    def _normalize_execution_record(
        self,
        record: Mapping[str, object],
    ) -> dict[str, object]:
        expected = {
            "schema_version",
            "record_type",
            "study_id",
            "manifest_sha256",
            "execution_id",
            "admission_id",
            "decision_id",
            "study_trade_id",
            "executed_at_utc",
            "side",
            "entry_price_snapshot",
            "size",
            "routing_prohibited",
            "record_sha256",
        }
        normalized = self._normalize_common_record(
            record,
            expected_fields=expected,
            record_type=_EXECUTION_RECORD_TYPE,
            allow_existing_hash=True,
        )
        _require_sha256(normalized["execution_id"], "execution_id")
        _require_sha256(normalized["admission_id"], "admission_id")
        _require_sha256(normalized["decision_id"], "decision_id")
        _require_exact_string(normalized["study_trade_id"], "study_trade_id")
        _require_utc_datetime(normalized["executed_at_utc"], "executed_at_utc")
        if normalized["side"] not in {"yes", "no"}:
            raise HorizonStudyArtifactStoreError("side is invalid")
        _require_decimal_string(
            normalized["entry_price_snapshot"],
            "entry_price_snapshot",
        )
        _require_decimal_string(normalized["size"], "size")
        if normalized["routing_prohibited"] is not True:
            raise HorizonStudyArtifactStoreError("routing_prohibited is invalid")
        return normalized

    def _normalize_settlement_record(
        self,
        record: Mapping[str, object],
    ) -> dict[str, object]:
        expected = {
            "schema_version",
            "record_type",
            "study_id",
            "manifest_sha256",
            "settlement_id",
            "study_trade_id",
            "settlement_observation_sha256",
            "observed_at_utc",
            "terminal_status",
            "normalized_result",
            "gross_pnl_cents",
            "modeled_fee_net_pnl_cents",
            "entry_fee_provenance",
            "settlement_fee_provenance",
            "fee_schedule_sha256",
            "profit_receipt_attested",
            "live_readiness_eligible",
            "routing_prohibited",
            "record_sha256",
        }
        normalized = self._normalize_common_record(
            record,
            expected_fields=expected,
            record_type=_SETTLEMENT_RECORD_TYPE,
            allow_existing_hash=True,
        )
        _require_sha256(normalized["settlement_id"], "settlement_id")
        _require_exact_string(normalized["study_trade_id"], "study_trade_id")
        _require_sha256(
            normalized["settlement_observation_sha256"],
            "settlement_observation_sha256",
        )
        _require_utc_datetime(normalized["observed_at_utc"], "observed_at_utc")
        _require_exact_string(normalized["terminal_status"], "terminal_status")
        _require_exact_string(normalized["normalized_result"], "normalized_result")
        _require_int(normalized["gross_pnl_cents"], "gross_pnl_cents")
        if normalized["modeled_fee_net_pnl_cents"] is not None:
            _require_int(
                normalized["modeled_fee_net_pnl_cents"],
                "modeled_fee_net_pnl_cents",
            )
        _require_exact_string(
            normalized["entry_fee_provenance"],
            "entry_fee_provenance",
        )
        _require_exact_string(
            normalized["settlement_fee_provenance"],
            "settlement_fee_provenance",
        )
        _require_sha256(normalized["fee_schedule_sha256"], "fee_schedule_sha256")
        if normalized["fee_schedule_sha256"] != self.manifest.fee_schedule_sha256:
            raise HorizonStudyArtifactStoreError("fee_schedule_sha256 is invalid")
        if normalized["profit_receipt_attested"] is not False:
            raise HorizonStudyArtifactStoreError(
                "profit_receipt_attested is invalid"
            )
        if normalized["live_readiness_eligible"] is not False:
            raise HorizonStudyArtifactStoreError(
                "live_readiness_eligible is invalid"
            )
        if normalized["routing_prohibited"] is not True:
            raise HorizonStudyArtifactStoreError("routing_prohibited is invalid")
        return normalized

    def _normalize_abort_record(self, record: Mapping[str, object]) -> dict[str, object]:
        expected = {
            "schema_version",
            "record_type",
            "study_id",
            "manifest_sha256",
            "abort_id",
            "aborted_at_utc",
            "reason",
            "routing_prohibited",
            "record_sha256",
        }
        normalized = self._normalize_common_record(
            record,
            expected_fields=expected,
            record_type=_ABORT_RECORD_TYPE,
            allow_existing_hash=True,
        )
        _require_sha256(normalized["abort_id"], "abort_id")
        _require_utc_datetime(normalized["aborted_at_utc"], "aborted_at_utc")
        _require_exact_string(normalized["reason"], "reason")
        if normalized["routing_prohibited"] is not True:
            raise HorizonStudyArtifactStoreError("routing_prohibited is invalid")
        return normalized

    def _normalize_common_record(
        self,
        record: Mapping[str, object],
        *,
        expected_fields: set[str],
        record_type: str,
        allow_existing_hash: bool,
    ) -> dict[str, object]:
        if not isinstance(record, Mapping) or set(record) not in (
            expected_fields,
            expected_fields - {"record_sha256"},
        ):
            raise HorizonStudyArtifactStoreError("record payload is invalid")
        normalized = dict(record)
        if normalized.get("schema_version") != _SCHEMA_VERSION:
            raise HorizonStudyArtifactStoreError("schema_version is invalid")
        if normalized.get("record_type") != record_type:
            raise HorizonStudyArtifactStoreError("record_type is invalid")
        if normalized.get("study_id") != self.manifest.study_id:
            raise HorizonStudyArtifactStoreError("study_id is invalid")
        self._require_manifest_hash(normalized.get("manifest_sha256"))
        computed_sha256 = _record_sha256(normalized)
        existing_sha256 = normalized.get("record_sha256")
        if existing_sha256 is None:
            normalized["record_sha256"] = computed_sha256
        else:
            if not allow_existing_hash:
                raise HorizonStudyArtifactStoreError("record hash is invalid")
            if _require_sha256(existing_sha256, "record_sha256") != computed_sha256:
                raise HorizonStudyArtifactStoreError("record hash is invalid")
        return normalized

def _claim_row_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "admission_id": str(row["admission_id"]),
        "state": str(row["state"]),
        "study_trade_id": row["study_trade_id"],
        "claimed_at_utc": str(row["claimed_at_utc"]),
        "updated_at_utc": str(row["updated_at_utc"]),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _path_exists_or_symlink(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise HorizonStudyArtifactStoreError("study path is invalid") from exc
    return True


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("artifact mirror write was incomplete")
        offset += written


def _input_source_identity(record: Mapping[str, object]) -> tuple[object, ...]:
    return (
        record["source"],
        record["source_url"],
        record["source_published_at_utc"],
        record["headline_sha256"],
        record["body_sha256"],
        record["market_snapshot_id"],
        record["market_snapshot_sha256"],
        record["policy_snapshot_sha256"],
    )


def _parse_canonical_json(value: str, label: str) -> object:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HorizonStudyArtifactStoreError(f"{label} is invalid") from exc
    if _canonical_json(parsed) != value:
        raise HorizonStudyArtifactStoreError(f"{label} is invalid")
    return parsed


def _record_sha256(record: Mapping[str, object]) -> str:
    payload = dict(record)
    payload.pop("record_sha256", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _absolute_path(value: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(Path(value).expanduser())))


def _assert_below(path: Path, root: Path, label: str) -> None:
    path_value = path.resolve(strict=False)
    root_value = root.resolve(strict=False)
    try:
        path_value.relative_to(root_value)
    except ValueError as exc:
        raise HorizonStudyArtifactStoreError(f"{label} escapes the study root") from exc


def _require_exact_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HorizonStudyArtifactStoreError(f"{name} is invalid")
    return value


def _require_sha256(value: object, name: str) -> str:
    text = _require_exact_string(value, name)
    if _SHA256_RE.fullmatch(text) is None:
        raise HorizonStudyArtifactStoreError(f"{name} is invalid")
    return text


def _require_decimal_string(value: object, name: str) -> str:
    text = _require_exact_string(value, name)
    if _DECIMAL_RE.fullmatch(text) is None:
        raise HorizonStudyArtifactStoreError(f"{name} is invalid")
    return text


def _require_decimal_in_range(
    value: object,
    name: str,
    *,
    lower_exclusive: Decimal,
    upper_inclusive: Decimal,
) -> str:
    text = _require_decimal_string(value, name)
    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:
        raise HorizonStudyArtifactStoreError(f"{name} is invalid") from exc
    if not lower_exclusive < decimal_value <= upper_inclusive:
        raise HorizonStudyArtifactStoreError(f"{name} is invalid")
    return text


def _require_utc_datetime(value: object, name: str) -> str:
    text = _require_exact_string(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HorizonStudyArtifactStoreError(f"{name} is invalid") from exc
    if parsed.tzinfo is None or text != parsed.astimezone(timezone.utc).isoformat(timespec="microseconds"):
        raise HorizonStudyArtifactStoreError(f"{name} is invalid")
    if not text.endswith(_UTC_SUFFIX):
        raise HorizonStudyArtifactStoreError(f"{name} is invalid")
    return text


def _require_optional_utc_datetime(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_utc_datetime(value, name)


def _require_optional_url(value: object, name: str) -> str | None:
    if value is None:
        return None
    text = _require_exact_string(value, name)
    if not text.startswith(("https://", "http://")):
        raise HorizonStudyArtifactStoreError(f"{name} is invalid")
    return text


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HorizonStudyArtifactStoreError(f"{name} is invalid")
    return value
