from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from trading.g7_skip_evidence import (
    G7SkipEvidenceRecord,
    G7SkipEvidenceSchemaError,
    G7SkipEvidenceStore,
    read_g7_skip_evidence_records,
    read_g7_skip_evidence_snapshot,
)


UTC = timezone.utc
NOW = datetime(2026, 7, 31, 12, 30, tzinfo=UTC)


def _observed_record(**overrides: object) -> G7SkipEvidenceRecord:
    payload: dict[str, object] = {
        "decision_key": "lifecycle-123:2026-07-31T12:29:00Z",
        "lifecycle_id": "lifecycle-123",
        "decision_at": datetime(2026, 7, 31, 12, 29, tzinfo=UTC),
        "captured_at": NOW,
        "venue": "kalshi",
        "market_ticker": "KXTEST-26JUL31-B52.5",
        "intended_side": "yes",
        "market_family": "macro",
        "runtime_paper_cohort_id": "legacy-pending-20260729",
        "runtime_paper_cohort_kind": "legacy_pending",
        "ordered_failures": ("G7_zero_liquidity",),
        "g7_failures": ("G7_zero_liquidity",),
        "trade_blocked_reason": "G7_zero_liquidity",
        "g7_inputs": {
            "minimum_market_liquidity_dollars": 5.0,
            "maximum_open_exposure_drawdown_pct": 0.20,
            "market_liquidity_dollars": 0.0,
            "market_price_momentum_cents": 0.0,
            "intended_side": "yes",
            "open_exposure_drawdown_pct": 0.0,
        },
        "g7_results": {
            "ordered_failures": ["G7_zero_liquidity"],
            "g7_failures": ["G7_zero_liquidity"],
            "non_drawdown_g7_failures": ["G7_zero_liquidity"],
            "trade_blocked_reason": "G7_zero_liquidity",
        },
        "liquidity_evidence_status": "observed",
        "execution_liquidity": {
            "source": "kalshi_orderbook",
            "side": "yes",
            "limit_price": 0.52,
            "best_price": 0.51,
            "executable_quantity": 7.0,
            "executable_notional": 3.71,
            "as_of": "2026-07-31T12:28:58Z",
            "raw_payload_hash": "a" * 64,
        },
        "diagnostic_only": True,
    }
    payload.update(overrides)
    return G7SkipEvidenceRecord(**payload)


def test_store_appends_immutable_receipts_idempotently_and_rejects_conflicts(tmp_path: Path) -> None:
    db_path = tmp_path / "g7_skip_evidence.db"
    store = G7SkipEvidenceStore(db_path=db_path)
    record = _observed_record()

    assert db_path.exists() is False
    assert store.initialize(applied_at=NOW) is True
    inserted = store.append_record(record)
    identical = store.append_record(record)
    conflict = store.append_record(
        replace(
            record,
            g7_inputs={
                **record.g7_inputs,
                "market_liquidity_dollars": 1.0,
            },
        )
    )

    assert inserted.status == "inserted"
    assert identical.status == "identical"
    assert conflict.status == "conflict"
    assert inserted.evidence_id == record.evidence_id
    assert inserted.payload_sha256 == record.payload_sha256

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM g7_skip_evidence_records").fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM g7_skip_evidence_records")


