from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.legacy_settlement_receipts import build_legacy_settlement_receipt
from trading.paper_cohorts import (
    initialize_legacy_pending_paper_cohort_manifest,
    legacy_open_exposure_fingerprint,
    resolve_runtime_paper_cohort,
)
from trading.paper_trader import PaperTrader
from trading.settlement import build_settlement_observation, MarketOutcome
from trading.settlement_store import SettlementStore
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
    plan_legacy_pending_finalization,
    serialize_legacy_pending_finalization_certificate,
    serialize_legacy_pending_finalization_plan,
)
from trading.venue import MarketRef, Venue


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


def _payload_inventory_sha256_for(entries: list[dict[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(
            entries,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


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
        "created_at_utc": "2026-08-01T18:00:00.000000+00:00",
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


def test_payload_inventory_preserves_file_byte_hash_domain_not_canonical_json_hash():
    raw_payload_bytes = b'{\n  "market":"KX-1",\n  "score":0.5\n}\n'
    raw_file_sha256 = hashlib.sha256(raw_payload_bytes).hexdigest()
    canonical_json_sha256 = hashlib.sha256(
        json.dumps(
            {"market": "KX-1", "score": 0.5},
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert raw_file_sha256 != canonical_json_sha256

    plan = SealedLegacyPendingFinalizationPlan(
        family_summary=_family_summary(),
        archive_target=_archive_target(),
        payload_inventory=(
            PayloadInventoryEntry(
                path_relative_to_archive_root="payload/legacy_pending_a/payload.json",
                file_type="file",
                size_bytes=len(raw_payload_bytes),
                sha256=raw_file_sha256,
            ),
        ),
    )

    serialized = serialize_legacy_pending_finalization_plan(plan)

    assert plan.payload_inventory[0].sha256 == raw_file_sha256
    assert raw_file_sha256 in serialized
    assert canonical_json_sha256 not in serialized


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


def test_contract_rejects_noncanonical_pending_cohort_ids():
    with pytest.raises(ValueError, match="pending family pending_cohort_ids is invalid"):
        LegacyPendingFamilySummary(
            pending_root_relative_to_storage_root="data/legacy_pending_paper_cohorts",
            legacy_db_relative_to_storage_root="data/paper_trades.db",
            shared_legacy_snapshot_sha256="1" * 64,
            shared_legacy_baseline_attestation="2" * 64,
            shared_legacy_open_rows_sha256="3" * 64,
            shared_legacy_open_trade_count=1,
            pending_manifest_sha256s=("a" * 64,),
            pending_cohort_ids=("UPPER/ID",),
            frozen_trade_ids=("trade-1",),
            applied_receipt_sha256s=("f" * 64,),
            root_db_sha256="4" * 64,
        )


def test_certificate_rejects_mismatched_target_and_confirmation_binding():
    plan = SealedLegacyPendingFinalizationPlan(
        family_summary=_family_summary(),
        archive_target=_archive_target(),
        payload_inventory=_inventory_entries(),
    )

    with pytest.raises(ValueError, match="finalization certificate archived_root_relative_to_storage_root is invalid"):
        LegacyPendingFinalizationCertificate(
            finalization_id="finalization-20260801t180000z",
            created_at_utc=datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc),
            family_summary=plan.family_summary,
            archive_target=LegacyPendingArchiveTarget(
                archived_root_relative_to_storage_root=(
                    "data/finalized_legacy_pending_paper_cohorts/finalization-20260801t180001z"
                )
            ),
            finalization_plan_sha256=finalization_plan_sha256(plan),
            payload_inventory_sha256=plan.payload_inventory_sha256,
            operator_confirmation=(
                "finalize legacy_pending data/finalized_legacy_pending_paper_cohorts/"
                "finalization-20260801t180000z"
            ),
        )

    with pytest.raises(ValueError, match="finalization certificate operator_confirmation is invalid"):
        LegacyPendingFinalizationCertificate(
            finalization_id="finalization-20260801t180000z",
            created_at_utc=datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc),
            family_summary=plan.family_summary,
            archive_target=plan.archive_target,
            finalization_plan_sha256=finalization_plan_sha256(plan),
            payload_inventory_sha256=plan.payload_inventory_sha256,
            operator_confirmation="yes",
        )


def test_parsers_reject_noncanonical_input_order_instead_of_silently_sorting():
    reversed_plan_payload = {
        "schema_version": FINALIZATION_PLAN_SCHEMA_VERSION,
        "pending_root_relative_to_storage_root": "data/legacy_pending_paper_cohorts",
        "archived_root_relative_to_storage_root": (
            "data/finalized_legacy_pending_paper_cohorts/finalization-20260801t180000z"
        ),
        "legacy_db_relative_to_storage_root": "data/paper_trades.db",
        "shared_legacy_snapshot_sha256": "1" * 64,
        "shared_legacy_baseline_attestation": "2" * 64,
        "shared_legacy_open_rows_sha256": "3" * 64,
        "shared_legacy_open_trade_count": 2,
        "pending_manifest_sha256s": ["b" * 64, "a" * 64],
        "pending_cohort_ids": ["legacy-pending-b", "legacy-pending-a"],
        "frozen_trade_ids": ["trade-2", "trade-1"],
        "applied_receipt_sha256s": ["f" * 64, "e" * 64],
        "root_db_sha256": "4" * 64,
        "payload_inventory": [
            {
                "path_relative_to_archive_root": "payload/legacy_snapshot/paper_trades.db",
                "file_type": "file",
                "size_bytes": 17,
                "sha256": "d" * 64,
            },
            {
                "path_relative_to_archive_root": "payload/legacy_pending_b/manifest.json",
                "file_type": "file",
                "size_bytes": 29,
                "sha256": "c" * 64,
            },
        ],
        "payload_inventory_sha256": _payload_inventory_sha256_for(
            [
                {
                    "path_relative_to_archive_root": "payload/legacy_pending_b/manifest.json",
                    "file_type": "file",
                    "size_bytes": 29,
                    "sha256": "c" * 64,
                },
                {
                    "path_relative_to_archive_root": "payload/legacy_snapshot/paper_trades.db",
                    "file_type": "file",
                    "size_bytes": 17,
                    "sha256": "d" * 64,
                },
            ]
        ),
    }

    with pytest.raises(ValueError, match="pending family pending_manifest_sha256s is invalid"):
        parse_legacy_pending_finalization_plan(reversed_plan_payload)


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
        "payload_inventory_sha256": _payload_inventory_sha256_for(
            [
                {
                    "path_relative_to_archive_root": "payload/legacy_pending_a/manifest.json",
                    "file_type": "file",
                    "size_bytes": 1,
                    "sha256": "4" * 64,
                }
            ]
        ),
    }
    with pytest.raises(ValueError, match="pending family root_db_sha256 is invalid"):
        parse_legacy_pending_finalization_plan(plan_payload)

    plan_payload["root_db_sha256"] = "4" * 64
    plan_payload["pending_cohort_ids"] = ["UPPER/ID"]
    with pytest.raises(ValueError, match="pending family pending_cohort_ids is invalid"):
        parse_legacy_pending_finalization_plan(plan_payload)

    certificate_payload = {
        "schema_version": FINALIZATION_CERTIFICATE_SCHEMA_VERSION,
        "finalization_id": "finalization/20260801t180000z",
        "created_at_utc": "2026-08-01T18:00:00.000000+00:00",
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

    certificate_payload["finalization_id"] = "finalization-20260801t180000z"
    certificate_payload["created_at_utc"] = "2026-08-01T18:00:00+00:00"
    with pytest.raises(ValueError, match="finalization certificate created_at_utc is invalid"):
        parse_legacy_pending_finalization_certificate(certificate_payload)


OBSERVED_AT = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
APPLIED_AT = datetime(2026, 7, 31, 12, 1, tzinfo=timezone.utc)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _db_fingerprint(path: Path) -> tuple[object, ...]:
    with sqlite3.connect(path) as conn:
        schema = tuple(
            conn.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        )
        tables: list[tuple[str, tuple[object, ...]]] = []
        for table in (
            "bot_state",
            "paper_trades",
            "paper_settlement_observations",
            "paper_legacy_settlement_receipt_applications",
            "paper_settlement_outbox",
            "paper_settlement_outbox_requirements",
        ):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is not None:
                tables.append((table, tuple(conn.execute(f"SELECT * FROM {table} ORDER BY rowid"))))
    return schema, tuple(tables)


def _insert_trade(
    db_path: Path,
    *,
    trade_id: str,
    alias: str,
    venue_market_id: str,
    side: str,
    contracts: int,
    price_cents: int,
    ts: str,
    resolved: int = 0,
    resolved_yes: int | None = None,
    terminal_state: str | None = None,
    settlement_observation_sha256: str | None = None,
    settled_at: str | None = None,
    resolved_ts: str | None = None,
    gross_payout_cents: str | None = None,
    gross_pnl_cents: str | None = None,
    pnl_dollars: float | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, venue, venue_market_id, identity_status,
                market_title, side, contracts, price_cents, cost_dollars,
                estimated_prob, entry_price_cents, edge, kelly_dollars,
                capped_dollars, signal_headline, signal_source, keywords_matched,
                reasoning, resolved, resolved_yes, terminal_state,
                settlement_observation_sha256, settled_at, resolved_ts,
                gross_payout_cents, gross_pnl_cents, pnl_dollars
            ) VALUES (?, ?, ?, ?, ?, 'mapped', ?, ?, ?, ?, ?, 0.61, ?, 0.21, 1.0, 1.0,
                      'legacy signal', 'legacy:test', '[]', 'legacy receipt fixture',
                      ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                ts,
                alias,
                Venue.POLYMARKET_US.value,
                venue_market_id,
                f"Market {alias}",
                side,
                contracts,
                price_cents,
                contracts * price_cents / 100.0,
                price_cents,
                resolved,
                resolved_yes,
                terminal_state,
                settlement_observation_sha256,
                settled_at,
                resolved_ts,
                gross_payout_cents,
                gross_pnl_cents,
                pnl_dollars,
            ),
        )


def _receipt(*, trade_id: str, alias: str, venue_market_id: str, outcome: MarketOutcome):
    market_ref = MarketRef(Venue.POLYMARKET_US, venue_market_id, alias)
    observation = build_settlement_observation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome={"outcome": outcome.value},
        authoritative_payload={"id": venue_market_id, "settled": True, "slug": alias},
        observed_at=OBSERVED_AT,
        effective_at=datetime(2026, 7, 31, 11, 59, tzinfo=timezone.utc),
        rules_version="test-rules-v1",
        source_id="test-authoritative-source",
    )
    return build_legacy_settlement_receipt(trade_id, observation)


def _apply_receipt(db_path: Path, receipt) -> None:
    with SettlementStore(db_path) as store:
        store._apply_legacy_directional_receipt(
            receipt,
            applied_at=APPLIED_AT,
            transaction_precondition=lambda _conn: None,
            before_mutation=lambda _conn: None,
        )


def _planner_fixture(
    tmp_path: Path,
    *,
    apply_baseline_trade_2_receipt: bool = True,
) -> dict[str, object]:
    root_db = tmp_path / "paper_trades.db"
    trader = PaperTrader(db_path=root_db, startup_context="test")
    trader._conn.close()
    with sqlite3.connect(root_db) as conn:
        conn.execute("UPDATE bot_state SET value='100.00' WHERE key='notional_bankroll'")
    _insert_trade(
        root_db,
        trade_id="baseline-trade-1",
        alias="baseline-market-1",
        venue_market_id="101",
        side="yes",
        contracts=2,
        price_cents=40,
        ts="2026-07-30T12:00:00+00:00",
    )
    _insert_trade(
        root_db,
        trade_id="baseline-trade-2",
        alias="baseline-market-2",
        venue_market_id="102",
        side="no",
        contracts=1,
        price_cents=60,
        ts="2026-07-30T12:05:00+00:00",
    )
    _insert_trade(
        root_db,
        trade_id="historical-resolved",
        alias="historical-market",
        venue_market_id="103",
        side="yes",
        contracts=1,
        price_cents=55,
        ts="2026-07-20T12:00:00+00:00",
    )

    historical_receipt = _receipt(
        trade_id="historical-resolved",
        alias="historical-market",
        venue_market_id="103",
        outcome=MarketOutcome.YES,
    )
    _apply_receipt(root_db, historical_receipt)

    legacy_open = legacy_open_exposure_fingerprint(root_db)
    pending_a = resolve_runtime_paper_cohort(
        "legacy-pending-a",
        legacy_starting_bankroll=None,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
        cohort_kind="legacy_pending",
    )
    pending_b = resolve_runtime_paper_cohort(
        "legacy-pending-b",
        legacy_starting_bankroll=None,
        active_starting_bankroll=150.0,
        db_root=tmp_path,
        cohort_kind="legacy_pending",
    )
    initialize_legacy_pending_paper_cohort_manifest(
        pending_a,
        max_days_to_close=14.0,
        legacy_db_path=root_db,
        legacy_starting_bankroll=100.0,
        expected_legacy_open_trade_count=legacy_open.unresolved_trade_count,
        expected_legacy_open_rows_sha256=legacy_open.rows_sha256,
    )
    initialize_legacy_pending_paper_cohort_manifest(
        pending_b,
        max_days_to_close=14.0,
        legacy_db_path=root_db,
        legacy_starting_bankroll=100.0,
        expected_legacy_open_trade_count=legacy_open.unresolved_trade_count,
        expected_legacy_open_rows_sha256=legacy_open.rows_sha256,
    )

    receipt_one = _receipt(
        trade_id="baseline-trade-1",
        alias="baseline-market-1",
        venue_market_id="101",
        outcome=MarketOutcome.YES,
    )
    receipt_two = _receipt(
        trade_id="baseline-trade-2",
        alias="baseline-market-2",
        venue_market_id="102",
        outcome=MarketOutcome.YES,
    )
    _apply_receipt(root_db, receipt_one)
    if apply_baseline_trade_2_receipt:
        _apply_receipt(root_db, receipt_two)

    pending_root = tmp_path / "legacy_pending_paper_cohorts"
    manifest_paths = sorted(pending_root.glob("*/cohort.json"))
    expected_manifest_sha256s = tuple(sorted(_sha256(path) for path in manifest_paths))
    expected_snapshot_sha256 = _sha256(pending_a.db_path.parent / "legacy_cutover.db")
    expected_trade_ids = ("baseline-trade-1", "baseline-trade-2")
    finalization_id = "finalization-20260801t180000z"
    return {
        "root_db": root_db,
        "pending_root": pending_root,
        "expected_root_sha256": _sha256(root_db),
        "expected_manifest_sha256s": expected_manifest_sha256s,
        "expected_snapshot_sha256": expected_snapshot_sha256,
        "expected_open_rows_sha256": legacy_open.rows_sha256,
        "expected_trade_ids": expected_trade_ids,
        "finalization_id": finalization_id,
        "manifest_paths": tuple(manifest_paths),
        "cohort_db_paths": (
            pending_a.db_path,
            pending_b.db_path,
        ),
        "snapshot_paths": (
            pending_a.db_path.parent / "legacy_cutover.db",
            pending_b.db_path.parent / "legacy_cutover.db",
        ),
        "receipt_sha256s": tuple(sorted((receipt_one.receipt_sha256, receipt_two.receipt_sha256))),
    }


def _plan_from_fixture(fixture: dict[str, object]) -> SealedLegacyPendingFinalizationPlan:
    return plan_legacy_pending_finalization(
        db_path=fixture["root_db"],
        pending_root=fixture["pending_root"],
        finalization_id=fixture["finalization_id"],
        expected_root_sha256=fixture["expected_root_sha256"],
        expected_pending_manifest_sha256s=fixture["expected_manifest_sha256s"],
        expected_legacy_snapshot_sha256=fixture["expected_snapshot_sha256"],
        expected_baseline_open_rows_sha256=fixture["expected_open_rows_sha256"],
        expected_baseline_trade_ids=fixture["expected_trade_ids"],
    )


def test_plan_legacy_pending_finalization_proves_family_and_is_read_only(
    tmp_path: Path,
) -> None:
    fixture = _planner_fixture(tmp_path)
    before = _db_fingerprint(fixture["root_db"])

    plan = _plan_from_fixture(fixture)

    assert plan.family_summary.pending_cohort_ids == (
        "legacy-pending-a",
        "legacy-pending-b",
    )
    assert plan.family_summary.pending_manifest_sha256s == fixture["expected_manifest_sha256s"]
    assert plan.family_summary.shared_legacy_snapshot_sha256 == fixture["expected_snapshot_sha256"]
    assert plan.family_summary.shared_legacy_open_rows_sha256 == fixture["expected_open_rows_sha256"]
    assert plan.family_summary.shared_legacy_open_trade_count == 2
    assert plan.family_summary.frozen_trade_ids == fixture["expected_trade_ids"]
    assert plan.family_summary.applied_receipt_sha256s == fixture["receipt_sha256s"]
    assert plan.family_summary.root_db_sha256 == fixture["expected_root_sha256"]
    assert (
        plan.archive_target.archived_root_relative_to_storage_root
        == "finalized_legacy_pending_paper_cohorts/finalization-20260801t180000z"
    )
    assert [entry.path_relative_to_archive_root for entry in plan.payload_inventory] == [
        "payload/legacy_pending_paper_cohorts/legacy-pending-a/cohort.json",
        "payload/legacy_pending_paper_cohorts/legacy-pending-a/legacy_cutover.db",
        "payload/legacy_pending_paper_cohorts/legacy-pending-a/paper_trades.db",
        "payload/legacy_pending_paper_cohorts/legacy-pending-b/cohort.json",
        "payload/legacy_pending_paper_cohorts/legacy-pending-b/legacy_cutover.db",
        "payload/legacy_pending_paper_cohorts/legacy-pending-b/paper_trades.db",
    ]
    assert finalization_plan_sha256(plan) == hashlib.sha256(
        serialize_legacy_pending_finalization_plan(plan).encode("utf-8")
    ).hexdigest()
    assert _db_fingerprint(fixture["root_db"]) == before


def test_plan_legacy_pending_finalization_requires_shared_binding_agreement(
    tmp_path: Path,
) -> None:
    fixture = _planner_fixture(tmp_path)
    manifest_path = fixture["pending_root"] / "legacy-pending-b" / "cohort.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["legacy_pending_open_rows_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="open exposure|manifest"):
        _plan_from_fixture(fixture)


def test_plan_legacy_pending_finalization_requires_exact_manifest_sha_set(
    tmp_path: Path,
) -> None:
    fixture = _planner_fixture(tmp_path)

    with pytest.raises(ValueError, match="pending manifest"):
        plan_legacy_pending_finalization(
            db_path=fixture["root_db"],
            pending_root=fixture["pending_root"],
            finalization_id=fixture["finalization_id"],
            expected_root_sha256=fixture["expected_root_sha256"],
            expected_pending_manifest_sha256s=("0" * 64,),
            expected_legacy_snapshot_sha256=fixture["expected_snapshot_sha256"],
            expected_baseline_open_rows_sha256=fixture["expected_open_rows_sha256"],
            expected_baseline_trade_ids=fixture["expected_trade_ids"],
        )


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("expected_legacy_snapshot_sha256", "0" * 64, "snapshot"),
        ("expected_baseline_open_rows_sha256", "0" * 64, "open rows"),
        ("expected_baseline_trade_ids", ("baseline-trade-1",), "trade"),
    ],
)
def test_plan_legacy_pending_finalization_requires_exact_baseline_proof_inputs(
    tmp_path: Path,
    field_name: str,
    value: object,
    message: str,
) -> None:
    fixture = _planner_fixture(tmp_path)
    kwargs = {
        "db_path": fixture["root_db"],
        "pending_root": fixture["pending_root"],
        "finalization_id": fixture["finalization_id"],
        "expected_root_sha256": fixture["expected_root_sha256"],
        "expected_pending_manifest_sha256s": fixture["expected_manifest_sha256s"],
        "expected_legacy_snapshot_sha256": fixture["expected_snapshot_sha256"],
        "expected_baseline_open_rows_sha256": fixture["expected_open_rows_sha256"],
        "expected_baseline_trade_ids": fixture["expected_trade_ids"],
    }
    kwargs[field_name] = value

    with pytest.raises(ValueError, match=message):
        plan_legacy_pending_finalization(**kwargs)


