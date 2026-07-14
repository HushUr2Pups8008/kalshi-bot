from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.migrate_paper_settlement_schema import (
    _migration_action,
    _parse_args,
    apply_settlement_schema,
    main,
    open_readonly,
    plan_settlement_schema,
)
from trading.paper_trader import PaperTrader, _DDL
from trading.settlement_store import (
    SETTLEMENT_DDL_SHA256,
    SETTLEMENT_PAPER_TRADE_COLUMNS_SQL,
    SETTLEMENT_SCHEMA_VERSION,
    SettlementStore,
)


SETTLEMENT_TABLES = {
    "paper_settlement_schema_meta",
    "paper_settlement_observations",
    "paper_settlement_quarantine",
    "paper_settlement_outbox",
    "paper_settlement_outbox_requirements",
    "paper_settlement_consumer_receipts",
    "paper_settlement_delivery_claims",
}
SETTLEMENT_COLUMNS = {
    "terminal_state",
    "settlement_observation_sha256",
    "settled_at",
    "gross_payout_cents",
    "gross_pnl_cents",
}
OBSERVATION_SHA = "a" * 64
PAYLOAD_SHA = "b" * 64
OUTBOX_ID = "c" * 64
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _create_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            venue TEXT NOT NULL DEFAULT 'kalshi',
            resolved INTEGER NOT NULL DEFAULT 0,
            venue_market_id TEXT,
            identity_status TEXT,
            quarantine_reason TEXT,
            side TEXT,
            contracts INTEGER,
            price_cents INTEGER,
            cost_dollars REAL
        )
        """
    )
    conn.commit()
    conn.close()


def _insert_trade(
    path: Path,
    trade_id: str,
    *,
    resolved: int = 0,
    mapped: bool = True,
) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ticker, venue, resolved, venue_market_id,
            identity_status, side, contracts, price_cents, cost_dollars
        ) VALUES (?, ?, 'kalshi', ?, ?, ?, 'yes', 1, 40, 0.4)
        """,
        (
            trade_id,
            f"KX-{trade_id}",
            resolved,
            f"KX-{trade_id}" if mapped else None,
            "mapped" if mapped else None,
        ),
    )
    conn.commit()
    conn.close()


def _migrate(path: Path) -> None:
    with open_readonly(path) as conn:
        plan = plan_settlement_schema(conn, path)
    apply_settlement_schema(
        path,
        plan,
        reviewed_plan_fingerprint=plan.fingerprint,
    )


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
    finally:
        conn.close()


