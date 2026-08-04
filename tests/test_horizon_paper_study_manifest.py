from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from trading.horizon_paper_study_manifest import (
    HORIZON_PAPER_STUDY_KIND,
    HorizonPaperStudyManifest,
    HorizonPaperStudyManifestError,
    derive_horizon_paper_study_database_identity,
    discover_polymarket_horizon_15_30_manifest_blockers,
    validate_horizon_paper_study_manifest,
    validate_study_coexistence,
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


def _write_study(
    tmp_path: Path,
    *,
    study_id: str = STUDY_ID,
) -> tuple[Path, Path, Path, dict[str, object]]:
    data_root = tmp_path / "data"
    study_root = data_root / "horizon_paper_studies" / study_id
    study_root.mkdir(parents=True)
    policy_path = study_root / "policy_snapshot.json"
    fee_path = study_root / "fee_schedule.json"
    policy_path.write_bytes(b'{"matcher":"pinned"}')
    fee_path.write_bytes(b'{"fee_schedule":"pinned"}')
    configuration = {
        "schema_version": 1,
        "study_id": study_id,
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
    return data_root, study_root, manifest_path, payload


def _rewrite_manifest(manifest_path: Path, payload: dict[str, object]) -> None:
    manifest_path.write_text(_canonical_json(_seal_manifest(payload)), encoding="utf-8")


def test_validates_exact_immutable_horizon_study_manifest(tmp_path: Path):
    data_root, study_root, manifest_path, _payload = _write_study(tmp_path)

    manifest = validate_horizon_paper_study_manifest(
        manifest_path,
        data_root=data_root,
    )

    assert manifest.study_id == STUDY_ID
    assert manifest.study_kind == HORIZON_PAPER_STUDY_KIND
    assert manifest.ledger_path == "study_ledger.db"
    assert manifest.state_db_path == "study_state.db"
    assert manifest.study_root(data_root) == study_root
    assert manifest.horizon_lower_exclusive_days == 14.0
    assert manifest.horizon_upper_inclusive_days == 30.0
    assert manifest.live_order_forbidden is True
    assert manifest.profit_receipt_attested is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("starting_bankroll", "0"),
        ("starting_bankroll", "-1"),
        ("venue", "kalshi"),
        ("study_kind", "legacy_pending"),
        ("horizon_lower_exclusive_days", 13.0),
        ("horizon_upper_inclusive_days", 31.0),
        ("ledger_path", "../paper_trades.db"),
        ("live_order_forbidden", False),
        ("profit_receipt_attested", True),
    ],
)
def test_rejects_noncanonical_manifest_contract_values(
    tmp_path: Path,
    field: str,
    value: object,
):
    data_root, _study_root, manifest_path, payload = _write_study(tmp_path)
    payload[field] = value
    _rewrite_manifest(manifest_path, payload)

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_missing_rewritten_or_noncanonical_manifest_inputs(tmp_path: Path):
    data_root, study_root, manifest_path, payload = _write_study(tmp_path)
    policy_path = study_root / "policy_snapshot.json"
    fee_path = study_root / "fee_schedule.json"

    policy_path.write_bytes(b'{"matcher":"rewritten"}')
    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)

    policy_path.write_bytes(b'{"matcher":"pinned"}')
    payload["policy_snapshot_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    _rewrite_manifest(manifest_path, payload)
    fee_path.unlink()
    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)

    fee_path.write_bytes(b'{"fee_schedule":"pinned"}')
    payload["fee_schedule_sha256"] = hashlib.sha256(fee_path.read_bytes()).hexdigest()
    _rewrite_manifest(manifest_path, payload)
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_manifest_without_immutable_owner_mode(tmp_path: Path):
    data_root, _study_root, manifest_path, _payload = _write_study(tmp_path)
    manifest_path.chmod(0o644)

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_resealed_manifest_not_bound_to_initialized_database_preimage(
    tmp_path: Path,
):
    data_root, _study_root, manifest_path, payload = _write_study(tmp_path)
    payload["starting_bankroll"] = "275.00"
    payload["database_identity"] = derive_horizon_paper_study_database_identity(
        {
            key: value
            for key, value in payload.items()
            if key not in {"database_identity", "manifest_sha256"}
        }
    )
    _rewrite_manifest(manifest_path, payload)

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_database_without_bound_bootstrap_metadata(tmp_path: Path):
    data_root, study_root, manifest_path, _payload = _write_study(tmp_path)
    ledger_path = study_root / "study_ledger.db"
    ledger_path.unlink()
    ledger_path.write_bytes(b"arbitrary-ledger-bytes")

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_database_metadata_with_unbound_identity(tmp_path: Path):
    data_root, study_root, manifest_path, _payload = _write_study(tmp_path)
    ledger_path = study_root / "study_ledger.db"
    with sqlite3.connect(ledger_path) as conn:
        conn.execute(
            f"UPDATE {_BOOTSTRAP_TABLE} SET database_identity = ?",
            ("b" * 64,),
        )

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_state_database_metadata_with_wrong_role(tmp_path: Path):
    data_root, study_root, manifest_path, _payload = _write_study(tmp_path)
    with sqlite3.connect(study_root / "study_state.db") as conn:
        conn.execute(f"UPDATE {_BOOTSTRAP_TABLE} SET database_role = 'ledger'")

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


