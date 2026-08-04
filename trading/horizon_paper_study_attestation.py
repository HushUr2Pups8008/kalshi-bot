"""Path-scoped runtime attestation for the isolated horizon paper study."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import uuid

from trading.horizon_paper_study_manifest import (
    HORIZON_PAPER_STUDY_KIND,
    HORIZON_PAPER_STUDY_MANIFEST_FILENAME,
    HORIZON_PAPER_STUDY_STORAGE_DIRNAME,
    HorizonPaperStudyManifest,
    HorizonPaperStudyManifestError,
)


_PRIMARY_SERVICE_LABEL = "com.jake.kalshi-bot"
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")


class HorizonStudyAttestationError(ValueError):
    """Raised when the study runtime attestation path or payload is unsafe."""


def write_runtime_attestation(
    *,
    manifest_path: Path | str,
    service_label: str,
    pid: int,
    started_at_utc: str,
    live_trading_enabled: bool,
) -> Path:
    manifest_path = Path(manifest_path).expanduser().resolve(strict=False)
    if manifest_path.name != HORIZON_PAPER_STUDY_MANIFEST_FILENAME:
        raise HorizonStudyAttestationError("study manifest path is invalid")
    try:
        data_root = manifest_path.parents[2]
    except IndexError as exc:
        raise HorizonStudyAttestationError("study manifest path is invalid") from exc
    try:
        manifest = _load_manifest(manifest_path)
    except HorizonPaperStudyManifestError as exc:
        raise HorizonStudyAttestationError("study manifest digest is invalid") from exc
    if manifest.study_kind != HORIZON_PAPER_STUDY_KIND:
        raise HorizonStudyAttestationError("study kind is invalid")
    expected_label = f"com.jake.horizon-paper-study.{manifest.study_id}"
    if service_label == _PRIMARY_SERVICE_LABEL or service_label != expected_label:
        raise HorizonStudyAttestationError("service label is invalid")
    if live_trading_enabled:
        raise HorizonStudyAttestationError("live trading is invalid")
    if manifest.ledger_path != "study_ledger.db" or Path(manifest.ledger_path).is_absolute():
        raise HorizonStudyAttestationError("ledger path is invalid")
    study_root = manifest_path.parent
    _reject_sqlite_sidecars(study_root / manifest.ledger_path)
    root = data_root.parent
    target = (
        root
        / "logs"
        / "state"
        / HORIZON_PAPER_STUDY_STORAGE_DIRNAME
        / manifest.study_id
        / "runtime_attestation.json"
    )
    _reject_symlink_aliases(
        study_id=manifest.study_id,
        primary_root=root,
    )
    primary_target = root / "logs" / "state" / "runtime_paper_cohort_attestation.json"
    if target == primary_target:
        raise HorizonStudyAttestationError("attestation path is invalid")
    _ensure_safe_directory(target.parent)
    payload = {
        "schema_version": 1,
        "study_id": manifest.study_id,
        "study_kind": manifest.study_kind,
        "service_label": service_label,
        "pid": _validate_pid(pid),
        "started_at_utc": _validate_started_at(started_at_utc),
        "ledger_path_relative_to_study_root": manifest.ledger_path,
        "database_identity": manifest.database_identity,
        "manifest_sha256": manifest.manifest_sha256,
        "live_trading_enabled": False,
    }
    encoded = (_canonical_json(payload) + "\n").encode("utf-8")
    temp = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    fd: int | None = None
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        _write_all(fd, encoded)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temp, target)
        _fsync_directory(target.parent)
    except OSError as exc:
        raise HorizonStudyAttestationError("attestation write failed") from exc
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return target


def _load_manifest(manifest_path: Path) -> HorizonPaperStudyManifest:
    raw = manifest_path.read_bytes()
    text = raw.decode("utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict) or _canonical_json(payload) != text:
        raise HorizonPaperStudyManifestError("horizon paper study manifest is invalid")
    return HorizonPaperStudyManifest.from_dict(payload)


def _reject_symlink_aliases(*, study_id: str, primary_root: Path) -> None:
    candidate_roots = [primary_root]
    parent_root = primary_root.parent
    if parent_root not in candidate_roots:
        candidate_roots.append(parent_root)
    for root in candidate_roots:
        candidate = (
            root
            / "logs"
            / "state"
            / HORIZON_PAPER_STUDY_STORAGE_DIRNAME
            / study_id
        )
        if not candidate.exists() and not candidate.is_symlink():
            continue
        try:
            stat_result = candidate.lstat()
        except OSError as exc:
            raise HorizonStudyAttestationError("attestation path is invalid") from exc
        if stat.S_ISLNK(stat_result.st_mode):
            raise HorizonStudyAttestationError("attestation path symlink is invalid")


def _validate_pid(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise HorizonStudyAttestationError("pid is invalid")
    return value


def _validate_started_at(value: object) -> str:
    if not isinstance(value, str):
        raise HorizonStudyAttestationError("started_at_utc is invalid")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _ensure_safe_directory(path: Path) -> None:
    parents = list(path.parents)[::-1] + [path]
    for candidate in parents:
        if candidate.exists():
            try:
                stat_result = candidate.lstat()
            except OSError as exc:
                raise HorizonStudyAttestationError("attestation path is invalid") from exc
            if stat.S_ISLNK(stat_result.st_mode):
                raise HorizonStudyAttestationError("attestation path symlink is invalid")
            if not stat.S_ISDIR(stat_result.st_mode):
                raise HorizonStudyAttestationError("attestation path is invalid")
    path.mkdir(parents=True, exist_ok=True)
    for candidate in list(path.parents)[::-1] + [path]:
        try:
            stat_result = candidate.lstat()
        except OSError as exc:
            raise HorizonStudyAttestationError("attestation path is invalid") from exc
        if stat.S_ISLNK(stat_result.st_mode):
            raise HorizonStudyAttestationError("attestation path symlink is invalid")


def _reject_sqlite_sidecars(path: Path) -> None:
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() or sidecar.is_symlink():
            raise HorizonStudyAttestationError("attestation sidecar is invalid")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("attestation write was incomplete")
        offset += written


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