def _columns(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    finally:
        conn.close()


def _artifact_names(path: Path) -> set[str]:
    return {candidate.name for candidate in path.parent.iterdir()}


def _insert_observation(
    conn: sqlite3.Connection,
    *,
    observation_sha256: str = OBSERVATION_SHA,
    applied_trade_count: int = 1,
    bankroll_before: str = "1000",
    payout: str = "100",
    bankroll_after: str = "1100",
) -> None:
    conn.execute(
        """
        INSERT INTO paper_settlement_observations (
            observation_sha256, venue, venue_market_id, alias, outcome,
            authoritative_outcome_json, canonical_payload_json, payload_sha256,
            observed_at, effective_at, rules_version, source_id,
            refund_cents_per_contract, refunds_entry_fee,
            supersedes_observation_sha256, applied_trade_count,
            bankroll_before_cents, gross_payout_cents, bankroll_after_cents,
            applied_at
        ) VALUES (?, 'kalshi', 'KX-t1', 'KX-t1', 'yes',
                  '{"outcome":"yes"}', '{"settled":true}', ?,
                  ?, ?, 'v1', 'kalshi-api', NULL, NULL, NULL, ?, ?, ?, ?, ?)
        """,
        (
            observation_sha256,
            PAYLOAD_SHA,
            NOW.isoformat(),
            (NOW - timedelta(minutes=1)).isoformat(),
            applied_trade_count,
            bankroll_before,
            payout,
            bankroll_after,
            NOW.isoformat(),
        ),
    )


def _seed_settled_trade(
    conn: sqlite3.Connection,
    *,
    payout: str = "100",
    gross_pnl: str = "60",
    observation_sha256: str | None = OBSERVATION_SHA,
) -> None:
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ticker, venue, resolved, venue_market_id, identity_status,
            side, contracts, price_cents, cost_dollars, terminal_state,
            settlement_observation_sha256, settled_at, gross_payout_cents,
            gross_pnl_cents
        ) VALUES ('t1', 'KX-t1', 'kalshi', 1, 'KX-t1', 'mapped',
                  'yes', 1, 40, 0.4, 'won', ?, ?, ?, ?)
        """,
        (observation_sha256, NOW.isoformat(), payout, gross_pnl),
    )


def _seed_outbox(conn: sqlite3.Connection, consumers: tuple[str, ...]) -> None:
    conn.execute(
        """
        INSERT INTO paper_settlement_outbox (
            outbox_id, event_version, event_kind, observation_sha256,
            trade_id, payload_json, created_at
        ) VALUES (?, 1, 'trade_settled', ?, 't1', '{}', ?)
        """,
        (OUTBOX_ID, OBSERVATION_SHA, NOW.isoformat()),
    )
    conn.executemany(
        """
        INSERT INTO paper_settlement_outbox_requirements (outbox_id, consumer_name)
        VALUES (?, ?)
        """,
        [(OUTBOX_ID, consumer) for consumer in consumers],
    )


def _seed_valid_accounting(path: Path, *, consumers: tuple[str, ...] = ()) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    _insert_observation(conn)
    _seed_settled_trade(conn)
    if consumers:
        _seed_outbox(conn, consumers)
    conn.commit()
    conn.close()


def test_fresh_paper_trader_initializes_full_settlement_schema(tmp_path, monkeypatch):
    db = tmp_path / "fresh.db"
    monkeypatch.setattr("trading.paper_trader.SourceCredibility", MagicMock())

    trader = PaperTrader(db_path=db, startup_context="test")
    try:
        assert SETTLEMENT_COLUMNS <= trader._paper_trades_columns()
        assert SETTLEMENT_TABLES <= _tables(db)
        meta = trader._conn.execute(
            "SELECT schema_version, ddl_sha256, migration_plan_sha256 "
            "FROM paper_settlement_schema_meta"
        ).fetchone()
        assert tuple(meta)[:2] == (SETTLEMENT_SCHEMA_VERSION, SETTLEMENT_DDL_SHA256)
        assert len(meta["migration_plan_sha256"]) == 64
        assert trader._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            trader._conn.execute(
                "INSERT INTO paper_settlement_outbox "
                "VALUES (?, 1, 'orphan', ?, 'missing', '{}', ?)",
                (OUTBOX_ID, OBSERVATION_SHA, NOW.isoformat()),
            )
    finally:
        trader._conn.close()
    with SettlementStore(db) as store:
        assert store.readiness(pre_cutover=True).ok


def test_existing_paper_trader_startup_does_not_add_settlement_schema(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "old.db"
    old_ddl = _DDL.replace(SETTLEMENT_PAPER_TRADE_COLUMNS_SQL, "")
    conn = sqlite3.connect(db)
    for statement in old_ddl.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.commit()
    conn.close()
    monkeypatch.setattr("trading.paper_trader.SourceCredibility", MagicMock())

    trader = PaperTrader(db_path=db, startup_context="test")
    try:
        assert not (SETTLEMENT_COLUMNS & trader._paper_trades_columns())
        assert not (SETTLEMENT_TABLES & _tables(db))
    finally:
        trader._conn.close()


@pytest.mark.parametrize("marker", ["?", "#", "%"])
def test_migration_dry_run_and_apply_are_uri_safe(tmp_path, marker):
    db = tmp_path / f"paper{marker}settlement.db"
    _create_legacy_db(db)
    _insert_trade(db, "open")
    before_bytes = db.read_bytes()
    before_names = _artifact_names(db)

    with open_readonly(db) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        plan = plan_settlement_schema(conn, db)

    assert db.read_bytes() == before_bytes
    assert _artifact_names(db) == before_names
    apply_settlement_schema(db, plan, reviewed_plan_fingerprint=plan.fingerprint)
    assert SETTLEMENT_COLUMNS <= _columns(db)
    assert SETTLEMENT_TABLES <= _tables(db)
    assert _artifact_names(db) == before_names


@pytest.mark.parametrize("reviewed", [None, "wrong"])
def test_apply_rejects_unreviewed_plan_before_database_access(
    tmp_path,
    monkeypatch,
    reviewed,
):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    with open_readonly(db) as conn:
        plan = plan_settlement_schema(conn, db)
    connect = MagicMock(side_effect=AssertionError("must not open database"))
    monkeypatch.setattr("scripts.migrate_paper_settlement_schema.sqlite3.connect", connect)

    with pytest.raises(ValueError, match="reviewed plan fingerprint"):
        apply_settlement_schema(db, plan, reviewed_plan_fingerprint=reviewed)

    connect.assert_not_called()


@pytest.mark.parametrize(
    "stage",
    [
        "after_ddl:terminal_state",
        "after_ddl:paper_settlement_observations",
        "after_ddl:immutable_paper_settlement_consumer_receipts_delete",
        "after_meta",
    ],
)
def test_migration_rolls_back_all_ddl_on_fault(tmp_path, stage):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    with open_readonly(db) as conn:
        plan = plan_settlement_schema(conn, db)

    with pytest.raises(RuntimeError, match="injected"):
        apply_settlement_schema(
            db,
            plan,
            reviewed_plan_fingerprint=plan.fingerprint,
            fault_hook=lambda current: (_ for _ in ()).throw(RuntimeError("injected"))
            if current == stage
            else None,
        )

    assert not (SETTLEMENT_COLUMNS & _columns(db))
    assert not (SETTLEMENT_TABLES & _tables(db))


def test_migration_aborts_on_sqlite_schema_drift(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    with open_readonly(db) as conn:
        plan = plan_settlement_schema(conn, db)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE drift_marker (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="sqlite_schema drift"):
        apply_settlement_schema(db, plan, reviewed_plan_fingerprint=plan.fingerprint)

    assert not (SETTLEMENT_COLUMNS & _columns(db))


def test_migration_aborts_on_exact_open_row_drift(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _insert_trade(db, "planned")
    with open_readonly(db) as conn:
        plan = plan_settlement_schema(conn, db)
    _insert_trade(db, "inserted")

    with pytest.raises(RuntimeError, match="open-row drift"):
        apply_settlement_schema(db, plan, reviewed_plan_fingerprint=plan.fingerprint)

    assert not (SETTLEMENT_COLUMNS & _columns(db))


def test_plan_json_is_deterministic_and_binds_contract_inputs(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _insert_trade(db, "open")
    with open_readonly(db) as conn:
        plan = plan_settlement_schema(conn, db)
    payload = json.loads(plan.to_json())

    assert plan.to_json() == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert payload["resolved_db_uri"] == db.resolve().as_uri()
    assert payload["schema_version"] == SETTLEMENT_SCHEMA_VERSION
    assert payload["ddl_sha256"] == SETTLEMENT_DDL_SHA256
    assert len(payload["sqlite_schema_sha256"]) == 64
    assert len(payload["open_rows_sha256"]) == 64
    assert len(payload["fingerprint"]) == 64


def test_planner_rejects_connection_for_different_database(tmp_path):
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    _create_legacy_db(first)
    _create_legacy_db(second)

    with open_readonly(first) as conn:
        with pytest.raises(RuntimeError, match="database path mismatch"):
            plan_settlement_schema(conn, second)


def test_cli_apply_requires_and_binds_reviewed_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["migrate_paper_settlement_schema.py", "--apply"],
    )
    with pytest.raises(SystemExit):
        _parse_args()

    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    args = SimpleNamespace(
        db=db,
        apply=True,
        reviewed_plan_fingerprint="reviewed",
    )
    plan = MagicMock(fingerprint="reviewed")
    plan.to_json.return_value = "{}"
    planner = MagicMock(return_value=plan)
    applier = MagicMock()
    monkeypatch.setattr("scripts.migrate_paper_settlement_schema._parse_args", lambda: args)
    monkeypatch.setattr("scripts.migrate_paper_settlement_schema.plan_settlement_schema", planner)
    monkeypatch.setattr("scripts.migrate_paper_settlement_schema.apply_settlement_schema", applier)

    assert main() == 0
    resolved = db.resolve()
    assert planner.call_args.args[1] == resolved
    assert applier.call_args.args[:2] == (resolved, plan)
    assert applier.call_args.kwargs["reviewed_plan_fingerprint"] == "reviewed"


def test_settlement_store_connection_rejects_foreign_key_orphans(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)

    with SettlementStore(db) as store:
        assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            store.connection.execute(
                """
                INSERT INTO paper_settlement_outbox (
                    outbox_id, event_version, event_kind, observation_sha256,
                    trade_id, payload_json, created_at
                ) VALUES (?, 1, 'orphan', ?, 'missing', '{}', ?)
                """,
                (OUTBOX_ID, OBSERVATION_SHA, NOW.isoformat()),
            )


@pytest.mark.parametrize(
    ("table", "update_sql", "delete_sql"),
    [
        (
            "paper_settlement_observations",
            "UPDATE paper_settlement_observations SET source_id=source_id",
            "DELETE FROM paper_settlement_observations",
        ),
        (
            "paper_settlement_quarantine",
            "UPDATE paper_settlement_quarantine SET reason_code=reason_code",
            "DELETE FROM paper_settlement_quarantine",
        ),
        (
            "paper_settlement_outbox",
            "UPDATE paper_settlement_outbox SET event_kind=event_kind",
            "DELETE FROM paper_settlement_outbox",
        ),
        (
            "paper_settlement_outbox_requirements",
            "UPDATE paper_settlement_outbox_requirements SET consumer_name=consumer_name",
            "DELETE FROM paper_settlement_outbox_requirements",
        ),
        (
            "paper_settlement_consumer_receipts",
            "UPDATE paper_settlement_consumer_receipts SET processed_at=processed_at",
            "DELETE FROM paper_settlement_consumer_receipts",
        ),
    ],
)
def test_settlement_history_tables_are_append_only(
    tmp_path,
    table,
    update_sql,
    delete_sql,
):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        INSERT INTO paper_settlement_quarantine VALUES (
            ?, ?, ?, 'kalshi', 'KX-t1', 'KX-t1', 'test_reason', '{}', ?, ?
        )
        """,
        ("d" * 64, OBSERVATION_SHA, PAYLOAD_SHA, "e" * 64, NOW.isoformat()),
    )
    conn.execute(
        "INSERT INTO paper_settlement_consumer_receipts VALUES (?, ?, ?, ?)",
        ("consumer-a", OUTBOX_ID, NOW.isoformat(), "f" * 64),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(update_sql)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(delete_sql)
    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    conn.close()


def test_unclaimed_requirement_is_valid_pending_work(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))

    with SettlementStore(db) as store:
        pending = store.pending_requirements()
        assert [(row.outbox_id, row.consumer_name) for row in pending] == [
            (OUTBOX_ID, "consumer-a")
        ]
        assert not store.is_outbox_drained(OUTBOX_ID)
        assert store.conservation(now=NOW).ok


