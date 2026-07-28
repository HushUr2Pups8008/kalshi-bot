from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.migrate_paper_settlement_schema import (
    apply_settlement_schema,
    open_readonly,
    plan_settlement_schema,
)
from trading.legacy_settlement_cutover import (
    LegacySettlementCutoverPlan,
    apply_legacy_settlement_cutover,
    plan_legacy_settlement_cutover,
    validate_legacy_settlement_cutover,
)
from trading.settlement_store import SettlementStore


NOW = datetime(2026, 7, 28, 16, 30, tzinfo=timezone.utc)
LEGACY_IDS = ("legacy-a", "legacy-b")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _create_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            venue TEXT NOT NULL DEFAULT 'kalshi',
            resolved INTEGER NOT NULL DEFAULT 0,
            resolved_yes INTEGER,
            resolved_ts TEXT,
            venue_market_id TEXT,
            identity_status TEXT,
            quarantine_reason TEXT,
            side TEXT,
            contracts INTEGER,
            price_cents INTEGER,
            cost_dollars REAL,
            pnl_dollars REAL,
            ts TEXT,
            estimated_prob REAL,
            entry_price_cents REAL,
            signal_source TEXT,
            keywords_matched TEXT,
            series_ticker TEXT,
            llm_magnitude TEXT,
            llm_confidence REAL,
            fast_lane_p REAL,
            accumulation_p REAL,
            structural_p REAL
        )
        """
    )
    conn.commit()
    conn.close()


def _migrate(path: Path) -> None:
    with open_readonly(path) as conn:
        plan = plan_settlement_schema(conn, path)
    apply_settlement_schema(path, plan, reviewed_plan_fingerprint=plan.fingerprint)


def _insert_trade(
    path: Path,
    trade_id: str,
    *,
    resolved: bool,
    pnl_dollars: float | None = None,
) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ticker, venue, resolved, resolved_yes, resolved_ts,
            venue_market_id, identity_status, side, contracts, price_cents,
            cost_dollars, pnl_dollars, ts
        ) VALUES (?, ?, 'kalshi', ?, ?, ?, ?, 'mapped', 'yes', 1, 40, 0.4, ?, ?)
        """,
        (
            trade_id,
            f"KX-{trade_id}",
            int(resolved),
            0 if resolved else None,
            "2026-07-15T14:58:22.940718+00:00" if resolved else None,
            f"KX-{trade_id}",
            pnl_dollars,
            "2026-06-22T14:23:31.822596+00:00",
        ),
    )
    conn.commit()
    conn.close()


def _legacy_accounting(path: Path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            """
            SELECT trade_id, resolved, resolved_yes, pnl_dollars, cost_dollars,
                   resolved_ts, identity_status, venue_market_id,
                   terminal_state, settlement_observation_sha256, settled_at,
                   gross_payout_cents, gross_pnl_cents
            FROM paper_trades
            WHERE trade_id IN (?, ?)
            ORDER BY trade_id
            """,
            LEGACY_IDS,
        ).fetchall()
    finally:
        conn.close()


def _seed_cutover_db(path: Path) -> None:
    _create_legacy_db(path)
    _migrate(path)
    _insert_trade(path, LEGACY_IDS[0], resolved=True, pnl_dollars=-0.50)
    _insert_trade(path, LEGACY_IDS[1], resolved=True, pnl_dollars=-0.28)
    _insert_trade(path, "open-canonical", resolved=False)


def _apply_cutover(path: Path) -> None:
    with open_readonly(path) as conn:
        plan = plan_legacy_settlement_cutover(conn, path)
    apply_legacy_settlement_cutover(
        path,
        plan,
        reviewed_plan_fingerprint=plan.fingerprint,
        reviewed_trade_ids=LEGACY_IDS,
    )