def test_plan_legacy_pending_finalization_requires_zero_unresolved_root_rows(
    tmp_path: Path,
) -> None:
    fixture = _planner_fixture(tmp_path)
    with sqlite3.connect(fixture["root_db"]) as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, venue, venue_market_id, identity_status,
                market_title, side, contracts, price_cents, cost_dollars,
                estimated_prob, entry_price_cents, edge, kelly_dollars,
                capped_dollars, signal_headline, signal_source, keywords_matched,
                reasoning, resolved
            ) VALUES (
                'late-open', '2026-07-30T12:10:00+00:00', 'late-open', 'polymarket_us',
                '500', 'mapped', 'Late open', 'yes', 1, 50, 0.50, 0.5, 50, 0.0, 1.0,
                1.0, 'legacy signal', 'legacy:test', '[]', 'fixture', 0
            )
            """
        )
    fixture["expected_root_sha256"] = _sha256(fixture["root_db"])

    with pytest.raises(ValueError, match="unresolved"):
        _plan_from_fixture(fixture)


def test_plan_legacy_pending_finalization_requires_exactly_one_receipt_per_frozen_trade(
    tmp_path: Path,
) -> None:
    fixture = _planner_fixture(tmp_path)
    with sqlite3.connect(fixture["root_db"]) as conn:
        conn.execute(
            "DROP TRIGGER immutable_paper_legacy_settlement_receipt_applications_delete"
        )
        conn.execute(
            """
            DELETE FROM paper_legacy_settlement_receipt_applications
            WHERE trade_id='baseline-trade-2'
            """
        )
    fixture["expected_root_sha256"] = _sha256(fixture["root_db"])

    with pytest.raises(ValueError, match="receipt"):
        _plan_from_fixture(fixture)


def test_plan_legacy_pending_finalization_restricts_receipt_proof_to_frozen_baseline_ids(
    tmp_path: Path,
) -> None:
    fixture = _planner_fixture(tmp_path)

    plan = _plan_from_fixture(fixture)

    assert plan.family_summary.frozen_trade_ids == ("baseline-trade-1", "baseline-trade-2")
    assert "historical-resolved" not in plan.family_summary.frozen_trade_ids


def test_plan_legacy_pending_finalization_rejects_conflicting_receipt_identity(
    tmp_path: Path,
) -> None:
    fixture = _planner_fixture(tmp_path)
    with sqlite3.connect(fixture["root_db"]) as conn:
        first_observation = conn.execute(
            """
            SELECT settlement_observation_sha256
            FROM paper_trades WHERE trade_id='baseline-trade-1'
            """
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE paper_trades
            SET settlement_observation_sha256=?
            WHERE trade_id='baseline-trade-2'
            """,
            (first_observation,),
        )
    fixture["expected_root_sha256"] = _sha256(fixture["root_db"])

    with pytest.raises(ValueError, match="receipt|trade linkage|identity"):
        _plan_from_fixture(fixture)


