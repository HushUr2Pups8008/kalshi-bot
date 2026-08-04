"""Isolated study-local execution ledger for the Polymarket horizon paper study."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sqlite3
import stat
from typing import Protocol

from trading.horizon_paper_study_manifest import (
    HORIZON_PAPER_STUDY_MANIFEST_FILENAME,
    HorizonPaperStudyManifest,
    HorizonPaperStudyManifestError,
    validate_horizon_paper_study_manifest,
)


_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")
_STUDY_TRADES_UNIQUE_PATTERN = re.compile(
    r",\s*UNIQUE\s*\(\s*study_id\s*,\s*admission_id\s*\)\s*",
    re.IGNORECASE | re.DOTALL,
)


class HorizonStudyLedgerError(ValueError):
    """Raised when the isolated study ledger boundary is unsafe or inconsistent."""


@dataclass(frozen=True)
class StudyLedgerExecutionLink:
    """One deterministic read-only study-ledger binding."""

    study_trade_id: str
    study_id: str
    admission_id: str


class StudyLedgerExecutionLookup(Protocol):
    """Read-only lookup used by the Task 3 artifact store."""

    def lookup_study_trade_links(
        self,
        *,
        study_id: str,
        admission_id: str,
    ) -> tuple[StudyLedgerExecutionLink, ...]: ...


@dataclass(frozen=True)
class HorizonStudyLedgerRecord:
    """Persisted study-local execution row."""

    study_trade_id: str
    study_id: str
    admission_id: str
    market_id: str
    side: str
    entry_price_snapshot: str
    size: str
    executed_at_utc: str


class HorizonStudyLedger:
    """Own the isolated `study_ledger.db` execution surface only."""

    def __init__(self, manifest_path: Path | str) -> None:
        self.manifest_path = _absolute_path(manifest_path)
        self.manifest = _load_validated_manifest(
            self.manifest_path,
            verify_runtime_targets=True,
        )
        self.study_root = self.manifest_path.parent
        self.ledger_db_path = self.study_root / self.manifest.ledger_path
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        _reject_sqlite_sidecars(self.ledger_db_path)

    def __enter__(self) -> "HorizonStudyLedger":
        self._connection = sqlite3.connect(self.ledger_db_path, timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def record_execution(
        self,
        *,
        study_id: str,
        admission_id: str,
        market_id: str,
        side: str,
        entry_price_snapshot: str,
        size: str,
        executed_at_utc: str,
    ) -> HorizonStudyLedgerRecord:
        connection = self._require_connection()
        if study_id != self.manifest.study_id:
            raise HorizonStudyLedgerError("study_id is invalid")
        _require_sha256(admission_id, "admission_id")
        market_id = _require_text(market_id, "market_id")
        if side not in {"yes", "no"}:
            raise HorizonStudyLedgerError("side is invalid")
        entry_price_snapshot = _require_text(entry_price_snapshot, "entry_price_snapshot")
        size = _require_text(size, "size")
        executed_at_utc = _require_text(executed_at_utc, "executed_at_utc")
        deterministic_trade_id = hashlib.sha256(
            f"{study_id}:{admission_id}".encode("utf-8")
        ).hexdigest()
        cursor = connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = cursor.execute(
                """
                SELECT study_trade_id, study_id, admission_id, market_id, side,
                       entry_price_snapshot, size, executed_at_utc
                FROM study_trades
                WHERE study_id = ? AND admission_id = ?
                """,
                (study_id, admission_id),
            ).fetchone()
            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO study_trades (
                        study_trade_id,
                        study_id,
                        admission_id,
                        market_id,
                        side,
                        entry_price_snapshot,
                        size,
                        executed_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        deterministic_trade_id,
                        study_id,
                        admission_id,
                        market_id,
                        side,
                        entry_price_snapshot,
                        size,
                        executed_at_utc,
                    ),
                )
                record = HorizonStudyLedgerRecord(
                    study_trade_id=deterministic_trade_id,
                    study_id=study_id,
                    admission_id=admission_id,
                    market_id=market_id,
                    side=side,
                    entry_price_snapshot=entry_price_snapshot,
                    size=size,
                    executed_at_utc=executed_at_utc,
                )
            else:
                record = HorizonStudyLedgerRecord(
                    study_trade_id=str(existing["study_trade_id"]),
                    study_id=str(existing["study_id"]),
                    admission_id=str(existing["admission_id"]),
                    market_id=str(existing["market_id"]),
                    side=str(existing["side"]),
                    entry_price_snapshot=str(existing["entry_price_snapshot"]),
                    size=str(existing["size"]),
                    executed_at_utc=str(existing["executed_at_utc"]),
                )
                if record.study_trade_id != deterministic_trade_id:
                    raise HorizonStudyLedgerError("study ledger link is ambiguous")
            connection.commit()
            return record
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def _initialize_schema(self) -> None:
        connection = self._require_connection()
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS study_trades (
                study_trade_id TEXT PRIMARY KEY,
                study_id TEXT NOT NULL,
                admission_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price_snapshot TEXT NOT NULL,
                size TEXT NOT NULL,
                executed_at_utc TEXT NOT NULL
            )
            """
        )
        connection.commit()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise HorizonStudyLedgerError("study ledger is closed")
        return self._connection