def _snapshot_db(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def test_cutover_plan_binds_notional_and_go_live_state(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO bot_state VALUES (?, ?)",
            (("notional_bankroll", "50.00"), ("go_live_confirmed", "false")),
        )

    with open_readonly(db) as conn:
        initial_plan = plan_legacy_settlement_cutover(conn, db)

    initial_snapshot = json.loads(initial_plan.bot_state_snapshot_json)
    assert initial_snapshot == {
        "go_live_confirmed": "false",
        "notional_bankroll": "50.00",
    }

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE bot_state SET value='80.00' WHERE key='notional_bankroll'"
        )

    with open_readonly(db) as conn:
        drifted_plan = plan_legacy_settlement_cutover(conn, db)

    assert drifted_plan.bot_state_snapshot_sha256 != initial_plan.bot_state_snapshot_sha256
    assert drifted_plan.state_fingerprint != initial_plan.state_fingerprint


def test_cutover_manifest_exempts_only_frozen_legacy_rows_without_mutating_them(
    tmp_path,
):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)

    with SettlementStore(db) as store:
        before = store.conservation(now=NOW)
    assert before.ok is False
    assert before.failures == ("resolved_observation_link",)

    accounting_before = _legacy_accounting(db)
    _apply_cutover(db)
    assert _legacy_accounting(db) == accounting_before

    with SettlementStore(db) as store:
        after = store.conservation(now=NOW)
    assert after.ok is True
    assert after.metrics["legacy_unattested_exemptions"] == 2
    assert after.metrics["resolved_rows_without_observation"] == 2

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM paper_settlement_observations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_settlement_outbox").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM paper_settlement_consumer_receipts"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cutover_manifest_detects_tampered_legacy_accounting(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    _apply_cutover(db)

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_trades SET pnl_dollars = -0.51 WHERE trade_id = ?",
        (LEGACY_IDS[0],),
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
    assert result.ok is False
    assert "legacy_cutover_snapshot_mismatch" in result.failures
    assert "resolved_observation_link" in result.failures


def test_cutover_manifest_rejects_new_unlinked_resolved_row(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    _apply_cutover(db)
    _insert_trade(db, "post-cutover-missing-receipt", resolved=True, pnl_dollars=-0.4)

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
    assert result.ok is False
    assert "legacy_cutover_trade_set_mismatch" in result.failures
    assert "resolved_observation_link" in result.failures


def test_cutover_manifest_uses_a_versioned_projection_across_additive_schema_changes(
    tmp_path,
):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    _apply_cutover(db)

    conn = sqlite3.connect(db)
    try:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN harmless_future_metadata TEXT")
        conn.commit()
    finally:
        conn.close()

    with SettlementStore(db) as store:
        assert store.conservation(now=NOW).ok


def test_cutover_plan_rejects_dangling_observation_pointer_without_creating_schema(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE paper_trades SET settlement_observation_sha256=? WHERE trade_id=?",
            ("a" * 64, LEGACY_IDS[0]),
        )
        conn.commit()
    finally:
        conn.close()

    with open_readonly(db) as readonly:
        with pytest.raises(RuntimeError, match="dangling canonical observation"):
            plan_legacy_settlement_cutover(readonly, db)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_settlement_legacy_%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cutover_manifest_allows_a_frozen_row_to_become_authoritatively_linked(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    _apply_cutover(db)
    observation_sha256 = "b" * 64
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            INSERT INTO paper_settlement_observations (
                observation_sha256, venue, venue_market_id, alias, outcome,
                authoritative_outcome_json, canonical_payload_json, payload_sha256,
                observed_at, effective_at, rules_version, source_id,
                applied_trade_count, bankroll_before_cents, gross_payout_cents,
                bankroll_after_cents, applied_at
            ) VALUES (?, 'kalshi', ?, ?, 'no', '{}', '{}', ?, ?, ?, 'test',
                      'test', 1, '10000', '0', '10000', ?)
            """,
            (
                observation_sha256,
                f"KX-{LEGACY_IDS[0]}",
                f"KX-{LEGACY_IDS[0]}",
                "c" * 64,
                NOW.isoformat(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE paper_trades SET settlement_observation_sha256=? WHERE trade_id=?",
            (observation_sha256, LEGACY_IDS[0]),
        )
        conn.commit()
    finally:
        conn.close()

    with open_readonly(db) as readonly:
        validation = validate_legacy_settlement_cutover(readonly)
    assert validation.ok
    assert validation.exempt_trade_ids == (LEGACY_IDS[1],)


def test_cutover_manifest_history_is_append_only(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    _apply_cutover(db)

    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                """
                UPDATE paper_settlement_legacy_resolution_exemptions
                SET reason_code = 'other'
                WHERE trade_id = ?
                """,
                (LEGACY_IDS[0],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM paper_settlement_legacy_cutover_manifest"
            )
    finally:
        conn.close()


def test_cutover_apply_requires_the_exact_reviewed_trade_set(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)

    with pytest.raises(ValueError, match="reviewed trade ids"):
        apply_legacy_settlement_cutover(
            db,
            plan,
            reviewed_plan_fingerprint=plan.fingerprint,
            reviewed_trade_ids=(LEGACY_IDS[0],),
        )

    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_schema
            WHERE name LIKE 'paper_settlement_legacy_%'
            """
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cutover_plan_artifact_round_trips_and_rejects_tampered_entries(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)

    assert LegacySettlementCutoverPlan.from_json(plan.to_json()) == plan
    tampered = json.loads(plan.to_json())
    tampered["entries"][0]["trade_id"] = "different-trade"
    with pytest.raises(ValueError, match="manifest"):
        LegacySettlementCutoverPlan.from_json(json.dumps(tampered))


def test_cutover_apply_revalidates_a_direct_plan_object_before_writing(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)
    tampered_entry = replace(plan.entries[0], trade_id="different-trade")
    tampered_plan = replace(
        plan,
        entries=(tampered_entry, *plan.entries[1:]),
    )

    with pytest.raises(ValueError, match="plan integrity check"):
        apply_legacy_settlement_cutover(
            db,
            tampered_plan,
            reviewed_plan_fingerprint=plan.fingerprint,
            reviewed_trade_ids=LEGACY_IDS,
        )

    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_settlement_legacy_%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cutover_apply_refuses_preexisting_foreign_key_violations_without_writing(
    tmp_path,
):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    _insert_trade(db, "unmapped-dangling", resolved=True, pnl_dollars=-0.2)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """
            UPDATE paper_trades
            SET identity_status=NULL, settlement_observation_sha256=?
            WHERE trade_id='unmapped-dangling'
            """,
            ("a" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)

    with pytest.raises(RuntimeError, match="foreign key check failed before"):
        apply_legacy_settlement_cutover(
            db,
            plan,
            reviewed_plan_fingerprint=plan.fingerprint,
            reviewed_trade_ids=LEGACY_IDS,
        )

    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_settlement_legacy_%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cutover_plan_explicitly_counts_unmapped_unattested_history(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE paper_trades SET identity_status=NULL WHERE trade_id=?",
            (LEGACY_IDS[1],),
        )
        conn.commit()
    finally:
        conn.close()

    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)

    assert [entry.trade_id for entry in plan.entries] == [LEGACY_IDS[0]]
    assert plan.unattested_resolved_count == 2
    assert plan.non_mapped_unattested_count == 1


def test_cutover_plan_rejects_any_existing_canonical_artifact(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT INTO paper_settlement_quarantine (
            quarantine_id, observation_sha256, payload_sha256, venue,
            venue_market_id, alias, reason_code, details_json,
            open_row_set_sha256, detected_at
        ) VALUES (?, ?, ?, 'kalshi', 'KX-legacy-a', 'KX-legacy-a', ?, '{}', ?, ?)
        """,
        (
            "quarantine-1",
            "a" * 64,
            "b" * 64,
            "test",
            "c" * 64,
            NOW.isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    with open_readonly(db) as readonly:
        with pytest.raises(RuntimeError, match="canonical settlement artifacts"):
            plan_legacy_settlement_cutover(readonly, db)


def test_cutover_cli_requires_explicit_target_reviewed_artifact_and_backup(tmp_path):
    db = tmp_path / "paper.db"
    backup = tmp_path / "paper-before-cutover.db"
    plan_file = tmp_path / "reviewed-plan.json"
    _seed_cutover_db(db)
    script = PROJECT_ROOT / "scripts" / "establish_legacy_settlement_cutover.py"

    missing_target = subprocess.run(
        [sys.executable, str(script)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert missing_target.returncode != 0
    assert "--db" in missing_target.stderr

    planned = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--write-plan",
            str(plan_file),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert planned.returncode == 0, planned.stderr
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    assert [entry["trade_id"] for entry in plan["entries"]] == list(LEGACY_IDS)

    source = sqlite3.connect(db)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--apply",
            "--plan-file",
            str(plan_file),
            "--backup-db",
            str(backup),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert refused.returncode != 0
    assert "reviewed plan fingerprint" in refused.stderr

    applied = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--apply",
            "--plan-file",
            str(plan_file),
            "--backup-db",
            str(backup),
            "--reviewed-plan-fingerprint",
            plan["fingerprint"],
            "--expected-trade-id",
            LEGACY_IDS[0],
            "--expected-trade-id",
            LEGACY_IDS[1],
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert applied.returncode == 0, applied.stderr
    receipt = json.loads(applied.stdout)
    assert receipt["operation"] == "verify"
    assert receipt["operational_ok"] is True
    assert receipt["profit_readiness"]["eligible"] is False
    with SettlementStore(db) as store:
        assert store.conservation(now=NOW).ok


def test_cutover_cli_refuses_to_write_a_plan_over_the_database(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    original = db.read_bytes()
    script = PROJECT_ROOT / "scripts" / "establish_legacy_settlement_cutover.py"

    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--write-plan",
            str(db),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert refused.returncode != 0
    assert "must not replace --db" in refused.stderr
    assert db.read_bytes() == original
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 3
    finally:
        conn.close()


def test_cutover_cli_refuses_to_write_a_plan_over_a_hardlink_alias(tmp_path):
    db = tmp_path / "paper.db"
    plan_alias = tmp_path / "paper-hardlink-plan.json"
    _seed_cutover_db(db)
    original = db.read_bytes()
    os.link(db, plan_alias)
    script = PROJECT_ROOT / "scripts" / "establish_legacy_settlement_cutover.py"

    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--write-plan",
            str(plan_alias),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert refused.returncode != 0
    assert "must not replace --db" in refused.stderr
    assert db.read_bytes() == original
    assert plan_alias.read_bytes() == original


def test_cutover_cli_refuses_a_hardlinked_backup_snapshot(tmp_path):
    db = tmp_path / "paper.db"
    backup = tmp_path / "paper-hardlink.db"
    plan_file = tmp_path / "reviewed-plan.json"
    _seed_cutover_db(db)
    os.link(db, backup)
    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)
    plan_file.write_text(plan.to_json(), encoding="utf-8")
    script = PROJECT_ROOT / "scripts" / "establish_legacy_settlement_cutover.py"

    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--apply",
            "--plan-file",
            str(plan_file),
            "--backup-db",
            str(backup),
            "--reviewed-plan-fingerprint",
            plan.fingerprint,
            "--expected-trade-id",
            LEGACY_IDS[0],
            "--expected-trade-id",
            LEGACY_IDS[1],
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert refused.returncode == 2
    assert "distinct pre-apply snapshot" in refused.stderr
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_settlement_legacy_%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX flock")
def test_cutover_cli_refuses_the_running_bot_lock_without_creating_cutover_objects(tmp_path):
    import fcntl

    db = tmp_path / "paper.db"
    backup = tmp_path / "paper-before-cutover.db"
    plan_file = tmp_path / "reviewed-plan.json"
    _seed_cutover_db(db)
    _snapshot_db(db, backup)
    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)
    plan_file.write_text(plan.to_json(), encoding="utf-8")
    script = PROJECT_ROOT / "scripts" / "establish_legacy_settlement_cutover.py"
    lock_path = db.parent / "bot_runtime.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused = subprocess.run(
            [
                sys.executable,
                str(script),
                "--db",
                str(db),
                "--apply",
                "--plan-file",
                str(plan_file),
                "--backup-db",
                str(backup),
                "--reviewed-plan-fingerprint",
                plan.fingerprint,
                "--expected-trade-id",
                LEGACY_IDS[0],
                "--expected-trade-id",
                LEGACY_IDS[1],
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    assert refused.returncode == 2
    assert "runtime lock is held" in refused.stderr
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_settlement_legacy_%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cutover_cli_rejects_a_backup_with_state_drift_without_writing(tmp_path):
    db = tmp_path / "paper.db"
    backup = tmp_path / "paper-before-cutover.db"
    plan_file = tmp_path / "reviewed-plan.json"
    _seed_cutover_db(db)
    _snapshot_db(db, backup)
    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)
    plan_file.write_text(plan.to_json(), encoding="utf-8")
    conn = sqlite3.connect(backup)
    try:
        conn.execute(
            "UPDATE paper_trades SET pnl_dollars=-0.51 WHERE trade_id=?",
            (LEGACY_IDS[0],),
        )
        conn.commit()
    finally:
        conn.close()

    script = PROJECT_ROOT / "scripts" / "establish_legacy_settlement_cutover.py"
    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--apply",
            "--plan-file",
            str(plan_file),
            "--backup-db",
            str(backup),
            "--reviewed-plan-fingerprint",
            plan.fingerprint,
            "--expected-trade-id",
            LEGACY_IDS[0],
            "--expected-trade-id",
            LEGACY_IDS[1],
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert refused.returncode == 2
    assert "backup state does not match" in refused.stderr
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_settlement_legacy_%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cutover_cli_rejects_backup_with_unselected_resolved_row_drift(tmp_path):
    db = tmp_path / "paper.db"
    backup = tmp_path / "paper-before-cutover.db"
    plan_file = tmp_path / "reviewed-plan.json"
    _seed_cutover_db(db)
    _insert_trade(db, "nonmapped-resolved", resolved=True, pnl_dollars=-0.11)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE paper_trades SET identity_status='unmapped' WHERE trade_id='nonmapped-resolved'"
        )
        conn.commit()
    finally:
        conn.close()
    _snapshot_db(db, backup)
    with open_readonly(db) as conn:
        plan = plan_legacy_settlement_cutover(conn, db)
    plan_file.write_text(plan.to_json(), encoding="utf-8")

    conn = sqlite3.connect(backup)
    try:
        conn.execute(
            "UPDATE paper_trades SET cost_dollars=0.41 WHERE trade_id='nonmapped-resolved'"
        )
        conn.commit()
    finally:
        conn.close()

    script = PROJECT_ROOT / "scripts" / "establish_legacy_settlement_cutover.py"
    refused = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--apply",
            "--plan-file",
            str(plan_file),
            "--backup-db",
            str(backup),
            "--reviewed-plan-fingerprint",
            plan.fingerprint,
            "--expected-trade-id",
            LEGACY_IDS[0],
            "--expected-trade-id",
            LEGACY_IDS[1],
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert refused.returncode == 2
    assert "backup state does not match" in refused.stderr
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_settlement_legacy_%'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cutover_plan_refuses_an_incompatible_bot_state_schema(tmp_path):
    db = tmp_path / "paper.db"
    _seed_cutover_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE bot_state (invalid_column TEXT)")
        conn.commit()
    finally:
        conn.close()

    with open_readonly(db) as conn, pytest.raises(sqlite3.OperationalError, match="key"):
        plan_legacy_settlement_cutover(conn, db)