def test_plan_legacy_pending_finalization_rejects_failed_conservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _planner_fixture(tmp_path)

    class _FailedCheck:
        ok = False

    monkeypatch.setattr(
        "trading.legacy_pending_finalization.SettlementStore.conservation",
        lambda self, *, now: _FailedCheck(),
    )

    with pytest.raises(ValueError, match="conservation"):
        _plan_from_fixture(fixture)


def test_plan_legacy_pending_finalization_rejects_root_identity_drift_mid_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _planner_fixture(tmp_path)
    original = plan_legacy_pending_finalization.__globals__["_unresolved_trade_count"]

    def swapped_unresolved_trade_count(root_file) -> int:
        root_path = root_file.path
        replacement = root_path.with_name("replacement-root.db")
        replacement.write_bytes(root_path.read_bytes())
        root_path.unlink()
        replacement.replace(root_path)
        return original(root_file)

    monkeypatch.setitem(
        plan_legacy_pending_finalization.__globals__,
        "_unresolved_trade_count",
        swapped_unresolved_trade_count,
    )

    with pytest.raises(ValueError, match="identity changed|reviewed value|legacy root"):
        _plan_from_fixture(fixture)


def test_plan_legacy_pending_finalization_rejects_snapshot_identity_drift_mid_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _planner_fixture(tmp_path)
    original = plan_legacy_pending_finalization.__globals__["_frozen_trade_ids"]

    def swapped_frozen_trade_ids(snapshot_file) -> tuple[str, ...]:
        snapshot_path = snapshot_file.path
        replacement = snapshot_path.with_name("replacement-snapshot.db")
        replacement.write_bytes(snapshot_path.read_bytes())
        snapshot_path.unlink()
        replacement.replace(snapshot_path)
        return original(snapshot_file)

    monkeypatch.setitem(
        plan_legacy_pending_finalization.__globals__,
        "_frozen_trade_ids",
        swapped_frozen_trade_ids,
    )

    with pytest.raises(ValueError, match="identity changed|reviewed value|baseline snapshot"):
        _plan_from_fixture(fixture)


def test_plan_legacy_pending_finalization_rejects_manifest_drift_before_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _planner_fixture(tmp_path)
    original = plan_legacy_pending_finalization.__globals__["_deterministic_payload_inventory"]

    def rewriting_inventory(payload_files):
        manifest_path = next(
            item.verified_file.path
            for item in payload_files
            if item.path_relative_to_archive_root.endswith("/cohort.json")
        )
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_path.write_text(
            json.dumps(payload, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return original(payload_files)

    monkeypatch.setitem(
        plan_legacy_pending_finalization.__globals__,
        "_deterministic_payload_inventory",
        rewriting_inventory,
    )

    with pytest.raises(ValueError, match="manifest|payload|identity changed|reviewed value"):
        _plan_from_fixture(fixture)
