"""Materialize a registered replay corpus from an attested active cohort snapshot.

This is deliberately not a general corpus CLI. It derives every corpus window
parameter from a protected OOS registration, accepts no runtime database path,
and never uses the mutable runtime database as the builder input.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Final
from uuid import uuid4

from scripts.edge_replay.build_corpus import (
    BuildResult,
    FEE_NET_REPLAY_FIELDS,
    _row_families,
    build_corpus,
)
from scripts.edge_replay.oos_registry import (
    DEFAULT_REGISTRY_PATH,
    OOSRegistration,
    OOSRegistrationAttestation,
    assert_materializable,
    attest_oos_registration_history,
    get_oos_registration,
)
from scripts.runtime_paper_cohort_scope import derive_runtime_paper_cohort_db_path
from trading.paper_accounting import (
    PAPER_ACCOUNTING_VERSION,
    paper_accounting_schema_contract_matches,
)
from trading.paper_cohorts import active_cohort_binding_for_db
from trading.runtime_paper_cohort_attestation import (
    RuntimePaperCohortAttestation,
    read_runtime_paper_cohort_attestation,
)


REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_ATTESTATION: Final[Path] = Path("logs/state/runtime_paper_cohort_attestation.json")
DEFAULT_REGIMES_DOC: Final[Path] = Path("docs/governance/corpus-regimes.md")
_OUTPUT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^corpus_[a-z0-9][a-z0-9._-]{2,191}\.jsonl$")
_SNAPSHOT_ROOT: Final[Path] = Path("edge_replay_snapshots")
_SNAPSHOT_SIDECAR_SUFFIXES: Final[tuple[str, ...]] = ("-journal", "-shm", "-wal")


class RuntimeCorpusMaterializationError(ValueError):
    """Raised when runtime provenance or replay economics cannot be trusted."""


@dataclass(frozen=True)
class RuntimeCorpusMaterializationResult:
    """The bounded provenance record emitted by one successful materialization."""

    output_path: Path
    row_count: int
    registration_id: str
    registration_hash: str
    runtime_cohort_id: str
    runtime_cohort_kind: str
    runtime_attestation_sha256: str
    snapshot_path_relative_to_data: str
    snapshot_sha256: str
    snapshot_creation_method: str
    protected_registration_trusted_ref: str
    protected_registration_commit: str
    protected_registration_integrated_at_utc: str
    materialized_at_utc: str

    def to_payload(self, *, repo_root: Path) -> dict[str, object]:
        return {
            "materialization_status": "materialized",
            "output_path": _relative_to(self.output_path, repo_root),
            "row_count": self.row_count,
            "registration_id": self.registration_id,
            "registration_hash": self.registration_hash,
            "runtime_cohort_id": self.runtime_cohort_id,
            "runtime_cohort_kind": self.runtime_cohort_kind,
            "runtime_attestation_sha256": self.runtime_attestation_sha256,
            "snapshot_path_relative_to_data": self.snapshot_path_relative_to_data,
            "snapshot_sha256": self.snapshot_sha256,
            "snapshot_creation_method": self.snapshot_creation_method,
            "protected_registration_trusted_ref": self.protected_registration_trusted_ref,
            "protected_registration_commit": self.protected_registration_commit,
            "protected_registration_integrated_at_utc": self.protected_registration_integrated_at_utc,
            "materialized_at_utc": self.materialized_at_utc,
        }


RegistrationAttestor = Callable[..., OOSRegistrationAttestation]


def materialize_runtime_corpus(
    *,
    repo_root: Path = REPO_ROOT,
    registration_id: str,
    runtime_attestation_path: Path = DEFAULT_RUNTIME_ATTESTATION,
    output_path: Path | None = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    regimes_doc_path: Path = DEFAULT_REGIMES_DOC,
    materialized_at_utc: datetime | None = None,
    registration_attestor: RegistrationAttestor = attest_oos_registration_history,
) -> RuntimeCorpusMaterializationResult:
    """Build one closed registered OOS corpus from an owned active-DB backup.

    The wrapper creates its own immutable SQLite online backup below
    ``data/edge_replay_snapshots/<cohort-id>/`` only after registration and
    active-runtime lineage validation. No caller can choose a database or
    snapshot path; :func:`build_corpus` receives only the generated snapshot.
    """

    root = _repository_root(repo_root)
    data_root = _required_directory(root / "data", label="data root")
    registry = _required_regular_file(
        _path_within(root, registry_path, label="OOS registry"),
        root=root,
        label="OOS registry",
    )
    regimes_doc = _required_regular_file(
        _path_within(root, regimes_doc_path, label="regimes document"),
        root=root,
        label="regimes document",
    )
    moment = _canonical_utc(materialized_at_utc or datetime.now(timezone.utc))

    try:
        registration = get_oos_registration(registration_id, registry)
        assert_materializable(registration, as_of_utc=moment)
    except ValueError as exc:
        raise RuntimeCorpusMaterializationError(f"registered window is not closed or valid: {exc}") from exc
    registration_proof = _attest_registration(
        registration,
        registry_path=registry,
        repo_root=root,
        attestor=registration_attestor,
    )

    receipt_path = _required_regular_file(
        _path_within(root, runtime_attestation_path, label="runtime attestation"),
        root=root,
        label="runtime attestation",
    )
    attestation_sha256 = _sha256_file(receipt_path)
    try:
        receipt = read_runtime_paper_cohort_attestation(
            receipt_path,
            storage_root=data_root,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeCorpusMaterializationError(f"runtime cohort attestation is unverified: {exc}") from exc
    runtime_db = _validated_active_runtime_database(
        root=root,
        data_root=data_root,
        receipt=receipt,
    )

    snapshot, actual_snapshot_sha256 = _create_owned_runtime_snapshot(
        data_root=data_root,
        runtime_db=runtime_db,
        cohort_id=receipt.cohort_id,
        registration_hash=registration.registration_hash,
        runtime_attestation_sha256=attestation_sha256,
        materialized_at_utc=moment,
        registration=registration,
        receipt=receipt,
    )
    staged_output: Path | None = None
    try:
        resolved_output = _validated_output_path(
            root=root,
            registration=registration,
            snapshot_sha256=actual_snapshot_sha256,
            output_path=output_path,
        )
        staged_output = _staged_output_path(root=root, output_path=resolved_output)
        built = build_corpus(
            start_utc=_parse_registration_utc(registration.window_start_utc),
            end_utc=_parse_registration_utc(registration.window_end_utc),
            market_families=list(registration.market_families),
            cohort_tag=f"RUNTIME_{receipt.cohort_id}",
            regime_label=registration.regime_label,
            output_path=staged_output,
            paper_trades_db=snapshot,
            regimes_doc_path=regimes_doc,
            built_at_utc=_format_utc(moment),
            include_contamination=False,
            include_fee_net_ledger=True,
            oos_registration_id=registration.id,
            oos_registry_path=registry,
        )
        _assert_materialized_build(built, registration_proof=registration_proof)
        _stamp_materialization_provenance(
            built.output_path,
            root=root,
            provenance={
                "runtime_attestation_sha256": attestation_sha256,
                "snapshot_sha256": actual_snapshot_sha256,
                "snapshot_creation_method": "sqlite_online_backup",
                "protected_registration_trusted_ref": registration_proof.trusted_ref,
                "protected_registration_commit": registration_proof.commit,
                "protected_registration_integrated_at_utc": registration_proof.integrated_at_utc,
            },
        )
        os.replace(staged_output, resolved_output)
    except RuntimeCorpusMaterializationError:
        _cleanup_staged_output(staged_output)
        _cleanup_owned_snapshot(snapshot)
        raise
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        _cleanup_staged_output(staged_output)
        _cleanup_owned_snapshot(snapshot)
        raise RuntimeCorpusMaterializationError(f"registered corpus build failed: {exc}") from exc

    return RuntimeCorpusMaterializationResult(
        output_path=resolved_output,
        row_count=built.row_count,
        registration_id=registration.id,
        registration_hash=registration_proof.registration_hash,
        runtime_cohort_id=receipt.cohort_id,
        runtime_cohort_kind=receipt.cohort_kind,
        runtime_attestation_sha256=attestation_sha256,
        snapshot_path_relative_to_data=_relative_to(snapshot, data_root),
        snapshot_sha256=actual_snapshot_sha256,
        snapshot_creation_method="sqlite_online_backup",
        protected_registration_trusted_ref=registration_proof.trusted_ref,
        protected_registration_commit=registration_proof.commit,
        protected_registration_integrated_at_utc=registration_proof.integrated_at_utc,
        materialized_at_utc=_format_utc(moment),
    )


def _attest_registration(
    registration: OOSRegistration,
    *,
    registry_path: Path,
    repo_root: Path,
    attestor: RegistrationAttestor,
) -> OOSRegistrationAttestation:
    try:
        proof = attestor(
            registration,
            registry_path=registry_path,
            repo_root=repo_root,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeCorpusMaterializationError(f"protected registration history is unverified: {exc}") from exc
    if (
        not isinstance(proof, OOSRegistrationAttestation)
        or proof.registration_id != registration.id
        or proof.registration_hash != registration.registration_hash
    ):
        raise RuntimeCorpusMaterializationError(
            "protected registration attestation does not match the requested registration"
        )
    return proof


def _validated_active_runtime_database(
    *,
    root: Path,
    data_root: Path,
    receipt: RuntimePaperCohortAttestation,
) -> Path:
    if receipt.cohort_kind != "active":
        raise RuntimeCorpusMaterializationError(
            "runtime cohort must be active; legacy and legacy_pending are ineligible"
        )
    if not receipt.manifest_bound:
        raise RuntimeCorpusMaterializationError("runtime cohort attestation must be manifest-bound")
    try:
        expected_db = derive_runtime_paper_cohort_db_path(
            root,
            receipt.cohort_id,
            receipt.cohort_kind,
        )
    except ValueError as exc:
        raise RuntimeCorpusMaterializationError(f"runtime cohort lineage is invalid: {exc}") from exc
    received_db = _path_from_data_relative(
        data_root,
        receipt.db_path_relative_to_storage_root,
        label="attested runtime database",
    )
    if not _same_path(expected_db, received_db):
        raise RuntimeCorpusMaterializationError("attested runtime database is not the canonical active cohort path")
    runtime_db = _required_regular_file(
        received_db,
        root=data_root,
        label="attested runtime database",
    )
    try:
        binding = active_cohort_binding_for_db(
            runtime_db,
            cohort_id=receipt.cohort_id,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeCorpusMaterializationError(f"active cohort binding is unverified: {exc}") from exc
    if (
        binding is None
        or binding.cohort_type != "active"
        or binding.cohort.db_path != runtime_db
        or binding.cohort_identity != receipt.cohort_identity
        or binding.manifest_sha256 != receipt.manifest_sha256
    ):
        raise RuntimeCorpusMaterializationError("attested runtime database does not match its active manifest binding")
    return runtime_db


def _create_owned_runtime_snapshot(
    *,
    data_root: Path,
    runtime_db: Path,
    cohort_id: str,
    registration_hash: str,
    runtime_attestation_sha256: str,
    materialized_at_utc: datetime,
    registration: OOSRegistration,
    receipt: RuntimePaperCohortAttestation,
) -> tuple[Path, str]:
    """Create and atomically publish a read-only SQLite online backup.

    The source is the already-attested active runtime database, opened in
    ``mode=ro``. The backup API gives one consistent SQLite view even while the
    runtime is writing; no raw file copy or caller-selected path is involved.
    """

    snapshot_directory = _owned_snapshot_directory(data_root=data_root, cohort_id=cohort_id)
    timestamp = _format_utc(materialized_at_utc).replace("-", "").replace(":", "").lower()
    name = f"snapshot_{registration_hash[:16]}_{runtime_attestation_sha256[:16]}_{timestamp}_{uuid4().hex}.sqlite"
    snapshot = snapshot_directory / name
    staging = snapshot_directory / f".{name}.tmp"
    _assert_no_symlink_components(data_root, snapshot, allow_missing=True)
    _assert_no_symlink_components(data_root, staging, allow_missing=True)
    if snapshot.exists() or snapshot.is_symlink() or staging.exists() or staging.is_symlink():
        raise RuntimeCorpusMaterializationError("owned runtime snapshot path already exists")
    _reserve_snapshot_staging_file(staging, data_root=data_root)

    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(f"{runtime_db.as_uri()}?mode=ro", uri=True)
        source.execute("PRAGMA query_only = ON")
        destination = sqlite3.connect(staging)
        source.backup(destination)
        destination.commit()
    except (OSError, sqlite3.Error, ValueError) as exc:
        _cleanup_owned_snapshot(staging)
        raise RuntimeCorpusMaterializationError(f"cannot create owned runtime snapshot: {exc}") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()

    try:
        _assert_immutable_snapshot_file(staging, root=data_root, require_readonly=False)
        if _same_file(staging, runtime_db):
            raise RuntimeCorpusMaterializationError("owned snapshot aliases the mutable attested runtime database")
        _fsync_file(staging)
        os.chmod(staging, 0o444)
        _assert_immutable_snapshot_file(staging, root=data_root, require_readonly=True)
        _fsync_file(staging)
        _validate_snapshot_contract(
            staging,
            registration=registration,
            receipt=receipt,
        )
        snapshot_sha256 = _sha256_file(staging)
        os.replace(staging, snapshot)
        _fsync_directory(snapshot_directory)
        snapshot = _validated_owned_snapshot(
            snapshot,
            data_root=data_root,
            cohort_id=cohort_id,
        )
        if _sha256_file(snapshot) != snapshot_sha256:
            raise RuntimeCorpusMaterializationError("owned snapshot changed during publication")
        return snapshot, snapshot_sha256
    except RuntimeCorpusMaterializationError:
        _cleanup_owned_snapshot(staging)
        _cleanup_owned_snapshot(snapshot)
        raise
    except OSError as exc:
        _cleanup_owned_snapshot(staging)
        _cleanup_owned_snapshot(snapshot)
        raise RuntimeCorpusMaterializationError(f"cannot publish owned runtime snapshot: {exc}") from exc


def _owned_snapshot_directory(*, data_root: Path, cohort_id: str) -> Path:
    directory = data_root / _SNAPSHOT_ROOT / cohort_id
    _assert_no_symlink_components(data_root, directory, allow_missing=True)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError("cannot create owned snapshot directory") from exc
    _assert_no_symlink_components(data_root, directory, allow_missing=False)
    return _required_directory(directory, label="owned snapshot directory")


def _reserve_snapshot_staging_file(staging: Path, *, data_root: Path) -> None:
    """Create a unique non-symlink SQLite destination before opening it."""

    _assert_no_symlink_components(data_root, staging, allow_missing=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(staging, flags, 0o600)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError("cannot reserve owned snapshot staging file") from exc
    else:
        os.close(descriptor)
    _assert_immutable_snapshot_file(staging, root=data_root, require_readonly=False)


def _validated_owned_snapshot(snapshot: Path, *, data_root: Path, cohort_id: str) -> Path:
    expected_directory = data_root / _SNAPSHOT_ROOT / cohort_id
    if snapshot.parent != expected_directory:
        raise RuntimeCorpusMaterializationError("owned snapshot is outside its active cohort directory")
    return _assert_immutable_snapshot_file(snapshot, root=data_root, require_readonly=True)


def _assert_immutable_snapshot_file(
    snapshot: Path,
    *,
    root: Path,
    require_readonly: bool,
) -> Path:
    snapshot = _required_regular_file(snapshot, root=root, label="owned snapshot")
    try:
        snapshot_stat = os.lstat(snapshot)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError("owned snapshot is unavailable") from exc
    if snapshot_stat.st_nlink != 1:
        raise RuntimeCorpusMaterializationError("owned snapshot must not be hard-linked")
    if require_readonly and snapshot_stat.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeCorpusMaterializationError("owned snapshot must be immutable (not writable)")
    for suffix in _SNAPSHOT_SIDECAR_SUFFIXES:
        sidecar = snapshot.with_name(snapshot.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise RuntimeCorpusMaterializationError("owned snapshot must not have SQLite sidecar files")
    return snapshot


def _cleanup_owned_snapshot(snapshot: Path) -> None:
    for candidate in (snapshot, *(snapshot.with_name(snapshot.name + suffix) for suffix in _SNAPSHOT_SIDECAR_SUFFIXES)):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _fsync_file(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError(f"cannot open snapshot for fsync: {path}") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError(f"cannot fsync snapshot: {path}") from exc
    finally:
        os.close(descriptor)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError("cannot open snapshot directory for fsync") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError("cannot fsync snapshot directory") from exc
    finally:
        os.close(descriptor)


def _validate_snapshot_contract(
    snapshot: Path,
    *,
    registration: OOSRegistration,
    receipt: RuntimePaperCohortAttestation,
) -> None:
    uri = f"{snapshot.as_uri()}?mode=ro"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
        if [str(row[0]) for row in integrity_rows] != ["ok"]:
            raise RuntimeCorpusMaterializationError("snapshot SQLite integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeCorpusMaterializationError("snapshot SQLite foreign-key check failed")
        identity = conn.execute(
            "SELECT cohort_id, cohort_identity, manifest_sha256 FROM paper_cohort_identity WHERE singleton = 1"
        ).fetchone()
        if identity is None or (
            identity["cohort_id"],
            identity["cohort_identity"],
            identity["manifest_sha256"],
        ) != (
            receipt.cohort_id,
            receipt.cohort_identity,
            receipt.manifest_sha256,
        ):
            raise RuntimeCorpusMaterializationError(
                "snapshot identity is not cryptographically bound to the attested active cohort"
            )
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(paper_trade_accounting)")}
        missing = set(FEE_NET_REPLAY_FIELDS) - columns
        if missing:
            rendered = ", ".join(sorted(missing))
            raise RuntimeCorpusMaterializationError(f"snapshot missing fee-net replay fields: {rendered}")
        if not paper_accounting_schema_contract_matches(conn):
            raise RuntimeCorpusMaterializationError(
                "snapshot fee-net accounting ledger does not match the active contract"
            )
        observed_families = _validate_selected_fee_net_rows(conn, registration)
        expected_families = set(registration.market_families)
        if not observed_families:
            raise RuntimeCorpusMaterializationError("snapshot contains zero materializable registered rows")
        if observed_families != expected_families:
            missing = sorted(expected_families - observed_families)
            unexpected = sorted(observed_families - expected_families)
            detail = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if unexpected:
                detail.append("unexpected=" + ",".join(unexpected))
            raise RuntimeCorpusMaterializationError(
                "snapshot does not cover the exact registered OOS family set"
                + (": " + "; ".join(detail) if detail else "")
            )
    except RuntimeCorpusMaterializationError:
        raise
    except sqlite3.Error as exc:
        raise RuntimeCorpusMaterializationError(f"snapshot fee-net replay ledger is unavailable: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def _validate_selected_fee_net_rows(
    conn: sqlite3.Connection,
    registration: OOSRegistration,
) -> set[str]:
    fields = ", ".join(f"paper_trade_accounting.{field} AS ledger_{field}" for field in FEE_NET_REPLAY_FIELDS)
    rows = conn.execute(
        "SELECT paper_trades.*, "
        f"{fields} "
        "FROM paper_trades "
        "LEFT JOIN paper_trade_accounting "
        "ON paper_trade_accounting.trade_id = paper_trades.trade_id "
        "WHERE paper_trades.ts >= ? AND paper_trades.ts < ? "
        "AND (paper_trades.cohort_extension IS NULL "
        "     OR paper_trades.cohort_extension NOT LIKE ?) "
        "ORDER BY paper_trades.ts ASC, paper_trades.trade_id ASC",
        (
            registration.window_start_utc,
            registration.window_end_utc,
            "contamination_window:%",
        ),
    ).fetchall()
    observed_families: set[str] = set()
    for raw in rows:
        row = {key: raw[key] for key in raw.keys()}
        matches = _row_families(row, registration.market_families)
        if not matches:
            continue
        trade_id = row.get("trade_id")
        if len(matches) != 1 or not isinstance(trade_id, str) or not trade_id:
            raise RuntimeCorpusMaterializationError(
                "selected snapshot trade does not have an exact registered family binding"
            )
        ledger = {field: row.get(f"ledger_{field}") for field in FEE_NET_REPLAY_FIELDS}
        _validate_fee_net_row(ledger, trade_id=trade_id)
        _validate_parent_settlement_link(row, ledger=ledger, trade_id=trade_id)
        observed_families.add(matches[0])
    return observed_families


def _validate_fee_net_row(row: dict[str, object], *, trade_id: str) -> None:
    required = set(FEE_NET_REPLAY_FIELDS)
    missing = sorted(field for field in required if row.get(field) is None)
    if missing:
        raise RuntimeCorpusMaterializationError(
            f"selected trade {trade_id!r} is missing fee-net replay fields: " + ", ".join(missing)
        )
    if row["accounting_version"] != PAPER_ACCOUNTING_VERSION:
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} has an unsupported accounting version")
    if not isinstance(row["settled_at"], str) or not isinstance(row["settlement_observation_sha256"], str):
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} does not carry settled fee-net evidence")
    net_fee = _decimal_field(row, "net_fee_dollars", trade_id)
    gross_entry = _decimal_field(row, "gross_entry_debit_dollars", trade_id)
    net_entry = _decimal_field(row, "net_entry_debit_dollars", trade_id)
    settlement_fee = _decimal_field(row, "settlement_fee_dollars", trade_id)
    settlement_refund = _decimal_field(row, "settlement_refund_dollars", trade_id)
    gross_payout = _decimal_field(row, "gross_settlement_payout_dollars", trade_id)
    net_payout = _decimal_field(row, "net_settlement_payout_dollars", trade_id)
    fee_net_pnl = _decimal_field(row, "fee_net_pnl_dollars", trade_id)
    if net_entry != gross_entry + net_fee:
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} has incoherent fee-net entry debit")
    if net_payout != gross_payout - settlement_fee + settlement_refund:
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} has incoherent fee-net settlement payout")
    if fee_net_pnl != net_payout - net_entry:
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} has incoherent fee-net P&L")


def _validate_parent_settlement_link(
    parent: dict[str, object],
    *,
    ledger: dict[str, object],
    trade_id: str,
) -> None:
    """Require the fee ledger to describe the same resolved canonical parent row."""

    if parent.get("resolved") != 1:
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} parent trade is not resolved")
    if parent.get("identity_status") != "mapped":
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} parent trade identity is not mapped")
    if parent.get("fee_net_accounting_version") != PAPER_ACCOUNTING_VERSION:
        raise RuntimeCorpusMaterializationError(
            f"selected trade {trade_id!r} parent trade has an unsupported fee-net accounting version"
        )
    if parent.get("settlement_observation_sha256") != ledger["settlement_observation_sha256"]:
        raise RuntimeCorpusMaterializationError(
            f"selected trade {trade_id!r} parent settlement observation does not match ledger"
        )
    if parent.get("settled_at") != ledger["settled_at"]:
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} parent settled_at does not match ledger")


def _assert_materialized_build(
    built: BuildResult,
    *,
    registration_proof: OOSRegistrationAttestation,
) -> None:
    if built.row_count <= 0:
        raise RuntimeCorpusMaterializationError("registered corpus contains zero materializable rows")
    if built.in_period_validation_only:
        raise RuntimeCorpusMaterializationError("registered corpus remained IN_PERIOD and is not materializable")
    if built.evidence_class != "registered_oos":
        raise RuntimeCorpusMaterializationError("builder did not emit registered OOS evidence")
    if built.registration_hash != registration_proof.registration_hash:
        raise RuntimeCorpusMaterializationError("builder did not preserve the exact protected OOS registration binding")


def _stamp_materialization_provenance(
    output_path: Path,
    *,
    root: Path,
    provenance: dict[str, str],
) -> None:
    """Atomically bind each emitted row to the attested snapshot and receipt."""

    output = _required_regular_file(output_path, root=root, label="built corpus output")
    temp = output.with_name(output.name + ".provenance.tmp")
    _assert_no_symlink_components(root, temp, allow_missing=True)
    try:
        with output.open(encoding="utf-8") as source, temp.open("x", encoding="utf-8") as destination:
            emitted = 0
            for raw_line in source:
                row = json.loads(raw_line)
                if not isinstance(row, dict):
                    raise RuntimeCorpusMaterializationError("built corpus contains a non-object JSONL row")
                duplicate = sorted(set(row).intersection(provenance))
                if duplicate:
                    raise RuntimeCorpusMaterializationError(
                        "built corpus already contains materialization provenance: " + ", ".join(duplicate)
                    )
                row.update(provenance)
                destination.write(json.dumps(row, sort_keys=True, ensure_ascii=True))
                destination.write("\n")
                emitted += 1
        if emitted <= 0:
            raise RuntimeCorpusMaterializationError("registered corpus contains zero materializable rows")
        os.replace(temp, output)
    except RuntimeCorpusMaterializationError:
        temp.unlink(missing_ok=True)
        raise
    except (OSError, TypeError, ValueError) as exc:
        temp.unlink(missing_ok=True)
        raise RuntimeCorpusMaterializationError(f"cannot stamp corpus materialization provenance: {exc}") from exc


def _decimal_field(row: dict[str, object], field: str, trade_id: str) -> Decimal:
    try:
        value = Decimal(str(row[field]))
    except (InvalidOperation, KeyError) as exc:
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} has invalid {field}") from exc
    if not value.is_finite():
        raise RuntimeCorpusMaterializationError(f"selected trade {trade_id!r} has invalid {field}")
    return value


def _validated_output_path(
    *,
    root: Path,
    registration: OOSRegistration,
    snapshot_sha256: str,
    output_path: Path | None,
) -> Path:
    candidate = output_path or (
        root / "logs" / "edge_replay" / f"corpus_{registration.id}_{snapshot_sha256[:16]}.jsonl"
    )
    resolved = _path_within(root, candidate, label="corpus output")
    relative = Path(_relative_to(resolved, root))
    if relative.parent != Path("logs/edge_replay"):
        raise RuntimeCorpusMaterializationError("corpus output must be a top-level file in logs/edge_replay")
    if not _OUTPUT_NAME_RE.fullmatch(resolved.name):
        raise RuntimeCorpusMaterializationError("corpus output must be named corpus_*.jsonl")
    _assert_no_symlink_components(root, resolved, allow_missing=True)
    if resolved.exists() and resolved.is_symlink():
        raise RuntimeCorpusMaterializationError("corpus output must not be a symlink")
    return resolved


def _staged_output_path(*, root: Path, output_path: Path) -> Path:
    """Reserve a private sibling so unverified rows never reach the final name."""

    staged = output_path.with_name(output_path.name + ".materializing")
    _assert_no_symlink_components(root, staged, allow_missing=True)
    if staged.exists() or staged.is_symlink():
        raise RuntimeCorpusMaterializationError("materialization staging output already exists")
    return staged


def _cleanup_staged_output(staged_output: Path | None) -> None:
    if staged_output is None:
        return
    for candidate in (
        staged_output,
        staged_output.with_name(staged_output.name + ".tmp"),
        staged_output.with_name(staged_output.name + ".provenance.tmp"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _repository_root(value: Path) -> Path:
    root = Path(value).expanduser()
    if not root.is_absolute():
        root = root.absolute()
    try:
        root_stat = os.lstat(root)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError("repository root is unavailable") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise RuntimeCorpusMaterializationError("repository root must be a real directory")
    return root


def _required_directory(path: Path, *, label: str) -> Path:
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise RuntimeCorpusMaterializationError(f"{label} must be a real directory")
    return path


def _path_within(root: Path, value: Path, *, label: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(candidate))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeCorpusMaterializationError(f"{label} must stay within repository root") from exc
    return candidate


def _path_from_data_relative(data_root: Path, value: str, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeCorpusMaterializationError(f"{label} must be data-relative")
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeCorpusMaterializationError(f"{label} must be a safe data-relative path")
    return _path_within(data_root, relative, label=label)


def _required_regular_file(path: Path, *, root: Path, label: str) -> Path:
    _assert_no_symlink_components(root, path, allow_missing=False)
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeCorpusMaterializationError(f"{label} must be a regular file")
    return path


def _assert_no_symlink_components(root: Path, path: Path, *, allow_missing: bool) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeCorpusMaterializationError("path escapes its approved root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            path_stat = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                continue
            raise RuntimeCorpusMaterializationError(f"required path is missing: {current}")
        except OSError as exc:
            raise RuntimeCorpusMaterializationError(f"cannot inspect path: {current}") from exc
        if stat.S_ISLNK(path_stat.st_mode):
            raise RuntimeCorpusMaterializationError(f"path must not contain a symlink: {current}")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError("cannot compare snapshot against runtime database") from exc


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise RuntimeCorpusMaterializationError("path escapes its approved root") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeCorpusMaterializationError(f"cannot hash {path}") from exc
    return digest.hexdigest()


def _canonical_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimeCorpusMaterializationError("materialized_at_utc must be timezone-aware")
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized


def _format_utc(value: datetime) -> str:
    return _canonical_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_registration_utc(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RuntimeCorpusMaterializationError("registered OOS window must use canonical UTC timestamps") from exc


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration-id", required=True)
    parser.add_argument(
        "--runtime-attestation",
        type=Path,
        default=DEFAULT_RUNTIME_ATTESTATION,
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    try:
        result = materialize_runtime_corpus(
            repo_root=args.repo_root,
            registration_id=args.registration_id,
            runtime_attestation_path=args.runtime_attestation,
            output_path=args.output,
        )
    except RuntimeCorpusMaterializationError as exc:
        print(f"[materialize_runtime_corpus] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_payload(repo_root=_repository_root(args.repo_root)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
