#!/usr/bin/env python3
"""One-shot, guarded reconciliation for legacy gross-only paper receipts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_open_paper_settlements import (
    PaperSettlementAuditSnapshotError,
    _load_open_rows,
    _load_open_rows_from_quiescent_snapshot,
    _open_rows_sha256,
    _sha256_json,
)
from trading.authoritative_settlement_source import AuthoritativeSettlementSource
from trading.legacy_settlement_receipts import LegacySettlementReceipt
from trading.settlement import MarketOutcome, SettlementObservation
from trading.settlement_store import LegacyReceiptApplicationError, SettlementStore
from trading.venue import MarketRef, Venue


_SHA256 = re.compile(r"[0-9a-f]{64}")
REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT_DB = REPO_ROOT / "data" / "paper_trades.db"


class LegacyReceiptReconciliationError(RuntimeError):
    """Raised when the one-shot operator boundary cannot prove a safe apply."""


@dataclass(frozen=True)
class ReviewedLegacyReceipt:
    receipt: LegacySettlementReceipt
    audit_report_path: Path
    audit_report_file_sha256: str
    audit_report_sha256: str
    snapshot_path: Path
    open_rows_sha256: str
    snapshot_sha256: str


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int


class _AuthoritativeSettlementSource(Protocol):
    async def get_settlement_exact(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation | None: ...


def _sha256_file(
    path: Path,
    *,
    expected_identity: _FileIdentity | None = None,
    label: str = "file",
) -> str:
    identity = expected_identity or _capture_plain_file_identity(path, label)
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != identity.device
            or metadata.st_ino != identity.inode
            or metadata.st_nlink != 1
        ):
            raise LegacyReceiptReconciliationError(
                f"{label} identity changed while hashing"
            )
        while chunk := os.read(file_descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(file_descriptor)
    return digest.hexdigest()


def _capture_plain_file_identity(path: Path, label: str) -> _FileIdentity:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LegacyReceiptReconciliationError(
            f"cannot inspect {label}: {path}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise LegacyReceiptReconciliationError(f"{label} must be a regular file")
    if metadata.st_nlink != 1:
        raise LegacyReceiptReconciliationError(
            f"{label} must not have hard-link aliases"
        )
    return _FileIdentity(device=metadata.st_dev, inode=metadata.st_ino)


def _require_same_plain_file(
    path: Path,
    expected: _FileIdentity,
    label: str,
) -> None:
    if _capture_plain_file_identity(path, label) != expected:
        raise LegacyReceiptReconciliationError(
            f"{label} identity changed during reconciliation"
        )


def _read_verified_plain_file(
    path: Path,
    identity: _FileIdentity,
    label: str,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise LegacyReceiptReconciliationError(f"cannot open {label}") from exc
    try:
        metadata = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != identity.device
            or metadata.st_ino != identity.inode
            or metadata.st_nlink != 1
        ):
            raise LegacyReceiptReconciliationError(
                f"{label} identity changed while reading"
            )
        chunks: list[bytes] = []
        while chunk := os.read(file_descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(file_descriptor)


def _require_absolute_path_without_symlinks(value: Path | str, label: str) -> Path:
    raw_path = Path(value).expanduser()
    if not raw_path.is_absolute():
        raise LegacyReceiptReconciliationError(f"{label} path must be absolute")
    path = Path(os.path.abspath(raw_path))
    current = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            current /= component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise LegacyReceiptReconciliationError(
                    f"{label} path must not traverse a symlink"
                )
            if current != path and not stat.S_ISDIR(metadata.st_mode):
                raise LegacyReceiptReconciliationError(
                    f"{label} path has a non-directory parent"
                )
    except OSError as exc:
        raise LegacyReceiptReconciliationError(
            f"cannot inspect {label} path: {path}"
        ) from exc
    return path


def _require_expected_hash(path: Path, expected_sha256: str, label: str) -> str:
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise LegacyReceiptReconciliationError(
            f"{label} requires a lowercase SHA-256"
        )
    identity = _capture_plain_file_identity(path, label)
    try:
        actual_sha256 = _sha256_file(
            path,
            expected_identity=identity,
            label=label,
        )
    except OSError as exc:
        raise LegacyReceiptReconciliationError(
            f"cannot hash {label}: {path}"
        ) from exc
    if actual_sha256 != expected_sha256:
        raise LegacyReceiptReconciliationError(
            f"{label} SHA-256 does not match the reviewed value"
        )
    return actual_sha256


def _require_approved_root(db_path: Path) -> Path:
    root = _require_absolute_path_without_symlinks(db_path, "legacy root")
    approved = _require_absolute_path_without_symlinks(
        LEGACY_ROOT_DB,
        "approved legacy root",
    )
    if root != approved:
        raise LegacyReceiptReconciliationError(
            "legacy receipt reconciliation only permits the approved legacy root"
        )
    if not root.is_file():
        raise LegacyReceiptReconciliationError("approved legacy root database is missing")
    _capture_plain_file_identity(root, "legacy root")
    return root


def _require_reviewed_snapshot_path(snapshot_db: Path | str) -> Path:
    snapshot_path = _require_absolute_path_without_symlinks(
        snapshot_db,
        "reviewed snapshot",
    )
    if not snapshot_path.is_file():
        raise LegacyReceiptReconciliationError("reviewed snapshot database is missing")
    _capture_plain_file_identity(snapshot_path, "reviewed snapshot")
    return snapshot_path


def _require_reviewed_audit_report_path(audit_report_path: Path | str) -> Path:
    report_path = _require_absolute_path_without_symlinks(
        audit_report_path,
        "reviewed audit report provenance",
    )
    _capture_plain_file_identity(report_path, "reviewed audit report provenance")
    return report_path


def _require_quiescent_sqlite_artifacts(path: Path, label: str) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = path.with_name(f"{path.name}{suffix}")
        if sidecar.exists():
            raise LegacyReceiptReconciliationError(
                f"{label} has active SQLite sidecar {sidecar.name}; quiesce first"
            )


def _reject_duplicate_json_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def load_reviewed_legacy_receipt(
    audit_report_path: Path | str,
    *,
    trade_id: str,
    snapshot_db: Path | str,
    expected_snapshot_sha256: str,
    expected_audit_report_sha256: str,
) -> ReviewedLegacyReceipt:
    """Recover one terminal receipt only from a hash-attested audit report."""

    report_path = _require_reviewed_audit_report_path(audit_report_path)
    snapshot_path = _require_reviewed_snapshot_path(snapshot_db)
    report_identity = _capture_plain_file_identity(
        report_path,
        "reviewed audit report provenance",
    )
    try:
        _require_expected_hash(
            snapshot_path,
            expected_snapshot_sha256,
            "reviewed snapshot",
        )
        if (
            not isinstance(expected_audit_report_sha256, str)
            or _SHA256.fullmatch(expected_audit_report_sha256) is None
        ):
            raise ValueError("reviewed audit report requires a lowercase SHA-256")
        report_bytes = _read_verified_plain_file(
            report_path,
            report_identity,
            "reviewed audit report provenance",
        )
        if hashlib.sha256(report_bytes).hexdigest() != expected_audit_report_sha256:
            raise ValueError("reviewed audit report SHA-256 does not match")
        report = json.loads(
            report_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_object_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
        if not isinstance(report, dict):
            raise ValueError("audit report must be an object")
        report_sha256 = report["report_sha256"]
        if not isinstance(report_sha256, str) or _SHA256.fullmatch(report_sha256) is None:
            raise ValueError("audit report hash is invalid")
        evidence_body = {
            "read_only": report["read_only"],
            "resolution_applied": report["resolution_applied"],
            "fetched_markets": report["fetched_markets"],
            "counts": report["counts"],
            "snapshot_artifacts": [
                {"size": item["size"], "sha256": item["sha256"]}
                for item in report["snapshot_artifacts"]
            ],
            "open_rows_sha256": report["open_rows_sha256"],
            "rows": report["rows"],
        }
        if _sha256_json(evidence_body) != report_sha256:
            raise ValueError("audit report hash does not match its evidence")
        if report["read_only"] is not True or report["resolution_applied"] is not False:
            raise ValueError("audit report was not read-only")
        report_path_value = Path(str(report["db_path"])).expanduser().resolve()
        if report_path_value != snapshot_path:
            raise ValueError("audit report targets a different snapshot")
        snapshot_artifacts = report["snapshot_artifacts"]
        if not isinstance(snapshot_artifacts, list) or not any(
            isinstance(item, dict)
            and item.get("name") == snapshot_path.name
            and item.get("sha256") == expected_snapshot_sha256
            and item.get("size") == snapshot_path.stat().st_size
            for item in snapshot_artifacts
        ):
            raise ValueError("audit report does not attest the reviewed snapshot")
        open_rows_sha256 = report["open_rows_sha256"]
        if not isinstance(open_rows_sha256, str) or _SHA256.fullmatch(open_rows_sha256) is None:
            raise ValueError("audit report open-row fingerprint is invalid")
        rows = report["rows"]
        if not isinstance(rows, list):
            raise ValueError("audit report rows are invalid")
        matching_rows = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("trade_id") == trade_id
        ]
        if len(matching_rows) != 1:
            raise ValueError("audit report does not identify exactly one trade")
        row = matching_rows[0]
        if row.get("status") != "authoritative_terminal":
            raise ValueError("audit report trade is not authoritative terminal evidence")
        bundle = row.get("receipt_bundle")
        if not isinstance(bundle, dict):
            raise ValueError("audit report has no reconstructable receipt bundle")
        receipt = LegacySettlementReceipt.from_dict(bundle)
        observation = receipt.observation
        if (
            receipt.trade_id != trade_id
            or row.get("ticker") != observation.market_ref.alias
            or row.get("venue") != observation.market_ref.venue.value
            or row.get("canonical_market_id") != observation.market_ref.venue_market_id
            or row.get("outcome") != observation.outcome.value
            or row.get("source_id") != observation.source_id
            or row.get("rules_version") != observation.rules_version
            or row.get("observed_at") != observation.observed_at.isoformat()
            or row.get("effective_at") != observation.effective_at.isoformat()
            or row.get("payload_sha256") != observation.payload_sha256
            or row.get("observation_sha256") != observation.observation_sha256
            or row.get("persisted_terminal_fields") not in ([], ())
        ):
            raise ValueError("audit report receipt fields are inconsistent")
        _require_same_plain_file(
            report_path,
            report_identity,
            "reviewed audit report provenance",
        )
        _require_expected_hash(
            report_path,
            expected_audit_report_sha256,
            "reviewed audit report",
        )
        _require_expected_hash(
            snapshot_path,
            expected_snapshot_sha256,
            "reviewed snapshot",
        )
    except (KeyError, LegacyReceiptReconciliationError, OSError, TypeError, ValueError) as exc:
        raise LegacyReceiptReconciliationError(
            "reviewed audit receipt provenance is invalid"
        ) from exc
    return ReviewedLegacyReceipt(
        receipt=receipt,
        audit_report_path=report_path,
        audit_report_file_sha256=expected_audit_report_sha256,
        audit_report_sha256=report_sha256,
        snapshot_path=snapshot_path,
        open_rows_sha256=open_rows_sha256,
        snapshot_sha256=expected_snapshot_sha256,
    )


def _revalidate_reviewed_receipt(
    review: ReviewedLegacyReceipt,
    *,
    snapshot_db: Path | str,
    expected_snapshot_sha256: str,
) -> ReviewedLegacyReceipt:
    if not isinstance(review, ReviewedLegacyReceipt):
        raise LegacyReceiptReconciliationError("reviewed audit receipt is invalid")
    snapshot_path = _require_reviewed_snapshot_path(snapshot_db)
    if (
        snapshot_path != review.snapshot_path
        or expected_snapshot_sha256 != review.snapshot_sha256
    ):
        raise LegacyReceiptReconciliationError(
            "reviewed audit receipt targets a different snapshot"
        )
    reloaded = load_reviewed_legacy_receipt(
        review.audit_report_path,
        trade_id=review.receipt.trade_id,
        snapshot_db=snapshot_path,
        expected_snapshot_sha256=expected_snapshot_sha256,
        expected_audit_report_sha256=review.audit_report_file_sha256,
    )
    if reloaded != review:
        raise LegacyReceiptReconciliationError(
            "reviewed audit receipt changed after initial validation"
        )
    return reloaded


def _snapshot_for(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
):
    _require_quiescent_sqlite_artifacts(path, label)
    _require_expected_hash(path, expected_sha256, label)
    try:
        return _load_open_rows_from_quiescent_snapshot(
            path,
            expected_sha256=expected_sha256,
        )
    except PaperSettlementAuditSnapshotError as exc:
        raise LegacyReceiptReconciliationError(
            f"{label} is not a caller-attested immutable snapshot"
        ) from exc


def _validate_receipt_target(rows, receipt: LegacySettlementReceipt) -> None:
    observation = receipt.observation
    if observation.outcome not in {MarketOutcome.YES, MarketOutcome.NO}:
        raise LegacyReceiptReconciliationError(
            "reviewed receipt must have a directional terminal outcome"
        )
    if observation.void_refund is not None or observation.supersedes_observation_sha256:
        raise LegacyReceiptReconciliationError(
            "reviewed receipt cannot carry void or supersession semantics"
        )
    matches = [row for row in rows if row.trade_id == receipt.trade_id]
    if len(matches) != 1:
        raise LegacyReceiptReconciliationError(
            "reviewed receipt does not identify exactly one open snapshot trade"
        )
    target = matches[0]
    if (
        target.ticker != observation.market_ref.alias
        or target.venue != observation.market_ref.venue.value
        or target.canonical_market_id != observation.market_ref.venue_market_id
        or target.identity_status != "mapped"
        or target.persisted_terminal_fields
    ):
        raise LegacyReceiptReconciliationError(
            "reviewed receipt does not match the open mapped snapshot trade"
        )
    same_market = [
        row
        for row in rows
        if row.venue == observation.market_ref.venue.value
        and row.canonical_market_id == observation.market_ref.venue_market_id
    ]
    if [row.trade_id for row in same_market] != [receipt.trade_id]:
        raise LegacyReceiptReconciliationError(
            "reviewed receipt market has ambiguous open snapshot trades"
        )


def plan_legacy_receipt_reconciliation(
    db_path: Path | str,
    *,
    snapshot_db: Path | str,
    review: ReviewedLegacyReceipt,
    expected_root_sha256: str,
    expected_snapshot_sha256: str,
) -> dict[str, str]:
    """Validate a no-write reconciliation plan against root and snapshot state."""

    review = _revalidate_reviewed_receipt(
        review,
        snapshot_db=snapshot_db,
        expected_snapshot_sha256=expected_snapshot_sha256,
    )
    receipt = review.receipt
    root = _require_approved_root(Path(db_path))
    snapshot_path = _require_reviewed_snapshot_path(snapshot_db)
    if snapshot_path == root:
        raise LegacyReceiptReconciliationError(
            "reviewed snapshot must be distinct from the mutable legacy root"
        )
    root_snapshot = _snapshot_for(
        root,
        expected_sha256=expected_root_sha256,
        label="legacy root",
    )
    reviewed_snapshot = _snapshot_for(
        snapshot_path,
        expected_sha256=expected_snapshot_sha256,
        label="reviewed snapshot",
    )
    if root_snapshot.open_rows_sha256 != reviewed_snapshot.open_rows_sha256:
        raise LegacyReceiptReconciliationError(
            "legacy root open rows differ from the reviewed snapshot"
        )
    if root_snapshot.open_rows_sha256 != review.open_rows_sha256:
        raise LegacyReceiptReconciliationError(
            "legacy root open rows differ from the reviewed audit report"
        )
    _validate_receipt_target(reviewed_snapshot.rows, receipt)
    _validate_receipt_target(root_snapshot.rows, receipt)
    return {
        "mode": "plan",
        "root_db": str(root),
        "snapshot_db": str(snapshot_path),
        "root_sha256": expected_root_sha256,
        "snapshot_sha256": expected_snapshot_sha256,
        "open_rows_sha256": root_snapshot.open_rows_sha256,
        "audit_report_file_sha256": review.audit_report_file_sha256,
        "audit_report_sha256": review.audit_report_sha256,
        "trade_id": receipt.trade_id,
        "receipt_sha256": receipt.receipt_sha256,
        "observation_sha256": receipt.observation.observation_sha256,
    }


def _attest_root_under_writer_lock(
    connection: sqlite3.Connection,
    *,
    root: Path,
    root_identity: _FileIdentity,
    review: ReviewedLegacyReceipt,
    expected_root_sha256: str,
) -> None:
    """Recheck the reviewed preimage through the connection holding the writer lock."""

    _require_approved_root(root)
    _require_same_plain_file(root, root_identity, "legacy root")
    _require_quiescent_sqlite_artifacts(root, "legacy root")
    _require_expected_hash(root, expected_root_sha256, "legacy root")
    journal_mode_row = connection.execute("PRAGMA journal_mode").fetchone()
    journal_mode = "" if journal_mode_row is None else str(journal_mode_row[0]).lower()
    if journal_mode == "wal":
        raise LegacyReceiptReconciliationError(
            "legacy root entered WAL mode during reconciliation"
        )
    locked_rows = tuple(_load_open_rows(connection))
    if _open_rows_sha256(locked_rows) != review.open_rows_sha256:
        raise LegacyReceiptReconciliationError(
            "legacy root open rows differ under the SQLite writer lock"
        )
    _validate_receipt_target(locked_rows, review.receipt)


@contextmanager
def _runtime_lock(db_path: Path) -> Iterator[None]:
    """Acquire main.py's parent lock without importing runtime startup code."""

    lock_path = db_path.parent / "bot_runtime.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_symlink():
        raise RuntimeError(f"runtime lock path must not be a symlink: {lock_path}")
    try:
        lock_fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        handle = os.fdopen(lock_fd, "a+", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot securely open runtime lock at {lock_path}") from exc
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(
            f"runtime lock is held at {lock_path}; stop the bot before --write"
        ) from exc
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "operation": "legacy_receipt_reconciliation",
                    "started_utc": datetime.now(timezone.utc).isoformat(),
                },
                separators=(",", ":"),
            )
        )
        handle.flush()
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _open_backup_parent(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path.parent, flags)
    except OSError as exc:
        raise LegacyReceiptReconciliationError(
            "cannot securely open the legacy receipt backup directory"
        ) from exc


