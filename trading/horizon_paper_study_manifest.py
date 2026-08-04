"""Immutable, isolated manifest contract for the Polymarket horizon paper study."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping
from urllib.parse import quote


HORIZON_PAPER_STUDY_KIND = "polymarket_horizon_15_30"
HORIZON_PAPER_STUDY_STORAGE_DIRNAME = "horizon_paper_studies"
HORIZON_PAPER_STUDY_MANIFEST_FILENAME = "manifest.json"
HORIZON_PAPER_STUDY_LIVE_TRANSITION_BLOCK = (
    "horizon paper study remains permanently isolated from live trading"
)
HORIZON_PAPER_STUDY_INVALID_LIVE_TRANSITION_BLOCK = (
    "invalid horizon paper study manifest blocks live trading"
)

_SCHEMA_VERSION = 1
_STUDY_ID_PATTERN = re.compile(r"^pm-horizon-15-30-([0-9]{8})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PRIMARY_LAUNCHD_LABEL = "com.jake.kalshi-bot"
_STUDY_LAUNCHD_LABEL = "com.jake.kalshi-horizon-paper-study"
_PRIMARY_LOG_FILENAMES = ("bot.log", "bot.error.log")
_STUDY_LOG_FILENAMES = (
    "horizon-paper-study.log",
    "horizon-paper-study.error.log",
)
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")
_BOOTSTRAP_METADATA_TABLE = "horizon_study_bootstrap"
_BOOTSTRAP_SCHEMA_VERSION = 1
_LEDGER_APPLICATION_ID = 0x48504C47
_STATE_APPLICATION_ID = 0x48505354
_BOOTSTRAP_METADATA_COLUMNS = (
    "singleton",
    "bootstrap_schema_version",
    "database_role",
    "study_id",
    "study_kind",
    "database_identity",
    "manifest_preimage_sha256",
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "study_id",
        "study_kind",
        "venue",
        "created_at_utc",
        "ledger_path",
        "state_db_path",
        "database_identity",
        "starting_bankroll",
        "horizon_lower_exclusive_days",
        "horizon_upper_inclusive_days",
        "policy_snapshot_sha256",
        "fee_schedule_sha256",
        "paper_execution_mode",
        "live_order_forbidden",
        "profit_receipt_attested",
        "manifest_sha256",
    }
)
_MANIFEST_CONFIGURATION_FIELDS = _MANIFEST_FIELDS - {
    "database_identity",
    "manifest_sha256",
}


class HorizonPaperStudyManifestError(ValueError):
    """The isolated study manifest or its storage topology is unsafe."""


@dataclass(frozen=True)
class HorizonPaperStudyManifest:
    """Canonical, self-hashed immutable inputs for one isolated study."""

    schema_version: int
    study_id: str
    study_kind: str
    venue: str
    created_at_utc: str
    ledger_path: str
    state_db_path: str
    database_identity: str
    starting_bankroll: str
    horizon_lower_exclusive_days: float
    horizon_upper_inclusive_days: float
    policy_snapshot_sha256: str
    fee_schedule_sha256: str
    paper_execution_mode: str
    live_order_forbidden: bool
    profit_receipt_attested: bool
    manifest_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != _SCHEMA_VERSION:
            _invalid("schema_version")
        _require_study_id(self.study_id)
        if self.study_kind != HORIZON_PAPER_STUDY_KIND:
            _invalid("study_kind")
        if self.venue != "polymarket_us":
            _invalid("venue")
        _require_canonical_utc_datetime(self.created_at_utc, "created_at_utc")
        if self.ledger_path != "study_ledger.db":
            _invalid("ledger_path")
        if self.state_db_path != "study_state.db":
            _invalid("state_db_path")
        _require_sha256(self.database_identity, "database_identity")
        if self.database_identity != derive_horizon_paper_study_database_identity(
            self._manifest_configuration()
        ):
            _invalid("database_identity")
        _require_positive_decimal(self.starting_bankroll, "starting_bankroll")
        _require_exact_horizons(
            self.horizon_lower_exclusive_days,
            self.horizon_upper_inclusive_days,
        )
        _require_sha256(self.policy_snapshot_sha256, "policy_snapshot_sha256")
        _require_sha256(self.fee_schedule_sha256, "fee_schedule_sha256")
        if self.paper_execution_mode != "isolated_paper_only":
            _invalid("paper_execution_mode")
        if self.live_order_forbidden is not True:
            _invalid("live_order_forbidden")
        if self.profit_receipt_attested is not False:
            _invalid("profit_receipt_attested")
        _require_sha256(self.manifest_sha256, "manifest_sha256")
        if self.manifest_sha256 != _sha256_canonical_json(self._payload_without_digest()):
            _invalid("manifest_sha256")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HorizonPaperStudyManifest":
        if not isinstance(value, Mapping) or set(value) != _MANIFEST_FIELDS:
            _invalid("manifest payload")
        return cls(
            schema_version=value["schema_version"],
            study_id=value["study_id"],
            study_kind=value["study_kind"],
            venue=value["venue"],
            created_at_utc=value["created_at_utc"],
            ledger_path=value["ledger_path"],
            state_db_path=value["state_db_path"],
            database_identity=value["database_identity"],
            starting_bankroll=value["starting_bankroll"],
            horizon_lower_exclusive_days=value["horizon_lower_exclusive_days"],
            horizon_upper_inclusive_days=value["horizon_upper_inclusive_days"],
            policy_snapshot_sha256=value["policy_snapshot_sha256"],
            fee_schedule_sha256=value["fee_schedule_sha256"],
            paper_execution_mode=value["paper_execution_mode"],
            live_order_forbidden=value["live_order_forbidden"],
            profit_receipt_attested=value["profit_receipt_attested"],
            manifest_sha256=value["manifest_sha256"],
        )

    def study_root(self, data_root: Path | str) -> Path:
        return Path(data_root) / HORIZON_PAPER_STUDY_STORAGE_DIRNAME / self.study_id

    def to_dict(self) -> dict[str, object]:
        return {
            **self._payload_without_digest(),
            "manifest_sha256": self.manifest_sha256,
        }

    def _payload_without_digest(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "study_kind": self.study_kind,
            "venue": self.venue,
            "created_at_utc": self.created_at_utc,
            "ledger_path": self.ledger_path,
            "state_db_path": self.state_db_path,
            "database_identity": self.database_identity,
            "starting_bankroll": self.starting_bankroll,
            "horizon_lower_exclusive_days": self.horizon_lower_exclusive_days,
            "horizon_upper_inclusive_days": self.horizon_upper_inclusive_days,
            "policy_snapshot_sha256": self.policy_snapshot_sha256,
            "fee_schedule_sha256": self.fee_schedule_sha256,
            "paper_execution_mode": self.paper_execution_mode,
            "live_order_forbidden": self.live_order_forbidden,
            "profit_receipt_attested": self.profit_receipt_attested,
        }

    def _manifest_configuration(self) -> dict[str, object]:
        payload = self._payload_without_digest()
        payload.pop("database_identity")
        return payload


def derive_horizon_paper_study_database_identity(
    manifest_configuration: Mapping[str, object],
) -> str:
    """Derive the immutable identity shared by the initialized study databases."""

    if (
        not isinstance(manifest_configuration, Mapping)
        or set(manifest_configuration) != _MANIFEST_CONFIGURATION_FIELDS
    ):
        _invalid("database identity configuration")
    configuration_sha256 = _sha256_canonical_json(dict(manifest_configuration))
    return _sha256_canonical_json(
        {
            "bootstrap_metadata_columns": _BOOTSTRAP_METADATA_COLUMNS,
            "bootstrap_metadata_table": _BOOTSTRAP_METADATA_TABLE,
            "bootstrap_schema_version": _BOOTSTRAP_SCHEMA_VERSION,
            "ledger_application_id": _LEDGER_APPLICATION_ID,
            "state_application_id": _STATE_APPLICATION_ID,
            "study_configuration_sha256": configuration_sha256,
        }
    )


def validate_horizon_paper_study_manifest(
    manifest_path: Path | str,
    *,
    data_root: Path | str,
    verify_runtime_targets: bool = True,
) -> HorizonPaperStudyManifest:
    """Read and validate one manifest without opening either study database."""

    storage_root = _absolute_path(data_root)
    candidate = _absolute_path(manifest_path)
    _assert_descends_from(candidate, storage_root, "manifest path")
    _assert_no_symlinks_from(storage_root, candidate, "manifest path")
    if candidate.name != HORIZON_PAPER_STUDY_MANIFEST_FILENAME:
        _invalid("manifest path")
    raw = _read_regular_file(candidate, "manifest", required_mode=0o600)
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HorizonPaperStudyManifestError("horizon paper study manifest is invalid") from exc
    if not isinstance(payload, dict) or _canonical_json(payload) != text:
        _invalid("manifest canonical JSON")
    manifest = HorizonPaperStudyManifest.from_dict(payload)
    expected_root = _absolute_path(manifest.study_root(storage_root))
    if candidate.parent != expected_root:
        _invalid("manifest study root")
    _assert_no_symlinks_from(storage_root, expected_root, "study root")
    _validate_snapshot(
        expected_root / "policy_snapshot.json",
        manifest.policy_snapshot_sha256,
        storage_root=storage_root,
        label="policy snapshot",
    )
    _validate_snapshot(
        expected_root / "fee_schedule.json",
        manifest.fee_schedule_sha256,
        storage_root=storage_root,
        label="fee schedule",
    )
    if verify_runtime_targets:
        _validate_study_database(
            expected_root / manifest.ledger_path,
            storage_root=storage_root,
            label="study ledger",
            manifest=manifest,
            role="ledger",
        )
        _validate_study_database(
            expected_root / manifest.state_db_path,
            storage_root=storage_root,
            label="study state",
            manifest=manifest,
            role="state",
        )
    return manifest


def validate_study_coexistence(
    manifest: HorizonPaperStudyManifest,
    *,
    data_root: Path | str,
    logs_root: Path | str | None = None,
    primary_launchd_label: str = _PRIMARY_LAUNCHD_LABEL,
    study_launchd_label: str = _STUDY_LAUNCHD_LABEL,
) -> None:
    """Prove a planned study has no state or path overlap before initialization."""

    if not isinstance(manifest, HorizonPaperStudyManifest):
        _invalid("study manifest")
    storage_root = _absolute_path(data_root)
    expected_logs_root = _absolute_path(storage_root.parent / "logs")
    logs = _absolute_path(logs_root) if logs_root is not None else expected_logs_root
    if logs != expected_logs_root:
        _invalid("study logs root")
    if primary_launchd_label != _PRIMARY_LAUNCHD_LABEL:
        _invalid("primary launchd label")
    if study_launchd_label != _STUDY_LAUNCHD_LABEL:
        _invalid("study launchd label")
    if study_launchd_label == primary_launchd_label:
        _invalid("study launchd label")

    study_root = _absolute_path(manifest.study_root(storage_root))
    _assert_no_symlinks_from(storage_root, study_root, "study root")
    if _path_exists_or_symlink(study_root):
        _invalid("study root already exists")

    study_state_receipt = (
        logs / "state" / HORIZON_PAPER_STUDY_STORAGE_DIRNAME / manifest.study_id / "runtime_attestation.json"
    )
    study_logs = tuple(logs / "app" / name for name in _STUDY_LOG_FILENAMES)
    study_paths = (
        study_root,
        study_root / manifest.ledger_path,
        study_root / manifest.state_db_path,
        study_root / "artifacts",
        study_root / "reports",
        study_root / "locks" / "runtime.lock",
        study_state_receipt,
        *study_logs,
    )
    protected_primary_paths = (
        storage_root / "paper_trades.db",
        storage_root / "paper_cohorts",
        storage_root / "legacy_pending_paper_cohorts",
        storage_root / "bot_runtime.lock",
        logs / "trades" / "live" / "trades.jsonl",
        logs / "state" / "runtime_paper_cohort_attestation.json",
        *(logs / "app" / name for name in _PRIMARY_LOG_FILENAMES),
    )
    for path in study_paths:
        root = storage_root if _is_below(path, storage_root) else logs
        _assert_no_symlinks_from(root, path, "study target")
        if _path_exists_or_symlink(path):
            _invalid("study target already exists")
        for protected in protected_primary_paths:
            if _paths_overlap(path, protected):
                _invalid("study target overlaps primary state")


def discover_polymarket_horizon_15_30_manifest_blockers(
    data_root: Path | str,
) -> tuple[str, ...]:
    """Read only manifest and snapshot files to permanently block primary go-live."""

    try:
        storage_root = _absolute_path(data_root)
        study_parent = storage_root / HORIZON_PAPER_STUDY_STORAGE_DIRNAME
        if not _path_exists_or_symlink(study_parent):
            return ()
        if study_parent.is_symlink() or not study_parent.is_dir():
            return (HORIZON_PAPER_STUDY_INVALID_LIVE_TRANSITION_BLOCK,)
        _assert_no_symlinks_from(storage_root, study_parent, "study storage root")
        entries = tuple(sorted(study_parent.iterdir(), key=lambda path: path.name))
        if not entries:
            return ()
        if len(entries) != 1:
            return (HORIZON_PAPER_STUDY_INVALID_LIVE_TRANSITION_BLOCK,)
        study_root = entries[0]
        if study_root.is_symlink() or not study_root.is_dir():
            return (HORIZON_PAPER_STUDY_INVALID_LIVE_TRANSITION_BLOCK,)
        validate_horizon_paper_study_manifest(
            study_root / HORIZON_PAPER_STUDY_MANIFEST_FILENAME,
            data_root=storage_root,
            verify_runtime_targets=False,
        )
    except (OSError, HorizonPaperStudyManifestError, ValueError, TypeError):
        return (HORIZON_PAPER_STUDY_INVALID_LIVE_TRANSITION_BLOCK,)
    return (HORIZON_PAPER_STUDY_LIVE_TRANSITION_BLOCK,)


def _validate_snapshot(
    path: Path,
    expected_sha256: str,
    *,
    storage_root: Path,
    label: str,
) -> None:
    _assert_no_symlinks_from(storage_root, path, label)
    actual_sha256 = hashlib.sha256(_read_regular_file(path, label)).hexdigest()
    if actual_sha256 != expected_sha256:
        _invalid(label)


def _validate_database_target(path: Path, *, storage_root: Path, label: str) -> None:
    _assert_no_symlinks_from(storage_root, path, label)
    _require_regular_file(path, label)


def _validate_study_database(
    path: Path,
    *,
    storage_root: Path,
    label: str,
    manifest: HorizonPaperStudyManifest,
    role: str,
) -> None:
    _validate_database_target(path, storage_root=storage_root, label=label)
    _reject_sqlite_sidecars(path, label)
    expected_application_id = {
        "ledger": _LEDGER_APPLICATION_ID,
        "state": _STATE_APPLICATION_ID,
    }[role]
    before = _require_regular_file(path, label)
    try:
        connection = sqlite3.connect(_sqlite_read_only_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise HorizonPaperStudyManifestError(
            f"horizon paper study {label} is not an initialized SQLite database"
        ) from exc
    try:
        application_id_row = connection.execute("PRAGMA application_id").fetchone()
        if application_id_row != (expected_application_id,):
            _invalid(label)
        columns = tuple(
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({_BOOTSTRAP_METADATA_TABLE})"
            )
        )
        if columns != _BOOTSTRAP_METADATA_COLUMNS:
            _invalid(label)
        rows = connection.execute(
            f"""
            SELECT {", ".join(_BOOTSTRAP_METADATA_COLUMNS)}
            FROM {_BOOTSTRAP_METADATA_TABLE}
            ORDER BY singleton
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise HorizonPaperStudyManifestError(
            f"horizon paper study {label} bootstrap metadata is invalid"
        ) from exc
    finally:
        connection.close()
    expected_row = (
        1,
        _BOOTSTRAP_SCHEMA_VERSION,
        role,
        manifest.study_id,
        manifest.study_kind,
        manifest.database_identity,
        manifest.manifest_sha256,
    )
    if rows != [expected_row]:
        _invalid(label)
    after = _require_regular_file(path, label)
    _reject_sqlite_sidecars(path, label)
    if _stat_identity(before) != _stat_identity(after):
        _invalid(label)