def test_claim_compare_and_set_respects_active_and_expired_leases(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))

    with SettlementStore(db) as store:
        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            now=NOW,
            lease_seconds=60,
        )
        assert store.claim_state("consumer-a", OUTBOX_ID, now=NOW) == "active"
        assert not store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-blocked",
            now=NOW + timedelta(seconds=30),
            lease_seconds=60,
        )
        assert (
            store.claim_state(
                "consumer-a",
                OUTBOX_ID,
                now=NOW + timedelta(seconds=61),
            )
            == "expired"
        )
        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-2",
            now=NOW + timedelta(seconds=61),
            lease_seconds=60,
        )
        claim = store.connection.execute(
            "SELECT claim_token, attempt_count FROM paper_settlement_delivery_claims"
        ).fetchone()
        assert tuple(claim) == ("token-2", 2)


def test_receipts_drain_multi_consumer_event(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a", "consumer-b"))

    with SettlementStore(db) as store:
        for index, consumer in enumerate(("consumer-a", "consumer-b"), start=1):
            token = f"token-{index}"
            assert store.acquire_claim(
                consumer,
                OUTBOX_ID,
                claim_token=token,
                now=NOW,
                lease_seconds=60,
            )
            assert store.record_receipt(
                consumer,
                OUTBOX_ID,
                claim_token=token,
                processed_at=NOW,
                result_sha256=str(index) * 64,
            )
            assert store.is_outbox_drained(OUTBOX_ID) is (index == 2)

        assert store.pending_requirements() == ()


def test_complete_claim_commits_consumer_effect_and_receipt_together(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE consumer_projection (outbox_id TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            now=NOW,
            lease_seconds=60,
        )

        def apply_effect(
            conn: sqlite3.Connection,
            requirement,
        ) -> None:
            assert requirement.outbox_id == OUTBOX_ID
            assert requirement.consumer_name == "consumer-a"
            conn.execute(
                "INSERT INTO consumer_projection (outbox_id, value) VALUES (?, ?)",
                (requirement.outbox_id, "applied"),
            )

        assert store.complete_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            processed_at=NOW + timedelta(seconds=1),
            result_sha256="a" * 64,
            apply=apply_effect,
        )
        assert tuple(
            store.connection.execute(
                "SELECT outbox_id, value FROM consumer_projection"
            ).fetchone()
        ) == (OUTBOX_ID, "applied")
        assert tuple(
            store.connection.execute(
                "SELECT consumer_name, outbox_id, result_sha256 "
                "FROM paper_settlement_consumer_receipts"
            ).fetchone()
        ) == ("consumer-a", OUTBOX_ID, "a" * 64)
        assert store.claim_state("consumer-a", OUTBOX_ID, now=NOW) is None


def test_complete_claim_rolls_back_effect_and_receipt_on_callback_failure(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE consumer_projection (outbox_id TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            now=NOW,
            lease_seconds=60,
        )

        def fail_after_effect(
            conn: sqlite3.Connection,
            requirement,
        ) -> None:
            conn.execute(
                "INSERT INTO consumer_projection (outbox_id, value) VALUES (?, ?)",
                (requirement.outbox_id, "must-roll-back"),
            )
            raise RuntimeError("injected consumer failure")

        with pytest.raises(RuntimeError, match="injected consumer failure"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="token-1",
                processed_at=NOW + timedelta(seconds=1),
                result_sha256="b" * 64,
                apply=fail_after_effect,
            )

        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM consumer_projection"
            ).fetchone()[0]
            == 0
        )
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM paper_settlement_consumer_receipts"
            ).fetchone()[0]
            == 0
        )
        assert store.claim_state("consumer-a", OUTBOX_ID, now=NOW) == "active"


