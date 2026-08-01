"""Canonical contracts and filesystem-only apply flow for legacy-pending finalization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from scripts.reconcile_legacy_paper_receipts import (
    LegacyReceiptReconciliationError,
    _capture_plain_file_identity,
    _require_absolute_path_without_symlinks,
    _require_quiescent_sqlite_artifacts,
    _require_same_plain_file,
    _read_verified_plain_file,
    _runtime_lock as _receipt_runtime_lock,
)
from trading.paper_cohorts import (
    LEGACY_PENDING_BASELINE_COHORT_ID,
    discover_legacy_pending_paper_risk_cohorts,
    legacy_open_exposure_fingerprint,
    validate_legacy_pending_paper_cohort_manifest,
)
from trading.settlement_store import (
    _LEGACY_RECEIPT_APPLICATION_TABLE,
    _validate_existing_legacy_receipt_application,
    LegacyReceiptApplicationError,
    SettlementStore,
)


FINALIZATION_PLAN_SCHEMA_VERSION = 1
FINALIZATION_CERTIFICATE_SCHEMA_VERSION = 1
FINALIZATION_CONTROL_DIRNAME = "control"
FINALIZATION_PAYLOAD_DIRNAME = "payload"
FINALIZATION_PLAN_FILENAME = "legacy_pending_finalization_plan.json"
FINALIZATION_CERTIFICATE_FILENAME = "finalization_certificate.json"
_CONTROL_ARTIFACT_BASENAMES = frozenset(
    {
        FINALIZATION_CERTIFICATE_FILENAME,
        FINALIZATION_PLAN_FILENAME,
    }
)
_FINALIZATION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class PayloadInventoryEntry:
    path_relative_to_archive_root: str
    file_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        path_value = _require_relative_path(
            self.path_relative_to_archive_root,
            "payload inventory path_relative_to_archive_root",
        )
        if PurePosixPath(path_value).name in _CONTROL_ARTIFACT_BASENAMES:
            raise ValueError("payload inventory path_relative_to_archive_root is invalid")
        if self.file_type != "file":
            raise ValueError("payload inventory file_type is invalid")
        size_value = _require_nonnegative_int(
            self.size_bytes,
            "payload inventory size_bytes",
        )
        sha_value = _require_sha256(self.sha256, "payload inventory sha256")
        object.__setattr__(self, "path_relative_to_archive_root", path_value)
        object.__setattr__(self, "size_bytes", size_value)
        object.__setattr__(self, "sha256", sha_value)

    def to_dict(self) -> dict[str, object]:
        return {
            "path_relative_to_archive_root": self.path_relative_to_archive_root,
            "file_type": self.file_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PayloadInventoryEntry":
        if not isinstance(value, Mapping):
            raise ValueError("payload inventory entry is invalid")
        expected_fields = {
            "path_relative_to_archive_root",
            "file_type",
            "size_bytes",
            "sha256",
        }
        if set(value) != expected_fields:
            raise ValueError("payload inventory entry is invalid")
        return cls(
            path_relative_to_archive_root=value["path_relative_to_archive_root"],
            file_type=value["file_type"],
            size_bytes=value["size_bytes"],
            sha256=value["sha256"],
        )


@dataclass(frozen=True)
class LegacyPendingFamilySummary:
    pending_root_relative_to_storage_root: str
    legacy_db_relative_to_storage_root: str
    shared_legacy_snapshot_sha256: str
    shared_legacy_baseline_attestation: str
    shared_legacy_open_rows_sha256: str
    shared_legacy_open_trade_count: int
    pending_manifest_sha256s: tuple[str, ...]
    pending_cohort_ids: tuple[str, ...]
    frozen_trade_ids: tuple[str, ...]
    applied_receipt_sha256s: tuple[str, ...]
    root_db_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pending_root_relative_to_storage_root",
            _require_relative_path(
                self.pending_root_relative_to_storage_root,
                "pending family pending_root_relative_to_storage_root",
            ),
        )
        object.__setattr__(
            self,
            "legacy_db_relative_to_storage_root",
            _require_relative_path(
                self.legacy_db_relative_to_storage_root,
                "pending family legacy_db_relative_to_storage_root",
            ),
        )
        object.__setattr__(
            self,
            "shared_legacy_snapshot_sha256",
            _require_sha256(
                self.shared_legacy_snapshot_sha256,
                "pending family shared_legacy_snapshot_sha256",
            ),
        )
        object.__setattr__(
            self,
            "shared_legacy_baseline_attestation",
            _require_sha256(
                self.shared_legacy_baseline_attestation,
                "pending family shared_legacy_baseline_attestation",
            ),
        )
        object.__setattr__(
            self,
            "shared_legacy_open_rows_sha256",
            _require_sha256(
                self.shared_legacy_open_rows_sha256,
                "pending family shared_legacy_open_rows_sha256",
            ),
        )
        object.__setattr__(
            self,
            "shared_legacy_open_trade_count",
            _require_positive_int(
                self.shared_legacy_open_trade_count,
                "pending family shared_legacy_open_trade_count",
            ),
        )
        object.__setattr__(
            self,
            "pending_manifest_sha256s",
            _sorted_unique_sha256s(
                self.pending_manifest_sha256s,
                "pending family pending_manifest_sha256s",
            ),
        )
        object.__setattr__(
            self,
            "pending_cohort_ids",
            _sorted_unique_cohort_ids(
                self.pending_cohort_ids,
                "pending family pending_cohort_ids",
            ),
        )
        object.__setattr__(
            self,
            "frozen_trade_ids",
            _sorted_unique_strings(
                self.frozen_trade_ids,
                "pending family frozen_trade_ids",
            ),
        )
        object.__setattr__(
            self,
            "applied_receipt_sha256s",
            _sorted_unique_sha256s(
                self.applied_receipt_sha256s,
                "pending family applied_receipt_sha256s",
            ),
        )
        object.__setattr__(
            self,
            "root_db_sha256",
            _require_sha256(self.root_db_sha256, "pending family root_db_sha256"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "pending_root_relative_to_storage_root": self.pending_root_relative_to_storage_root,
            "legacy_db_relative_to_storage_root": self.legacy_db_relative_to_storage_root,
            "shared_legacy_snapshot_sha256": self.shared_legacy_snapshot_sha256,
            "shared_legacy_baseline_attestation": self.shared_legacy_baseline_attestation,
            "shared_legacy_open_rows_sha256": self.shared_legacy_open_rows_sha256,
            "shared_legacy_open_trade_count": self.shared_legacy_open_trade_count,
            "pending_manifest_sha256s": list(self.pending_manifest_sha256s),
            "pending_cohort_ids": list(self.pending_cohort_ids),
            "frozen_trade_ids": list(self.frozen_trade_ids),
            "applied_receipt_sha256s": list(self.applied_receipt_sha256s),
            "root_db_sha256": self.root_db_sha256,
        }


@dataclass(frozen=True)
class LegacyPendingArchiveTarget:
    archived_root_relative_to_storage_root: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "archived_root_relative_to_storage_root",
            _require_relative_path(
                self.archived_root_relative_to_storage_root,
                "archive target archived_root_relative_to_storage_root",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "archived_root_relative_to_storage_root": self.archived_root_relative_to_storage_root,
        }


@dataclass(frozen=True)
class _VerifiedPlainFile:
    path: Path
    identity: object
    sha256: str
    size_bytes: int
    label: str
    sqlite: bool = False


@dataclass(frozen=True)
class _VerifiedArchivedPayloadFile:
    verified_file: _VerifiedPlainFile
    path_relative_to_archive_root: str


@dataclass(frozen=True)
class SealedLegacyPendingFinalizationPlan:
    family_summary: LegacyPendingFamilySummary
    archive_target: LegacyPendingArchiveTarget
    payload_inventory: tuple[PayloadInventoryEntry, ...]
    schema_version: int = FINALIZATION_PLAN_SCHEMA_VERSION
    payload_inventory_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != FINALIZATION_PLAN_SCHEMA_VERSION
        ):
            raise ValueError("sealed finalization plan schema_version is invalid")
        if not isinstance(self.family_summary, LegacyPendingFamilySummary):
            raise ValueError("sealed finalization plan family_summary is invalid")
        if not isinstance(self.archive_target, LegacyPendingArchiveTarget):
            raise ValueError("sealed finalization plan archive_target is invalid")
        inventory = _sorted_inventory(self.payload_inventory)
        object.__setattr__(self, "payload_inventory", inventory)
        object.__setattr__(
            self,
            "payload_inventory_sha256",
            hashlib.sha256(_canonical_json([entry.to_dict() for entry in inventory]).encode("utf-8")).hexdigest(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            **self.family_summary.to_dict(),
            **self.archive_target.to_dict(),
            "payload_inventory": [entry.to_dict() for entry in self.payload_inventory],
            "payload_inventory_sha256": self.payload_inventory_sha256,
        }


@dataclass(frozen=True)
class LegacyPendingFinalizationCertificate:
    finalization_id: str
    created_at_utc: datetime
    family_summary: LegacyPendingFamilySummary
    archive_target: LegacyPendingArchiveTarget
    finalization_plan_sha256: str
    payload_inventory_sha256: str
    operator_confirmation: str
    schema_version: int = FINALIZATION_CERTIFICATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != FINALIZATION_CERTIFICATE_SCHEMA_VERSION
        ):
            raise ValueError("finalization certificate schema_version is invalid")
        object.__setattr__(
            self,
            "finalization_id",
            _require_finalization_id(self.finalization_id),
        )
        object.__setattr__(
            self,
            "created_at_utc",
            _require_utc_datetime(
                self.created_at_utc,
                "finalization certificate created_at_utc",
            ),
        )
        if not isinstance(self.family_summary, LegacyPendingFamilySummary):
            raise ValueError("finalization certificate family_summary is invalid")
        if not isinstance(self.archive_target, LegacyPendingArchiveTarget):
            raise ValueError("finalization certificate archive_target is invalid")
        if _archive_target_finalization_id(self.archive_target) != self.finalization_id:
            raise ValueError(
                "finalization certificate archived_root_relative_to_storage_root is invalid"
            )
        object.__setattr__(
            self,
            "finalization_plan_sha256",
            _require_sha256(
                self.finalization_plan_sha256,
                "finalization certificate finalization_plan_sha256",
            ),
        )
        object.__setattr__(
            self,
            "payload_inventory_sha256",
            _require_sha256(
                self.payload_inventory_sha256,
                "finalization certificate payload_inventory_sha256",
            ),
        )
        object.__setattr__(
            self,
            "operator_confirmation",
            _require_operator_confirmation(
                self.operator_confirmation,
                archive_target=self.archive_target,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "finalization_id": self.finalization_id,
            "created_at_utc": _canonical_utc_datetime_text(self.created_at_utc),
            **self.family_summary.to_dict(),
            **self.archive_target.to_dict(),
            "finalization_plan_sha256": self.finalization_plan_sha256,
            "payload_inventory_sha256": self.payload_inventory_sha256,
            "operator_confirmation": self.operator_confirmation,
        }


@dataclass(frozen=True)
class LegacyPendingFinalizationApplyResult:
    archive_root: Path
    certificate: LegacyPendingFinalizationCertificate
    replayed: bool


def serialize_legacy_pending_finalization_plan(
    plan: SealedLegacyPendingFinalizationPlan,
) -> str:
    if not isinstance(plan, SealedLegacyPendingFinalizationPlan):
        raise ValueError("sealed finalization plan is invalid")
    return _canonical_json(plan.to_dict())


def finalization_plan_sha256(plan: SealedLegacyPendingFinalizationPlan) -> str:
    return hashlib.sha256(serialize_legacy_pending_finalization_plan(plan).encode("utf-8")).hexdigest()


def parse_legacy_pending_finalization_plan(
    value: Mapping[str, object],
) -> SealedLegacyPendingFinalizationPlan:
    if not isinstance(value, Mapping):
        raise ValueError("sealed finalization plan is invalid")
    expected_fields = {
        "schema_version",
        "pending_root_relative_to_storage_root",
        "legacy_db_relative_to_storage_root",
        "shared_legacy_snapshot_sha256",
        "shared_legacy_baseline_attestation",
        "shared_legacy_open_rows_sha256",
        "shared_legacy_open_trade_count",
        "pending_manifest_sha256s",
        "pending_cohort_ids",
        "frozen_trade_ids",
        "applied_receipt_sha256s",
        "root_db_sha256",
        "archived_root_relative_to_storage_root",
        "payload_inventory",
        "payload_inventory_sha256",
    }
    if set(value) != expected_fields:
        raise ValueError("sealed finalization plan is invalid")
    plan = SealedLegacyPendingFinalizationPlan(
        schema_version=value["schema_version"],
        family_summary=_parse_pending_family_summary(value),
        archive_target=LegacyPendingArchiveTarget(
            archived_root_relative_to_storage_root=value["archived_root_relative_to_storage_root"],
        ),
        payload_inventory=_parse_canonical_payload_inventory(value["payload_inventory"]),
    )
    payload_inventory_sha256 = _require_sha256(
        value["payload_inventory_sha256"],
        "sealed finalization plan payload_inventory_sha256",
    )
    if payload_inventory_sha256 != plan.payload_inventory_sha256:
        raise ValueError("sealed finalization plan payload_inventory_sha256 is invalid")
    return plan


def serialize_legacy_pending_finalization_certificate(
    certificate: LegacyPendingFinalizationCertificate,
) -> str:
    if not isinstance(certificate, LegacyPendingFinalizationCertificate):
        raise ValueError("finalization certificate is invalid")
    return _canonical_json(certificate.to_dict())


def parse_legacy_pending_finalization_certificate(
    value: Mapping[str, object],
) -> LegacyPendingFinalizationCertificate:
    if not isinstance(value, Mapping):
        raise ValueError("finalization certificate is invalid")
    expected_fields = {
        "schema_version",
        "finalization_id",
        "created_at_utc",
        "pending_root_relative_to_storage_root",
        "legacy_db_relative_to_storage_root",
        "shared_legacy_snapshot_sha256",
        "shared_legacy_baseline_attestation",
        "shared_legacy_open_rows_sha256",
        "shared_legacy_open_trade_count",
        "pending_manifest_sha256s",
        "pending_cohort_ids",
        "frozen_trade_ids",
        "applied_receipt_sha256s",
        "root_db_sha256",
        "archived_root_relative_to_storage_root",
        "finalization_plan_sha256",
        "payload_inventory_sha256",
        "operator_confirmation",
    }
    if set(value) != expected_fields:
        raise ValueError("finalization certificate is invalid")
    finalization_id = _require_finalization_id(value["finalization_id"])
    return LegacyPendingFinalizationCertificate(
        schema_version=value["schema_version"],
        finalization_id=finalization_id,
        created_at_utc=_parse_utc_datetime(
            value["created_at_utc"],
            "finalization certificate created_at_utc",
        ),
        family_summary=_parse_pending_family_summary(value),
        archive_target=LegacyPendingArchiveTarget(
            archived_root_relative_to_storage_root=value["archived_root_relative_to_storage_root"],
        ),
        finalization_plan_sha256=value["finalization_plan_sha256"],
        payload_inventory_sha256=value["payload_inventory_sha256"],
        operator_confirmation=value["operator_confirmation"],
    )


def _parse_pending_family_summary(
    value: Mapping[str, object],
) -> LegacyPendingFamilySummary:
    pending_manifest_sha256s = _require_canonical_sha256_sequence(
        value["pending_manifest_sha256s"],
        "pending family pending_manifest_sha256s",
    )
    pending_cohort_ids = _require_canonical_cohort_id_sequence(
        value["pending_cohort_ids"],
        "pending family pending_cohort_ids",
    )
    frozen_trade_ids = _require_canonical_string_sequence(
        value["frozen_trade_ids"],
        "pending family frozen_trade_ids",
    )
    applied_receipt_sha256s = _require_canonical_sha256_sequence(
        value["applied_receipt_sha256s"],
        "pending family applied_receipt_sha256s",
    )
    return LegacyPendingFamilySummary(
        pending_root_relative_to_storage_root=value["pending_root_relative_to_storage_root"],
        legacy_db_relative_to_storage_root=value["legacy_db_relative_to_storage_root"],
        shared_legacy_snapshot_sha256=value["shared_legacy_snapshot_sha256"],
        shared_legacy_baseline_attestation=value["shared_legacy_baseline_attestation"],
        shared_legacy_open_rows_sha256=value["shared_legacy_open_rows_sha256"],
        shared_legacy_open_trade_count=value["shared_legacy_open_trade_count"],
        pending_manifest_sha256s=pending_manifest_sha256s,
        pending_cohort_ids=pending_cohort_ids,
        frozen_trade_ids=frozen_trade_ids,
        applied_receipt_sha256s=applied_receipt_sha256s,
        root_db_sha256=value["root_db_sha256"],
    )


def _parse_payload_inventory(value: object) -> tuple[PayloadInventoryEntry, ...]:
    entries = _require_sequence(value)
    parsed: list[PayloadInventoryEntry] = []
    for entry in entries:
        if isinstance(entry, PayloadInventoryEntry):
            parsed.append(entry)
        else:
            parsed.append(PayloadInventoryEntry.from_dict(entry))
    return tuple(parsed)


def _parse_canonical_payload_inventory(value: object) -> tuple[PayloadInventoryEntry, ...]:
    parsed = _parse_payload_inventory(value)
    _require_canonical_inventory(parsed)
    return parsed


def _sorted_inventory(value: object) -> tuple[PayloadInventoryEntry, ...]:
    entries = _parse_payload_inventory(value)
    if not entries:
        raise ValueError("sealed finalization plan payload_inventory is invalid")
    sorted_entries = tuple(
        sorted(entries, key=lambda entry: entry.path_relative_to_archive_root)
    )
    if len({entry.path_relative_to_archive_root for entry in sorted_entries}) != len(sorted_entries):
        raise ValueError("sealed finalization plan payload_inventory is invalid")
    return sorted_entries


def _sorted_unique_sha256s(value: object, name: str) -> tuple[str, ...]:
    raw_items = _require_sequence(value)
    normalized = tuple(_require_sha256(item, name) for item in raw_items)
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} is invalid")
    return tuple(sorted(normalized))


def _sorted_unique_cohort_ids(value: object, name: str) -> tuple[str, ...]:
    raw_items = _require_sequence(value)
    normalized = tuple(_require_cohort_id(item, name) for item in raw_items)
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} is invalid")
    return tuple(sorted(normalized))


def _sorted_unique_strings(value: object, name: str) -> tuple[str, ...]:
    raw_items = _require_sequence(value)
    normalized = tuple(_require_exact_string(item, name) for item in raw_items)
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} is invalid")
    return tuple(sorted(normalized))


def _require_canonical_sha256_sequence(value: object, name: str) -> tuple[str, ...]:
    raw_items = tuple(_require_sequence(value))
    normalized = tuple(_require_sha256(item, name) for item in raw_items)
    if not normalized or len(set(normalized)) != len(normalized) or tuple(sorted(normalized)) != normalized:
        raise ValueError(f"{name} is invalid")
    return normalized


def _require_canonical_cohort_id_sequence(value: object, name: str) -> tuple[str, ...]:
    raw_items = tuple(_require_sequence(value))
    normalized = tuple(_require_cohort_id(item, name) for item in raw_items)
    if not normalized or len(set(normalized)) != len(normalized) or tuple(sorted(normalized)) != normalized:
        raise ValueError(f"{name} is invalid")
    return normalized


def _require_canonical_string_sequence(value: object, name: str) -> tuple[str, ...]:
    raw_items = tuple(_require_sequence(value))
    normalized = tuple(_require_exact_string(item, name) for item in raw_items)
    if not normalized or len(set(normalized)) != len(normalized) or tuple(sorted(normalized)) != normalized:
        raise ValueError(f"{name} is invalid")
    return normalized


def _require_canonical_inventory(value: tuple[PayloadInventoryEntry, ...]) -> tuple[PayloadInventoryEntry, ...]:
    if not value:
        raise ValueError("sealed finalization plan payload_inventory is invalid")
    paths = tuple(entry.path_relative_to_archive_root for entry in value)
    if len(set(paths)) != len(paths) or tuple(sorted(paths)) != paths:
        raise ValueError("sealed finalization plan payload_inventory is invalid")
    return value


def _require_sequence(value: object) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("sequence is invalid")
    return value


def _require_relative_path(value: object, name: str) -> str:
    text = _require_exact_string(value, name)
    if "\\" in text:
        raise ValueError(f"{name} is invalid")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{name} is invalid")
    if path.as_posix() != text:
        raise ValueError(f"{name} is invalid")
    return text


def _require_exact_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} is invalid")
    return value


def _require_sha256(value: object, name: str) -> str:
    text = _require_exact_string(value, name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{name} is invalid")
    return text


def _require_cohort_id(value: object, name: str) -> str:
    text = _require_exact_string(value, name)
    if not _FINALIZATION_ID_PATTERN.fullmatch(text):
        raise ValueError(f"{name} is invalid")
    return text


def _require_nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} is invalid")
    return value


def _require_positive_int(value: object, name: str) -> int:
    parsed = _require_nonnegative_int(value, name)
    if parsed <= 0:
        raise ValueError(f"{name} is invalid")
    return parsed


def _parse_utc_datetime(value: object, name: str) -> datetime:
    text = _require_exact_string(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} is invalid")
    canonical = parsed.astimezone(timezone.utc)
    if _canonical_utc_datetime_text(canonical) != text:
        raise ValueError(f"{name} is invalid")
    return canonical


def _require_utc_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} is invalid")
    return value.astimezone(timezone.utc)


def _canonical_utc_datetime_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _require_finalization_id(value: object) -> str:
    text = _require_exact_string(value, "finalization certificate finalization_id")
    if not _FINALIZATION_ID_PATTERN.fullmatch(text):
        raise ValueError("finalization certificate finalization_id is invalid")
    return text


def _archive_target_finalization_id(value: LegacyPendingArchiveTarget) -> str:
    return PurePosixPath(value.archived_root_relative_to_storage_root).name


def _require_operator_confirmation(
    value: object,
    *,
    archive_target: LegacyPendingArchiveTarget,
) -> str:
    text = _require_exact_string(value, "finalization certificate operator_confirmation")
    expected = f"finalize legacy_pending {archive_target.archived_root_relative_to_storage_root}"
    if text != expected:
        raise ValueError("finalization certificate operator_confirmation is invalid")
    return text


def plan_legacy_pending_finalization(
    *,
    db_path: Path | str,
    pending_root: Path | str,
    finalization_id: str,
    expected_root_sha256: str,
    expected_pending_manifest_sha256s: Sequence[str],
    expected_legacy_snapshot_sha256: str,
    expected_baseline_open_rows_sha256: str,
    expected_baseline_trade_ids: Sequence[str],
) -> SealedLegacyPendingFinalizationPlan:
    archive_target = LegacyPendingArchiveTarget(
        archived_root_relative_to_storage_root=(
            f"finalized_legacy_pending_paper_cohorts/{_require_finalization_id(finalization_id)}"
        )
    )
    root_file = _require_read_only_sqlite_path(
        db_path,
        label="legacy root",
        expected_sha256=expected_root_sha256,
    )
    pending_root_path = _require_directory_without_symlinks(
        pending_root,
        label="legacy pending root",
    )
    cohorts = discover_legacy_pending_paper_risk_cohorts(root_file.path.parent)
    if len(cohorts) < 2 or cohorts[0].cohort_id != LEGACY_PENDING_BASELINE_COHORT_ID:
        raise ValueError("legacy pending family is invalid")
    if pending_root_path != root_file.path.parent / "legacy_pending_paper_cohorts":
        raise ValueError("legacy pending root is invalid")

    validated_bindings = []
    for cohort in cohorts[1:]:
        _revalidate_verified_plain_file(root_file)
        validated_bindings.append(
            validate_legacy_pending_paper_cohort_manifest(
                cohort,
                max_days_to_close=_manifest_max_days_to_close(cohort),
                legacy_db_path=root_file.path,
                legacy_starting_bankroll=None,
            )
        )
        _revalidate_verified_plain_file(root_file)
    validated_bindings = tuple(validated_bindings)
    if not validated_bindings:
        raise ValueError("legacy pending family is invalid")
    _require_shared_pending_binding_agreement(validated_bindings)

    payload_files = _verified_archived_payload_files(
        validated_bindings,
        storage_root=root_file.path.parent,
    )
    actual_manifest_sha256s = tuple(
        sorted(
            item.verified_file.sha256
            for item in payload_files
            if item.path_relative_to_archive_root.endswith("/cohort.json")
        )
    )
    expected_manifests = _require_canonical_sha256_sequence(
        expected_pending_manifest_sha256s,
        "expected_pending_manifest_sha256s",
    )
    if actual_manifest_sha256s != expected_manifests:
        raise ValueError("legacy pending manifest proof is invalid")

    baseline_binding = validated_bindings[0]
    baseline_snapshot = _verified_shared_snapshot_file(
        payload_files,
        expected_sha256=expected_legacy_snapshot_sha256,
    )
    snapshot_fingerprint = _legacy_open_exposure_fingerprint_verified(
        baseline_snapshot
    )
    if snapshot_fingerprint.rows_sha256 != baseline_binding.legacy_open_exposure.rows_sha256:
        raise ValueError("legacy pending baseline open rows proof is invalid")
    if snapshot_fingerprint.rows_sha256 != _require_sha256(
        expected_baseline_open_rows_sha256,
        "expected_baseline_open_rows_sha256",
    ):
        raise ValueError("legacy pending baseline open rows proof is invalid")
    frozen_trade_ids = _frozen_trade_ids(baseline_snapshot)
    expected_trade_ids_value = _require_canonical_string_sequence(
        expected_baseline_trade_ids,
        "expected_baseline_trade_ids",
    )
    if frozen_trade_ids != expected_trade_ids_value:
        raise ValueError("legacy pending baseline trade proof is invalid")
    if _unresolved_trade_count(root_file) != 0:
        raise ValueError("legacy root unresolved trade proof is invalid")

    receipt_sha256s = _validated_receipt_sha256s_for_frozen_trades(
        root_file,
        frozen_trade_ids,
    )
    _conservation_check(root_file)

    pending_root_relative = _relative_to_storage_root(
        pending_root_path,
        root_file.path.parent,
    )
    root_relative = _relative_to_storage_root(root_file.path, root_file.path.parent)
    payload_inventory = _deterministic_payload_inventory(payload_files)
    _revalidate_verified_plain_file(root_file)
    _revalidate_verified_plain_file(baseline_snapshot)
    _revalidate_payload_files(payload_files)
    return SealedLegacyPendingFinalizationPlan(
        family_summary=LegacyPendingFamilySummary(
            pending_root_relative_to_storage_root=pending_root_relative,
            legacy_db_relative_to_storage_root=root_relative,
            shared_legacy_snapshot_sha256=baseline_binding.legacy_snapshot_sha256,
            shared_legacy_baseline_attestation=baseline_binding.legacy_baseline_attestation,
            shared_legacy_open_rows_sha256=baseline_binding.legacy_open_exposure.rows_sha256,
            shared_legacy_open_trade_count=baseline_binding.legacy_open_exposure.unresolved_trade_count,
            pending_manifest_sha256s=actual_manifest_sha256s,
            pending_cohort_ids=tuple(binding.cohort.cohort_id for binding in validated_bindings),
            frozen_trade_ids=frozen_trade_ids,
            applied_receipt_sha256s=receipt_sha256s,
            root_db_sha256=_require_sha256(expected_root_sha256, "expected_root_sha256"),
        ),
        archive_target=archive_target,
        payload_inventory=payload_inventory,
    )


def apply_legacy_pending_finalization(
    *,
    db_path: Path | str,
    pending_root: Path | str,
    sealed_plan_path: Path | str,
    expected_finalization_plan_sha256: str,
    operator_confirmation: str,
    write: bool,
    clock: Callable[[], datetime] | None = None,
) -> LegacyPendingFinalizationApplyResult:
    if not write:
        raise ValueError("legacy pending finalization apply requires --write")
    reviewed_plan, reviewed_plan_bytes = _load_reviewed_finalization_plan(
        sealed_plan_path,
        expected_finalization_plan_sha256=expected_finalization_plan_sha256,
    )
    _require_operator_confirmation(
        operator_confirmation,
        archive_target=reviewed_plan.archive_target,
    )
    root_path = _require_absolute_path_without_symlinks(db_path, "legacy root")
    storage_root = root_path.parent
    archive_root = storage_root / reviewed_plan.archive_target.archived_root_relative_to_storage_root
    replay_certificate = _validate_published_archive(
        archive_root=archive_root,
        reviewed_plan=reviewed_plan,
        reviewed_plan_bytes=reviewed_plan_bytes,
        expected_finalization_plan_sha256=expected_finalization_plan_sha256,
    )
    if replay_certificate is not None:
        return LegacyPendingFinalizationApplyResult(
            archive_root=archive_root,
            certificate=replay_certificate,
            replayed=True,
        )
    pending_root_path = _require_absolute_path_without_symlinks(
        pending_root,
        "legacy pending root",
    )
    if not pending_root_path.exists():
        raise ValueError("legacy pending root is missing and no published archive is available")
    if pending_root_path != storage_root / reviewed_plan.family_summary.pending_root_relative_to_storage_root:
        raise ValueError("legacy pending root is invalid")
    if root_path != storage_root / reviewed_plan.family_summary.legacy_db_relative_to_storage_root:
        raise ValueError("legacy root is invalid")

    finalization_id = _archive_target_finalization_id(reviewed_plan.archive_target)
    archive_parent = _prepare_archive_parent(archive_root.parent)
    stage_root = _reserve_hidden_stage_root(
        archive_parent=archive_parent,
        finalization_id=finalization_id,
    )
    published = False
    try:
        with _runtime_lock_for_finalization(root_path):
            locked_plan = plan_legacy_pending_finalization(
                db_path=root_path,
                pending_root=pending_root_path,
                finalization_id=finalization_id,
                expected_root_sha256=reviewed_plan.family_summary.root_db_sha256,
                expected_pending_manifest_sha256s=reviewed_plan.family_summary.pending_manifest_sha256s,
                expected_legacy_snapshot_sha256=reviewed_plan.family_summary.shared_legacy_snapshot_sha256,
                expected_baseline_open_rows_sha256=reviewed_plan.family_summary.shared_legacy_open_rows_sha256,
                expected_baseline_trade_ids=reviewed_plan.family_summary.frozen_trade_ids,
            )
            locked_plan_bytes = serialize_legacy_pending_finalization_plan(locked_plan).encode(
                "utf-8"
            )
            if locked_plan_bytes != reviewed_plan_bytes:
                raise ValueError("sealed finalization plan changed under the runtime lock")
            if finalization_plan_sha256(locked_plan) != expected_finalization_plan_sha256:
                raise ValueError("sealed finalization plan SHA-256 changed under the runtime lock")

            _stage_archive_payload(
                plan=reviewed_plan,
                storage_root=storage_root,
                stage_root=stage_root,
            )
            staged_inventory = _recompute_payload_inventory(stage_root)
            if staged_inventory != reviewed_plan.payload_inventory:
                raise ValueError("staged payload inventory differs from the reviewed plan")
            if _inventory_sha256(staged_inventory) != reviewed_plan.payload_inventory_sha256:
                raise ValueError("staged payload inventory SHA-256 differs from the reviewed plan")

            certificate = _write_staged_control_files(
                stage_root=stage_root,
                reviewed_plan=reviewed_plan,
                reviewed_plan_bytes=reviewed_plan_bytes,
                expected_finalization_plan_sha256=expected_finalization_plan_sha256,
                operator_confirmation=operator_confirmation,
                clock=(clock or _utc_now),
            )
            _fsync_tree(stage_root)
            os.replace(stage_root, archive_root)
            published = True
            _fsync_directory(archive_parent)
            published_certificate = _validate_published_archive(
                archive_root=archive_root,
                reviewed_plan=reviewed_plan,
                reviewed_plan_bytes=reviewed_plan_bytes,
                expected_finalization_plan_sha256=expected_finalization_plan_sha256,
            )
            assert published_certificate is not None
            _remove_live_pending_root(
                pending_root=pending_root_path,
                archive_root=archive_root,
            )
            _fsync_directory(storage_root)
            return LegacyPendingFinalizationApplyResult(
                archive_root=archive_root,
                certificate=published_certificate,
                replayed=False,
            )
    finally:
        if not published:
            _cleanup_stage_root(stage_root)


def _runtime_lock_for_finalization(db_path: Path) -> object:
    return _receipt_runtime_lock(db_path)


def _load_reviewed_finalization_plan(
    path: Path | str,
    *,
    expected_finalization_plan_sha256: str,
) -> tuple[SealedLegacyPendingFinalizationPlan, bytes]:
    expected_sha = _require_sha256(
        expected_finalization_plan_sha256,
        "expected-finalization-plan-sha",
    )
    verified = _require_verified_plain_file(
        path,
        label="sealed finalization plan",
        expected_sha256=expected_sha,
        sqlite=False,
    )
    reviewed_bytes = _read_verified_plain_file(
        verified.path,
        verified.identity,
        verified.label,
    )
    try:
        payload = json.loads(reviewed_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sealed finalization plan is invalid") from exc
    reviewed_plan = parse_legacy_pending_finalization_plan(payload)
    if serialize_legacy_pending_finalization_plan(reviewed_plan).encode("utf-8") != reviewed_bytes:
        raise ValueError("sealed finalization plan is invalid")
    return reviewed_plan, reviewed_bytes


def _prepare_archive_parent(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return _require_directory_without_symlinks(path, label="finalized archive parent")


def _reserve_hidden_stage_root(*, archive_parent: Path, finalization_id: str) -> Path:
    if (archive_parent / finalization_id).exists():
        raise ValueError("finalization archive target already exists")
    for _attempt in range(32):
        candidate = archive_parent / f".{finalization_id}.{secrets.token_hex(6)}.tmp"
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return candidate
    raise ValueError("unable to reserve hidden finalization staging directory")


def _stage_archive_payload(
    *,
    plan: SealedLegacyPendingFinalizationPlan,
    storage_root: Path,
    stage_root: Path,
) -> None:
    for entry in plan.payload_inventory:
        destination = stage_root / entry.path_relative_to_archive_root
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = _source_path_for_payload_entry(entry, storage_root=storage_root)
        verified = _require_verified_plain_file(
            source,
            label="legacy pending payload file",
            expected_sha256=entry.sha256,
            sqlite=source.suffix == ".db",
        )
        data = _read_verified_plain_file(source, verified.identity, verified.label)
        _write_new_file(destination, data)


def _source_path_for_payload_entry(
    entry: PayloadInventoryEntry,
    *,
    storage_root: Path,
) -> Path:
    prefix = f"{FINALIZATION_PAYLOAD_DIRNAME}/"
    if not entry.path_relative_to_archive_root.startswith(prefix):
        raise ValueError("sealed finalization plan payload_inventory is invalid")
    return storage_root / entry.path_relative_to_archive_root[len(prefix) :]


def _write_staged_control_files(
    *,
    stage_root: Path,
    reviewed_plan: SealedLegacyPendingFinalizationPlan,
    reviewed_plan_bytes: bytes,
    expected_finalization_plan_sha256: str,
    operator_confirmation: str,
    clock: Callable[[], datetime],
) -> LegacyPendingFinalizationCertificate:
    control_root = stage_root / FINALIZATION_CONTROL_DIRNAME
    control_root.mkdir(parents=True, exist_ok=True)
    plan_path = control_root / FINALIZATION_PLAN_FILENAME
    certificate_path = control_root / FINALIZATION_CERTIFICATE_FILENAME
    _write_new_file(plan_path, reviewed_plan_bytes)
    certificate = LegacyPendingFinalizationCertificate(
        finalization_id=_archive_target_finalization_id(reviewed_plan.archive_target),
        created_at_utc=clock(),
        family_summary=reviewed_plan.family_summary,
        archive_target=reviewed_plan.archive_target,
        finalization_plan_sha256=expected_finalization_plan_sha256,
        payload_inventory_sha256=reviewed_plan.payload_inventory_sha256,
        operator_confirmation=operator_confirmation,
    )
    _write_new_file(
        certificate_path,
        serialize_legacy_pending_finalization_certificate(certificate).encode("utf-8"),
    )
    if _sha256_path(plan_path) != expected_finalization_plan_sha256:
        raise ValueError("staged sealed finalization plan SHA-256 is invalid")
    staged_plan, staged_plan_bytes = _load_reviewed_finalization_plan(
        plan_path,
        expected_finalization_plan_sha256=expected_finalization_plan_sha256,
    )
    if staged_plan != reviewed_plan or staged_plan_bytes != reviewed_plan_bytes:
        raise ValueError("staged sealed finalization plan is invalid")
    staged_inventory = _recompute_payload_inventory(stage_root)
    if staged_inventory != reviewed_plan.payload_inventory:
        raise ValueError("staged payload inventory differs from the reviewed plan")
    parsed_certificate = _load_finalization_certificate(certificate_path)
    if parsed_certificate != certificate:
        raise ValueError("staged finalization certificate is invalid")
    return certificate


def _validate_published_archive(
    *,
    archive_root: Path,
    reviewed_plan: SealedLegacyPendingFinalizationPlan,
    reviewed_plan_bytes: bytes,
    expected_finalization_plan_sha256: str,
) -> LegacyPendingFinalizationCertificate | None:
    if not archive_root.exists():
        return None
    if not archive_root.is_dir():
        raise ValueError("published finalization archive is invalid")
    plan_path = archive_root / FINALIZATION_CONTROL_DIRNAME / FINALIZATION_PLAN_FILENAME
    certificate_path = (
        archive_root / FINALIZATION_CONTROL_DIRNAME / FINALIZATION_CERTIFICATE_FILENAME
    )
    archived_plan, archived_plan_bytes = _load_reviewed_finalization_plan(
        plan_path,
        expected_finalization_plan_sha256=expected_finalization_plan_sha256,
    )
    if archived_plan != reviewed_plan or archived_plan_bytes != reviewed_plan_bytes:
        raise ValueError("published finalization archive plan is invalid")
    inventory = _recompute_payload_inventory(archive_root)
    if inventory != reviewed_plan.payload_inventory:
        raise ValueError("published finalization archive payload inventory is invalid")
    if _inventory_sha256(inventory) != reviewed_plan.payload_inventory_sha256:
        raise ValueError("published finalization archive payload inventory is invalid")
    certificate = _load_finalization_certificate(certificate_path)
    if certificate.family_summary != reviewed_plan.family_summary:
        raise ValueError("published finalization archive certificate is invalid")
    if certificate.archive_target != reviewed_plan.archive_target:
        raise ValueError("published finalization archive certificate is invalid")
    if certificate.finalization_plan_sha256 != expected_finalization_plan_sha256:
        raise ValueError("published finalization archive certificate is invalid")
    if certificate.payload_inventory_sha256 != reviewed_plan.payload_inventory_sha256:
        raise ValueError("published finalization archive certificate is invalid")
    return certificate


def _load_finalization_certificate(path: Path) -> LegacyPendingFinalizationCertificate:
    verified = _require_verified_plain_file(
        path,
        label="finalization certificate",
        expected_sha256=None,
        sqlite=False,
    )
    payload_bytes = _read_verified_plain_file(verified.path, verified.identity, verified.label)
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("finalization certificate is invalid") from exc
    certificate = parse_legacy_pending_finalization_certificate(payload)
    if serialize_legacy_pending_finalization_certificate(certificate).encode("utf-8") != payload_bytes:
        raise ValueError("finalization certificate is invalid")
    return certificate


def _recompute_payload_inventory(archive_root: Path) -> tuple[PayloadInventoryEntry, ...]:
    payload_root = archive_root / FINALIZATION_PAYLOAD_DIRNAME
    if not payload_root.is_dir():
        raise ValueError("published finalization archive payload inventory is invalid")
    discovered: list[PayloadInventoryEntry] = []
    for path in sorted(payload_root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(archive_root).as_posix()
        verified = _require_verified_plain_file(
            path,
            label="archived payload file",
            expected_sha256=None,
            sqlite=path.suffix == ".db",
        )
        discovered.append(
            PayloadInventoryEntry(
                path_relative_to_archive_root=relative,
                file_type="file",
                size_bytes=verified.size_bytes,
                sha256=verified.sha256,
            )
        )
    return _require_canonical_inventory(tuple(discovered))


def _remove_live_pending_root(*, pending_root: Path, archive_root: Path) -> None:
    if not archive_root.is_dir():
        raise ValueError("published finalization archive is invalid")
    if not pending_root.exists():
        return
    tombstone = pending_root.parent / f".{pending_root.name}.removed"
    if tombstone.exists():
        shutil.rmtree(tombstone)
    os.replace(pending_root, tombstone)
    shutil.rmtree(tombstone)


def _cleanup_stage_root(stage_root: Path) -> None:
    if stage_root.exists():
        shutil.rmtree(stage_root)


def _write_new_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    file_paths = sorted(path for path in root.rglob("*") if path.is_file())
    for path in file_paths:
        _fsync_file(path)
    dir_paths = sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for path in dir_paths:
        _fsync_directory(path)
    _fsync_directory(root)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _inventory_sha256(value: Sequence[PayloadInventoryEntry]) -> str:
    return hashlib.sha256(
        _canonical_json([entry.to_dict() for entry in value]).encode("utf-8")
    ).hexdigest()


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _verified_shared_snapshot_file(
    payload_files: Sequence[_VerifiedArchivedPayloadFile],
    *,
    expected_sha256: str,
) -> _VerifiedPlainFile:
    snapshot_files = [
        item.verified_file
        for item in payload_files
        if item.path_relative_to_archive_root.endswith("/legacy_cutover.db")
    ]
    if not snapshot_files:
        raise ValueError("legacy pending baseline snapshot proof is invalid")
    baseline_snapshot = snapshot_files[0]
    if baseline_snapshot.sha256 != _require_sha256(
        expected_sha256,
        "expected_legacy_snapshot_sha256",
    ):
        raise ValueError("legacy pending baseline snapshot proof is invalid")
    for snapshot_file in snapshot_files[1:]:
        if snapshot_file.sha256 != baseline_snapshot.sha256:
            raise ValueError("legacy pending baseline snapshot proof is invalid")
    return baseline_snapshot


def _legacy_open_exposure_fingerprint_verified(
    snapshot_file: _VerifiedPlainFile,
):
    _revalidate_verified_plain_file(snapshot_file)
    fingerprint = legacy_open_exposure_fingerprint(snapshot_file.path)
    _revalidate_verified_plain_file(snapshot_file)
    return fingerprint


def _conservation_check(root_file: _VerifiedPlainFile) -> None:
    _revalidate_verified_plain_file(root_file)
    with SettlementStore(root_file.path, read_only=True) as store:
        if not store.conservation(now=datetime.now(timezone.utc)).ok:
            raise ValueError("legacy root conservation proof is invalid")
    _revalidate_verified_plain_file(root_file)


def _verified_archived_payload_files(
    bindings: Sequence[object],
    *,
    storage_root: Path,
) -> tuple[_VerifiedArchivedPayloadFile, ...]:
    archived_files: list[_VerifiedArchivedPayloadFile] = []
    for binding in bindings:
        cohort_dir = Path(binding.cohort.db_path).parent
        manifest_path = cohort_dir / "cohort.json"
        manifest_file = _require_verified_plain_file(
            manifest_path,
            label="legacy pending manifest",
            expected_sha256=binding.manifest_sha256,
            sqlite=False,
        )
        snapshot_path = cohort_dir / "legacy_cutover.db"
        snapshot_file = _require_verified_plain_file(
            snapshot_path,
            label="legacy pending baseline snapshot",
            expected_sha256=binding.legacy_snapshot_sha256,
            sqlite=True,
        )
        cohort_db_file = _require_verified_plain_file(
            Path(binding.cohort.db_path),
            label="legacy pending cohort database",
            expected_sha256=None,
            sqlite=True,
        )
        archived_files.extend(
            (
                _VerifiedArchivedPayloadFile(
                    verified_file=manifest_file,
                    path_relative_to_archive_root=(
                        "payload/" + _relative_to_storage_root(manifest_path, storage_root)
                    ),
                ),
                _VerifiedArchivedPayloadFile(
                    verified_file=snapshot_file,
                    path_relative_to_archive_root=(
                        "payload/" + _relative_to_storage_root(snapshot_path, storage_root)
                    ),
                ),
                _VerifiedArchivedPayloadFile(
                    verified_file=cohort_db_file,
                    path_relative_to_archive_root=(
                        "payload/"
                        + _relative_to_storage_root(Path(binding.cohort.db_path), storage_root)
                    ),
                ),
            )
        )
    return tuple(archived_files)


def _manifest_max_days_to_close(cohort: object) -> float:
    manifest_path = Path(getattr(cohort, "db_path")).parent / "cohort.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    value = payload.get("max_days_to_close")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("legacy pending manifest horizon is invalid")
    return float(value)


def _require_directory_without_symlinks(value: Path | str, *, label: str) -> Path:
    path = _require_absolute_path_without_symlinks(value, label)
    if not path.is_dir():
        raise ValueError(f"{label} directory is invalid")
    return path


def _require_verified_plain_file(
    value: Path | str,
    *,
    label: str,
    expected_sha256: str | None,
    sqlite: bool,
) -> _VerifiedPlainFile:
    try:
        path = _require_absolute_path_without_symlinks(value, label)
        if not path.is_file():
            raise ValueError(f"{label} is invalid")
        identity = _capture_plain_file_identity(path, label)
        if sqlite:
            _require_quiescent_sqlite_artifacts(path, label)
        data = _read_verified_plain_file(path, identity, label)
    except LegacyReceiptReconciliationError as exc:
        raise ValueError(str(exc)) from exc
    sha256 = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and sha256 != _require_sha256(expected_sha256, label):
        raise ValueError(f"{label} SHA-256 does not match the reviewed value")
    verified = _VerifiedPlainFile(
        path=path,
        identity=identity,
        sha256=sha256,
        size_bytes=len(data),
        label=label,
        sqlite=sqlite,
    )
    if sqlite:
        _revalidate_verified_plain_file(verified)
        uri = f"{path.as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as conn:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise ValueError(f"{label} database is invalid")
                if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError(f"{label} database is invalid")
        except sqlite3.Error as exc:
            raise ValueError(f"{label} database is invalid") from exc
        _revalidate_verified_plain_file(verified)
    return verified


def _require_read_only_sqlite_path(
    value: Path | str,
    *,
    label: str,
    expected_sha256: str,
) -> _VerifiedPlainFile:
    return _require_verified_plain_file(
        value,
        label=label,
        expected_sha256=expected_sha256,
        sqlite=True,
    )


def _require_shared_pending_binding_agreement(bindings: Sequence[object]) -> None:
    shared = {
        (
            Path(binding.legacy_db_path).resolve(),
            binding.legacy_starting_bankroll,
            binding.legacy_baseline_attestation,
            binding.legacy_snapshot_sha256,
            binding.legacy_open_exposure.unresolved_trade_count,
            binding.legacy_open_exposure.rows_sha256,
        )
        for binding in bindings
    }
    if len(shared) != 1:
        raise ValueError("legacy pending family binding agreement is invalid")


def _revalidate_verified_plain_file(verified: _VerifiedPlainFile) -> None:
    try:
        _require_same_plain_file(verified.path, verified.identity, verified.label)
        if verified.sqlite:
            _require_quiescent_sqlite_artifacts(verified.path, verified.label)
        data = _read_verified_plain_file(verified.path, verified.identity, verified.label)
    except LegacyReceiptReconciliationError as exc:
        raise ValueError(str(exc)) from exc
    if len(data) != verified.size_bytes:
        raise ValueError(f"{verified.label} identity changed while reading")
    if hashlib.sha256(data).hexdigest() != verified.sha256:
        raise ValueError(f"{verified.label} SHA-256 does not match the reviewed value")


def _frozen_trade_ids(snapshot_file: _VerifiedPlainFile) -> tuple[str, ...]:
    _revalidate_verified_plain_file(snapshot_file)
    uri = f"{snapshot_file.path.as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute(
                """
                SELECT trade_id
                FROM paper_trades
                WHERE resolved = 0
                ORDER BY trade_id
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("legacy pending baseline trade proof is invalid") from exc
    _revalidate_verified_plain_file(snapshot_file)
    trade_ids = tuple(str(row[0]) for row in rows)
    if not trade_ids or any(not item for item in trade_ids):
        raise ValueError("legacy pending baseline trade proof is invalid")
    return trade_ids


