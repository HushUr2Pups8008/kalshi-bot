from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from trading.legacy_pending_finalization import (
    FINALIZATION_CERTIFICATE_SCHEMA_VERSION,
    FINALIZATION_PLAN_SCHEMA_VERSION,
    LegacyPendingArchiveTarget,
    LegacyPendingFamilySummary,
    LegacyPendingFinalizationCertificate,
    PayloadInventoryEntry,
    SealedLegacyPendingFinalizationPlan,
    finalization_plan_sha256,
    parse_legacy_pending_finalization_certificate,
    parse_legacy_pending_finalization_plan,
    serialize_legacy_pending_finalization_certificate,
    serialize_legacy_pending_finalization_plan,
)


def _family_summary() -> LegacyPendingFamilySummary:
    return LegacyPendingFamilySummary(
        pending_root_relative_to_storage_root="data/legacy_pending_paper_cohorts",
        legacy_db_relative_to_storage_root="data/paper_trades.db",
        shared_legacy_snapshot_sha256="1" * 64,
        shared_legacy_baseline_attestation="2" * 64,
        shared_legacy_open_rows_sha256="3" * 64,
        shared_legacy_open_trade_count=2,
        pending_manifest_sha256s=("b" * 64, "a" * 64),
        pending_cohort_ids=("legacy-pending-b", "legacy-pending-a"),
        frozen_trade_ids=("trade-2", "trade-1"),
        applied_receipt_sha256s=("f" * 64, "e" * 64),
        root_db_sha256="4" * 64,
    )


def _archive_target() -> LegacyPendingArchiveTarget:
    return LegacyPendingArchiveTarget(
        archived_root_relative_to_storage_root=(
            "data/finalized_legacy_pending_paper_cohorts/finalization-20260801t180000z"
        )
    )


def _inventory_entries() -> tuple[PayloadInventoryEntry, ...]:
    return (
        PayloadInventoryEntry(
            path_relative_to_archive_root="payload/legacy_pending_b/manifest.json",
            file_type="file",
            size_bytes=29,
            sha256="c" * 64,
        ),
        PayloadInventoryEntry(
            path_relative_to_archive_root="payload/legacy_snapshot/paper_trades.db",
            file_type="file",
            size_bytes=17,
            sha256="d" * 64,
        ),
    )


