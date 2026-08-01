"""Nonsecret receipt proving the paper cohort bound by a successful runtime boot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import stat
import uuid
from typing import Any


RUNTIME_PAPER_COHORT_ATTESTATION_SCHEMA_VERSION = 1
_COHORT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_COHORT_KINDS = frozenset(("legacy", "legacy_pending", "active"))
_IDENTITY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_RECEIPT_BYTES = 16 * 1024


class RuntimePaperCohortAttestationError(ValueError):
    """Raised when a runtime cohort receipt is invalid or unsafe to consume."""


@dataclass(frozen=True)
class RuntimePaperCohortAttestation:
    """A versioned, nonsecret record of one successful runtime cohort binding."""

    pid: int
    started_utc: datetime
    cohort_id: str
    cohort_kind: str
    db_path_relative_to_storage_root: str
    manifest_bound: bool
    cohort_identity: str | None
    manifest_sha256: str | None
    schema_version: int = RUNTIME_PAPER_COHORT_ATTESTATION_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        """Return the canonical JSON-safe receipt payload."""

        return {
            "schema_version": self.schema_version,
            "pid": self.pid,
            "started_utc": _format_utc(self.started_utc),
            "cohort_id": self.cohort_id,
            "cohort_kind": self.cohort_kind,
            "db_path_relative_to_storage_root": self.db_path_relative_to_storage_root,
            "manifest_bound": self.manifest_bound,
            "cohort_identity": self.cohort_identity,
            "manifest_sha256": self.manifest_sha256,
        }


def build_runtime_paper_cohort_attestation(
    cohort: Any,
    *,
    cohort_kind: str,
    binding: Any | None,
    pid: int | None = None,
    started_utc: datetime | None = None,
) -> RuntimePaperCohortAttestation:
    """Build a receipt from an already validated runtime cohort and binding.

    The deliberately structural inputs keep this module usable from startup and
    read-only diagnostics without importing configuration or runtime modules.
    """

    storage_root = _required_path(getattr(cohort, "storage_root", None), "storage root")
    database_path = _required_path(getattr(cohort, "db_path", None), "database path")
    cohort_id = _required_text(getattr(cohort, "cohort_id", None), "cohort ID")
    relative_database_path = _relative_database_path(database_path, storage_root)
    normalized_kind = _validated_cohort_kind(cohort_kind)
    normalized_pid = os.getpid() if pid is None else _validated_pid(pid)
    normalized_started_utc = _validated_started_utc(
        datetime.now(UTC) if started_utc is None else started_utc
    )

    if binding is None:
        if normalized_kind != "legacy":
            raise RuntimePaperCohortAttestationError(
                "manifest-bound cohort requires a validated binding"
            )
        return _validated_attestation(
            RuntimePaperCohortAttestation(
                pid=normalized_pid,
                started_utc=normalized_started_utc,
                cohort_id=cohort_id,
                cohort_kind=normalized_kind,
                db_path_relative_to_storage_root=relative_database_path,
                manifest_bound=False,
                cohort_identity=None,
                manifest_sha256=None,
            )
        )

    binding_cohort = getattr(binding, "cohort", None)
    if binding_cohort is not cohort and (
        getattr(binding_cohort, "cohort_id", None) != cohort_id
        or getattr(binding_cohort, "db_path", None) != database_path
    ):
        raise RuntimePaperCohortAttestationError(
            "validated binding does not match runtime cohort"
        )
    if getattr(binding, "cohort_type", None) != normalized_kind:
        raise RuntimePaperCohortAttestationError(
            "validated binding kind does not match runtime cohort"
        )
    return _validated_attestation(
        RuntimePaperCohortAttestation(
            pid=normalized_pid,
            started_utc=normalized_started_utc,
            cohort_id=cohort_id,
            cohort_kind=normalized_kind,
            db_path_relative_to_storage_root=relative_database_path,
            manifest_bound=True,
            cohort_identity=_required_text(
                getattr(binding, "cohort_identity", None), "cohort identity"
            ),
            manifest_sha256=_required_text(
                getattr(binding, "manifest_sha256", None), "manifest SHA-256"
            ),
        )
    )


def write_runtime_paper_cohort_attestation(
    attestation: RuntimePaperCohortAttestation,
    path: Path,
) -> None:
    """Atomically write a validated receipt without following a target symlink."""

    receipt = _validated_attestation(attestation)
    target = Path(path)
    _ensure_safe_parent(target.parent)
    _reject_unsafe_existing_target(target)
    encoded = (
        json.dumps(receipt.to_payload(), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    temporary_path = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    fd: int | None = None
    try:
        fd = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        _write_all(fd, encoded)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temporary_path, target)
        _fsync_directory(target.parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def read_runtime_paper_cohort_attestation(
    path: Path,
    *,
    storage_root: Path,
    expected_pid: int | None = None,
) -> RuntimePaperCohortAttestation:
    """Read a receipt without following symlinks and validate its lineage shape."""

    receipt, _ = read_runtime_paper_cohort_attestation_with_bytes(
        path,
        storage_root=storage_root,
        expected_pid=expected_pid,
    )
    return receipt


def read_runtime_paper_cohort_attestation_with_bytes(
    path: Path,
    *,
    storage_root: Path,
    expected_pid: int | None = None,
) -> tuple[RuntimePaperCohortAttestation, bytes]:
    """Read a validated receipt and return the exact bytes that authorized it."""

    target = Path(path)
    _reject_unsafe_existing_target(target)
    raw = _read_regular_file(target)
    payload = _decode_payload(raw)
    receipt = _attestation_from_payload(payload)
    _validated_attestation(receipt)
    _relative_database_path(
        Path(storage_root) / receipt.db_path_relative_to_storage_root,
        Path(storage_root),
    )
    if expected_pid is not None and receipt.pid != _validated_pid(expected_pid):
        raise RuntimePaperCohortAttestationError(
            "receipt PID does not match the current bot process"
        )
    return receipt, raw


def _format_utc(value: datetime) -> str:
    return _validated_started_utc(value).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _validated_started_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimePaperCohortAttestationError("receipt start time must be UTC-aware")
    return value.astimezone(UTC)


def _validated_pid(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise RuntimePaperCohortAttestationError("receipt PID must be a positive integer")
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimePaperCohortAttestationError(f"receipt {label} must be nonempty")
    return value


def _required_path(value: object, label: str) -> Path:
    if not isinstance(value, Path):
        raise RuntimePaperCohortAttestationError(f"receipt {label} must be a path")
    return value


def _validated_cohort_kind(value: object) -> str:
    if not isinstance(value, str) or value not in _COHORT_KINDS:
        raise RuntimePaperCohortAttestationError("receipt cohort kind is invalid")
    return value


def _relative_database_path(database_path: Path, storage_root: Path) -> str:
    try:
        relative = database_path.resolve(strict=False).relative_to(
            storage_root.resolve(strict=False)
        )
    except ValueError as exc:
        raise RuntimePaperCohortAttestationError(
            "receipt database path must be relative to the storage root"
        ) from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimePaperCohortAttestationError(
            "receipt database path must be a safe relative path"
        )
    return relative.as_posix()


def _validated_attestation(
    receipt: RuntimePaperCohortAttestation,
) -> RuntimePaperCohortAttestation:
    if not isinstance(receipt, RuntimePaperCohortAttestation):
        raise RuntimePaperCohortAttestationError("receipt has an invalid type")
    if (
        type(receipt.schema_version) is not int
        or receipt.schema_version != RUNTIME_PAPER_COHORT_ATTESTATION_SCHEMA_VERSION
    ):
        raise RuntimePaperCohortAttestationError("receipt schema version is unsupported")
    _validated_pid(receipt.pid)
    _validated_started_utc(receipt.started_utc)
    if not isinstance(receipt.cohort_id, str) or not _COHORT_ID_PATTERN.fullmatch(
        receipt.cohort_id
    ):
        raise RuntimePaperCohortAttestationError("receipt cohort ID is invalid")
    _validated_cohort_kind(receipt.cohort_kind)
    if not isinstance(receipt.db_path_relative_to_storage_root, str):
        raise RuntimePaperCohortAttestationError(
            "receipt database path must be a safe relative path"
        )
    relative = Path(receipt.db_path_relative_to_storage_root)
    if (
        not receipt.db_path_relative_to_storage_root
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise RuntimePaperCohortAttestationError(
            "receipt database path must be a safe relative path"
        )
    if type(receipt.manifest_bound) is not bool:
        raise RuntimePaperCohortAttestationError("receipt manifest-bound flag is invalid")
    if receipt.manifest_bound:
        if not isinstance(receipt.cohort_identity, str) or not _IDENTITY_PATTERN.fullmatch(
            receipt.cohort_identity
        ):
            raise RuntimePaperCohortAttestationError("receipt cohort identity is invalid")
        if not isinstance(receipt.manifest_sha256, str) or not _SHA256_PATTERN.fullmatch(
            receipt.manifest_sha256
        ):
            raise RuntimePaperCohortAttestationError("receipt manifest SHA-256 is invalid")
    elif receipt.cohort_kind != "legacy":
        raise RuntimePaperCohortAttestationError(
            "non-legacy receipt must be manifest-bound"
        )
    elif receipt.cohort_identity is not None or receipt.manifest_sha256 is not None:
        raise RuntimePaperCohortAttestationError(
            "legacy receipt must not include manifest identity"
        )
    return receipt


def _ensure_safe_parent(parent: Path) -> None:
    _reject_symlink_ancestors(parent)
    parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(parent)
    try:
        mode = parent.lstat().st_mode
    except OSError as exc:
        raise RuntimePaperCohortAttestationError(
            "receipt parent directory is unavailable"
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise RuntimePaperCohortAttestationError(
            "receipt parent directory must be a real directory"
        )


def _reject_unsafe_existing_target(path: Path) -> None:
    _reject_symlink_ancestors(path)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimePaperCohortAttestationError("receipt path is unavailable") from exc
    if stat.S_ISLNK(mode):
        raise RuntimePaperCohortAttestationError("receipt path must not be a symlink")
    if not stat.S_ISREG(mode):
        raise RuntimePaperCohortAttestationError("receipt path must be a regular file")


def _reject_symlink_ancestors(path: Path) -> None:
    """Reject a path whose existing lexical component is a symlink."""

    current = Path(path)
    while True:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimePaperCohortAttestationError(
                "receipt path ancestor is unavailable"
            ) from exc
        else:
            if stat.S_ISLNK(mode):
                raise RuntimePaperCohortAttestationError(
                    "receipt path must not contain a symlink"
                )
        if current.parent == current:
            return
        current = current.parent


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("failed to write receipt")
        offset += written


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RuntimePaperCohortAttestationError("receipt cannot be read safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimePaperCohortAttestationError("receipt path must be a regular file")
        chunks: list[bytes] = []
        remaining = _MAX_RECEIPT_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(fd)
    if len(payload) > _MAX_RECEIPT_BYTES:
        raise RuntimePaperCohortAttestationError("receipt is too large")
    return payload


def _decode_payload(raw: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise RuntimePaperCohortAttestationError(
                    f"receipt contains duplicate key: {key}"
                )
            payload[key] = value
        return payload

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimePaperCohortAttestationError("receipt JSON is malformed") from exc
    if not isinstance(payload, dict):
        raise RuntimePaperCohortAttestationError("receipt JSON must be an object")
    return payload


def _attestation_from_payload(
    payload: dict[str, object],
) -> RuntimePaperCohortAttestation:
    expected_keys = {
        "schema_version",
        "pid",
        "started_utc",
        "cohort_id",
        "cohort_kind",
        "db_path_relative_to_storage_root",
        "manifest_bound",
        "cohort_identity",
        "manifest_sha256",
    }
    if set(payload) != expected_keys:
        raise RuntimePaperCohortAttestationError("receipt fields are invalid")
    raw_started = payload["started_utc"]
    if not isinstance(raw_started, str):
        raise RuntimePaperCohortAttestationError("receipt start time is invalid")
    try:
        started_utc = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimePaperCohortAttestationError("receipt start time is invalid") from exc
    return RuntimePaperCohortAttestation(
        schema_version=payload["schema_version"],
        pid=payload["pid"],
        started_utc=started_utc,
        cohort_id=payload["cohort_id"],
        cohort_kind=payload["cohort_kind"],
        db_path_relative_to_storage_root=payload["db_path_relative_to_storage_root"],
        manifest_bound=payload["manifest_bound"],
        cohort_identity=payload["cohort_identity"],
        manifest_sha256=payload["manifest_sha256"],
    )