def _reserve_backup_temp(
    parent_fd: int,
    parent: Path,
    backup_path: Path,
) -> tuple[str, Path, _FileIdentity]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(8):
        temp_name = f".{backup_path.name}.{secrets.token_hex(12)}.tmp"
        try:
            temp_fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise LegacyReceiptReconciliationError(
                "cannot reserve a legacy receipt backup temporary file"
            ) from exc
        else:
            os.close(temp_fd)
            temp_path = parent / temp_name
            return (
                temp_name,
                temp_path,
                _capture_plain_file_identity(temp_path, "legacy receipt backup temporary"),
            )
    raise LegacyReceiptReconciliationError(
        "cannot reserve a unique legacy receipt backup temporary file"
    )


def _remove_owned_temp_backup(path: Path, identity: _FileIdentity | None) -> None:
    if identity is None:
        return
    try:
        metadata = path.lstat()
        if (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_dev == identity.device
            and metadata.st_ino == identity.inode
        ):
            path.unlink()
    except OSError:
        pass


def _fsync_plain_file(path: Path, identity: _FileIdentity, label: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise LegacyReceiptReconciliationError(f"cannot open {label} for fsync") from exc
    try:
        metadata = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != identity.device
            or metadata.st_ino != identity.inode
            or metadata.st_nlink != 1
        ):
            raise LegacyReceiptReconciliationError(
                f"{label} identity changed before fsync"
            )
        os.fsync(file_descriptor)
    except OSError as exc:
        raise LegacyReceiptReconciliationError(f"cannot fsync {label}") from exc
    finally:
        os.close(file_descriptor)