def test_complete_claim_rolls_back_effect_when_receipt_insert_fails(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE consumer_projection (outbox_id TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute(
        """
        CREATE TRIGGER inject_receipt_failure
        BEFORE INSERT ON paper_settlement_consumer_receipts
        BEGIN
            SELECT RAISE(ABORT, 'injected receipt failure');
        END
        """
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            now=NOW,
            lease_seconds=60,
        )

        def apply_effect(conn: sqlite3.Connection, requirement) -> None:
            conn.execute(
                "INSERT INTO consumer_projection (outbox_id, value) VALUES (?, ?)",
                (requirement.outbox_id, "must-roll-back"),
            )

        with pytest.raises(sqlite3.IntegrityError, match="injected receipt failure"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="token-1",
                processed_at=NOW + timedelta(seconds=1),
                result_sha256="c" * 64,
                apply=apply_effect,
            )

        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM consumer_projection"
            ).fetchone()[0]
            == 0
        )
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM paper_settlement_consumer_receipts"
            ).fetchone()[0]
            == 0
        )
        assert store.claim_state("consumer-a", OUTBOX_ID, now=NOW) == "active"


def test_complete_claim_validates_ownership_and_retry_before_callback(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))
    callback_calls: list[str] = []

    def apply_effect(_conn: sqlite3.Connection, requirement) -> None:
        callback_calls.append(requirement.outbox_id)

    with SettlementStore(db) as store:
        with pytest.raises(RuntimeError, match="active claim token"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="missing",
                processed_at=NOW,
                result_sha256="d" * 64,
                apply=apply_effect,
            )
        assert callback_calls == []

        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            now=NOW,
            lease_seconds=60,
        )
        with pytest.raises(RuntimeError, match="active claim token"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="wrong-token",
                processed_at=NOW + timedelta(seconds=1),
                result_sha256="d" * 64,
                apply=apply_effect,
            )
        with pytest.raises(RuntimeError, match="claim lease expired"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="token-1",
                processed_at=NOW + timedelta(seconds=60),
                result_sha256="d" * 64,
                apply=apply_effect,
            )
        assert callback_calls == []

        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-2",
            now=NOW + timedelta(seconds=61),
            lease_seconds=60,
        )
        assert store.complete_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-2",
            processed_at=NOW + timedelta(seconds=62),
            result_sha256="d" * 64,
            apply=apply_effect,
        )
        assert callback_calls == [OUTBOX_ID]

        assert not store.complete_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="no-longer-relevant",
            processed_at=NOW + timedelta(seconds=63),
            result_sha256="d" * 64,
            apply=apply_effect,
        )
        with pytest.raises(RuntimeError, match="receipt result drift"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="no-longer-relevant",
                processed_at=NOW + timedelta(seconds=63),
                result_sha256="e" * 64,
                apply=apply_effect,
            )
        assert callback_calls == [OUTBOX_ID]