def _unresolved_trade_count(root_file: _VerifiedPlainFile) -> int:
    _revalidate_verified_plain_file(root_file)
    uri = f"{root_file.path.as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM paper_trades WHERE COALESCE(resolved, 0) = 0"
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("legacy root unresolved trade proof is invalid") from exc
    _revalidate_verified_plain_file(root_file)
    assert row is not None
    return int(row[0])


def _validated_receipt_sha256s_for_frozen_trades(
    root_file: _VerifiedPlainFile,
    frozen_trade_ids: Sequence[str],
) -> tuple[str, ...]:
    _revalidate_verified_plain_file(root_file)
    with SettlementStore(root_file.path, read_only=True) as store:
        conn = store._conn
        rows = conn.execute(
            f"""
            SELECT trade_id, observation_sha256, receipt_schema_version,
                   receipt_json, receipt_sha256, applied_at
            FROM {_LEGACY_RECEIPT_APPLICATION_TABLE}
            WHERE trade_id IN ({",".join("?" for _ in frozen_trade_ids)})
            ORDER BY trade_id
            """,
            tuple(frozen_trade_ids),
        ).fetchall()
        if len(rows) != len(frozen_trade_ids):
            raise ValueError("legacy pending receipt proof is invalid")
        receipt_sha256s: list[str] = []
        observation_ids: set[str] = set()
        seen_receipts: set[str] = set()
        for expected_trade_id, row in zip(frozen_trade_ids, rows, strict=True):
            if str(row["trade_id"]) != expected_trade_id:
                raise ValueError("legacy pending receipt proof is invalid")
            try:
                result = _validate_existing_legacy_receipt_application(
                    conn,
                    row,
                    _load_receipt_from_row(row),
                    applied_at=_parse_datetime(row["applied_at"]),
                )
            except (LegacyReceiptApplicationError, ValueError) as exc:
                raise ValueError("legacy pending receipt proof is invalid") from exc
            receipt_sha = str(row["receipt_sha256"])
            observation_sha = str(row["observation_sha256"])
            if receipt_sha in seen_receipts or observation_sha in observation_ids:
                raise ValueError("legacy pending receipt proof is invalid")
            seen_receipts.add(receipt_sha)
            observation_ids.add(observation_sha)
            receipt_sha256s.append(result.observation_sha256 and receipt_sha)
    _revalidate_verified_plain_file(root_file)
    return tuple(sorted(receipt_sha256s))


