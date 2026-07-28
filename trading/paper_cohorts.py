"""Immutable paper-cohort identity and read-only all-cohort risk helpers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import sys
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from config import MAX_MARKET_DAYS_TO_EXPIRY
from utils.output_paths import DB_STATE_DIR


LEGACY_PAPER_COHORT_ID = "legacy"
_COHORT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_IDENTITY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ACTIVE_COHORTS_DIRNAME = "paper_cohorts"
_MANIFEST_FILENAME = "cohort.json"
_LEGACY_SNAPSHOT_FILENAME = "legacy_cutover.db"
_MANIFEST_SCHEMA_VERSION = 4
_IDENTITY_TABLE = "paper_cohort_identity"
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_IDENTITY_PROVISIONED = "provisioned"
_IDENTITY_INITIALIZED = "initialized"
_LEGACY_BASELINE_VERIFICATION = "operator_attested_unverified"


@dataclass(frozen=True)
class PaperCohort:
    """One paper account and its immutable accounting boundary."""

    cohort_id: str
    db_path: Path
    starting_bankroll: float
    writable: bool
    storage_root: Path


@dataclass(frozen=True)
class ActivePaperCohortBinding:
    """Manifest and database identity for one provisioned active cohort."""

    cohort: PaperCohort
    cohort_identity: str
    manifest_sha256: str
    max_days_to_close: float
    legacy_db_path: Path
    legacy_snapshot_path: Path
    legacy_starting_bankroll: float
    legacy_baseline_attestation: str
    legacy_baseline_verification: str
    legacy_snapshot_sha256: str


@dataclass(frozen=True)
class _ActiveDatabaseIdentity:
    cohort_id: str
    cohort_identity: str
    manifest_sha256: str
    initialization_state: str
    initialized_table_names: tuple[str, ...] | None


@dataclass(frozen=True)
class CohortOpenExposure:
    """Read-only mark and accounting state for a single cohort."""

    cohort_id: str
    db_path: Path
    configured_bankroll: float
    notional_bankroll: float
    marked_value: float
    total_entry_cost: float | None
    unknown_entry_cost: float | None
    priced_count: int
    unpriced_count: int
    snapshot_fallback_count: int
    unresolved_trade_count: int
    valuation_as_of: str | None

    @property
    def mark_to_market_equity(self) -> float:
        return self.notional_bankroll + self.marked_value

    @property
    def drawdown_pct(self) -> float:
        return max(
            0.0,
            (self.configured_bankroll - self.mark_to_market_equity)
            / self.configured_bankroll,
        )


@dataclass(frozen=True)
class AggregateOpenExposureSnapshot:
    """Aggregate of every configured cohort, or a fail-closed status."""

    ok: bool
    failure_status: str
    configured_bankroll: float | None
    notional_bankroll: float | None
    marked_value: float | None
    total_entry_cost: float | None
    unknown_entry_cost: float | None
    priced_count: int | None
    unpriced_count: int | None
    snapshot_fallback_count: int | None
    unresolved_trade_count: int | None
    cohorts: tuple[CohortOpenExposure, ...]

    @property
    def drawdown_pct(self) -> float | None:
        """Aggregate drawdown is diagnostic only; never use it as a live gate."""

        if not self.ok:
            return None
        assert self.configured_bankroll is not None
        assert self.notional_bankroll is not None
        assert self.marked_value is not None
        return max(
            0.0,
            (self.configured_bankroll - (self.notional_bankroll + self.marked_value))
            / self.configured_bankroll,
        )


def resolve_runtime_paper_cohort(
    cohort_id: str,
    *,
    legacy_starting_bankroll: float | None,
    active_starting_bankroll: float | None,
    db_root: Path = DB_STATE_DIR,
) -> PaperCohort:
    """Resolve the one writable runtime cohort without opening or creating state."""

    normalized_id = _validate_cohort_id(cohort_id)
    root = Path(db_root)
    if normalized_id == LEGACY_PAPER_COHORT_ID:
        legacy_bankroll = _positive_finite_bankroll(
            legacy_starting_bankroll,
            label="legacy starting bankroll",
        )
        return PaperCohort(
            cohort_id=LEGACY_PAPER_COHORT_ID,
            db_path=root / "paper_trades.db",
            starting_bankroll=legacy_bankroll,
            writable=True,
            storage_root=root,
        )

    active_bankroll = _positive_finite_bankroll(
        active_starting_bankroll,
        label="active starting bankroll",
    )
    return PaperCohort(
        cohort_id=normalized_id,
        db_path=root / _ACTIVE_COHORTS_DIRNAME / normalized_id / "paper_trades.db",
        starting_bankroll=active_bankroll,
        writable=True,
        storage_root=root,
    )


def risk_cohorts_for_runtime(
    runtime_cohort: PaperCohort,
    *,
    legacy_starting_bankroll: float,
) -> tuple[PaperCohort, ...]:
    """Compatibility helper; live readiness must use discovery instead."""

    if runtime_cohort.cohort_id == LEGACY_PAPER_COHORT_ID:
        return (runtime_cohort,)
    legacy = PaperCohort(
        cohort_id=LEGACY_PAPER_COHORT_ID,
        db_path=runtime_cohort.storage_root / "paper_trades.db",
        starting_bankroll=_positive_finite_bankroll(
            legacy_starting_bankroll,
            label="legacy starting bankroll",
        ),
        writable=False,
        storage_root=runtime_cohort.storage_root,
    )
    return (legacy, runtime_cohort)


def initialize_active_paper_cohort_manifest(
    cohort: PaperCohort,
    *,
    max_days_to_close: float,
    legacy_db_path: Path,
    legacy_starting_bankroll: float,
) -> Path:
    """Atomically publish a cohort with an immutable, quiesced legacy snapshot."""

    _require_active_cohort(cohort)
    horizon = _active_horizon(max_days_to_close)
    legacy_bankroll = _positive_finite_bankroll(
        legacy_starting_bankroll,
        label="legacy starting bankroll",
    )
    legacy_path = _validate_legacy_db_path(legacy_db_path, cohort.storage_root)

    cohort_dir = cohort.db_path.parent
    cohorts_root = cohort_dir.parent
    cohorts_root.mkdir(parents=True, exist_ok=True)
    if cohorts_root.is_symlink() or not cohorts_root.is_dir():
        raise ValueError("active cohort root is not a directory")

    # The root legacy account can have legitimate post-cutover settlement updates.
    # Bind this cohort to a copied, hash-verified cutover artifact instead of its
    # mutable source DB. The shared runtime lock establishes a quiescent snapshot.
    with _runtime_lock(cohort.storage_root):
        if cohort_dir.exists():
            raise FileExistsError("active cohort directory already exists; refusing to adopt state")
        existing_cohorts = discover_paper_risk_cohorts(cohort.storage_root)
        if existing_cohorts and existing_cohorts[0].starting_bankroll != legacy_bankroll:
            raise ValueError("active cohorts disagree on immutable legacy starting bankroll")
        _database_file_stat(legacy_path, label="legacy database")
        legacy_source_sha256 = _verified_sqlite_sha256(legacy_path)
        if _legacy_unresolved_trade_count(legacy_path):
            raise ValueError(
                "legacy cutover requires zero unresolved paper trades; "
                "reconcile legacy before provisioning"
            )
        cohort_identity = uuid.uuid4().hex
        staging_dir = cohorts_root / f".{cohort.cohort_id}-{cohort_identity}.provisioning"
        published = False
        try:
            os.mkdir(staging_dir, 0o700)
            staged_snapshot_path = staging_dir / _LEGACY_SNAPSHOT_FILENAME
            _copy_verified_sqlite_snapshot(
                legacy_path,
                staged_snapshot_path,
                expected_sha256=legacy_source_sha256,
            )
            # Cooperative writers share bot_runtime.lock. A second hash check
            # turns an out-of-contract writer into a failed provisioning, never
            # a misbound manifest.
            if _verified_sqlite_sha256(legacy_path) != legacy_source_sha256:
                raise RuntimeError("legacy database changed during cutover provisioning")
            payload = _manifest_payload(
                cohort,
                cohort_identity=cohort_identity,
                max_days_to_close=horizon,
                legacy_db_path=legacy_path,
                legacy_snapshot_path=cohort_dir / _LEGACY_SNAPSHOT_FILENAME,
                legacy_starting_bankroll=legacy_bankroll,
                legacy_source_sha256=legacy_source_sha256,
                legacy_snapshot_sha256=legacy_source_sha256,
            )
            encoded = _encode_manifest(payload)
            manifest_sha256 = hashlib.sha256(encoded).hexdigest()
            staged_db_path = staging_dir / cohort.db_path.name
            _bootstrap_identity_database(
                staged_db_path,
                cohort_id=cohort.cohort_id,
                cohort_identity=cohort_identity,
                manifest_sha256=manifest_sha256,
            )
            _assert_distinct_cohort_database_files(
                (
                    ("legacy database", legacy_path),
                    ("active cohort database", staged_db_path),
                    ("legacy cutover snapshot", staged_snapshot_path),
                )
            )
            _write_new_file(staging_dir / _MANIFEST_FILENAME, encoded)
            _fsync_directory(staging_dir)
            os.rename(staging_dir, cohort_dir)
            published = True
            _fsync_directory(cohorts_root)
        except BaseException:
            if not published:
                _remove_staging_directory(staging_dir)
            raise
    return cohort_dir / _MANIFEST_FILENAME


def validate_active_paper_cohort_manifest(
    cohort: PaperCohort,
    *,
    max_days_to_close: float,
    legacy_db_path: Path,
    legacy_starting_bankroll: float | None = None,
) -> ActivePaperCohortBinding:
    """Validate selected runtime config, immutable cutover snapshot, and DB identity."""

    _require_active_cohort(cohort)
    binding = _load_active_binding(_manifest_path_for(cohort), cohort.storage_root)
    expected_legacy_path = _validate_legacy_db_path(legacy_db_path, cohort.storage_root)
    # Current legacy risk remains independently checked. Its hash is deliberately
    # not compared to the cutover snapshot because terminal legacy settlement can
    # legitimately update the live predecessor after cutover.
    _verified_sqlite_sha256(expected_legacy_path)
    expected = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "cohort_id": cohort.cohort_id,
        "db_path_relative_to_storage_root": _relative_to_storage_root(
            cohort.db_path,
            cohort.storage_root,
        ),
        "starting_bankroll": cohort.starting_bankroll,
        "max_days_to_close": _active_horizon(max_days_to_close),
        "legacy_db_path_relative_to_storage_root": _relative_to_storage_root(
            expected_legacy_path,
            cohort.storage_root,
        ),
    }
    if legacy_starting_bankroll is not None:
        expected["legacy_starting_bankroll"] = _positive_finite_bankroll(
            legacy_starting_bankroll,
            label="legacy starting bankroll",
        )
    actual = _read_manifest_payload(_manifest_path_for(cohort))
    for key, expected_value in expected.items():
        if actual.get(key) != expected_value:
            label = "horizon" if key == "max_days_to_close" else key
            raise ValueError(f"active cohort manifest {label} does not match runtime")
    _validate_active_database_identity(binding)
    return binding


def active_cohort_binding_for_db(
    db_path: Path,
    *,
    cohort_id: str | None = None,
) -> ActivePaperCohortBinding | None:
    """Resolve an active DB from its manifest and refuse unbound standard paths."""

    path = Path(db_path)
    manifest_path = path.parent / _MANIFEST_FILENAME
    if manifest_path.is_file():
        storage_root = _storage_root_for_active_db(path)
        binding = _load_active_binding(manifest_path, storage_root)
        if _absolute_path(binding.cohort.db_path) != _absolute_path(path):
            raise ValueError("active cohort manifest database path does not match runtime")
        if cohort_id is not None and binding.cohort.cohort_id != _validate_cohort_id(cohort_id):
            raise ValueError("active cohort identity does not match runtime cohort")
        _validate_active_database_identity(binding)
        # A direct PaperTrader construction must not bypass all-cohort cutover
        # reconciliation by validating only its adjacent manifest.
        discover_paper_risk_cohorts(storage_root)
        return binding
    if _active_database_identity_for_path(path) is not None:
        raise ValueError("orphaned active cohort identity database")
    if _ACTIVE_COHORTS_DIRNAME in path.parts:
        raise ValueError("active cohort database manifest missing")
    if cohort_id is not None and _validate_cohort_id(cohort_id) != LEGACY_PAPER_COHORT_ID:
        raise ValueError("active cohort database manifest missing")
    return None


def discover_paper_risk_cohorts(db_root: Path = DB_STATE_DIR) -> tuple[PaperCohort, ...]:
    """Discover every provisioned active cohort and its immutable legacy baseline.

    This is intentionally independent of ``PAPER_COHORT_ID``. A cohort cannot be
    hidden from a future live-readiness check by selecting a different runtime DB.
    """

    storage_root = Path(db_root)
    cohorts_root = storage_root / _ACTIVE_COHORTS_DIRNAME
    if cohorts_root.is_symlink():
        raise ValueError("active cohort root must not be a symlink")
    if not cohorts_root.exists():
        return ()
    if not cohorts_root.is_dir():
        raise ValueError("active cohort root is not a directory")

    bindings: list[ActivePaperCohortBinding] = []
    for entry in sorted(cohorts_root.iterdir(), key=lambda item: item.name):
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError("active cohort root contains an unknown file")
        manifest_path = entry / _MANIFEST_FILENAME
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError(f"active cohort manifest missing: {manifest_path}")
        binding = _load_active_binding(manifest_path, storage_root)
        if binding.cohort.db_path.parent.resolve() != entry.resolve():
            raise ValueError("active cohort manifest is outside its provisioned directory")
        _validate_active_database_identity(binding)
        bindings.append(binding)

    if not bindings:
        return ()
    _assert_distinct_cohort_database_files(_all_bound_database_files(bindings))
    baseline_values = {binding.legacy_starting_bankroll for binding in bindings}
    if len(baseline_values) != 1:
        raise ValueError("active cohorts disagree on immutable legacy starting bankroll")
    attestation_values = {binding.legacy_baseline_attestation for binding in bindings}
    if len(attestation_values) != 1:
        raise ValueError("active cohorts disagree on legacy baseline attestation")
    snapshot_hashes = {binding.legacy_snapshot_sha256 for binding in bindings}
    if len(snapshot_hashes) != 1:
        raise ValueError("active cohorts require explicit legacy reconciliation")
    legacy_snapshot_path = bindings[0].legacy_snapshot_path
    legacy_paths = {binding.legacy_db_path.resolve() for binding in bindings}
    if len(legacy_paths) != 1:
        raise ValueError("active cohorts disagree on immutable legacy database path")
    legacy_path = next(iter(legacy_paths))
    if not legacy_path.is_file():
        raise ValueError("legacy database missing")
    if _verified_sqlite_sha256(legacy_path) != next(iter(snapshot_hashes)):
        raise ValueError("legacy database diverged from immutable cutover snapshot")
    legacy = PaperCohort(
        cohort_id=LEGACY_PAPER_COHORT_ID,
        db_path=legacy_snapshot_path,
        starting_bankroll=next(iter(baseline_values)),
        writable=False,
        storage_root=storage_root,
    )
    active = tuple(
        PaperCohort(
            cohort_id=binding.cohort.cohort_id,
            db_path=binding.cohort.db_path,
            starting_bankroll=binding.cohort.starting_bankroll,
            writable=False,
            storage_root=storage_root,
        )
        for binding in bindings
    )
    return (legacy, *active)


def provisioned_active_cohort_block_reason(storage_root: Path = DB_STATE_DIR) -> str | None:
    """Return a non-bypassable live-transition block for every active cohort."""

    try:
        cohorts = discover_paper_risk_cohorts(storage_root)
    except Exception as exc:  # noqa: BLE001 - live-money boundary must fail closed
        return f"paper cohort discovery is unavailable ({str(exc)[:80]})"
    if not cohorts:
        return None
    return (
        "active paper cohort remains isolated from live trading until all-cohort "
        "settlement and realized-profit reconciliation is explicitly reviewed"
    )


def immutable_paper_database_block_reason(
    db_path: Path,
    *,
    storage_root: Path | None = None,
) -> str | None:
    """Block every alias to a legacy root or manifest-bound cutover snapshot."""

    path = Path(db_path)
    for candidate_root in _candidate_storage_roots_for_immutable_database(
        path,
        storage_root=storage_root,
    ):
        try:
            if not _active_cohort_root_entry_exists(candidate_root):
                continue
            immutable_paths = _immutable_database_paths(candidate_root)
        except Exception as exc:  # noqa: BLE001 - immutable state must fail closed
            return f"paper cohort discovery is unavailable ({str(exc)[:80]})"
        if any(_same_database_file(path, immutable_path) for immutable_path in immutable_paths):
            return "active paper cohort keeps legacy cutover databases immutable"
    return None


def unbound_paper_runtime_database_block_reason(
    db_path: Path,
    *,
    storage_root: Path | None = None,
) -> str | None:
    """Require runtime and CLI callers to use the manifest-bound active database."""

    for candidate_root in _candidate_storage_roots_for_immutable_database(
        Path(db_path),
        storage_root=storage_root,
    ):
        try:
            if not _active_cohort_root_entry_exists(candidate_root):
                continue
            if discover_paper_risk_cohorts(candidate_root):
                return "active paper cohort requires a manifest-bound runtime database"
        except Exception as exc:  # noqa: BLE001 - runtime money paths must fail closed
            return f"paper cohort discovery is unavailable ({str(exc)[:80]})"
    return None


def aggregate_open_exposure_snapshot(
    cohorts: Sequence[PaperCohort],
    *,
    marks_provider: Callable[[Path], Mapping[str, Any] | None] | None = None,
) -> AggregateOpenExposureSnapshot:
    """Read each cohort independently and fail closed on any unknown state."""

    configured_cohorts = tuple(cohorts)
    if not configured_cohorts:
        return _failure("no_cohorts")
    if len({cohort.cohort_id for cohort in configured_cohorts}) != len(configured_cohorts):
        return _failure("duplicate_cohort")
    if len({cohort.db_path.resolve() for cohort in configured_cohorts}) != len(configured_cohorts):
        return _failure("duplicate_cohort_db")

    if marks_provider is None:
        from scripts.mark_open_positions import compute_open_position_marks

        marks_provider = compute_open_position_marks

    records: list[CohortOpenExposure] = []
    for cohort in configured_cohorts:
        try:
            notional_bankroll, unresolved_trade_count = _read_cohort_state(cohort.db_path)
        except Exception:
            return _failure("cohort_state_unavailable")
        try:
            marks = marks_provider(cohort.db_path)
        except Exception:
            return _failure("cohort_marks_unavailable")
        try:
            records.append(
                _cohort_exposure_from_marks(
                    cohort,
                    notional_bankroll=notional_bankroll,
                    unresolved_trade_count=unresolved_trade_count,
                    marks=marks,
                )
            )
        except ValueError:
            return _failure("invalid_cohort_mark")

    return AggregateOpenExposureSnapshot(
        ok=True,
        failure_status="none",
        configured_bankroll=sum(record.configured_bankroll for record in records),
        notional_bankroll=sum(record.notional_bankroll for record in records),
        marked_value=sum(record.marked_value for record in records),
        total_entry_cost=_sum_optional(record.total_entry_cost for record in records),
        unknown_entry_cost=_sum_optional(record.unknown_entry_cost for record in records),
        priced_count=sum(record.priced_count for record in records),
        unpriced_count=sum(record.unpriced_count for record in records),
        snapshot_fallback_count=sum(record.snapshot_fallback_count for record in records),
        unresolved_trade_count=sum(record.unresolved_trade_count for record in records),
        cohorts=tuple(records),
    )


def _load_active_binding(manifest_path: Path, storage_root: Path) -> ActivePaperCohortBinding:
    payload = _read_manifest_payload(manifest_path)
    if payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise ValueError("active cohort manifest schema_version is unsupported")
    cohort_id = _validate_cohort_id(_required_text(payload, "cohort_id"))
    if cohort_id == LEGACY_PAPER_COHORT_ID:
        raise ValueError("legacy cohort does not use an active manifest")
    db_path = _path_from_relative(
        _required_text(payload, "db_path_relative_to_storage_root"),
        storage_root,
    )
    if db_path.name != "paper_trades.db":
        raise ValueError("active cohort manifest database filename is invalid")
    if _absolute_path(db_path.parent) != _absolute_path(manifest_path.parent):
        raise ValueError("active cohort manifest database path is invalid")
    if db_path.is_symlink():
        raise ValueError("active cohort manifest database must not be a symlink")
    legacy_db_path = _path_from_relative(
        _required_text(payload, "legacy_db_path_relative_to_storage_root"),
        storage_root,
    )
    if _absolute_path(legacy_db_path) != _absolute_path(Path(storage_root) / "paper_trades.db"):
        raise ValueError("active cohort manifest legacy database path is invalid")
    if legacy_db_path.is_symlink():
        raise ValueError("active cohort manifest legacy database must not be a symlink")
    legacy_snapshot_path = _path_from_relative(
        _required_text(payload, "legacy_snapshot_path_relative_to_storage_root"),
        storage_root,
    )
    if _absolute_path(legacy_snapshot_path) != _absolute_path(
        manifest_path.parent / _LEGACY_SNAPSHOT_FILENAME
    ):
        raise ValueError("active cohort manifest legacy cutover snapshot path is invalid")
    _database_file_stat(db_path, label="active cohort database")
    _database_file_stat(legacy_db_path, label="legacy database")
    _database_file_stat(legacy_snapshot_path, label="legacy cutover snapshot")
    cohort = PaperCohort(
        cohort_id=cohort_id,
        db_path=db_path,
        starting_bankroll=_positive_finite_bankroll(
            payload.get("starting_bankroll"),
            label="active starting bankroll",
        ),
        writable=False,
        storage_root=Path(storage_root),
    )
    cohort_identity = _required_text(payload, "cohort_identity")
    if not _IDENTITY_PATTERN.fullmatch(cohort_identity):
        raise ValueError("active cohort manifest identity is invalid")
    legacy_source_sha256 = _required_sha256(payload, "legacy_source_db_sha256")
    legacy_snapshot_sha256 = _required_sha256(payload, "legacy_snapshot_sha256")
    if legacy_source_sha256 != legacy_snapshot_sha256:
        raise ValueError("active cohort manifest cutover source does not match snapshot")
    legacy_starting_bankroll = _positive_finite_bankroll(
        payload.get("legacy_starting_bankroll"),
        label="legacy starting bankroll",
    )
    legacy_baseline_attestation = _required_sha256(
        payload,
        "legacy_baseline_attestation",
    )
    if legacy_baseline_attestation != _legacy_baseline_attestation(
        legacy_snapshot_sha256,
        legacy_starting_bankroll,
    ):
        raise ValueError("active cohort manifest legacy baseline attestation is invalid")
    if payload.get("legacy_baseline_verification") != _LEGACY_BASELINE_VERIFICATION:
        raise ValueError("active cohort manifest legacy baseline verification is invalid")
    return ActivePaperCohortBinding(
        cohort=cohort,
        cohort_identity=cohort_identity,
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        max_days_to_close=_active_horizon(payload.get("max_days_to_close")),
        legacy_db_path=legacy_db_path,
        legacy_snapshot_path=legacy_snapshot_path,
        legacy_starting_bankroll=legacy_starting_bankroll,
        legacy_baseline_attestation=legacy_baseline_attestation,
        legacy_baseline_verification=_LEGACY_BASELINE_VERIFICATION,
        legacy_snapshot_sha256=legacy_snapshot_sha256,
    )


def _validate_active_database_identity(binding: ActivePaperCohortBinding) -> _ActiveDatabaseIdentity:
    _assert_distinct_cohort_database_files(_bound_database_files(binding))
    identity = _active_database_identity_for_path(
        binding.cohort.db_path,
        require_identity=True,
    )
    assert identity is not None
    _assert_active_database_identity_matches_binding(identity, binding)
    return identity


def _assert_active_database_identity_matches_binding(
    identity: _ActiveDatabaseIdentity,
    binding: ActivePaperCohortBinding,
) -> None:
    if identity.cohort_id != binding.cohort.cohort_id or identity.cohort_identity != binding.cohort_identity:
        raise ValueError("active cohort database identity does not match manifest")
    if identity.manifest_sha256 != binding.manifest_sha256:
        raise ValueError("active cohort database identity does not match manifest")
    if _verified_sqlite_sha256(binding.legacy_snapshot_path) != binding.legacy_snapshot_sha256:
        raise ValueError("active cohort manifest legacy_snapshot_sha256 does not match cutover snapshot")


def _manifest_payload(
    cohort: PaperCohort,
    *,
    cohort_identity: str,
    max_days_to_close: float,
    legacy_db_path: Path,
    legacy_snapshot_path: Path,
    legacy_starting_bankroll: float,
    legacy_source_sha256: str,
    legacy_snapshot_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "cohort_id": cohort.cohort_id,
        "cohort_identity": cohort_identity,
        "db_path_relative_to_storage_root": _relative_to_storage_root(
            cohort.db_path,
            cohort.storage_root,
        ),
        "starting_bankroll": cohort.starting_bankroll,
        "max_days_to_close": _active_horizon(max_days_to_close),
        "legacy_db_path_relative_to_storage_root": _relative_to_storage_root(
            legacy_db_path,
            cohort.storage_root,
        ),
        "legacy_snapshot_path_relative_to_storage_root": _relative_to_storage_root(
            legacy_snapshot_path,
            cohort.storage_root,
        ),
        "legacy_starting_bankroll": _positive_finite_bankroll(
            legacy_starting_bankroll,
            label="legacy starting bankroll",
        ),
        "legacy_baseline_attestation": _legacy_baseline_attestation(
            legacy_snapshot_sha256,
            _positive_finite_bankroll(
                legacy_starting_bankroll,
                label="legacy starting bankroll",
            ),
        ),
        "legacy_baseline_verification": _LEGACY_BASELINE_VERIFICATION,
        "legacy_source_db_sha256": legacy_source_sha256,
        "legacy_snapshot_sha256": legacy_snapshot_sha256,
    }


def _legacy_baseline_attestation(snapshot_sha256: str, bankroll: float) -> str:
    """Bind an operator-confirmed historical baseline to one immutable snapshot."""

    canonical = {
        "schema": "legacy-baseline-attestation-v1",
        "legacy_snapshot_sha256": snapshot_sha256,
        "legacy_starting_bankroll": _canonical_attested_bankroll(bankroll),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _canonical_attested_bankroll(value: float) -> str:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("legacy starting bankroll attestation is invalid") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError("legacy starting bankroll attestation is invalid")
    return format(decimal_value.normalize(), "f")


def _bootstrap_identity_database(
    db_path: Path,
    *,
    cohort_id: str,
    cohort_identity: str,
    manifest_sha256: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"CREATE TABLE {_IDENTITY_TABLE} ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
            "cohort_id TEXT NOT NULL, "
            "cohort_identity TEXT NOT NULL, "
            "manifest_sha256 TEXT NOT NULL, "
            "initialization_state TEXT NOT NULL "
            f"CHECK(initialization_state IN ('{_IDENTITY_PROVISIONED}', '{_IDENTITY_INITIALIZED}')), "
            "initialized_table_names_json TEXT"
            ")"
        )
        conn.execute(
            f"INSERT INTO {_IDENTITY_TABLE}("
            "singleton, cohort_id, cohort_identity, manifest_sha256, initialization_state, "
            "initialized_table_names_json) VALUES (1, ?, ?, ?, ?, NULL)",
            (
                cohort_id,
                cohort_identity,
                manifest_sha256,
                _IDENTITY_PROVISIONED,
            ),
        )
        conn.commit()
    with db_path.open("rb") as db_file:
        os.fsync(db_file.fileno())


def _active_database_identity_for_path(
    db_path: Path,
    *,
    require_identity: bool = False,
) -> _ActiveDatabaseIdentity | None:
    """Read the identity sentinel without treating ordinary legacy DBs as active."""

    path = Path(db_path)
    if not path.is_file():
        if require_identity:
            raise ValueError("active cohort database missing")
        return None
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?",
                (_IDENTITY_TABLE,),
            ).fetchone()
            if table is None:
                if require_identity:
                    raise ValueError("active cohort database identity is missing")
                return None
            _reject_sqlite_sidecars(path)
            row = conn.execute(
                f"SELECT cohort_id, cohort_identity, manifest_sha256, initialization_state, "
                f"initialized_table_names_json FROM {_IDENTITY_TABLE} WHERE singleton = 1"
            ).fetchone()
    except ValueError:
        raise
    except sqlite3.Error as exc:
        if require_identity:
            raise ValueError("active cohort database identity is unavailable") from exc
        return None
    return _active_database_identity_from_row(row)


def _active_database_identity_from_connection(
    connection: sqlite3.Connection,
) -> _ActiveDatabaseIdentity:
    try:
        row = connection.execute(
            f"SELECT cohort_id, cohort_identity, manifest_sha256, initialization_state, "
            f"initialized_table_names_json FROM {_IDENTITY_TABLE} WHERE singleton = 1"
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("active cohort database identity is unavailable") from exc
    return _active_database_identity_from_row(row)


def _active_database_identity_from_row(row: object) -> _ActiveDatabaseIdentity:
    if row is None:
        raise ValueError("active cohort database identity is missing")
    cohort_id, cohort_identity, manifest_sha256, initialization_state, serialized_tables = row
    if not isinstance(cohort_id, str) or not _COHORT_ID_PATTERN.fullmatch(cohort_id):
        raise ValueError("active cohort database identity is invalid")
    if not isinstance(cohort_identity, str) or not _IDENTITY_PATTERN.fullmatch(cohort_identity):
        raise ValueError("active cohort database identity is invalid")
    if not isinstance(manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise ValueError("active cohort database identity is invalid")
    if initialization_state == _IDENTITY_PROVISIONED:
        if serialized_tables is not None:
            raise ValueError("active cohort provisioned identity has initialized schema")
        initialized_table_names = None
    elif initialization_state == _IDENTITY_INITIALIZED:
        initialized_table_names = _parse_initialized_table_names(serialized_tables)
    else:
        raise ValueError("active cohort database initialization state is invalid")
    return _ActiveDatabaseIdentity(
        cohort_id=cohort_id,
        cohort_identity=cohort_identity,
        manifest_sha256=manifest_sha256,
        initialization_state=initialization_state,
        initialized_table_names=initialized_table_names,
    )


def active_cohort_initialized_table_names(
    binding: ActivePaperCohortBinding,
) -> tuple[str, ...] | None:
    """Return immutable bootstrap tables, after validating binding and snapshot."""

    return _validate_active_database_identity(binding).initialized_table_names


def assert_initialized_active_cohort_schema(
    connection: sqlite3.Connection,
    binding: ActivePaperCohortBinding,
) -> None:
    """Reject a damaged initialized cohort before any bootstrap DDL can run."""

    expected = active_cohort_initialized_table_names(binding)
    if expected is None:
        return
    missing = sorted(set(expected) - _application_table_names(connection))
    if missing:
        raise RuntimeError(
            "initialized active cohort database is missing core schema: "
            + ", ".join(missing)
        )


def mark_active_cohort_database_initialized(
    connection: sqlite3.Connection,
    binding: ActivePaperCohortBinding,
    *,
    commit: bool = True,
) -> None:
    """Persist bootstrap completion only after PaperTrader initialized every schema."""

    identity = (
        _validate_active_database_identity(binding)
        if commit
        else _active_database_identity_from_connection(connection)
    )
    if not commit:
        _assert_active_database_identity_matches_binding(identity, binding)
    table_names = tuple(sorted(_application_table_names(connection)))
    if not table_names or {"paper_trades", "bot_state"} - set(table_names):
        raise RuntimeError("active cohort initialization did not create required paper schema")
    if identity.initialization_state == _IDENTITY_INITIALIZED:
        missing = sorted(set(identity.initialized_table_names or ()) - set(table_names))
        if missing:
            raise RuntimeError(
                "initialized active cohort database is missing core schema: "
                + ", ".join(missing)
            )
        return
    if identity.initialization_state != _IDENTITY_PROVISIONED:
        raise RuntimeError("active cohort database initialization state is invalid")
    statement = (
        f"UPDATE {_IDENTITY_TABLE} SET initialization_state = ?, "
        "initialized_table_names_json = ? WHERE singleton = 1 "
        "AND initialization_state = ?"
    )
    parameters = (
        _IDENTITY_INITIALIZED,
        json.dumps(table_names, separators=(",", ":")),
        _IDENTITY_PROVISIONED,
    )
    if commit:
        with connection:
            updated = connection.execute(statement, parameters).rowcount
    else:
        updated = connection.execute(statement, parameters).rowcount
    if updated != 1:
        raise RuntimeError("active cohort initialization state changed concurrently")


def _application_table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' AND name != ?",
        (_IDENTITY_TABLE,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def _parse_initialized_table_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError("active cohort initialized table identity is invalid")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("active cohort initialized table identity is invalid") from exc
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("active cohort initialized table identity is invalid")
    names: list[str] = []
    for name in parsed:
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            or name == _IDENTITY_TABLE
        ):
            raise ValueError("active cohort initialized table identity is invalid")
        names.append(name)
    if names != sorted(set(names)):
        raise ValueError("active cohort initialized table identity is invalid")
    return tuple(names)


def _read_manifest_payload(manifest_path: Path) -> dict[str, object]:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"active cohort manifest missing: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("active cohort manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("active cohort manifest must be a JSON object")
    return payload


def _encode_manifest(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_new_file(path: Path, contents: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(contents)
        output.flush()
        os.fsync(output.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _remove_staging_directory(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        child.unlink(missing_ok=True)
    path.rmdir()


def _copy_verified_sqlite_snapshot(
    source_path: Path,
    snapshot_path: Path,
    *,
    expected_sha256: str,
) -> None:
    shutil.copyfile(source_path, snapshot_path)
    with snapshot_path.open("rb") as snapshot_file:
        os.fsync(snapshot_file.fileno())
    if _verified_sqlite_sha256(snapshot_path) != expected_sha256:
        raise RuntimeError("legacy cutover snapshot does not match the quiesced source database")


def _legacy_unresolved_trade_count(path: Path) -> int:
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'paper_trades'"
            ).fetchone()
            if table is None:
                raise ValueError("legacy database lacks paper_trades for cutover")
            columns = {
                str(column[1])
                for column in conn.execute("PRAGMA table_info(paper_trades)").fetchall()
            }
            if "resolved" not in columns:
                raise ValueError("legacy paper trade resolved state is invalid")
            invalid_state = conn.execute(
                """
                SELECT 1
                FROM paper_trades
                WHERE typeof(resolved) != 'integer' OR resolved NOT IN (0, 1)
                LIMIT 1
                """
            ).fetchone()
            if invalid_state is not None:
                raise ValueError("legacy paper trade resolved state is invalid")
            row = conn.execute(
                "SELECT COUNT(*) FROM paper_trades WHERE resolved = 0"
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("legacy paper trade state is unreadable for cutover") from exc
    if row is None or not isinstance(row[0], int) or row[0] < 0:
        raise ValueError("legacy unresolved trade state is invalid")
    return row[0]


@contextmanager
def _runtime_lock(storage_root: Path) -> Iterator[None]:
    """Acquire the same cooperative runtime lock before snapshotting legacy state."""

    lock_path = Path(storage_root) / "bot_runtime.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
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
            f"runtime lock is held at {lock_path}; stop the bot before provisioning"
        ) from exc
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "operation": "active_paper_cohort_provisioning",
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
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


def _verified_sqlite_sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError("legacy database missing")
    _reject_sqlite_sidecars(path)
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity_row is None or integrity_row[0] != "ok":
                raise ValueError("legacy database integrity check failed")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("legacy database foreign-key check failed")
    except sqlite3.Error as exc:
        raise ValueError("legacy database is unreadable") from exc

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _database_file_stat(path: Path, *, label: str) -> os.stat_result:
    """Require a canonical, unaliased regular SQLite file before trusting it."""

    try:
        file_stat = Path(path).lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"paper cohort database missing: {label}") from exc
    except OSError as exc:
        raise ValueError(f"paper cohort database is unavailable: {label}") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise ValueError(f"immutable database must not be aliased: {label} is a symlink")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"paper cohort database is not a regular file: {label}")
    if file_stat.st_nlink != 1:
        raise ValueError(f"immutable database must not be aliased: {label} is a hard link")
    return file_stat


def _bound_database_files(
    binding: ActivePaperCohortBinding,
) -> tuple[tuple[str, Path], ...]:
    return (
        ("legacy database", binding.legacy_db_path),
        (f"active cohort database {binding.cohort.cohort_id}", binding.cohort.db_path),
        (f"legacy cutover snapshot {binding.cohort.cohort_id}", binding.legacy_snapshot_path),
    )


def _all_bound_database_files(
    bindings: Sequence[ActivePaperCohortBinding],
) -> tuple[tuple[str, Path], ...]:
    if not bindings:
        return ()
    files: list[tuple[str, Path]] = [("legacy database", bindings[0].legacy_db_path)]
    for binding in bindings:
        files.extend(
            (
                (f"active cohort database {binding.cohort.cohort_id}", binding.cohort.db_path),
                (f"legacy cutover snapshot {binding.cohort.cohort_id}", binding.legacy_snapshot_path),
            )
        )
    return tuple(files)


def _assert_distinct_cohort_database_files(entries: Iterable[tuple[str, Path]]) -> None:
    seen: dict[tuple[int, int], str] = {}
    for label, path in entries:
        file_stat = _database_file_stat(path, label=label)
        identity = (file_stat.st_dev, file_stat.st_ino)
        previous = seen.setdefault(identity, label)
        if previous != label:
            raise ValueError(
                f"immutable database must not be aliased: {previous} and {label}"
            )


def _absolute_path(path: Path) -> Path:
    """Normalize dot segments without resolving a potentially hostile leaf symlink."""

    return Path(os.path.abspath(os.fspath(path)))


def _candidate_storage_roots_for_immutable_database(
    db_path: Path,
    *,
    storage_root: Path | None = None,
) -> tuple[Path, ...]:
    candidates: dict[str, Path] = {}

    def add(directory: Path) -> None:
        candidate = _absolute_path(directory)
        candidates.setdefault(str(candidate), candidate)
        if candidate.name == _ACTIVE_COHORTS_DIRNAME:
            root = candidate.parent
            candidates.setdefault(str(root), root)
        elif candidate.parent.name == _ACTIVE_COHORTS_DIRNAME:
            root = candidate.parent.parent
            candidates.setdefault(str(root), root)

    path = Path(db_path)
    if storage_root is not None:
        add(Path(storage_root))
    add(Path(DB_STATE_DIR))
    add(path.parent)
    try:
        add(path.resolve(strict=False).parent)
    except (OSError, RuntimeError):
        pass
    return tuple(candidates.values())


def _active_cohort_root_entry_exists(storage_root: Path) -> bool:
    """Only a genuinely absent cohort root may be ignored by money-path guards."""

    try:
        (Path(storage_root) / _ACTIVE_COHORTS_DIRNAME).lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("active cohort root is unavailable") from exc
    return True


def _immutable_database_paths(storage_root: Path) -> tuple[Path, ...]:
    root = _absolute_path(storage_root)
    if not discover_paper_risk_cohorts(root):
        return ()
    snapshots: list[Path] = []
    cohorts_root = root / _ACTIVE_COHORTS_DIRNAME
    for entry in sorted(cohorts_root.iterdir(), key=lambda item: item.name):
        binding = _load_active_binding(entry / _MANIFEST_FILENAME, root)
        snapshots.append(binding.legacy_snapshot_path)
    return (root / "paper_trades.db", *snapshots)


def _same_database_file(left: Path, right: Path) -> bool:
    try:
        return Path(left).is_file() and Path(right).is_file() and os.path.samefile(left, right)
    except OSError:
        return False


def _reject_sqlite_sidecars(path: Path) -> None:
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        if Path(f"{path}{suffix}").exists():
            raise ValueError(f"SQLite sidecar present for immutable database: {path.name}{suffix}")


def _validate_legacy_db_path(path: Path, storage_root: Path) -> Path:
    candidate = _absolute_path(Path(path))
    expected = _absolute_path(Path(storage_root) / "paper_trades.db")
    if candidate != expected:
        raise ValueError("legacy database path must be the immutable root paper_trades.db")
    if not candidate.exists() and not candidate.is_symlink():
        raise ValueError("legacy database missing")
    _database_file_stat(candidate, label="legacy database")
    return candidate


def _storage_root_for_active_db(path: Path) -> Path:
    parent = path.parent
    if parent.parent.name != _ACTIVE_COHORTS_DIRNAME:
        raise ValueError("active cohort database path is outside the standard cohort root")
    return parent.parent.parent


def _path_from_relative(value: str, storage_root: Path) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError("cohort path must remain below the configured storage root")
    root = _absolute_path(Path(storage_root))
    candidate = _absolute_path(root / relative)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("cohort path must remain below the configured storage root") from exc
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("cohort path must remain below the configured storage root") from exc
    return candidate


def _relative_to_storage_root(path: Path, storage_root: Path) -> str:
    try:
        return path.resolve().relative_to(Path(storage_root).resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("cohort path must remain below the configured storage root") from exc


def _manifest_path_for(cohort: PaperCohort) -> Path:
    return cohort.db_path.parent / _MANIFEST_FILENAME


def _require_active_cohort(cohort: PaperCohort) -> None:
    if cohort.cohort_id == LEGACY_PAPER_COHORT_ID:
        raise ValueError("legacy cohort does not use an active manifest")


def _validate_cohort_id(value: str) -> str:
    cohort_id = str(value or "").strip().lower()
    if not _COHORT_ID_PATTERN.fullmatch(cohort_id):
        raise ValueError(
            "paper cohort id must contain only lowercase letters, digits, and hyphens"
        )
    return cohort_id


def _active_horizon(value: object) -> float:
    days = _positive_finite_days(value)
    if days > MAX_MARKET_DAYS_TO_EXPIRY:
        raise ValueError(
            "active cohort horizon cannot exceed the observed universe "
            f"({MAX_MARKET_DAYS_TO_EXPIRY} days)"
        )
    return days


def _positive_finite_days(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("cohort horizon must be a positive finite number")
    try:
        days = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("cohort horizon must be a positive finite number") from exc
    if not math.isfinite(days) or days <= 0:
        raise ValueError("cohort horizon must be a positive finite number")
    return days


def _positive_finite_bankroll(value: float | None, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive finite number")
    try:
        bankroll = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive finite number") from exc
    if not math.isfinite(bankroll) or bankroll <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    return bankroll


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"active cohort manifest {key} is invalid")
    return value


def _required_sha256(payload: Mapping[str, object], key: str) -> str:
    value = _required_text(payload, key)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"active cohort manifest {key} is invalid")
    return value


def _read_cohort_state(db_path: Path) -> tuple[float, int]:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT value FROM bot_state WHERE key = 'notional_bankroll'"
        ).fetchone()
        if row is None:
            raise ValueError("notional bankroll missing")
        notional_bankroll = _positive_or_zero_finite(row[0], label="notional bankroll")
        unresolved_row = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE COALESCE(resolved, 0) = 0"
        ).fetchone()
        if unresolved_row is None:
            raise ValueError("unresolved trade count missing")
        unresolved_trade_count = int(unresolved_row[0])
        if unresolved_trade_count < 0:
            raise ValueError("invalid unresolved trade count")
        return notional_bankroll, unresolved_trade_count


def _cohort_exposure_from_marks(
    cohort: PaperCohort,
    *,
    notional_bankroll: float,
    unresolved_trade_count: int,
    marks: Mapping[str, Any] | None,
) -> CohortOpenExposure:
    if not isinstance(marks, Mapping):
        raise ValueError("marks unavailable")
    marked_value = _positive_or_zero_finite(marks.get("marked_value"), label="marked value")
    total_entry_cost = _optional_finite(marks.get("total_cost"), label="total cost")
    unknown_entry_cost = _optional_finite(marks.get("unknown_cost"), label="unknown cost")
    return CohortOpenExposure(
        cohort_id=cohort.cohort_id,
        db_path=cohort.db_path,
        configured_bankroll=cohort.starting_bankroll,
        notional_bankroll=notional_bankroll,
        marked_value=marked_value,
        total_entry_cost=total_entry_cost,
        unknown_entry_cost=unknown_entry_cost,
        priced_count=_nonnegative_int(marks.get("priced_count"), label="priced count"),
        unpriced_count=_nonnegative_int(marks.get("unpriced_count"), label="unpriced count"),
        snapshot_fallback_count=_nonnegative_int(
            marks.get("snapshot_fallback_count"),
            label="snapshot fallback count",
        ),
        unresolved_trade_count=unresolved_trade_count,
        valuation_as_of=_optional_text(marks.get("as_of")),
    )


def _positive_or_zero_finite(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return numeric


def _optional_finite(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    return _positive_or_zero_finite(value, label=label)


def _nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if numeric < 0 or numeric != value:
        raise ValueError(f"{label} must be a non-negative integer")
    return numeric


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("valuation timestamp must be text")
    return value


def _sum_optional(values: Sequence[float | None] | Any) -> float | None:
    materialized = tuple(values)
    if any(value is None for value in materialized):
        return None
    return sum(value for value in materialized if value is not None)


def _failure(status: str) -> AggregateOpenExposureSnapshot:
    return AggregateOpenExposureSnapshot(
        ok=False,
        failure_status=status,
        configured_bankroll=None,
        notional_bankroll=None,
        marked_value=None,
        total_entry_cost=None,
        unknown_entry_cost=None,
        priced_count=None,
        unpriced_count=None,
        snapshot_fallback_count=None,
        unresolved_trade_count=None,
        cohorts=(),
    )