def _reject_sqlite_sidecars(path: Path, label: str) -> None:
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        if _path_exists_or_symlink(Path(f"{path}{suffix}")):
            _invalid(label)


def _sqlite_read_only_uri(path: Path) -> str:
    return f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"


def _read_regular_file(
    path: Path,
    label: str,
    *,
    required_mode: int | None = None,
) -> bytes:
    before = _require_regular_file(path, label, required_mode=required_mode)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HorizonPaperStudyManifestError(
            f"horizon paper study {label} is unreadable"
        ) from exc
    after = _require_regular_file(path, label, required_mode=required_mode)
    if _stat_identity(before) != _stat_identity(after):
        _invalid(label)
    return data


def _require_regular_file(
    path: Path,
    label: str,
    *,
    required_mode: int | None = None,
) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise HorizonPaperStudyManifestError(
            f"horizon paper study {label} is missing"
        ) from exc
    if (
        stat.S_ISLNK(file_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_nlink != 1
    ):
        _invalid(label)
    if required_mode is not None and stat.S_IMODE(file_stat.st_mode) != required_mode:
        _invalid(label)
    return file_stat


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
    )


def _assert_no_symlinks_from(root: Path, target: Path, label: str) -> None:
    root_path = _absolute_path(root)
    target_path = _absolute_path(target)
    _assert_descends_from(target_path, root_path, label)
    current = root_path
    if _path_exists_or_symlink(current) and current.is_symlink():
        _invalid(label)
    relative = target_path.relative_to(root_path)
    for part in relative.parts:
        current = current / part
        if _path_exists_or_symlink(current) and current.is_symlink():
            _invalid(label)