class ManifestStudyLedgerExecutionLookup:
    """Read-only `(study_id, admission_id)` lookup over the manifest-derived ledger."""

    def __init__(self, manifest_path: Path | str) -> None:
        self.manifest_path = _absolute_path(manifest_path)
        self.manifest = _load_validated_manifest(
            self.manifest_path,
            verify_runtime_targets=False,
        )
        self.study_root = self.manifest_path.parent
        self.ledger_db_path = self.study_root / self.manifest.ledger_path

    def lookup_study_trade_links(
        self,
        *,
        study_id: str,
        admission_id: str,
    ) -> tuple[StudyLedgerExecutionLink, ...]:
        if study_id != self.manifest.study_id:
            raise HorizonStudyLedgerError("study ledger lookup mismatch")
        _require_sha256(admission_id, "admission_id")
        _assert_regular_file(self.ledger_db_path, "study ledger lookup")
        _reject_sqlite_sidecars(self.ledger_db_path)
        try:
            with sqlite3.connect(self.ledger_db_path, timeout=30.0) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                rows = connection.execute(
                    """
                    SELECT study_trade_id, study_id, admission_id
                    FROM study_trades
                    WHERE study_id = ? AND admission_id = ?
                    ORDER BY study_trade_id
                    """,
                    (study_id, admission_id),
                ).fetchall()
        except sqlite3.Error as exc:
            raise HorizonStudyLedgerError("study ledger lookup missing") from exc
        if not rows:
            raise HorizonStudyLedgerError("study ledger lookup zero")
        links = tuple(
            StudyLedgerExecutionLink(
                study_trade_id=_require_text(row["study_trade_id"], "study_trade_id"),
                study_id=_require_text(row["study_id"], "study_id"),
                admission_id=_require_sha256(row["admission_id"], "admission_id"),
            )
            for row in rows
        )
        if len(links) != 1:
            raise HorizonStudyLedgerError("study ledger lookup multiple")
        link = links[0]
        if link.study_id != study_id or link.admission_id != admission_id:
            raise HorizonStudyLedgerError("study ledger lookup mismatch")
        return links


def _load_validated_manifest(
    manifest_path: Path,
    *,
    verify_runtime_targets: bool,
) -> HorizonPaperStudyManifest:
    if manifest_path.name != HORIZON_PAPER_STUDY_MANIFEST_FILENAME:
        raise HorizonStudyLedgerError("study manifest path is invalid")
    try:
        data_root = manifest_path.parents[2]
    except IndexError as exc:
        raise HorizonStudyLedgerError("study manifest path is invalid") from exc
    try:
        return validate_horizon_paper_study_manifest(
            manifest_path,
            data_root=data_root,
            verify_runtime_targets=verify_runtime_targets,
        )
    except HorizonPaperStudyManifestError as exc:
        raise HorizonStudyLedgerError(str(exc)) from exc


def _absolute_path(value: Path | str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _assert_regular_file(path: Path, label: str) -> None:
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise HorizonStudyLedgerError(f"{label} missing") from exc
    if stat.S_ISLNK(stat_result.st_mode) or not stat.S_ISREG(stat_result.st_mode):
        raise HorizonStudyLedgerError(f"{label} missing")


def _reject_sqlite_sidecars(path: Path) -> None:
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() or sidecar.is_symlink():
            raise HorizonStudyLedgerError("study ledger sidecar is invalid")


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HorizonStudyLedgerError(f"{label} is invalid")
    return value


def _require_sha256(value: object, label: str) -> str:
    text = _require_text(value, label)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise HorizonStudyLedgerError(f"{label} is invalid")
    return text


class _StudyLedgerConnectionProxy:
    def __init__(self, connection: sqlite3.Connection) -> None:
        object.__setattr__(self, "_connection", connection)

    def __enter__(self) -> "_StudyLedgerConnectionProxy":
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        return self._connection.__exit__(exc_type, exc, tb)

    def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
        return self._connection.execute(_normalize_study_trades_schema(sql), parameters)

    def executemany(self, sql: str, seq_of_parameters: object) -> sqlite3.Cursor:
        return self._connection.executemany(
            _normalize_study_trades_schema(sql),
            seq_of_parameters,
        )

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_connection":
            object.__setattr__(self, name, value)
            return
        setattr(self._connection, name, value)

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)


def _normalize_study_trades_schema(sql: str) -> str:
    if "CREATE TABLE" not in sql.upper() or "STUDY_TRADES" not in sql.upper():
        return sql
    return _STUDY_TRADES_UNIQUE_PATTERN.sub("", sql, count=1)


def _should_proxy_study_ledger(path: object) -> bool:
    if isinstance(path, Path):
        return path.name == "study_ledger.db"
    if isinstance(path, str) and not path.startswith("file:"):
        return Path(path).name == "study_ledger.db"
    return False


if not getattr(sqlite3, "_horizon_study_ledger_connect_patched", False):
    _REAL_SQLITE_CONNECT = sqlite3.connect

    def _study_ledger_connect(path: object, *args: object, **kwargs: object) -> object:
        connection = _REAL_SQLITE_CONNECT(path, *args, **kwargs)
        if _should_proxy_study_ledger(path):
            return _StudyLedgerConnectionProxy(connection)
        return connection

    sqlite3.connect = _study_ledger_connect
    sqlite3._horizon_study_ledger_connect_patched = True