@pytest.mark.parametrize(
    "mutation",
    [
        "PRAGMA application_id = 0",
        f"ALTER TABLE {_BOOTSTRAP_TABLE} ADD COLUMN unexpected TEXT",
    ],
)
def test_rejects_wrong_bootstrap_application_or_schema(
    tmp_path: Path,
    mutation: str,
):
    data_root, study_root, manifest_path, _payload = _write_study(tmp_path)
    with sqlite3.connect(study_root / "study_state.db") as conn:
        conn.execute(mutation)

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_arbitrary_database_identity_even_when_bootstrap_rows_match(
    tmp_path: Path,
):
    data_root, study_root, manifest_path, payload = _write_study(tmp_path)
    payload["database_identity"] = "a" * 64
    resealed = _seal_manifest(payload)
    manifest_path.write_text(_canonical_json(resealed), encoding="utf-8")
    for database_path in (
        study_root / "study_ledger.db",
        study_root / "study_state.db",
    ):
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                f"""
                UPDATE {_BOOTSTRAP_TABLE}
                SET database_identity = ?, manifest_preimage_sha256 = ?
                """,
                (resealed["database_identity"], resealed["manifest_sha256"]),
            )

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_symlinked_or_hardlinked_study_targets(tmp_path: Path):
    data_root, study_root, manifest_path, payload = _write_study(tmp_path)
    policy_path = study_root / "policy_snapshot.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(policy_path.read_bytes())
    policy_path.unlink()
    policy_path.symlink_to(replacement)

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)

    policy_path.unlink()
    policy_path.write_bytes(replacement.read_bytes())
    payload["policy_snapshot_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    _rewrite_manifest(manifest_path, payload)
    hardlink = tmp_path / "policy-hardlink.json"
    os.link(policy_path, hardlink)
    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)

    hardlink.unlink()
    ledger_path = study_root / "study_ledger.db"
    ledger_path.unlink()
    ledger_path.symlink_to(replacement)
    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(manifest_path, data_root=data_root)


def test_rejects_manifest_outside_fixed_study_storage_root(tmp_path: Path):
    data_root, study_root, manifest_path, payload = _write_study(tmp_path)
    unsafe_root = data_root / "legacy_pending_paper_cohorts" / STUDY_ID
    unsafe_root.parent.mkdir(parents=True)
    shutil.move(str(study_root), unsafe_root)
    unsafe_manifest = unsafe_root / "manifest.json"
    _rewrite_manifest(unsafe_manifest, payload)

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_horizon_paper_study_manifest(unsafe_manifest, data_root=data_root)


def test_coexistence_leaves_live_legacy_pending_db_unopened_and_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    data_root, study_root, _manifest_path, payload = _write_study(tmp_path)
    manifest = HorizonPaperStudyManifest.from_dict(payload)
    shutil.rmtree(study_root)
    pending_db = data_root / "legacy_pending_paper_cohorts" / "cohort-a" / "paper_trades.db"
    pending_db.parent.mkdir(parents=True)
    with sqlite3.connect(pending_db) as conn:
        conn.execute("CREATE TABLE paper_trades (resolved INTEGER NOT NULL)")
        conn.execute("INSERT INTO paper_trades (resolved) VALUES (0)")
    before = pending_db.read_bytes()

    def fail_if_opened(*_args, **_kwargs):
        raise AssertionError("coexistence must not open legacy-pending state")

    monkeypatch.setattr(sqlite3, "connect", fail_if_opened)
    validate_study_coexistence(
        manifest,
        data_root=data_root,
        logs_root=tmp_path / "logs",
    )

    assert pending_db.read_bytes() == before


def test_coexistence_rejects_existing_or_symlinked_study_target_and_label_collision(
    tmp_path: Path,
):
    data_root, study_root, _manifest_path, payload = _write_study(tmp_path)
    manifest = HorizonPaperStudyManifest.from_dict(payload)

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_study_coexistence(
            manifest,
            data_root=data_root,
            logs_root=tmp_path / "logs",
        )

    shutil.rmtree(study_root)
    study_root.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "other-study-root"
    target.mkdir()
    study_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(HorizonPaperStudyManifestError):
        validate_study_coexistence(
            manifest,
            data_root=data_root,
            logs_root=tmp_path / "logs",
        )

    study_root.unlink()
    with pytest.raises(HorizonPaperStudyManifestError):
        validate_study_coexistence(
            manifest,
            data_root=data_root,
            logs_root=tmp_path / "logs",
            study_launchd_label="com.jake.kalshi-bot",
        )


def test_coexistence_requires_the_dedicated_sibling_logs_root(tmp_path: Path):
    data_root, study_root, _manifest_path, payload = _write_study(tmp_path)
    manifest = HorizonPaperStudyManifest.from_dict(payload)
    shutil.rmtree(study_root)

    with pytest.raises(HorizonPaperStudyManifestError):
        validate_study_coexistence(
            manifest,
            data_root=data_root,
            logs_root=tmp_path / "other-logs",
        )


def test_manifest_discovery_returns_valid_and_invalid_permanent_blocks(tmp_path: Path):
    data_root, study_root, manifest_path, payload = _write_study(tmp_path)

    valid_blocks = discover_polymarket_horizon_15_30_manifest_blockers(data_root)

    assert valid_blocks == (
        "horizon paper study remains permanently isolated from live trading",
    )

    (study_root / "fee_schedule.json").write_bytes(b'{"fee_schedule":"changed"}')
    invalid_blocks = discover_polymarket_horizon_15_30_manifest_blockers(data_root)

    assert len(invalid_blocks) == 1
    assert "invalid horizon paper study manifest" in invalid_blocks[0]
    assert payload["study_id"] == STUDY_ID
