"""Isolation and aggregate-risk contracts for paper cohorts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from trading.paper_cohorts import (
    LEGACY_PAPER_COHORT_ID,
    active_cohort_binding_for_db,
    aggregate_open_exposure_snapshot,
    discover_paper_risk_cohorts,
    initialize_active_paper_cohort_manifest,
    resolve_runtime_paper_cohort,
    risk_cohorts_for_runtime,
    validate_active_paper_cohort_manifest,
)


def _state_db(path: Path, *, notional_bankroll: float, unresolved: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO bot_state(key, value) VALUES ('notional_bankroll', ?)",
            (str(notional_bankroll),),
        )
        conn.execute("CREATE TABLE paper_trades (resolved INTEGER NOT NULL)")
        conn.executemany(
            "INSERT INTO paper_trades(resolved) VALUES (?)",
            [(0,)] * unresolved,
        )


def _marks(marked_value: float, *, priced_count: int = 1) -> dict[str, object]:
    return {
        "marked_value": marked_value,
        "total_cost": marked_value,
        "unknown_cost": 0.0,
        "priced_count": priced_count,
        "unpriced_count": 0,
        "snapshot_fallback_count": 0,
        "as_of": "2026-07-28T00:00:00+00:00",
    }


def test_legacy_runtime_cohort_keeps_legacy_db_and_configured_bankroll(tmp_path: Path):
    runtime = resolve_runtime_paper_cohort(
        LEGACY_PAPER_COHORT_ID,
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=None,
        db_root=tmp_path,
    )

    assert runtime.cohort_id == LEGACY_PAPER_COHORT_ID
    assert runtime.db_path == tmp_path / "paper_trades.db"
    assert runtime.starting_bankroll == pytest.approx(500.0)
    assert runtime.writable is True
    assert risk_cohorts_for_runtime(runtime, legacy_starting_bankroll=500.0) == (runtime,)


def test_active_runtime_cohort_requires_explicit_bankroll_and_isolated_path(tmp_path: Path):
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=None,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )

    assert runtime.db_path == tmp_path / "paper_cohorts" / "active-20260728" / "paper_trades.db"
    assert runtime.starting_bankroll == pytest.approx(125.0)
    assert runtime.writable is True

    legacy, active = risk_cohorts_for_runtime(runtime, legacy_starting_bankroll=500.0)
    assert legacy.cohort_id == LEGACY_PAPER_COHORT_ID
    assert legacy.db_path == tmp_path / "paper_trades.db"
    assert legacy.writable is False
    assert active == runtime


def test_active_cohort_requires_immutable_manifest_before_runtime_bootstrap(tmp_path: Path):
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)

    with pytest.raises(FileNotFoundError, match="manifest"):
        validate_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )

    manifest_path = initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )

    assert manifest_path == runtime.db_path.parent / "cohort.json"
    assert manifest_path.exists()
    assert runtime.db_path.exists() is True
    binding = validate_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    assert binding.cohort_identity
    assert binding.legacy_starting_bankroll == pytest.approx(500.0)
    assert binding.legacy_baseline_attestation
    assert binding.legacy_baseline_verification == "operator_attested_unverified"
    assert validate_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=None,
    ) == binding
    assert active_cohort_binding_for_db(runtime.db_path) == binding
    with pytest.raises(ValueError, match="horizon"):
        validate_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=7.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )
    altered_bankroll = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=None,
        active_starting_bankroll=130.0,
        db_root=tmp_path,
    )
    with pytest.raises(ValueError, match="starting_bankroll"):
        validate_active_paper_cohort_manifest(
            altered_bankroll,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=None,
        )


def test_active_manifest_rejects_tampered_legacy_baseline_attestation(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-attestation",
        legacy_starting_bankroll=None,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    manifest_path = initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["legacy_baseline_attestation"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="baseline attestation"):
        validate_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=None,
        )


def test_active_cohort_manifest_rejects_missing_or_foreign_database_identity(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )

    runtime.db_path.unlink()
    with pytest.raises(ValueError, match="database missing"):
        validate_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )

    with sqlite3.connect(runtime.db_path) as conn:
        conn.execute("CREATE TABLE foreign_state (value TEXT)")
    with pytest.raises(ValueError, match="identity"):
        active_cohort_binding_for_db(runtime.db_path)


def test_active_cohort_identity_cannot_be_copied_outside_its_manifest_directory(
    tmp_path: Path,
):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )

    copied_path = tmp_path / "copied" / "paper_trades.db"
    copied_path.parent.mkdir()
    shutil.copy2(runtime.db_path, copied_path)

    with pytest.raises(ValueError, match="orphaned active cohort identity"):
        active_cohort_binding_for_db(copied_path)


def test_active_cohort_manifest_binds_immutable_cutover_snapshot(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    binding = validate_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    snapshot_path = binding.legacy_snapshot_path
    snapshot_sha256 = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert discover_paper_risk_cohorts(tmp_path)[0].db_path == snapshot_path

    with sqlite3.connect(legacy_path) as conn:
        conn.execute("CREATE TABLE post_provision_change (value TEXT)")
    assert validate_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    ).legacy_snapshot_path == snapshot_path
    assert hashlib.sha256(snapshot_path.read_bytes()).hexdigest() == snapshot_sha256
    with pytest.raises(ValueError, match="diverged"):
        discover_paper_risk_cohorts(tmp_path)
    with pytest.raises(ValueError, match="diverged"):
        active_cohort_binding_for_db(runtime.db_path)

    with sqlite3.connect(snapshot_path) as conn:
        conn.execute("CREATE TABLE tampered_cutover_snapshot (value TEXT)")
    with pytest.raises(ValueError, match="legacy_snapshot_sha256"):
        validate_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlink permissions")
@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_active_cohort_manifest_rejects_cutover_snapshot_alias_to_legacy(
    tmp_path: Path,
    alias_kind: str,
):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    snapshot_path = runtime.db_path.parent / "legacy_cutover.db"
    snapshot_path.unlink()
    if alias_kind == "symlink":
        snapshot_path.symlink_to(legacy_path)
    else:
        os.link(legacy_path, snapshot_path)

    with pytest.raises(ValueError, match="immutable database must not be aliased"):
        validate_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )
    with pytest.raises(ValueError, match="immutable database must not be aliased"):
        discover_paper_risk_cohorts(tmp_path)


def test_active_cohort_manifest_rejects_cutover_snapshot_hardlink_between_cohorts(
    tmp_path: Path,
):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    first = resolve_runtime_paper_cohort(
        "active-20260728-a",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    second = resolve_runtime_paper_cohort(
        "active-20260728-b",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    for cohort in (first, second):
        initialize_active_paper_cohort_manifest(
            cohort,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )
    first_snapshot = first.db_path.parent / "legacy_cutover.db"
    second_snapshot = second.db_path.parent / "legacy_cutover.db"
    second_snapshot.unlink()
    os.link(first_snapshot, second_snapshot)

    with pytest.raises(ValueError, match="immutable database must not be aliased"):
        discover_paper_risk_cohorts(tmp_path)


def test_active_cohort_provisioning_rejects_external_hardlinked_legacy_database(
    tmp_path: Path,
):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    os.link(legacy_path, tmp_path / "legacy-external-alias.db")
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )

    with pytest.raises(ValueError, match="immutable database must not be aliased"):
        initialize_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )
    assert not runtime.db_path.parent.exists()


def test_active_cohort_manifest_rejects_external_hardlinked_active_database(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    os.link(runtime.db_path, tmp_path / "active-external-alias.db")

    with pytest.raises(ValueError, match="immutable database must not be aliased"):
        active_cohort_binding_for_db(runtime.db_path)


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlink permissions")
def test_discover_paper_risk_cohorts_rejects_symlinked_cohort_directory(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    alias_dir = runtime.db_path.parent.parent / "active-alias"
    alias_dir.symlink_to(runtime.db_path.parent, target_is_directory=True)

    with pytest.raises(ValueError, match="unknown file"):
        discover_paper_risk_cohorts(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl lock contract is POSIX-only")
def test_active_cohort_provisioning_requires_unheld_runtime_lock(tmp_path: Path):
    import fcntl

    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    lock_path = tmp_path / "bot_runtime.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="runtime lock is held"):
            initialize_active_paper_cohort_manifest(
                runtime,
                max_days_to_close=14.0,
                legacy_db_path=legacy_path,
                legacy_starting_bankroll=500.0,
            )
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def test_active_cohort_provisioning_requires_reconciled_legacy_trades(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0, unresolved=1)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=None,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )

    with pytest.raises(ValueError, match="zero unresolved"):
        initialize_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )
    assert not runtime.db_path.parent.exists()


@pytest.mark.parametrize(
    ("column_sql", "resolved_state"),
    [
        ("INTEGER", "false"),
        ("INTEGER", -1),
        ("INTEGER", 2),
        ("INTEGER", 0.5),
        ("INTEGER", None),
        ("TEXT", "0"),
    ],
    ids=["text-false", "negative", "out-of-range", "fractional", "null", "text-zero"],
)
def test_active_cohort_provisioning_rejects_noncanonical_legacy_resolution_state(
    tmp_path: Path,
    column_sql: str,
    resolved_state: object,
):
    legacy_path = tmp_path / "paper_trades.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.execute(f"CREATE TABLE paper_trades (resolved {column_sql})")
        conn.execute("INSERT INTO paper_trades(resolved) VALUES (?)", (resolved_state,))
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=None,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )

    with pytest.raises(ValueError, match="resolved state is invalid"):
        initialize_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )
    assert not runtime.db_path.parent.exists()


def test_active_cohort_provisioning_requires_legacy_resolved_column(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.execute("CREATE TABLE paper_trades (status TEXT)")
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=None,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )

    with pytest.raises(ValueError, match="resolved state is invalid"):
        initialize_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )
    assert not runtime.db_path.parent.exists()


def test_active_cohort_provisioning_accepts_canonical_resolved_legacy_trades(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    with sqlite3.connect(legacy_path) as conn:
        conn.execute("INSERT INTO paper_trades(resolved) VALUES (1)")
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=None,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )

    manifest_path = initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )

    assert manifest_path.exists()


def test_active_cohort_manifest_rejects_horizon_beyond_observed_universe(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    runtime = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )

    with pytest.raises(ValueError, match="observed universe"):
        initialize_active_paper_cohort_manifest(
            runtime,
            max_days_to_close=31.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )


def test_discover_paper_risk_cohorts_keeps_every_provisioned_active_cohort_visible(
    tmp_path: Path,
):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    for cohort_id in ("active-a", "active-b"):
        cohort = resolve_runtime_paper_cohort(
            cohort_id,
            legacy_starting_bankroll=500.0,
            active_starting_bankroll=125.0,
            db_root=tmp_path,
        )
        initialize_active_paper_cohort_manifest(
            cohort,
            max_days_to_close=14.0,
            legacy_db_path=legacy_path,
            legacy_starting_bankroll=500.0,
        )

    cohorts = discover_paper_risk_cohorts(tmp_path)

    assert [cohort.cohort_id for cohort in cohorts] == ["legacy", "active-a", "active-b"]
    assert all(cohort.writable is False for cohort in cohorts)


def test_discover_paper_risk_cohorts_rejects_stranded_active_database(tmp_path: Path):
    legacy_path = tmp_path / "paper_trades.db"
    _state_db(legacy_path, notional_bankroll=500.0)
    stranded = tmp_path / "paper_cohorts" / "stranded" / "paper_trades.db"
    _state_db(stranded, notional_bankroll=100.0)

    with pytest.raises(ValueError, match="manifest"):
        discover_paper_risk_cohorts(tmp_path)


@pytest.mark.parametrize("cohort_id", ("", "legacy/overwrite", "legacy..", "active cohort"))
def test_runtime_cohort_rejects_unsafe_identity(cohort_id: str, tmp_path: Path):
    with pytest.raises(ValueError, match="cohort"):
        resolve_runtime_paper_cohort(
            cohort_id,
            legacy_starting_bankroll=500.0,
            active_starting_bankroll=100.0,
            db_root=tmp_path,
        )


def test_active_runtime_cohort_rejects_missing_or_nonpositive_explicit_bankroll(tmp_path: Path):
    for bankroll in (None, 0.0, -1.0, float("inf")):
        with pytest.raises(ValueError, match="starting bankroll"):
            resolve_runtime_paper_cohort(
                "active",
                legacy_starting_bankroll=500.0,
                active_starting_bankroll=bankroll,
                db_root=tmp_path,
            )


def test_aggregate_open_exposure_sums_cohorts_and_preserves_provenance(tmp_path: Path):
    legacy = resolve_runtime_paper_cohort(
        "legacy",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=None,
        db_root=tmp_path,
    )
    active = resolve_runtime_paper_cohort(
        "active",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=100.0,
        db_root=tmp_path,
    )
    _state_db(legacy.db_path, notional_bankroll=450.0, unresolved=2)
    _state_db(active.db_path, notional_bankroll=80.0, unresolved=1)
    legacy_sha_before = hashlib.sha256(legacy.db_path.read_bytes()).hexdigest()
    marks_by_path = {legacy.db_path: _marks(10.0), active.db_path: _marks(5.0)}

    snapshot = aggregate_open_exposure_snapshot(
        (legacy, active),
        marks_provider=lambda path: marks_by_path[path],
    )

    assert snapshot.ok is True
    assert snapshot.configured_bankroll == pytest.approx(600.0)
    assert snapshot.notional_bankroll == pytest.approx(530.0)
    assert snapshot.marked_value == pytest.approx(15.0)
    assert snapshot.unresolved_trade_count == 3
    assert [item.cohort_id for item in snapshot.cohorts] == ["legacy", "active"]
    assert snapshot.cohorts[0].db_path == legacy.db_path
    assert snapshot.cohorts[1].db_path == active.db_path
    assert hashlib.sha256(legacy.db_path.read_bytes()).hexdigest() == legacy_sha_before


def test_aggregate_open_exposure_fails_closed_when_any_cohort_is_unreadable(tmp_path: Path):
    legacy = resolve_runtime_paper_cohort(
        "legacy",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=None,
        db_root=tmp_path,
    )
    active = resolve_runtime_paper_cohort(
        "active",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=100.0,
        db_root=tmp_path,
    )
    _state_db(active.db_path, notional_bankroll=80.0)

    snapshot = aggregate_open_exposure_snapshot(
        (legacy, active),
        marks_provider=lambda _path: _marks(1.0),
    )

    assert snapshot.ok is False
    assert snapshot.failure_status == "cohort_state_unavailable"
    assert snapshot.cohorts == ()
