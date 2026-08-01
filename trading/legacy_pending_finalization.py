"""Pure canonical contracts for legacy-pending finalization artifacts.

This module intentionally has no filesystem or database behavior. It defines the
sealed plan, payload inventory, and certificate shapes that later finalization
steps will consume.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Mapping, Sequence


FINALIZATION_PLAN_SCHEMA_VERSION = 1
FINALIZATION_CERTIFICATE_SCHEMA_VERSION = 1
_CONTROL_ARTIFACT_BASENAMES = frozenset(
    {
        "finalization_certificate.json",
        "legacy_pending_finalization_plan.json",
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
            _sorted_unique_strings(
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
            _require_exact_string(
                self.operator_confirmation,
                "finalization certificate operator_confirmation",
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
        payload_inventory=_parse_payload_inventory(value["payload_inventory"]),
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
    return LegacyPendingFamilySummary(
        pending_root_relative_to_storage_root=value["pending_root_relative_to_storage_root"],
        legacy_db_relative_to_storage_root=value["legacy_db_relative_to_storage_root"],
        shared_legacy_snapshot_sha256=value["shared_legacy_snapshot_sha256"],
        shared_legacy_baseline_attestation=value["shared_legacy_baseline_attestation"],
        shared_legacy_open_rows_sha256=value["shared_legacy_open_rows_sha256"],
        shared_legacy_open_trade_count=value["shared_legacy_open_trade_count"],
        pending_manifest_sha256s=tuple(_require_sequence(value["pending_manifest_sha256s"])),
        pending_cohort_ids=tuple(_require_sequence(value["pending_cohort_ids"])),
        frozen_trade_ids=tuple(_require_sequence(value["frozen_trade_ids"])),
        applied_receipt_sha256s=tuple(_require_sequence(value["applied_receipt_sha256s"])),
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


def _sorted_unique_strings(value: object, name: str) -> tuple[str, ...]:
    raw_items = _require_sequence(value)
    normalized = tuple(_require_exact_string(item, name) for item in raw_items)
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} is invalid")
    return tuple(sorted(normalized))


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


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