def test_complete_claim_rejects_awaitable_callback_before_receipt(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))
    callback_entered = False

    async def apply_effect(_conn: sqlite3.Connection, _requirement) -> None:
        nonlocal callback_entered
        callback_entered = True

    with SettlementStore(db) as store:
        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            now=NOW,
            lease_seconds=60,
        )
        with pytest.raises(TypeError, match="callback must be synchronous"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="token-1",
                processed_at=NOW + timedelta(seconds=1),
                result_sha256="f" * 64,
                apply=apply_effect,
            )
        assert not callback_entered
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM paper_settlement_consumer_receipts"
            ).fetchone()[0]
            == 0
        )
        assert store.claim_state("consumer-a", OUTBOX_ID, now=NOW) == "active"


@pytest.mark.parametrize(
    "transaction_action",
    ("commit", "rollback", "sql_commit", "savepoint"),
)
def test_complete_claim_blocks_callback_transaction_control(
    tmp_path,
    transaction_action,
):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a",))
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE consumer_projection (outbox_id TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            now=NOW,
            lease_seconds=60,
        )

        def apply_effect(conn, requirement) -> None:
            conn.execute(
                "INSERT INTO consumer_projection (outbox_id, value) VALUES (?, ?)",
                (requirement.outbox_id, "must-roll-back"),
            )
            if transaction_action == "commit":
                conn.commit()
            elif transaction_action == "rollback":
                conn.rollback()
            elif transaction_action == "sql_commit":
                conn.execute("COMMIT")
            else:
                conn.execute("SAVEPOINT callback")

        with pytest.raises(RuntimeError, match="callback transaction control"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="token-1",
                processed_at=NOW + timedelta(seconds=1),
                result_sha256="f" * 64,
                apply=apply_effect,
            )
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM consumer_projection"
            ).fetchone()[0]
            == 0
        )
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM paper_settlement_consumer_receipts"
            ).fetchone()[0]
            == 0
        )
        assert store.claim_state("consumer-a", OUTBOX_ID, now=NOW) == "active"


