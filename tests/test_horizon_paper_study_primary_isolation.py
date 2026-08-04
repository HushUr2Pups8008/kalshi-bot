from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import main
from trading.horizon_paper_study_manifest import (
    derive_horizon_paper_study_database_identity,
)


STUDY_ID = "pm-horizon-15-30-20260805"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _write_manifest_only_study(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    data_root = tmp_path / "data"
    study_root = data_root / "horizon_paper_studies" / STUDY_ID
    study_root.mkdir(parents=True)
    policy_path = study_root / "policy_snapshot.json"
    fee_path = study_root / "fee_schedule.json"
    policy_path.write_bytes(b'{"matcher":"pinned"}')
    fee_path.write_bytes(b'{"fee_schedule":"pinned"}')
    ledger_path = study_root / "study_ledger.db"
    state_path = study_root / "study_state.db"
    ledger_path.write_bytes(b"do-not-open-ledger")
    state_path.write_bytes(b"do-not-open-state")
    configuration = {
        "schema_version": 1,
        "study_id": STUDY_ID,
        "study_kind": "polymarket_horizon_15_30",
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
    payload = {
        **configuration,
        "database_identity": derive_horizon_paper_study_database_identity(
            configuration
        ),
    }
    payload["manifest_sha256"] = hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    manifest_path = study_root / "manifest.json"
    manifest_path.write_text(_canonical_json(payload), encoding="utf-8")
    manifest_path.chmod(0o600)
    attestation_path = (
        tmp_path
        / "logs"
        / "state"
        / "horizon_paper_studies"
        / STUDY_ID
        / "runtime_attestation.json"
    )
    attestation_path.parent.mkdir(parents=True)
    attestation_path.write_bytes(b"do-not-open-attestation")
    return data_root, ledger_path, state_path, attestation_path


def test_primary_live_guard_manifest_blocks_without_opening_study_state(
    tmp_path: Path,
    monkeypatch,
):
    data_root, ledger_path, state_path, attestation_path = _write_manifest_only_study(
        tmp_path
    )
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path in {ledger_path, state_path, attestation_path}:
            raise AssertionError(f"primary guard opened isolated study state: {path}")
        return original_read_bytes(path)

    def fail_if_sqlite_opened(*_args, **_kwargs):
        raise AssertionError("primary guard must not open a study database")

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(sqlite3, "connect", fail_if_sqlite_opened)

    active, failures = main._provisioned_cohort_live_risk_gate_failures(
        db_root=data_root
    )

    assert active is True
    assert failures == [
        "horizon paper study remains permanently isolated from live trading"
    ]


def test_primary_live_guard_fails_closed_for_changed_study_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    data_root, ledger_path, state_path, attestation_path = _write_manifest_only_study(
        tmp_path
    )
    (ledger_path.parent / "policy_snapshot.json").write_bytes(b'{"matcher":"changed"}')
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path in {ledger_path, state_path, attestation_path}:
            raise AssertionError(f"primary guard opened isolated study state: {path}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    active, failures = main._provisioned_cohort_live_risk_gate_failures(
        db_root=data_root
    )

    assert active is True
    assert len(failures) == 1
    assert "invalid horizon paper study manifest" in failures[0]