def _load_receipt_from_row(row: Mapping[str, object]):
    from trading.settlement_store import _load_legacy_receipt_application

    return _load_legacy_receipt_application(row).receipt


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("legacy pending receipt proof is invalid")
    return datetime.fromisoformat(value)


def _revalidate_payload_files(
    payload_files: Sequence[_VerifiedArchivedPayloadFile],
) -> None:
    for payload_file in payload_files:
        _revalidate_verified_plain_file(payload_file.verified_file)


def _deterministic_payload_inventory(
    payload_files: Sequence[_VerifiedArchivedPayloadFile],
) -> tuple[PayloadInventoryEntry, ...]:
    _revalidate_payload_files(payload_files)
    return tuple(
        PayloadInventoryEntry(
            path_relative_to_archive_root=item.path_relative_to_archive_root,
            file_type="file",
            size_bytes=item.verified_file.size_bytes,
            sha256=item.verified_file.sha256,
        )
        for item in sorted(
            payload_files,
            key=lambda item: item.path_relative_to_archive_root,
        )
    )


def _relative_to_storage_root(path: Path, storage_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(storage_root.resolve())
    except ValueError as exc:
        raise ValueError("legacy pending path is invalid") from exc
    return PurePosixPath(relative.as_posix()).as_posix()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