@pytest.mark.parametrize(
    ("status", "metadata"),
    [
        (
            "unavailable",
            {
                "source": "kalshi_orderbook",
                "status": "unavailable",
                "reason": "ValueError",
            },
        ),
        (
            "not_queried",
            {
                "status": "not_queried",
                "reason": "initial_blend_blocked",
            },
        ),
    ],
)
def test_receipt_classes_accept_only_their_non_observed_evidence_shape(
    status: str,
    metadata: dict[str, str],
) -> None:
    record = _observed_record(
        liquidity_evidence_status=status,
        execution_liquidity=metadata,
    )

    assert record.liquidity_evidence_status == status


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "execution_liquidity": {
                "source": "kalshi_orderbook",
                "side": "yes",
                "limit_price": 0.52,
                "best_price": 0.53,
                "executable_quantity": 7.0,
                "executable_notional": 3.71,
                "as_of": "2026-07-31T12:28:58Z",
            }
        },
        {
            "liquidity_evidence_status": "unavailable",
            "execution_liquidity": {
                "source": "kalshi_orderbook",
                "status": "unavailable",
                "reason": "ValueError",
                "limit_price": 0.52,
            },
        },
        {
            "liquidity_evidence_status": "not_queried",
            "execution_liquidity": {"status": "not_queried"},
        },
        {"diagnostic_only": False},
    ],
)
def test_receipt_classes_reject_ambiguous_or_non_diagnostic_payloads(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _observed_record(**overrides)


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "source": "kalshi_orderbook",
            "side": "bad-side",
            "limit_price": 0.52,
            "best_price": 0.53,
            "executable_quantity": 7.0,
            "executable_notional": 3.71,
            "as_of": "2026-07-31T12:28:58Z",
            "raw_payload_hash": "a" * 64,
        },
        {
            "source": "kalshi_orderbook",
            "side": "yes",
            "limit_price": 0.52,
            "best_price": 0.53,
            "executable_quantity": -1.0,
            "executable_notional": 3.71,
            "as_of": "2026-07-31T12:28:58Z",
            "raw_payload_hash": "a" * 64,
        },
        {
            "source": "kalshi_orderbook",
            "side": "yes",
            "limit_price": 0.52,
            "best_price": 0.53,
            "executable_quantity": 7.0,
            "executable_notional": 3.71,
            "as_of": "2026-07-31T12:29:01Z",
            "raw_payload_hash": "a" * 64,
        },
    ],
)
def test_observed_receipts_reject_invalid_or_future_execution_facts(metadata: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _observed_record(execution_liquidity=metadata)


def test_nonobserved_receipts_require_typed_reason_and_complete_g7_projections() -> None:
    with pytest.raises(ValueError):
        _observed_record(
            liquidity_evidence_status="not_queried",
            execution_liquidity={"status": "not_queried", "reason": "not a typed reason!"},
        )
    with pytest.raises(ValueError):
        _observed_record(g7_inputs={})
    with pytest.raises(ValueError):
        _observed_record(
            ordered_failures=("G1_other", "G7_zero_liquidity"),
            g7_failures=("G7_zero_liquidity",),
            trade_blocked_reason="G1_other",
            g7_results={
                "ordered_failures": ["G1_other", "G7_zero_liquidity"],
                "g7_failures": ["G7_zero_liquidity"],
                "non_drawdown_g7_failures": ["G7_zero_liquidity"],
                "trade_blocked_reason": "G1_other",
            },
        )


def test_existing_only_and_read_snapshot_never_create_a_missing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-g7-evidence.db"
    store = G7SkipEvidenceStore(db_path=db_path, existing_only=True)

    assert store.initialize(applied_at=NOW) is False
    missing = read_g7_skip_evidence_snapshot(db_path)

    assert db_path.exists() is False
    assert missing.exists is False
    assert missing.record_count == 0
    assert missing.integrity_check == "missing"


def test_read_snapshot_validates_existing_store_and_exposes_only_diagnostic_receipt_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "g7_skip_evidence.db"
    store = G7SkipEvidenceStore(db_path=db_path)
    assert store.initialize(applied_at=NOW) is True
    assert store.append_record(_observed_record()).status == "inserted"

    snapshot = read_g7_skip_evidence_snapshot(db_path)

    assert snapshot.exists is True
    assert snapshot.schema_valid is True
    assert snapshot.integrity_check == "ok"
    assert snapshot.record_count == 1
    assert snapshot.receipt_counts_by_status == (("observed", 1),)
    assert snapshot.latest_captured_at == NOW


def test_read_only_record_iterator_round_trips_validated_receipts_without_creating_missing_paths(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing-g7-evidence.db"
    assert read_g7_skip_evidence_records(missing_path) == ()
    assert missing_path.exists() is False

    db_path = tmp_path / "g7_skip_evidence.db"
    record = _observed_record()
    store = G7SkipEvidenceStore(db_path=db_path)
    assert store.initialize(applied_at=NOW) is True
    assert store.append_record(record).status == "inserted"

    assert read_g7_skip_evidence_records(db_path) == (record,)


def test_initialize_migrates_pre_lineage_receipt_store_without_backfill(tmp_path: Path) -> None:
    db_path = tmp_path / "g7_skip_evidence.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE g7_skip_evidence_schema_meta (
                schema_version INTEGER PRIMARY KEY,
                ddl_sha256 TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE g7_skip_evidence_records (
                evidence_id TEXT PRIMARY KEY,
                receipt_version INTEGER NOT NULL,
                decision_key TEXT NOT NULL UNIQUE,
                payload_sha256 TEXT NOT NULL,
                decision_at TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                lifecycle_id TEXT NOT NULL,
                venue TEXT NOT NULL,
                market_ticker TEXT NOT NULL,
                intended_side TEXT,
                market_family TEXT,
                ordered_failures_json TEXT NOT NULL,
                g7_failures_json TEXT NOT NULL,
                trade_blocked_reason TEXT NOT NULL,
                g7_inputs_json TEXT NOT NULL,
                g7_results_json TEXT NOT NULL,
                liquidity_evidence_status TEXT NOT NULL,
                execution_liquidity_json TEXT NOT NULL,
                diagnostic_only INTEGER NOT NULL CHECK (diagnostic_only = 1)
            )
            """
        )
        conn.execute(
            "INSERT INTO g7_skip_evidence_schema_meta (schema_version, ddl_sha256, applied_at) VALUES (1, ?, ?)",
            ("legacy-ddl", "2026-07-31T12:30:00Z"),
        )
        record = _observed_record(
            runtime_paper_cohort_id=None,
            runtime_paper_cohort_kind=None,
        )
        conn.execute(
            """
            INSERT INTO g7_skip_evidence_records (
                evidence_id, receipt_version, decision_key, payload_sha256, decision_at,
                captured_at, lifecycle_id, venue, market_ticker, intended_side,
                market_family, ordered_failures_json, g7_failures_json, trade_blocked_reason,
                g7_inputs_json, g7_results_json, liquidity_evidence_status,
                execution_liquidity_json, diagnostic_only
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.evidence_id,
                record.receipt_version,
                record.decision_key,
                record.payload_sha256,
                record.payload["decision_at"],
                record.payload["captured_at"],
                record.lifecycle_id,
                record.venue,
                record.market_ticker,
                record.intended_side,
                record.market_family,
                json.dumps(record.payload["ordered_failures"]),
                json.dumps(record.payload["g7_failures"]),
                record.trade_blocked_reason,
                json.dumps(record.payload["g7_inputs"]),
                json.dumps(record.payload["g7_results"]),
                record.liquidity_evidence_status,
                json.dumps(record.payload["execution_liquidity"]),
                1,
            ),
        )

    store = G7SkipEvidenceStore(db_path=db_path)
    assert store.initialize(applied_at=NOW) is True

    [migrated] = read_g7_skip_evidence_records(db_path)
    assert migrated.runtime_paper_cohort_id is None
    assert migrated.runtime_paper_cohort_kind is None


def test_snapshot_rejects_altered_trigger_contract_before_reporting_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "g7_skip_evidence.db"
    store = G7SkipEvidenceStore(db_path=db_path)
    assert store.initialize(applied_at=NOW) is True
    assert store.append_record(_observed_record()).status == "inserted"

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER immutable_g7_skip_evidence_records_update")
        conn.execute(
            "CREATE TRIGGER immutable_g7_skip_evidence_records_update "
            "BEFORE UPDATE ON g7_skip_evidence_records BEGIN SELECT 1; END"
        )

    snapshot = read_g7_skip_evidence_snapshot(db_path)

    assert snapshot.schema_valid is False
    assert snapshot.integrity_check == "ok"
    assert snapshot.record_count == 0


def test_snapshot_rejects_receipt_hash_tampering_even_after_schema_is_restored(tmp_path: Path) -> None:
    db_path = tmp_path / "g7_skip_evidence.db"
    store = G7SkipEvidenceStore(db_path=db_path)
    assert store.initialize(applied_at=NOW) is True
    assert store.append_record(_observed_record()).status == "inserted"

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER immutable_g7_skip_evidence_records_update")
        conn.execute("UPDATE g7_skip_evidence_records SET payload_sha256 = ?", ("b" * 64,))
    assert store.initialize(applied_at=NOW) is True

    snapshot = read_g7_skip_evidence_snapshot(db_path)

    assert snapshot.schema_valid is False
    assert snapshot.integrity_check == "receipt_invalid"
    assert snapshot.record_count == 0
    with pytest.raises(G7SkipEvidenceSchemaError, match="hash"):
        read_g7_skip_evidence_records(db_path)
