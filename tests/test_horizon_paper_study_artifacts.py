from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from trading.horizon_paper_study_artifacts import (
    HorizonStudyArtifactStore,
    HorizonStudyArtifactStoreError,
    StudyLedgerExecutionLink,
)
from trading.horizon_paper_study_manifest import (
    HORIZON_PAPER_STUDY_KIND,
    derive_horizon_paper_study_database_identity,
)


STUDY_ID = "pm-horizon-15-30-20260805"
_BOOTSTRAP_TABLE = "horizon_study_bootstrap"
_LEDGER_APPLICATION_ID = 0x48504C47
_STATE_APPLICATION_ID = 0x48505354


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _record_hash(record: dict[str, object]) -> str:
    payload = dict(record)
    payload.pop("record_sha256", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _input_id(record: dict[str, object]) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "source": record["source"],
                "source_url": record["source_url"],
                "source_published_at_utc": record["source_published_at_utc"],
                "headline_sha256": record["headline_sha256"],
                "body_sha256": record["body_sha256"],
                "market_snapshot_id": record["market_snapshot_id"],
                "market_snapshot_sha256": record["market_snapshot_sha256"],
            }
        ).encode("utf-8")
    ).hexdigest()


def _seal_manifest(payload: dict[str, object]) -> dict[str, object]:
    sealed = dict(payload)
    sealed.pop("manifest_sha256", None)
    sealed["manifest_sha256"] = hashlib.sha256(
        _canonical_json(sealed).encode("utf-8")
    ).hexdigest()
    return sealed


def _write_initialized_database(
    path: Path,
    *,
    role: str,
    payload: dict[str, object],
) -> None:
    application_id = {
        "ledger": _LEDGER_APPLICATION_ID,
        "state": _STATE_APPLICATION_ID,
    }[role]
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA application_id = {application_id}")
        conn.execute(
            f"""
            CREATE TABLE {_BOOTSTRAP_TABLE} (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                bootstrap_schema_version INTEGER NOT NULL,
                database_role TEXT NOT NULL,
                study_id TEXT NOT NULL,
                study_kind TEXT NOT NULL,
                database_identity TEXT NOT NULL,
                manifest_preimage_sha256 TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {_BOOTSTRAP_TABLE} (
                singleton,
                bootstrap_schema_version,
                database_role,
                study_id,
                study_kind,
                database_identity,
                manifest_preimage_sha256
            ) VALUES (1, 1, ?, ?, ?, ?, ?)
            """,
            (
                role,
                payload["study_id"],
                payload["study_kind"],
                payload["database_identity"],
                payload["manifest_sha256"],
            ),
        )