def test_sealed_finalization_plan_serializes_exact_canonical_fields_and_hashes():
    plan = SealedLegacyPendingFinalizationPlan(
        family_summary=_family_summary(),
        archive_target=_archive_target(),
        payload_inventory=tuple(reversed(_inventory_entries())),
    )

    expected_payload = {
        "archived_root_relative_to_storage_root": (
            "data/finalized_legacy_pending_paper_cohorts/finalization-20260801t180000z"
        ),
        "applied_receipt_sha256s": ["e" * 64, "f" * 64],
        "frozen_trade_ids": ["trade-1", "trade-2"],
        "legacy_db_relative_to_storage_root": "data/paper_trades.db",
        "payload_inventory": [
            {
                "file_type": "file",
                "path_relative_to_archive_root": "payload/legacy_pending_b/manifest.json",
                "sha256": "c" * 64,
                "size_bytes": 29,
            },
            {
                "file_type": "file",
                "path_relative_to_archive_root": "payload/legacy_snapshot/paper_trades.db",
                "sha256": "d" * 64,
                "size_bytes": 17,
            },
        ],
        "payload_inventory_sha256": hashlib.sha256(
            json.dumps(
                [
                    {
                        "file_type": "file",
                        "path_relative_to_archive_root": "payload/legacy_pending_b/manifest.json",
                        "sha256": "c" * 64,
                        "size_bytes": 29,
                    },
                    {
                        "file_type": "file",
                        "path_relative_to_archive_root": "payload/legacy_snapshot/paper_trades.db",
                        "sha256": "d" * 64,
                        "size_bytes": 17,
                    },
                ],
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        "pending_cohort_ids": ["legacy-pending-a", "legacy-pending-b"],
        "pending_manifest_sha256s": ["a" * 64, "b" * 64],
        "pending_root_relative_to_storage_root": "data/legacy_pending_paper_cohorts",
        "root_db_sha256": "4" * 64,
        "schema_version": FINALIZATION_PLAN_SCHEMA_VERSION,
        "shared_legacy_baseline_attestation": "2" * 64,
        "shared_legacy_open_rows_sha256": "3" * 64,
        "shared_legacy_open_trade_count": 2,
        "shared_legacy_snapshot_sha256": "1" * 64,
    }

    assert serialize_legacy_pending_finalization_plan(plan) == json.dumps(
        expected_payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert plan.payload_inventory_sha256 == expected_payload["payload_inventory_sha256"]
    assert finalization_plan_sha256(plan) == hashlib.sha256(
        serialize_legacy_pending_finalization_plan(plan).encode("utf-8")
    ).hexdigest()
    assert parse_legacy_pending_finalization_plan(expected_payload) == plan


def test_finalization_certificate_serializes_exact_canonical_fields():
    plan = SealedLegacyPendingFinalizationPlan(
        family_summary=_family_summary(),
        archive_target=_archive_target(),
        payload_inventory=_inventory_entries(),
    )
    certificate = LegacyPendingFinalizationCertificate(
        finalization_id="finalization-20260801t180000z",
        created_at_utc=datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc),
        family_summary=plan.family_summary,
        archive_target=plan.archive_target,
        finalization_plan_sha256=finalization_plan_sha256(plan),
        payload_inventory_sha256=plan.payload_inventory_sha256,
        operator_confirmation=(
            "finalize legacy_pending data/finalized_legacy_pending_paper_cohorts/"
            "finalization-20260801t180000z"
        ),
    )

    expected_payload = {
        "applied_receipt_sha256s": ["e" * 64, "f" * 64],
        "archived_root_relative_to_storage_root": (
            "data/finalized_legacy_pending_paper_cohorts/finalization-20260801t180000z"
        ),
        "created_at_utc": "2026-08-01T18:00:00+00:00",
        "finalization_id": "finalization-20260801t180000z",
        "finalization_plan_sha256": finalization_plan_sha256(plan),
        "frozen_trade_ids": ["trade-1", "trade-2"],
        "legacy_db_relative_to_storage_root": "data/paper_trades.db",
        "operator_confirmation": (
            "finalize legacy_pending data/finalized_legacy_pending_paper_cohorts/"
            "finalization-20260801t180000z"
        ),
        "payload_inventory_sha256": plan.payload_inventory_sha256,
        "pending_cohort_ids": ["legacy-pending-a", "legacy-pending-b"],
        "pending_manifest_sha256s": ["a" * 64, "b" * 64],
        "pending_root_relative_to_storage_root": "data/legacy_pending_paper_cohorts",
        "root_db_sha256": "4" * 64,
        "schema_version": FINALIZATION_CERTIFICATE_SCHEMA_VERSION,
        "shared_legacy_baseline_attestation": "2" * 64,
        "shared_legacy_open_rows_sha256": "3" * 64,
        "shared_legacy_open_trade_count": 2,
        "shared_legacy_snapshot_sha256": "1" * 64,
    }

    assert serialize_legacy_pending_finalization_certificate(certificate) == json.dumps(
        expected_payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert parse_legacy_pending_finalization_certificate(expected_payload) == certificate


def test_payload_inventory_rejects_control_files_from_hashed_domain():
    with pytest.raises(ValueError, match="payload inventory path_relative_to_archive_root is invalid"):
        PayloadInventoryEntry(
            path_relative_to_archive_root="control/finalization_certificate.json",
            file_type="file",
            size_bytes=1,
            sha256="a" * 64,
        )

    with pytest.raises(ValueError, match="payload inventory path_relative_to_archive_root is invalid"):
        PayloadInventoryEntry(
            path_relative_to_archive_root="control/legacy_pending_finalization_plan.json",
            file_type="file",
            size_bytes=1,
            sha256="a" * 64,
        )


def test_contract_rejects_empty_pending_family_and_duplicate_manifest_hashes():
    with pytest.raises(ValueError, match="pending family pending_manifest_sha256s is invalid"):
        LegacyPendingFamilySummary(
            pending_root_relative_to_storage_root="data/legacy_pending_paper_cohorts",
            legacy_db_relative_to_storage_root="data/paper_trades.db",
            shared_legacy_snapshot_sha256="1" * 64,
            shared_legacy_baseline_attestation="2" * 64,
            shared_legacy_open_rows_sha256="3" * 64,
            shared_legacy_open_trade_count=1,
            pending_manifest_sha256s=(),
            pending_cohort_ids=("legacy-pending-a",),
            frozen_trade_ids=("trade-1",),
            applied_receipt_sha256s=("f" * 64,),
            root_db_sha256="4" * 64,
        )

    with pytest.raises(ValueError, match="pending family pending_manifest_sha256s is invalid"):
        LegacyPendingFamilySummary(
            pending_root_relative_to_storage_root="data/legacy_pending_paper_cohorts",
            legacy_db_relative_to_storage_root="data/paper_trades.db",
            shared_legacy_snapshot_sha256="1" * 64,
            shared_legacy_baseline_attestation="2" * 64,
            shared_legacy_open_rows_sha256="3" * 64,
            shared_legacy_open_trade_count=1,
            pending_manifest_sha256s=("a" * 64, "a" * 64),
            pending_cohort_ids=("legacy-pending-a",),
            frozen_trade_ids=("trade-1",),
            applied_receipt_sha256s=("f" * 64,),
            root_db_sha256="4" * 64,
        )


def test_parsers_reject_noncanonical_ids_timestamps_and_sha256():
    plan_payload = {
        "schema_version": FINALIZATION_PLAN_SCHEMA_VERSION,
        "pending_root_relative_to_storage_root": "data/legacy_pending_paper_cohorts",
        "archived_root_relative_to_storage_root": (
            "data/finalized_legacy_pending_paper_cohorts/finalization-20260801t180000z"
        ),
        "legacy_db_relative_to_storage_root": "data/paper_trades.db",
        "shared_legacy_snapshot_sha256": "1" * 64,
        "shared_legacy_baseline_attestation": "2" * 64,
        "shared_legacy_open_rows_sha256": "3" * 64,
        "shared_legacy_open_trade_count": 1,
        "pending_manifest_sha256s": ["a" * 64],
        "pending_cohort_ids": ["legacy-pending-a"],
        "frozen_trade_ids": ["trade-1"],
        "applied_receipt_sha256s": ["f" * 64],
        "root_db_sha256": "not-a-sha",
        "payload_inventory": [
            {
                "path_relative_to_archive_root": "payload/legacy_pending_a/manifest.json",
                "file_type": "file",
                "size_bytes": 1,
                "sha256": "4" * 64,
            }
        ],
        "payload_inventory_sha256": "5" * 64,
    }
    with pytest.raises(ValueError, match="pending family root_db_sha256 is invalid"):
        parse_legacy_pending_finalization_plan(plan_payload)

    certificate_payload = {
        "schema_version": FINALIZATION_CERTIFICATE_SCHEMA_VERSION,
        "finalization_id": " finalization-20260801t180000z",
        "created_at_utc": "2026-08-01T18:00:00",
        "pending_root_relative_to_storage_root": "data/legacy_pending_paper_cohorts",
        "archived_root_relative_to_storage_root": (
            "data/finalized_legacy_pending_paper_cohorts/finalization-20260801t180000z"
        ),
        "legacy_db_relative_to_storage_root": "data/paper_trades.db",
        "shared_legacy_snapshot_sha256": "1" * 64,
        "shared_legacy_baseline_attestation": "2" * 64,
        "shared_legacy_open_rows_sha256": "3" * 64,
        "shared_legacy_open_trade_count": 1,
        "pending_manifest_sha256s": ["a" * 64],
        "pending_cohort_ids": ["legacy-pending-a"],
        "frozen_trade_ids": ["trade-1"],
        "applied_receipt_sha256s": ["f" * 64],
        "finalization_plan_sha256": "6" * 64,
        "root_db_sha256": "4" * 64,
        "payload_inventory_sha256": "5" * 64,
        "operator_confirmation": "confirm finalization-20260801t180000z",
    }
    with pytest.raises(ValueError, match="finalization certificate finalization_id is invalid"):
        parse_legacy_pending_finalization_certificate(certificate_payload)