def _assert_descends_from(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HorizonPaperStudyManifestError(
            f"horizon paper study {label} escapes its storage root"
        ) from exc
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise HorizonPaperStudyManifestError(
            f"horizon paper study {label} escapes its storage root"
        ) from exc


def _paths_overlap(left: Path, right: Path) -> bool:
    left_path = left.resolve(strict=False)
    right_path = right.resolve(strict=False)
    return _is_below(left_path, right_path) or _is_below(right_path, left_path)


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _path_exists_or_symlink(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise HorizonPaperStudyManifestError(
            "horizon paper study path is unavailable"
        ) from exc
    return True


def _absolute_path(value: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(Path(value).expanduser())))


def _require_study_id(value: object) -> str:
    text = _require_exact_string(value, "study_id")
    matched = _STUDY_ID_PATTERN.fullmatch(text)
    if matched is None:
        _invalid("study_id")
    try:
        datetime.strptime(matched.group(1), "%Y%m%d")
    except ValueError as exc:
        raise HorizonPaperStudyManifestError(
            "horizon paper study study_id is invalid"
        ) from exc
    return text


def _require_exact_horizons(lower: object, upper: object) -> None:
    if (
        isinstance(lower, bool)
        or isinstance(upper, bool)
        or not isinstance(lower, float)
        or not isinstance(upper, float)
        or not math.isfinite(lower)
        or not math.isfinite(upper)
        or lower != 14.0
        or upper != 30.0
    ):
        _invalid("horizon")


def _require_positive_decimal(value: object, name: str) -> Decimal:
    text = _require_exact_string(value, name)
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text) is None:
        _invalid(name)
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise HorizonPaperStudyManifestError(
            f"horizon paper study {name} is invalid"
        ) from exc
    if not parsed.is_finite() or parsed <= 0:
        _invalid(name)
    return parsed


def _require_canonical_utc_datetime(value: object, name: str) -> str:
    text = _require_exact_string(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HorizonPaperStudyManifestError(
            f"horizon paper study {name} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _invalid(name)
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if canonical != text:
        _invalid(name)
    return text


def _require_sha256(value: object, name: str) -> str:
    text = _require_exact_string(value, name)
    if _SHA256_PATTERN.fullmatch(text) is None:
        _invalid(name)
    return text


def _require_exact_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _invalid(name)
    return value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise HorizonPaperStudyManifestError(
            "horizon paper study manifest is invalid"
        ) from exc


def _sha256_canonical_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _invalid(name: str) -> None:
    raise HorizonPaperStudyManifestError(f"horizon paper study {name} is invalid")