def _notional_bankroll_value(path: Path, label: str) -> object:
    try:
        uri = f"{path.as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30.0)
    except sqlite3.Error as exc:
        raise LegacyReceiptReconciliationError(
            f"cannot open {label} to verify notional bankroll"
        ) from exc
    try:
        rows = connection.execute(
            "SELECT value FROM bot_state WHERE key='notional_bankroll'"
        ).fetchall()
    except sqlite3.Error as exc:
        raise LegacyReceiptReconciliationError(
            f"cannot read {label} notional bankroll"
        ) from exc
    finally:
        connection.close()
    if len(rows) != 1:
        raise LegacyReceiptReconciliationError(
            f"{label} must contain exactly one notional bankroll value"
        )
    return rows[0][0]


def _write_adjacent_backup(
    db_path: Path,
    receipt: LegacySettlementReceipt,
    *,
    root_identity: _FileIdentity,
    review: ReviewedLegacyReceipt,
) -> Path:
    """Create a durable, no-clobber preimage while the caller holds SQLite's lock."""

    _require_same_plain_file(db_path, root_identity, "legacy root")
    backup_path = db_path.with_name(
        f"{db_path.stem}.legacy-receipt-{receipt.receipt_sha256[:12]}.bak"
    )
    parent_fd = _open_backup_parent(db_path)
    temp_name: str | None = None
    temp_path: Path | None = None
    temp_identity: _FileIdentity | None = None
    published = False
    try:
        temp_name, temp_path, temp_identity = _reserve_backup_temp(
            parent_fd,
            db_path.parent,
            backup_path,
        )
        source: sqlite3.Connection | None = None
        backup: sqlite3.Connection | None = None
        try:
            source_uri = f"{db_path.as_uri()}?mode=ro"
            source = sqlite3.connect(
                source_uri,
                uri=True,
                isolation_level=None,
                timeout=30.0,
            )
            backup = sqlite3.connect(temp_path, isolation_level=None, timeout=30.0)
            source.execute("PRAGMA query_only = ON")
            source.backup(backup)
            integrity = tuple(str(row[0]) for row in backup.execute("PRAGMA integrity_check"))
            if integrity != ("ok",):
                raise LegacyReceiptReconciliationError(
                    "legacy receipt backup failed SQLite integrity check"
                )
        finally:
            if backup is not None:
                backup.close()
            if source is not None:
                source.close()
        _require_quiescent_sqlite_artifacts(temp_path, "legacy receipt backup")
        _require_same_plain_file(
            temp_path,
            temp_identity,
            "legacy receipt backup temporary",
        )
        backup_sha256 = _sha256_file(temp_path)
        backup_snapshot = _snapshot_for(
            temp_path,
            expected_sha256=backup_sha256,
            label="legacy receipt backup",
        )
        if backup_snapshot.open_rows_sha256 != review.open_rows_sha256:
            raise LegacyReceiptReconciliationError(
                "legacy receipt backup does not match the reviewed open rows"
            )
        _validate_receipt_target(backup_snapshot.rows, receipt)
        if _notional_bankroll_value(temp_path, "legacy receipt backup") != _notional_bankroll_value(
            db_path,
            "legacy root",
        ):
            raise LegacyReceiptReconciliationError(
                "legacy receipt backup notional bankroll differs from its preimage"
            )
        _fsync_plain_file(temp_path, temp_identity, "legacy receipt backup temporary")
        try:
            os.link(
                temp_name,
                backup_path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise LegacyReceiptReconciliationError(
                f"refusing to overwrite existing backup {backup_path.name}"
            ) from exc
        except OSError as exc:
            raise LegacyReceiptReconciliationError(
                "cannot publish the legacy receipt backup"
            ) from exc
        final_metadata = backup_path.lstat()
        if (
            not stat.S_ISREG(final_metadata.st_mode)
            or final_metadata.st_dev != temp_identity.device
            or final_metadata.st_ino != temp_identity.inode
        ):
            raise LegacyReceiptReconciliationError(
                "legacy receipt backup publish identity is invalid"
            )
        os.unlink(temp_name, dir_fd=parent_fd)
        temp_name = None
        temp_path = None
        os.fsync(parent_fd)
        _capture_plain_file_identity(backup_path, "legacy receipt backup")
        published = True
        return backup_path
    except LegacyReceiptReconciliationError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise LegacyReceiptReconciliationError(
            "cannot create adjacent legacy receipt backup"
        ) from exc
    finally:
        if not published and temp_path is not None:
            _remove_owned_temp_backup(temp_path, temp_identity)
        os.close(parent_fd)


async def apply_legacy_receipt_reconciliation(
    db_path: Path | str,
    *,
    snapshot_db: Path | str,
    review: ReviewedLegacyReceipt,
    expected_root_sha256: str,
    expected_snapshot_sha256: str,
    source: _AuthoritativeSettlementSource,
    allow_network: bool,
    write: bool,
    applied_at: datetime | None = None,
) -> dict[str, object]:
    """Refetch one reviewed receipt, back up root, and perform one atomic apply."""

    if not allow_network or not write:
        raise LegacyReceiptReconciliationError(
            "legacy receipt reconciliation requires both --allow-network and --write"
        )
    root = _require_approved_root(Path(db_path))
    review = _revalidate_reviewed_receipt(
        review,
        snapshot_db=snapshot_db,
        expected_snapshot_sha256=expected_snapshot_sha256,
    )
    receipt = review.receipt
    application_time = applied_at or datetime.now(timezone.utc)
    try:
        with _runtime_lock(root):
            plan = plan_legacy_receipt_reconciliation(
                root,
                snapshot_db=snapshot_db,
                review=review,
                expected_root_sha256=expected_root_sha256,
                expected_snapshot_sha256=expected_snapshot_sha256,
            )
            try:
                fresh_observation = await source.get_settlement_exact(
                    receipt.observation.market_ref,
                    prior_observation=receipt.observation,
                )
            except Exception as exc:
                raise LegacyReceiptReconciliationError(
                    "fresh source re-fetch failed"
                ) from exc
            if fresh_observation != receipt.observation:
                raise LegacyReceiptReconciliationError(
                    "fresh source re-fetch does not match the reviewed receipt"
                )
            plan_legacy_receipt_reconciliation(
                root,
                snapshot_db=snapshot_db,
                review=review,
                expected_root_sha256=expected_root_sha256,
                expected_snapshot_sha256=expected_snapshot_sha256,
            )
            root_identity = _capture_plain_file_identity(root, "legacy root")
            backup_paths: list[Path] = []

            def attest_preimage(connection: sqlite3.Connection) -> None:
                locked_review = _revalidate_reviewed_receipt(
                    review,
                    snapshot_db=snapshot_db,
                    expected_snapshot_sha256=expected_snapshot_sha256,
                )
                if locked_review != review:
                    raise LegacyReceiptReconciliationError(
                        "reviewed audit receipt changed before application"
                    )
                _attest_root_under_writer_lock(
                    connection,
                    root=root,
                    root_identity=root_identity,
                    review=review,
                    expected_root_sha256=expected_root_sha256,
                )

            def backup_preimage(_connection: sqlite3.Connection) -> None:
                if backup_paths:
                    raise LegacyReceiptReconciliationError(
                        "legacy receipt backup was invoked more than once"
                    )
                backup_paths.append(
                    _write_adjacent_backup(
                        root,
                        receipt,
                        root_identity=root_identity,
                        review=review,
                    )
                )

            try:
                with SettlementStore(root) as store:
                    result = store._apply_legacy_directional_receipt(
                        receipt,
                        applied_at=application_time,
                        transaction_precondition=attest_preimage,
                        before_mutation=backup_preimage,
                    )
            except LegacyReceiptApplicationError as exc:
                if isinstance(exc.__cause__, LegacyReceiptReconciliationError):
                    raise exc.__cause__ from exc
                raise LegacyReceiptReconciliationError(
                    "legacy receipt application failed after backup"
                ) from exc
            if len(backup_paths) != 1:
                raise LegacyReceiptReconciliationError(
                    "legacy receipt application committed without one durable backup"
                )
            backup_path = backup_paths[0]
            _require_same_plain_file(root, root_identity, "legacy root")
    except LegacyReceiptReconciliationError:
        raise
    except RuntimeError as exc:
        raise LegacyReceiptReconciliationError(str(exc)) from exc
    return {
        **plan,
        "mode": "applied",
        "applied": result.applied,
        "gross_payout_cents": result.gross_payout_cents,
        "gross_pnl_cents": result.gross_pnl_cents,
        "backup_db": str(backup_path),
        "backup_sha256": _sha256_file(backup_path),
    }


def _default_source(receipt: LegacySettlementReceipt) -> AuthoritativeSettlementSource:
    if receipt.observation.market_ref.venue is not Venue.POLYMARKET_US:
        raise LegacyReceiptReconciliationError(
            "legacy receipt CLI only supports the reviewed Polymarket legacy root"
        )
    from polymarket.public_client import PolymarketPublicClient

    return AuthoritativeSettlementSource(
        kalshi_client=None,
        polymarket_client=PolymarketPublicClient(),
        clock=lambda: receipt.observation.observed_at,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan or explicitly reconcile one reviewed legacy paper receipt."
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--snapshot-db", type=Path, required=True)
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--trade-id", required=True)
    parser.add_argument("--expected-root-sha256", required=True)
    parser.add_argument("--expected-snapshot-sha256", required=True)
    parser.add_argument("--expected-audit-report-sha256", required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.allow_network != args.write:
        parser.error("--allow-network and --write must be supplied together")
    return args


async def _main_async(args: argparse.Namespace) -> int:
    review = load_reviewed_legacy_receipt(
        args.audit_report,
        trade_id=args.trade_id,
        snapshot_db=args.snapshot_db,
        expected_snapshot_sha256=args.expected_snapshot_sha256,
        expected_audit_report_sha256=args.expected_audit_report_sha256,
    )
    if not args.write:
        plan = plan_legacy_receipt_reconciliation(
            args.db,
            snapshot_db=args.snapshot_db,
            review=review,
            expected_root_sha256=args.expected_root_sha256,
            expected_snapshot_sha256=args.expected_snapshot_sha256,
        )
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    result = await apply_legacy_receipt_reconciliation(
        args.db,
        snapshot_db=args.snapshot_db,
        review=review,
        expected_root_sha256=args.expected_root_sha256,
        expected_snapshot_sha256=args.expected_snapshot_sha256,
        source=_default_source(review.receipt),
        allow_network=args.allow_network,
        write=args.write,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(_main_async(_parse_args(argv)))
    except LegacyReceiptReconciliationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