def test_readiness_reports_valid_and_invalid_schema_state(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    with SettlementStore(db) as store:
        assert store.readiness(pre_cutover=True).ok

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO paper_trades (trade_id, ticker, venue, resolved) "
        "VALUES ('unmapped', 'KX-unmapped', 'kalshi', 0)"
    )
    conn.commit()
    conn.close()
    with SettlementStore(db) as store:
        result = store.readiness(pre_cutover=True)
        assert not result.ok
        assert "open_rows_mapped" in result.failures


def test_readiness_rejects_missing_schema_object(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER immutable_paper_settlement_outbox_update")
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.readiness(pre_cutover=True)
        assert not result.ok
        assert "schema_objects" in result.failures


def _replace_schema_sql(
    conn: sqlite3.Connection,
    *,
    object_type: str,
    name: str,
    old: str,
    new: str,
) -> None:
    sql = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type=? AND name=?",
        (object_type, name),
    ).fetchone()[0]
    assert old in sql
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(
        "UPDATE sqlite_schema SET sql=? WHERE type=? AND name=?",
        (sql.replace(old, new, 1), object_type, name),
    )
    conn.execute("PRAGMA schema_version = 999")
    conn.execute("PRAGMA writable_schema=OFF")


def _tamper_settlement_contract(conn: sqlite3.Connection, tamper: str) -> None:
    if tamper == "table":
        meta = conn.execute(
            "SELECT * FROM paper_settlement_schema_meta"
        ).fetchone()
        conn.execute("DROP TABLE paper_settlement_schema_meta")
        conn.execute(
            """
            CREATE TABLE paper_settlement_schema_meta (
                schema_version INTEGER PRIMARY KEY,
                ddl_sha256 TEXT NOT NULL UNIQUE,
                migration_plan_sha256 TEXT NOT NULL,
                applied_at BLOB NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO paper_settlement_schema_meta VALUES (?, ?, ?, ?)",
            tuple(meta),
        )
    elif tamper == "index":
        conn.execute("DROP INDEX paper_trades_settlement_observation_idx")
        conn.execute(
            "CREATE INDEX paper_trades_settlement_observation_idx "
            "ON paper_trades(gross_payout_cents)"
        )
    elif tamper == "trigger":
        conn.execute("DROP TRIGGER immutable_paper_settlement_outbox_update")
        conn.execute(
            """
            CREATE TRIGGER immutable_paper_settlement_outbox_update
            BEFORE UPDATE ON paper_settlement_outbox
            BEGIN
                SELECT 1;
            END
            """
        )
    else:  # pragma: no cover - parametrization owns the closed set
        raise AssertionError(tamper)


@pytest.mark.parametrize("tamper", ["table", "index", "trigger"])
def test_exact_schema_contract_rejects_altered_same_name_objects(tmp_path, tamper):
    db = tmp_path / f"same-name-{tamper}.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    _tamper_settlement_contract(conn, tamper)
    conn.commit()
    with pytest.raises(RuntimeError, match="does not match target contract"):
        _migration_action(conn)
    conn.close()

    with SettlementStore(db) as store:
        result = store.readiness(pre_cutover=True)
        assert not result.ok
        assert "schema_objects" in result.failures
    with open_readonly(db) as conn:
        with pytest.raises(RuntimeError, match="does not match target contract"):
            plan_settlement_schema(conn, db)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("terminal_state TEXT CHECK", "terminal_state BLOB CHECK"),
        (
            "terminal_state IN ('won','lost','void')",
            "terminal_state IN ('won','lost')",
        ),
        (
            "REFERENCES paper_settlement_observations(observation_sha256)",
            "REFERENCES paper_settlement_observations(payload_sha256)",
        ),
    ],
    ids=["column-type", "terminal-check", "observation-fk"],
)
def test_exact_schema_contract_validates_added_paper_trade_columns(
    tmp_path,
    old,
    new,
):
    db = tmp_path / "paper-column-contract.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    _replace_schema_sql(
        conn,
        object_type="table",
        name="paper_trades",
        old=old,
        new=new,
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.readiness(pre_cutover=True)
        assert not result.ok
        assert "schema_objects" in result.failures
    with open_readonly(db) as conn:
        with pytest.raises(RuntimeError, match="does not match target contract"):
            plan_settlement_schema(conn, db)


def test_exact_schema_contract_rejects_terminal_state_nocase_collation(tmp_path):
    db = tmp_path / "paper-column-collation.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    _replace_schema_sql(
        conn,
        object_type="table",
        name="paper_trades",
        old="terminal_state TEXT CHECK",
        new="terminal_state TEXT COLLATE NOCASE CHECK",
    )
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ticker, venue, resolved, venue_market_id, identity_status,
            side, contracts, price_cents, cost_dollars, terminal_state
        ) VALUES ('nocase', 'KX-NOCASE', 'kalshi', 0, 'KX-NOCASE', 'mapped',
                  'yes', 1, 40, 0.4, 'WON')
        """
    )
    conn.commit()
    assert conn.execute(
        "SELECT terminal_state FROM paper_trades WHERE trade_id='nocase'"
    ).fetchone()[0] == "WON"
    with pytest.raises(RuntimeError, match="does not match target contract"):
        _migration_action(conn)
    conn.close()

    with SettlementStore(db) as store:
        result = store.readiness(pre_cutover=True)
        assert not result.ok
        assert "schema_objects" in result.failures
    with open_readonly(db) as conn:
        with pytest.raises(RuntimeError, match="does not match target contract"):
            plan_settlement_schema(conn, db)


@pytest.mark.parametrize(
    "invalid",
    [
        "trade_count",
        "trade_payout",
        "gross_pnl",
        "bankroll",
        "resolved_link",
        "claim_lease",
    ],
)
def test_conservation_reports_accounting_and_lease_violations(tmp_path, invalid):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    _insert_observation(
        conn,
        applied_trade_count=2 if invalid == "trade_count" else 1,
        bankroll_after="1099" if invalid == "bankroll" else "1100",
    )
    _seed_settled_trade(
        conn,
        payout="99" if invalid == "trade_payout" else "100",
        gross_pnl="060" if invalid == "gross_pnl" else "60",
        observation_sha256=None if invalid == "resolved_link" else OBSERVATION_SHA,
    )
    if invalid == "claim_lease":
        _seed_outbox(conn, ("consumer-a",))
        conn.execute(
            "INSERT INTO paper_settlement_delivery_claims VALUES (?, ?, ?, ?, 1, ?)",
            ("consumer-a", OUTBOX_ID, "token", "not-a-time", NOW.isoformat()),
        )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert result.failures


def test_conservation_accepts_exact_valid_accounting(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert result.ok
        assert result.failures == ()