def _write_study_root(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    data_root = tmp_path / "data"
    study_root = data_root / "horizon_paper_studies" / STUDY_ID
    study_root.mkdir(parents=True)
    policy_path = study_root / "policy_snapshot.json"
    fee_path = study_root / "fee_schedule.json"
    policy_path.write_bytes(b'{"matcher":"pinned"}')
    fee_path.write_bytes(b'{"fee_schedule":"pinned"}')
    configuration = {
        "schema_version": 1,
        "study_id": STUDY_ID,
        "study_kind": HORIZON_PAPER_STUDY_KIND,
        "venue": "polymarket_us",
        "created_at_utc": "2026-08-05T00:00:00.000000+00:00",
        "ledger_path": "study_ledger.db",
        "state_db_path": "study_state.db",
        "starting_bankroll": "250.00",
        "horizon_lower_exclusive_days": 14.0,
        "horizon_upper_inclusive_days": 30.0,
        "policy_snapshot_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "fee_schedule_sha256": hashlib.sha256(fee_path.read_bytes()).hexdigest(),
        "paper_execution_mode": "isolated_paper_only",
        "live_order_forbidden": True,
        "profit_receipt_attested": False,
    }
    payload = _seal_manifest(
        {
            **configuration,
            "database_identity": derive_horizon_paper_study_database_identity(
                configuration
            ),
        }
    )
    _write_initialized_database(
        study_root / "study_ledger.db",
        role="ledger",
        payload=payload,
    )
    _write_initialized_database(
        study_root / "study_state.db",
        role="state",
        payload=payload,
    )
    manifest_path = study_root / "manifest.json"
    manifest_path.write_text(_canonical_json(payload), encoding="utf-8")
    manifest_path.chmod(0o600)
    return study_root, manifest_path, payload


def _input_record(manifest_sha256: str) -> dict[str, object]:
    headline_sha256 = hashlib.sha256(b"headline").hexdigest()
    body_sha256 = hashlib.sha256(b"body").hexdigest()
    market_snapshot_sha256 = hashlib.sha256(b"market-snapshot").hexdigest()
    input_id = hashlib.sha256(
        _canonical_json(
            {
                "source": "reuters",
                "source_url": "https://example.com/story",
                "source_published_at_utc": "2026-08-05T01:00:00.000000+00:00",
                "headline_sha256": headline_sha256,
                "body_sha256": body_sha256,
                "market_snapshot_id": "market-snapshot-1",
                "market_snapshot_sha256": market_snapshot_sha256,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "record_type": "POLYMARKET_HORIZON_STUDY_INPUT",
        "study_id": STUDY_ID,
        "manifest_sha256": manifest_sha256,
        "input_id": input_id,
        "observed_at_utc": "2026-08-05T01:02:03.000000+00:00",
        "source": "reuters",
        "source_url": "https://example.com/story",
        "source_published_at_utc": "2026-08-05T01:00:00.000000+00:00",
        "headline_sha256": headline_sha256,
        "body_sha256": body_sha256,
        "market_snapshot_id": "market-snapshot-1",
        "market_snapshot_sha256": market_snapshot_sha256,
        "policy_snapshot_sha256": hashlib.sha256(b'{"matcher":"pinned"}').hexdigest(),
        "routing_prohibited": True,
    }


def _admission_record(input_record: dict[str, object]) -> dict[str, object]:
    admission_id = hashlib.sha256(
        _canonical_json(
            {
                "study_id": STUDY_ID,
                "input_id": input_record["input_id"],
                "market_id": "market-123",
                "market_snapshot_id": input_record["market_snapshot_id"],
                "policy_snapshot_sha256": input_record["policy_snapshot_sha256"],
            }
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "record_type": "POLYMARKET_HORIZON_STUDY_SHADOW_ADMISSION",
        "study_id": STUDY_ID,
        "manifest_sha256": input_record["manifest_sha256"],
        "admission_id": admission_id,
        "input_id": input_record["input_id"],
        "venue": "polymarket_us",
        "market_id": "market-123",
        "market_close_time_utc": "2026-08-25T00:00:00.000000+00:00",
        "days_to_close": "20.0",
        "market_snapshot_sha256": input_record["market_snapshot_sha256"],
        "match_score": "0.42",
        "min_match_score": "0.20",
        "selection_status": "qualified",
        "policy_snapshot_sha256": input_record["policy_snapshot_sha256"],
        "routing_prohibited": True,
        "primary_route_called": False,
    }


def _decision_record(admission_record: dict[str, object]) -> dict[str, object]:
    decision_id = hashlib.sha256(
        f"{STUDY_ID}:{admission_record['admission_id']}".encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "record_type": "POLYMARKET_HORIZON_STUDY_DECISION",
        "study_id": STUDY_ID,
        "manifest_sha256": admission_record["manifest_sha256"],
        "decision_id": decision_id,
        "admission_id": admission_record["admission_id"],
        "analysis_input_sha256": hashlib.sha256(b"analysis").hexdigest(),
        "research_snapshot_sha256": hashlib.sha256(b"research").hexdigest(),
        "counter_evidence_status": "cleared",
        "market_price_snapshot": "0.37",
        "estimated_edge": "0.08",
        "decision_status": "execute",
        "routing_prohibited": True,
    }


def _execution_record(
    admission_record: dict[str, object],
    decision_record: dict[str, object],
) -> dict[str, object]:
    execution_id = hashlib.sha256(
        f"{STUDY_ID}:{admission_record['admission_id']}:execution".encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "record_type": "POLYMARKET_HORIZON_STUDY_EXECUTION",
        "study_id": STUDY_ID,
        "manifest_sha256": admission_record["manifest_sha256"],
        "execution_id": execution_id,
        "admission_id": admission_record["admission_id"],
        "decision_id": decision_record["decision_id"],
        "study_trade_id": "study-trade-1",
        "executed_at_utc": "2026-08-05T01:05:00.000000+00:00",
        "side": "yes",
        "entry_price_snapshot": "0.37",
        "size": "5",
        "routing_prohibited": True,
    }


def _settlement_record(execution_record: dict[str, object]) -> dict[str, object]:
    settlement_id = hashlib.sha256(
        f"{STUDY_ID}:{execution_record['study_trade_id']}:settlement".encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "record_type": "POLYMARKET_HORIZON_STUDY_SETTLEMENT",
        "study_id": STUDY_ID,
        "manifest_sha256": execution_record["manifest_sha256"],
        "settlement_id": settlement_id,
        "study_trade_id": execution_record["study_trade_id"],
        "settlement_observation_sha256": hashlib.sha256(b"terminal-obs").hexdigest(),
        "observed_at_utc": "2026-08-25T00:01:00.000000+00:00",
        "terminal_status": "resolved",
        "normalized_result": "yes",
        "gross_pnl_cents": 315,
        "modeled_fee_net_pnl_cents": 299,
        "entry_fee_provenance": "modeled_pinned_schedule",
        "settlement_fee_provenance": "modeled_pinned_schedule",
        "fee_schedule_sha256": hashlib.sha256(b'{"fee_schedule":"pinned"}').hexdigest(),
        "profit_receipt_attested": False,
        "live_readiness_eligible": False,
        "routing_prohibited": True,
    }


def _abort_record(manifest_sha256: str) -> dict[str, object]:
    abort_id = hashlib.sha256(
        f"{STUDY_ID}:{manifest_sha256}:fatal".encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "record_type": "POLYMARKET_HORIZON_STUDY_ABORT",
        "study_id": STUDY_ID,
        "manifest_sha256": manifest_sha256,
        "abort_id": abort_id,
        "aborted_at_utc": "2026-08-05T01:06:00.000000+00:00",
        "reason": "fatal",
        "routing_prohibited": True,
    }


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class _StudyLedgerLookup:
    def __init__(
        self,
        result: object = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def lookup_study_trade_links(
        self,
        *,
        study_id: str,
        admission_id: str,
    ) -> object:
        self.calls.append((study_id, admission_id))
        if self.error is not None:
            raise self.error
        return self.result


def _record_full_line(record: dict[str, object]) -> bytes:
    return (_canonical_json(record) + "\n").encode("utf-8")


def _write_claim(
    store: HorizonStudyArtifactStore,
    admission: dict[str, object],
) -> None:
    store.claim_execution(
        manifest_sha256=admission["manifest_sha256"],
        admission_id=admission["admission_id"],
        claimed_at_utc="2026-08-05T01:04:30.000000+00:00",
    )


def _assert_no_execution_artifact(study_root: Path) -> None:
    state_db_path = study_root / "study_state.db"
    with sqlite3.connect(state_db_path) as conn:
        generic_count = conn.execute(
            "SELECT COUNT(*) FROM artifact_payload_journal "
            "WHERE record_type = 'POLYMARKET_HORIZON_STUDY_EXECUTION'"
        ).fetchone()[0]
        claim_count = conn.execute("SELECT COUNT(*) FROM execution_claims").fetchone()[0]
    assert generic_count == 0
    assert claim_count == 1
    assert not (study_root / "artifacts" / "executions.jsonl").exists()


def test_records_exact_canonical_artifacts_and_constrains_writes_to_study_root(
    tmp_path: Path,
):
    study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        input_record = store.record_input(_input_record(payload["manifest_sha256"]))
        assert set(input_record) == {
            "schema_version",
            "record_type",
            "study_id",
            "manifest_sha256",
            "input_id",
            "observed_at_utc",
            "source",
            "source_url",
            "source_published_at_utc",
            "headline_sha256",
            "body_sha256",
            "market_snapshot_id",
            "market_snapshot_sha256",
            "policy_snapshot_sha256",
            "routing_prohibited",
            "record_sha256",
        }
        assert input_record["routing_prohibited"] is True
        assert input_record["record_sha256"] == _record_hash(input_record)
        assert (study_root / "artifacts" / "inputs.jsonl").read_text(encoding="utf-8").strip() == _canonical_json(
            input_record
        )

        admission = store.record_shadow_admission(_admission_record(input_record))
        assert admission["record_sha256"] == _record_hash(admission)
        assert admission["routing_prohibited"] is True
        assert admission["primary_route_called"] is False

        decision = store.record_decision(_decision_record(admission))
        assert decision["record_sha256"] == _record_hash(decision)

        _write_claim(store, admission)
        execution = store.record_execution(_execution_record(admission, decision))
        assert execution["record_sha256"] == _record_hash(execution)

        settlement = store.record_settlement(_settlement_record(execution))
        assert settlement["record_sha256"] == _record_hash(settlement)
        assert settlement["profit_receipt_attested"] is False
        assert settlement["live_readiness_eligible"] is False

        abort = store.abort(_abort_record(payload["manifest_sha256"]))
        assert abort["record_sha256"] == _record_hash(abort)

        created = {
            path.relative_to(study_root).as_posix()
            for path in study_root.rglob("*")
            if path.is_file()
        }
        assert created >= {
            "manifest.json",
            "study_ledger.db",
            "study_state.db",
            "artifacts/inputs.jsonl",
            "artifacts/shadow_admissions.jsonl",
            "artifacts/decisions.jsonl",
            "artifacts/executions.jsonl",
            "artifacts/settlements.jsonl",
            "artifacts/aborts.jsonl",
            "locks/runtime.lock",
        }


def test_duplicate_idempotency_and_conflicting_duplicates_abort(tmp_path: Path):
    _study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        input_payload = _input_record(payload["manifest_sha256"])
        first = store.record_input(input_payload)
        assert store.record_input(dict(input_payload)) == first
        assert len(_read_jsonl(store.inputs_path)) == 1

        admission_payload = _admission_record(first)
        admission = store.record_shadow_admission(admission_payload)
        assert store.record_shadow_admission(dict(admission_payload)) == admission
        assert len(_read_jsonl(store.shadow_admissions_path)) == 1

        uniqueness_conflict = dict(admission_payload)
        uniqueness_conflict["admission_id"] = "f" * 64
        with pytest.raises(HorizonStudyArtifactStoreError):
            store.record_shadow_admission(uniqueness_conflict)

        conflicting = dict(input_payload)
        conflicting["observed_at_utc"] = "2026-08-05T01:02:04.000000+00:00"
        with pytest.raises(HorizonStudyArtifactStoreError, match="duplicate"):
            store.record_input(conflicting)
        with pytest.raises(HorizonStudyArtifactStoreError, match="fail-closed"):
            store.abort(_abort_record(payload["manifest_sha256"]))


def test_requires_exclusive_runtime_lock(tmp_path: Path):
    _study_root, manifest_path, _payload = _write_study_root(tmp_path)
    store = HorizonStudyArtifactStore(manifest_path)
    try:
        with pytest.raises(HorizonStudyArtifactStoreError, match="runtime lock"):
            HorizonStudyArtifactStore(manifest_path)
    finally:
        store.close()


def test_recovers_missing_input_mirror_after_state_commit(tmp_path: Path, monkeypatch):
    _study_root, manifest_path, payload = _write_study_root(tmp_path)
    store = HorizonStudyArtifactStore(manifest_path)
    input_payload = _input_record(payload["manifest_sha256"])
    original = store._append_audit_payload

    def fail_after_commit(*args, **kwargs):
        raise OSError("mirror write failed")

    monkeypatch.setattr(store, "_append_audit_payload", fail_after_commit)
    with pytest.raises(HorizonStudyArtifactStoreError, match="mirror"):
        store.record_input(input_payload)
    monkeypatch.setattr(store, "_append_audit_payload", original)

    with sqlite3.connect(store.state_db_path) as conn:
        rows = conn.execute("SELECT input_id, record_sha256 FROM input_receipts").fetchall()
    assert rows == [(input_payload["input_id"], _record_hash(input_payload))]
    assert store.inputs_path.exists() is False
    store.close()

    reopened = HorizonStudyArtifactStore(manifest_path)
    try:
        rows = _read_jsonl(reopened.inputs_path)
        assert rows == [{**input_payload, "record_sha256": _record_hash(input_payload)}]
    finally:
        reopened.close()


def test_startup_aborts_on_invalid_mirror_json_or_hash(tmp_path: Path):
    _study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        store.record_input(_input_record(payload["manifest_sha256"]))

    inputs_path = manifest_path.parent / "artifacts" / "inputs.jsonl"
    inputs_path.write_text('{"not":"valid"\n', encoding="utf-8")
    with pytest.raises(HorizonStudyArtifactStoreError, match="invalid"):
        HorizonStudyArtifactStore(manifest_path)

    valid_line = _canonical_json({**_input_record(payload["manifest_sha256"]), "record_sha256": "0" * 64})
    inputs_path.write_text(valid_line + "\n", encoding="utf-8")
    with pytest.raises(HorizonStudyArtifactStoreError, match="hash"):
        HorizonStudyArtifactStore(manifest_path)


def test_startup_aborts_on_ambiguous_execution_recovery(tmp_path: Path):
    _study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        input_record = store.record_input(_input_record(payload["manifest_sha256"]))
        admission = store.record_shadow_admission(_admission_record(input_record))
        _write_claim(store, admission)

    with pytest.raises(HorizonStudyArtifactStoreError, match="ambiguous|orphaned"):
        HorizonStudyArtifactStore(
            manifest_path,
            study_ledger_execution_lookup=_StudyLedgerLookup(
                (
                    StudyLedgerExecutionLink(
                        study_trade_id="study-trade-1",
                        study_id=STUDY_ID,
                        admission_id=admission["admission_id"],
                    ),
                    StudyLedgerExecutionLink(
                        study_trade_id="study-trade-2",
                        study_id=STUDY_ID,
                        admission_id=admission["admission_id"],
                    ),
                )
            ),
        )


def test_generic_journal_is_the_state_authority_for_all_six_record_types(
    tmp_path: Path,
):
    study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        input_record = store.record_input(_input_record(payload["manifest_sha256"]))
        admission = store.record_shadow_admission(_admission_record(input_record))
        decision = store.record_decision(_decision_record(admission))
        _write_claim(store, admission)
        execution = store.record_execution(_execution_record(admission, decision))
        settlement = store.record_settlement(_settlement_record(execution))
        abort = store.abort(_abort_record(payload["manifest_sha256"]))

        expected = [
            (
                "POLYMARKET_HORIZON_STUDY_INPUT",
                input_record["input_id"],
                "artifacts/inputs.jsonl",
                input_record,
            ),
            (
                "POLYMARKET_HORIZON_STUDY_SHADOW_ADMISSION",
                admission["admission_id"],
                "artifacts/shadow_admissions.jsonl",
                admission,
            ),
            (
                "POLYMARKET_HORIZON_STUDY_DECISION",
                decision["decision_id"],
                "artifacts/decisions.jsonl",
                decision,
            ),
            (
                "POLYMARKET_HORIZON_STUDY_EXECUTION",
                execution["execution_id"],
                "artifacts/executions.jsonl",
                execution,
            ),
            (
                "POLYMARKET_HORIZON_STUDY_SETTLEMENT",
                settlement["settlement_id"],
                "artifacts/settlements.jsonl",
                settlement,
            ),
            (
                "POLYMARKET_HORIZON_STUDY_ABORT",
                abort["abort_id"],
                "artifacts/aborts.jsonl",
                abort,
            ),
        ]
        rows = store._connection.execute(
            """
            SELECT record_type, record_id, mirror_relative_path, record_sha256,
                   canonical_payload_json
            FROM artifact_payload_journal
            ORDER BY journal_sequence
            """
        ).fetchall()
        assert [tuple(row[:3]) for row in rows] == [item[:3] for item in expected]
        assert [row["record_sha256"] for row in rows] == [
            item[3]["record_sha256"] for item in expected
        ]
        assert [row["canonical_payload_json"] for row in rows] == [
            _canonical_json(item[3]) for item in expected
        ]
        assert {
            table: tuple(
                row[1]
                for row in store._connection.execute(f"PRAGMA table_info({table})")
            )
            for table in (
                "input_receipts",
                "shadow_admissions",
                "execution_claims",
                "settlement_receipts",
            )
        } == {
            "input_receipts": (
                "input_id",
                "journal_sequence",
                "record_sha256",
                "observed_at_utc",
            ),
            "shadow_admissions": (
                "admission_id",
                "input_id",
                "market_id",
                "policy_snapshot_sha256",
                "journal_sequence",
                "record_sha256",
            ),
            "execution_claims": (
                "admission_id",
                "state",
                "study_trade_id",
                "execution_journal_sequence",
                "claimed_at_utc",
                "updated_at_utc",
            ),
            "settlement_receipts": (
                "study_trade_id",
                "settlement_observation_sha256",
                "journal_sequence",
                "record_sha256",
            ),
        }
    assert study_root.joinpath("artifacts", "executions.jsonl").read_bytes() == _record_full_line(
        execution
    )


def test_shadow_admission_id_uses_the_input_snapshot_id_not_snapshot_hash(
    tmp_path: Path,
):
    _study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        input_record = store.record_input(_input_record(payload["manifest_sha256"]))
        invalid = _admission_record(input_record)
        invalid["admission_id"] = hashlib.sha256(
            _canonical_json(
                {
                    "study_id": STUDY_ID,
                    "input_id": input_record["input_id"],
                    "market_id": invalid["market_id"],
                    "market_snapshot_id": input_record["market_snapshot_sha256"],
                    "policy_snapshot_sha256": input_record["policy_snapshot_sha256"],
                }
            ).encode("utf-8")
        ).hexdigest()
        with pytest.raises(HorizonStudyArtifactStoreError, match="admission_id"):
            store.record_shadow_admission(invalid)

        admission = store.record_shadow_admission(_admission_record(input_record))
        assert admission["admission_id"] != invalid["admission_id"]


def test_rebuilds_multiple_missing_mirror_rows_in_journal_sequence_order(
    tmp_path: Path,
):
    _study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        first = store.record_input(_input_record(payload["manifest_sha256"]))
        second_payload = dict(_input_record(payload["manifest_sha256"]))
        second_payload["source_url"] = "https://example.com/second-story"
        second_payload["input_id"] = _input_id(second_payload)
        second = store.record_input(second_payload)
        expected = _record_full_line(first) + _record_full_line(second)
        store.inputs_path.unlink()

    with HorizonStudyArtifactStore(manifest_path) as reopened:
        assert reopened.inputs_path.read_bytes() == expected


def test_startup_rejects_typed_and_generic_orphans_before_mirror_repair(
    tmp_path: Path,
):
    study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        store.record_input(_input_record(payload["manifest_sha256"]))
    inputs_path = study_root / "artifacts" / "inputs.jsonl"
    inputs_path.unlink()
    with sqlite3.connect(study_root / "study_state.db") as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("UPDATE input_receipts SET journal_sequence = 999")
    with pytest.raises(HorizonStudyArtifactStoreError, match="orphan|index"):
        HorizonStudyArtifactStore(manifest_path)
    assert not inputs_path.exists()

    other_root, other_manifest_path, other_payload = _write_study_root(tmp_path / "other")
    with HorizonStudyArtifactStore(other_manifest_path) as store:
        store.record_input(_input_record(other_payload["manifest_sha256"]))
    other_inputs_path = other_root / "artifacts" / "inputs.jsonl"
    other_inputs_path.unlink()
    with sqlite3.connect(other_root / "study_state.db") as conn:
        conn.execute("DELETE FROM input_receipts")
    with pytest.raises(HorizonStudyArtifactStoreError, match="orphan|index"):
        HorizonStudyArtifactStore(other_manifest_path)
    assert not other_inputs_path.exists()


def test_startup_rejects_changed_authoritative_payload_and_wrong_mirror_path(
    tmp_path: Path,
):
    study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        store.record_input(_input_record(payload["manifest_sha256"]))
    with sqlite3.connect(study_root / "study_state.db") as conn:
        conn.execute(
            "UPDATE artifact_payload_journal "
            "SET mirror_relative_path = 'artifacts/aborts.jsonl'"
        )
    with pytest.raises(HorizonStudyArtifactStoreError, match="mirror"):
        HorizonStudyArtifactStore(manifest_path)

    other_root, other_manifest_path, other_payload = _write_study_root(tmp_path / "other")
    with HorizonStudyArtifactStore(other_manifest_path) as store:
        store.record_input(_input_record(other_payload["manifest_sha256"]))
    with sqlite3.connect(other_root / "study_state.db") as conn:
        conn.execute(
            "UPDATE artifact_payload_journal "
            "SET canonical_payload_json = '{\"changed\":true}'"
        )
    with pytest.raises(HorizonStudyArtifactStoreError, match="payload|hash"):
        HorizonStudyArtifactStore(other_manifest_path)


@pytest.mark.parametrize(
    "lookup",
    (
        None,
        _StudyLedgerLookup(None),
        _StudyLedgerLookup((), error=RuntimeError("unavailable")),
        _StudyLedgerLookup(()),
        _StudyLedgerLookup(
            (
                StudyLedgerExecutionLink(
                    study_trade_id="study-trade-1",
                    study_id=STUDY_ID,
                    admission_id="f" * 64,
                ),
            )
        ),
        _StudyLedgerLookup(
            (
                StudyLedgerExecutionLink(
                    study_trade_id="study-trade-1",
                    study_id=STUDY_ID,
                    admission_id="a" * 64,
                ),
                StudyLedgerExecutionLink(
                    study_trade_id="study-trade-2",
                    study_id=STUDY_ID,
                    admission_id="a" * 64,
                ),
            )
        ),
    ),
    ids=("missing", "unavailable", "raises", "zero", "mismatched", "multiple"),
)
def test_execution_recovery_lookup_fail_closed_without_writing_new_execution_state(
    tmp_path: Path,
    lookup: _StudyLedgerLookup | None,
):
    study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        input_record = store.record_input(_input_record(payload["manifest_sha256"]))
        admission = store.record_shadow_admission(_admission_record(input_record))
        _write_claim(store, admission)

    with pytest.raises(HorizonStudyArtifactStoreError, match="lookup|execution|orphan|ambiguous"):
        HorizonStudyArtifactStore(
            manifest_path,
            study_ledger_execution_lookup=lookup,
        )
    _assert_no_execution_artifact(study_root)


def test_execution_recovery_accepts_only_one_matching_read_only_link(tmp_path: Path):
    study_root, manifest_path, payload = _write_study_root(tmp_path)
    with HorizonStudyArtifactStore(manifest_path) as store:
        input_record = store.record_input(_input_record(payload["manifest_sha256"]))
        admission = store.record_shadow_admission(_admission_record(input_record))
        _write_claim(store, admission)

    lookup = _StudyLedgerLookup(
        (
            StudyLedgerExecutionLink(
                study_trade_id="study-trade-1",
                study_id=STUDY_ID,
                admission_id=admission["admission_id"],
            ),
        )
    )
    with HorizonStudyArtifactStore(
        manifest_path,
        study_ledger_execution_lookup=lookup,
    ):
        pass
    assert lookup.calls == [(STUDY_ID, admission["admission_id"])]
    _assert_no_execution_artifact(study_root)


@pytest.mark.parametrize("suffix", ("-journal", "-shm", "-wal"))
def test_startup_refuses_sqlite_sidecars(tmp_path: Path, suffix: str):
    study_root, manifest_path, _payload = _write_study_root(tmp_path)
    Path(f"{study_root / 'study_state.db'}{suffix}").write_bytes(b"sidecar")
    with pytest.raises(HorizonStudyArtifactStoreError, match="study state|invalid"):
        HorizonStudyArtifactStore(manifest_path)


def test_bootstrap_application_id_metadata_and_preimage_survive_writes_and_close(
    tmp_path: Path,
):
    study_root, manifest_path, payload = _write_study_root(tmp_path)
    state_path = study_root / "study_state.db"
    with sqlite3.connect(state_path) as conn:
        before_application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        before_bootstrap = conn.execute(
            "SELECT * FROM horizon_study_bootstrap"
        ).fetchall()

    with HorizonStudyArtifactStore(manifest_path) as store:
        store.record_input(_input_record(payload["manifest_sha256"]))

    with sqlite3.connect(state_path) as conn:
        after_application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        after_bootstrap = conn.execute(
            "SELECT * FROM horizon_study_bootstrap"
        ).fetchall()
    assert after_application_id == before_application_id
    assert after_bootstrap == before_bootstrap
    assert not any(Path(f"{state_path}{suffix}").exists() for suffix in ("-journal", "-shm", "-wal"))


def test_startup_refuses_changed_bootstrap_application_id_or_preimage(tmp_path: Path):
    study_root, manifest_path, _payload = _write_study_root(tmp_path)
    with sqlite3.connect(study_root / "study_state.db") as conn:
        conn.execute("PRAGMA application_id = 0")
    with pytest.raises(HorizonStudyArtifactStoreError, match="study state|invalid"):
        HorizonStudyArtifactStore(manifest_path)

    other_root, other_manifest_path, _other_payload = _write_study_root(tmp_path / "other")
    with sqlite3.connect(other_root / "study_state.db") as conn:
        conn.execute(
            "UPDATE horizon_study_bootstrap SET manifest_preimage_sha256 = ?",
            ("0" * 64,),
        )
    with pytest.raises(HorizonStudyArtifactStoreError, match="study state|invalid"):
        HorizonStudyArtifactStore(other_manifest_path)


def test_rejects_symlinked_artifact_parent_before_any_state_write(tmp_path: Path):
    study_root, manifest_path, _payload = _write_study_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (study_root / "artifacts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(HorizonStudyArtifactStoreError, match="study path"):
        HorizonStudyArtifactStore(manifest_path)
