from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
from trading.settlement import (
    MarketOutcome,
    SettlementObservation,
    VoidRefundContract,
    build_settlement_observation,
)
from trading.settlement_store import (
    SETTLEMENT_DDL_SHA256,
    SETTLEMENT_EVENT_VERSION,
    SETTLEMENT_PAPER_TRADE_COLUMNS_SQL,
    SETTLEMENT_SCHEMA_VERSION,
    SettlementStore,
    paper_trade_settled_outbox_contract,
    settlement_keyword_directions,
    settlement_result_sha256,
)
from trading.venue import MarketRef, Venue


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


def test_fee_accounting_schema_does_not_change_gross_settlement_v1_contract():
    assert SETTLEMENT_SCHEMA_VERSION == 1
    assert SETTLEMENT_EVENT_VERSION == 1
    assert SETTLEMENT_DDL_SHA256 == (
        "312ffc84d37e4f1e4fb235c6bd124da5bc7b3b7f844623272aea91fa0fd4eb9e"
    )
OUTBOX_ID = "c" * 64
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
TEST_MARKET_REF = MarketRef(Venue.KALSHI, "KX-t1", "KX-t1")


def _build_test_observation(
    *,
    outcome: str = "yes",
    market_ref: MarketRef = TEST_MARKET_REF,
    observed_at: datetime = NOW,
    effective_at: datetime = NOW - timedelta(minutes=1),
    refund_cents_per_contract: str | None = None,
    refunds_entry_fee: int | None = None,
    authoritative_outcome: object | None = None,
    authoritative_payload: object | None = None,
    previous_observation: SettlementObservation | None = None,
    supersedes_observation_sha256: str | None = None,
) -> SettlementObservation:
    void_refund = None
    if outcome == "void":
        if refund_cents_per_contract is None or refunds_entry_fee not in {0, 1}:
            raise ValueError("void test observation requires an explicit refund contract")
        void_refund = VoidRefundContract(
            refund_cents_per_contract=Decimal(refund_cents_per_contract),
            refunds_entry_fee=bool(refunds_entry_fee),
        )
    return build_settlement_observation(
        market_ref=market_ref,
        outcome=MarketOutcome(outcome),
        authoritative_outcome=(
            {"outcome": outcome}
            if authoritative_outcome is None
            else authoritative_outcome
        ),
        authoritative_payload=(
            {"settled": True}
            if authoritative_payload is None
            else authoritative_payload
        ),
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version="v1",
        source_id="kalshi-api",
        void_refund=void_refund,
        previous_observation=previous_observation,
        supersedes_observation_sha256=supersedes_observation_sha256,
    )


BASE_OBSERVATION = _build_test_observation()
OBSERVATION_SHA = BASE_OBSERVATION.observation_sha256
PAYLOAD_SHA = BASE_OBSERVATION.payload_sha256


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


def test_settlement_v1_conservation_does_not_require_fee_accounting_column(
    tmp_path,
):
    db = tmp_path / "legacy-receipt-v1.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
        assert "fee_net_accounting_version" not in columns

    with SettlementStore(db) as store:
        check = store.conservation(now=NOW)

    assert check.ok is True


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
    outcome: str = "yes",
    applied_trade_count: int = 1,
    bankroll_before: str = "1000",
    payout: str = "100",
    bankroll_after: str = "1100",
    refund_cents_per_contract: str | None = None,
    refunds_entry_fee: int | None = None,
    market_ref: MarketRef = TEST_MARKET_REF,
    observed_at: datetime = NOW,
    effective_at: datetime = NOW - timedelta(minutes=1),
    applied_at: datetime = NOW,
    authoritative_outcome: object | None = None,
    authoritative_payload: object | None = None,
    previous_observation: SettlementObservation | None = None,
    supersedes_observation_sha256: str | None = None,
) -> SettlementObservation:
    observation = _build_test_observation(
        outcome=outcome,
        market_ref=market_ref,
        observed_at=observed_at,
        effective_at=effective_at,
        refund_cents_per_contract=refund_cents_per_contract,
        refunds_entry_fee=refunds_entry_fee,
        authoritative_outcome=authoritative_outcome,
        authoritative_payload=authoritative_payload,
        previous_observation=previous_observation,
        supersedes_observation_sha256=supersedes_observation_sha256,
    )
    void_refund = observation.void_refund
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation.observation_sha256,
            observation.market_ref.venue.value,
            observation.market_ref.venue_market_id,
            observation.market_ref.alias,
            observation.outcome.value,
            observation.authoritative_outcome_json,
            observation.canonical_payload_json,
            observation.payload_sha256,
            observation.observed_at.isoformat(),
            observation.effective_at.isoformat(),
            observation.rules_version,
            observation.source_id,
            (
                format(void_refund.refund_cents_per_contract.normalize(), "f")
                if void_refund is not None
                else None
            ),
            int(void_refund.refunds_entry_fee) if void_refund is not None else None,
            observation.supersedes_observation_sha256,
            applied_trade_count,
            bankroll_before,
            payout,
            bankroll_after,
            applied_at.isoformat(),
        ),
    )
    return observation


def _mutate_observation(
    conn: sqlite3.Connection,
    mutation: str,
    parameters: tuple[object, ...] = (),
) -> None:
    _mutate_append_only_table(
        conn,
        "paper_settlement_observations",
        "update",
        mutation,
        parameters,
    )


def _mutate_append_only_table(
    conn: sqlite3.Connection,
    table: str,
    operation: str,
    mutation: str,
    parameters: tuple[object, ...] = (),
) -> None:
    assert operation in {"update", "delete"}
    trigger_name = f"immutable_{table}_{operation}"
    row = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?",
        (trigger_name,),
    ).fetchone()
    assert row is not None and row[0]
    conn.execute(f"DROP TRIGGER {trigger_name}")
    try:
        conn.execute(mutation, parameters)
    finally:
        conn.execute(str(row[0]))


def _seed_settled_trade(
    conn: sqlite3.Connection,
    *,
    trade_id: str = "t1",
    market_ref: MarketRef = TEST_MARKET_REF,
    payout: str = "100",
    gross_pnl: str = "60",
    observation_sha256: str | None = OBSERVATION_SHA,
    side: str = "yes",
    terminal_state: str = "won",
    resolved_yes: int | None = 1,
    contracts: int | float = 1,
    price_cents: int | float = 40,
    cost_dollars: float = 0.4,
    pnl_dollars: float | None = 0.6,
    settled_at: str = NOW.isoformat(),
    resolved_ts: str = NOW.isoformat(),
) -> None:
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ticker, venue, resolved, venue_market_id, identity_status,
            side, contracts, price_cents, cost_dollars, terminal_state,
            settlement_observation_sha256, settled_at, gross_payout_cents,
            gross_pnl_cents, resolved_yes, pnl_dollars, resolved_ts,
            ts, estimated_prob, entry_price_cents, signal_source,
            keywords_matched, series_ticker, llm_magnitude, llm_confidence,
            fast_lane_p, accumulation_p, structural_p
        ) VALUES (?, ?, ?, 1, ?, 'mapped',
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, 0.7, 40.0, 'wire:test', '["ceasefire"]', 'KXTEST',
                  'moderate', 0.8, 0.7, 0.65, 0.6)
        """,
        (
            trade_id,
            market_ref.alias,
            market_ref.venue.value,
            market_ref.venue_market_id,
            side,
            contracts,
            price_cents,
            cost_dollars,
            terminal_state,
            observation_sha256,
            settled_at,
            payout,
            gross_pnl,
            resolved_yes,
            pnl_dollars,
            resolved_ts,
            (NOW - timedelta(hours=1)).isoformat(),
        ),
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


def _seed_canonical_outbox(
    conn: sqlite3.Connection,
    observation: SettlementObservation,
    *,
    trade_id: str = "t1",
    keyword_directions: dict[str, str] | None = None,
) -> str:
    cursor = conn.execute(
        """
        SELECT trade_id, ticker, side, resolved_yes, terminal_state,
               gross_payout_cents, gross_pnl_cents, ts AS entry_ts,
               signal_source, series_ticker, estimated_prob, entry_price_cents,
               cost_dollars, llm_magnitude, llm_confidence, keywords_matched,
               fast_lane_p, accumulation_p, structural_p
        FROM paper_trades WHERE trade_id=?
        """,
        (trade_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    trade = {column[0]: value for column, value in zip(cursor.description, row)}
    trade["won"] = (
        None if observation.outcome is MarketOutcome.VOID else trade["terminal_state"] == "won"
    )
    applied_at = conn.execute(
        "SELECT applied_at FROM paper_settlement_observations "
        "WHERE observation_sha256=?",
        (observation.observation_sha256,),
    ).fetchone()
    assert applied_at is not None
    contract = paper_trade_settled_outbox_contract(
        observation,
        trade,
        created_at=str(applied_at[0]),
        keyword_directions=(
            settlement_keyword_directions()
            if keyword_directions is None
            else keyword_directions
        ),
    )
    conn.execute(
        "INSERT INTO paper_settlement_outbox VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            contract.outbox_id,
            contract.event_version,
            contract.event_kind,
            contract.observation_sha256,
            contract.trade_id,
            contract.payload_json,
            contract.created_at,
        ),
    )
    conn.executemany(
        "INSERT INTO paper_settlement_outbox_requirements VALUES (?, ?)",
        [(contract.outbox_id, consumer) for consumer in contract.requirements],
    )
    return contract.outbox_id


def _seed_valid_accounting(path: Path, *, consumers: tuple[str, ...] = ()) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    observation = _insert_observation(conn)
    _seed_settled_trade(conn)
    if consumers:
        _seed_outbox(conn, consumers)
    else:
        _seed_canonical_outbox(conn, observation)
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
    _seed_valid_accounting(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    outbox_id = conn.execute(
        "SELECT outbox_id FROM paper_settlement_outbox"
    ).fetchone()[0]
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
        (
            "paper_trade_log",
            outbox_id,
            NOW.isoformat(),
            settlement_result_sha256(outbox_id, "paper_trade_log"),
        ),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(update_sql)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(delete_sql)
    expected_rows = 4 if table == "paper_settlement_outbox_requirements" else 1
    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == expected_rows
    conn.close()


def test_unclaimed_requirement_is_valid_pending_work(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    with SettlementStore(db) as store:
        pending = store.pending_requirements()
        assert {row.consumer_name for row in pending} == {
            "paper_trade_log",
            "source_credibility",
            "calibration_state",
            "keyword_outcomes",
        }
        outbox_id = pending[0].outbox_id
        assert not store.is_outbox_drained(outbox_id)
        assert store.conservation(now=NOW).ok


def test_canonical_delivery_completion_requires_conservation_and_a_drained_outbox(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    with SettlementStore(db, read_only=True) as store:
        assert store.conservation(now=NOW).ok
        assert store.canonical_delivery_complete_trade_ids(now=NOW) == ()
        assert store.canonical_delivery_complete_outbox_payloads(now=NOW) == ()

    with SettlementStore(db) as store:
        pending = store.pending_requirements()
    expected_outbox_id = pending[0].outbox_id
    conn = sqlite3.connect(db)
    try:
        conn.executemany(
            """
            INSERT INTO paper_settlement_consumer_receipts (
                consumer_name, outbox_id, processed_at, result_sha256
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    requirement.consumer_name,
                    requirement.outbox_id,
                    NOW.isoformat(),
                    settlement_result_sha256(
                        requirement.outbox_id,
                        requirement.consumer_name,
                    ),
                )
                for requirement in pending
            ],
        )
        conn.commit()
    finally:
        conn.close()

    with SettlementStore(db, read_only=True) as store:
        assert store.canonical_delivery_complete_trade_ids(now=NOW) == ("t1",)
        payloads = store.canonical_delivery_complete_outbox_payloads(now=NOW)
        assert len(payloads) == 1
        assert payloads[0].outbox_id == expected_outbox_id
        assert payloads[0].trade_id == "t1"
        assert '"trade_id":"t1"' in payloads[0].payload_json


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


def test_direct_receipt_recording_is_disabled(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db, consumers=("consumer-a", "consumer-b"))

    with SettlementStore(db) as store:
        assert store.acquire_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="token-1",
            now=NOW,
            lease_seconds=60,
        )
        with pytest.raises(RuntimeError, match="Direct receipt recording is disabled"):
            store.record_receipt(
                "consumer-a",
                OUTBOX_ID,
                claim_token="token-1",
                processed_at=NOW,
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
            )

        assert not store.is_outbox_drained(OUTBOX_ID)


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
            result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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
        ) == (
            "consumer-a",
            OUTBOX_ID,
            settlement_result_sha256(OUTBOX_ID, "consumer-a"),
        )
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
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
                apply=apply_effect,
            )
        with pytest.raises(RuntimeError, match="claim lease expired"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="token-1",
                processed_at=NOW + timedelta(seconds=60),
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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
            result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
            apply=apply_effect,
        )
        assert callback_calls == [OUTBOX_ID]

        assert not store.complete_claim(
            "consumer-a",
            OUTBOX_ID,
            claim_token="no-longer-relevant",
            processed_at=NOW + timedelta(seconds=63),
            result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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


def test_complete_claim_public_connection_cannot_escape_callback_guard(tmp_path):
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
        public_connection = store.connection
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
            public_connection.set_authorizer(None)
            public_connection.commit()

        with pytest.raises(RuntimeError, match="callback transaction control"):
            store.complete_claim(
                "consumer-a",
                OUTBOX_ID,
                claim_token="token-1",
                processed_at=NOW + timedelta(seconds=1),
                result_sha256=settlement_result_sha256(OUTBOX_ID, "consumer-a"),
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

    conn = sqlite3.connect(db)
    requirements = conn.execute(
        "SELECT outbox_id, consumer_name "
        "FROM paper_settlement_outbox_requirements"
    ).fetchall()
    conn.executemany(
        "INSERT INTO paper_settlement_consumer_receipts VALUES (?, ?, ?, ?)",
        [
            (
                consumer_name,
                outbox_id,
                NOW.isoformat(),
                settlement_result_sha256(outbox_id, consumer_name),
            )
            for outbox_id, consumer_name in requirements
        ],
    )
    conn.commit()
    conn.close()

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


def test_preflight_rejects_foreign_key_violations_in_migrated_database(tmp_path):
    db = tmp_path / "orphaned-settlement.db"
    _create_legacy_db(db)
    _migrate(db)

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT INTO paper_settlement_outbox (
            outbox_id, event_version, event_kind, observation_sha256,
            trade_id, payload_json, created_at
        ) VALUES (?, 1, 'paper_trade_settled', ?, 'missing-trade', '{}', ?)
        """,
        (OUTBOX_ID, "d" * 64, NOW.isoformat()),
    )
    conn.commit()
    assert len(conn.execute("PRAGMA foreign_key_check").fetchall()) == 2
    conn.close()

    with SettlementStore(db) as store:
        readiness = store.readiness(pre_cutover=True)
        assert not readiness.ok
        assert "foreign_keys" in readiness.failures
        assert readiness.metrics["foreign_key_violations"] == 2

        conservation = store.conservation(now=NOW)
        assert not conservation.ok
        assert "foreign_keys" in conservation.failures
        assert conservation.metrics["foreign_key_violations"] == 2


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
    observation = _insert_observation(
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


@pytest.mark.parametrize(
    ("mutation", "expected_failure", "metric"),
    [
        (
            "UPDATE paper_trades SET gross_pnl_cents='999999999999999999' "
            "WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET pnl_dollars=999999999 WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET pnl_dollars=NULL WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET contracts=0 WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET contracts=1.5 WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET price_cents=0 WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET price_cents=100 WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET price_cents=40.5 WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET cost_dollars=0.5, gross_pnl_cents='50', "
            "pnl_dollars=0.5 WHERE trade_id='t1'",
            f"trade_financials:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_financials",
        ),
        (
            "UPDATE paper_trades SET venue_market_id='KX-WRONG' "
            "WHERE trade_id='t1'",
            f"trade_identity:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_identity",
        ),
        (
            "UPDATE paper_trades SET terminal_state='lost', resolved_yes=0 "
            "WHERE trade_id='t1'",
            f"trade_outcome:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_outcomes",
        ),
    ],
    ids=[
        "gross-pnl",
        "legacy-pnl",
        "null-legacy-pnl",
        "zero-contracts",
        "fractional-contracts",
        "zero-price",
        "hundred-price",
        "fractional-price",
        "entry-cost",
        "venue-market-id",
        "outcome",
    ],
)
def test_conservation_rejects_linked_trade_mutations(
    tmp_path,
    mutation,
    expected_failure,
    metric,
):
    db = tmp_path / "mutated-linked-trade.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    conn = sqlite3.connect(db)
    conn.execute(mutation)
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert expected_failure in result.failures
        assert result.metrics[metric] == 1


def test_conservation_rejects_internally_balanced_fabricated_yes_payout(tmp_path):
    db = tmp_path / "fabricated-yes-payout.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    _insert_observation(conn, payout="90", bankroll_after="1090")
    _seed_settled_trade(
        conn,
        payout="90",
        gross_pnl="50",
        pnl_dollars=0.5,
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert f"trade_financials:{OBSERVATION_SHA}:t1" in result.failures
        assert result.metrics["invalid_linked_trade_financials"] == 1


def test_conservation_rejects_internally_balanced_fabricated_void_refund(tmp_path):
    db = tmp_path / "fabricated-void-refund.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    observation = _insert_observation(
        conn,
        outcome="void",
        payout="40",
        bankroll_after="1040",
        refund_cents_per_contract="50",
        refunds_entry_fee=0,
    )
    _seed_settled_trade(
        conn,
        payout="40",
        gross_pnl="0",
        terminal_state="void",
        resolved_yes=None,
        pnl_dollars=0.0,
        observation_sha256=observation.observation_sha256,
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert (
            f"trade_financials:{observation.observation_sha256}:t1"
            in result.failures
        )
        assert result.metrics["invalid_linked_trade_financials"] == 1


@pytest.mark.parametrize(
    (
        "outcome",
        "refund_cents_per_contract",
        "refunds_entry_fee",
        "payout",
        "gross_pnl",
        "pnl_dollars",
        "bankroll_after",
        "terminal_state",
        "resolved_yes",
    ),
    [
        ("void", "50", None, "50", "10", 0.1, "1050", "void", None),
        ("yes", None, 0, "100", "60", 0.6, "1100", "won", 1),
    ],
    ids=["void-null-fee-flag", "directional-stray-fee-flag"],
)
def test_conservation_rejects_incoherent_observation_refund_fields(
    tmp_path,
    outcome,
    refund_cents_per_contract,
    refunds_entry_fee,
    payout,
    gross_pnl,
    pnl_dollars,
    bankroll_after,
    terminal_state,
    resolved_yes,
):
    db = tmp_path / "incoherent-observation-refund.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    canonical_fee = 0 if outcome == "void" else None
    observation = _insert_observation(
        conn,
        outcome=outcome,
        payout=payout,
        bankroll_after=bankroll_after,
        refund_cents_per_contract=refund_cents_per_contract,
        refunds_entry_fee=canonical_fee,
    )
    if refunds_entry_fee != canonical_fee:
        _mutate_observation(
            conn,
            "UPDATE paper_settlement_observations SET refunds_entry_fee=? "
            "WHERE observation_sha256=?",
            (refunds_entry_fee, observation.observation_sha256),
        )
    _seed_settled_trade(
        conn,
        payout=payout,
        gross_pnl=gross_pnl,
        terminal_state=terminal_state,
        resolved_yes=resolved_yes,
        pnl_dollars=pnl_dollars,
        observation_sha256=observation.observation_sha256,
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert (
            f"trade_financials:{observation.observation_sha256}:t1"
            in result.failures
        )
        assert result.metrics["invalid_linked_trade_financials"] == 1


def test_conservation_accepts_observation_alias_drift(tmp_path):
    db = tmp_path / "linked-trade-alias-drift.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    observation = _insert_observation(
        conn,
        market_ref=MarketRef(Venue.KALSHI, "KX-t1", "newer-alias"),
    )
    _seed_settled_trade(
        conn,
        market_ref=MarketRef(Venue.KALSHI, "KX-t1", "older-alias"),
        observation_sha256=observation.observation_sha256,
    )
    _seed_canonical_outbox(conn, observation)
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert result.ok
        assert result.metrics["invalid_linked_trade_identity"] == 0
        assert result.metrics["linked_trade_alias_drifts"] == 1


def test_conservation_uses_persisted_keyword_direction_snapshot(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "historical-keyword-directions.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    observation = _insert_observation(conn)
    _seed_settled_trade(conn)
    _seed_canonical_outbox(
        conn,
        observation,
        keyword_directions={"ceasefire": "yes"},
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        "config.GEOPOLITICAL_SIGNALS",
        [{"keywords": ["ceasefire"], "direction": "no"}],
    )

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert result.ok
        assert result.metrics["invalid_settlement_outboxes"] == 0


@pytest.mark.parametrize(
    ("invalid", "expected_failure", "metric"),
    [
        (
            "missing-outbox",
            f"outbox_count:{OBSERVATION_SHA}:t1",
            "invalid_settlement_outboxes",
        ),
        (
            "bad-outbox-id",
            f"outbox_contract:{'d' * 64}",
            "invalid_settlement_outboxes",
        ),
        (
            "duplicate-outbox",
            f"outbox_count:{OBSERVATION_SHA}:t1",
            "invalid_settlement_outboxes",
        ),
        ("payload", "outbox_contract", "invalid_settlement_outboxes"),
        ("keyword-direction", "outbox_contract", "invalid_settlement_outboxes"),
        ("keyword-correct", "outbox_contract", "invalid_settlement_outboxes"),
        ("missing-keyword", "outbox_contract", "invalid_settlement_outboxes"),
        ("created-at", "outbox_contract", "invalid_settlement_outboxes"),
        (
            "missing-requirement",
            "outbox_requirements",
            "invalid_settlement_outbox_requirements",
        ),
        (
            "extra-requirement",
            "outbox_requirements",
            "invalid_settlement_outbox_requirements",
        ),
    ],
)
def test_conservation_rejects_corrupt_settlement_outbox_graph(
    tmp_path,
    invalid,
    expected_failure,
    metric,
):
    db = tmp_path / "corrupt-settlement-outbox.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT outbox_id, event_version, event_kind, observation_sha256, "
        "trade_id, payload_json, created_at FROM paper_settlement_outbox"
    ).fetchone()
    assert row is not None
    outbox_id = str(row[0])
    if invalid == "missing-outbox":
        _mutate_append_only_table(
            conn,
            "paper_settlement_outbox_requirements",
            "delete",
            "DELETE FROM paper_settlement_outbox_requirements WHERE outbox_id=?",
            (outbox_id,),
        )
        _mutate_append_only_table(
            conn,
            "paper_settlement_outbox",
            "delete",
            "DELETE FROM paper_settlement_outbox WHERE outbox_id=?",
            (outbox_id,),
        )
    elif invalid == "bad-outbox-id":
        bad_id = "d" * 64
        _mutate_append_only_table(
            conn,
            "paper_settlement_outbox_requirements",
            "update",
            "UPDATE paper_settlement_outbox_requirements SET outbox_id=?",
            (bad_id,),
        )
        _mutate_append_only_table(
            conn,
            "paper_settlement_outbox",
            "update",
            "UPDATE paper_settlement_outbox SET outbox_id=?",
            (bad_id,),
        )
    elif invalid == "duplicate-outbox":
        duplicate_id = "e" * 64
        conn.execute(
            "INSERT INTO paper_settlement_outbox VALUES (?, ?, ?, ?, ?, ?, ?)",
            (duplicate_id, *row[1:]),
        )
        requirements = conn.execute(
            "SELECT consumer_name FROM paper_settlement_outbox_requirements "
            "WHERE outbox_id=?",
            (outbox_id,),
        ).fetchall()
        conn.executemany(
            "INSERT INTO paper_settlement_outbox_requirements VALUES (?, ?)",
            [(duplicate_id, requirement[0]) for requirement in requirements],
        )
    elif invalid == "payload":
        payload = json.loads(str(row[5]))
        payload["gross_payout_cents"] = "90"
        _mutate_append_only_table(
            conn,
            "paper_settlement_outbox",
            "update",
            "UPDATE paper_settlement_outbox SET payload_json=?",
            (json.dumps(payload, separators=(",", ":"), sort_keys=True),),
        )
    elif invalid in {"keyword-direction", "keyword-correct", "missing-keyword"}:
        payload = json.loads(str(row[5]))
        if invalid == "keyword-direction":
            payload["keyword_outcomes"][0]["direction"] = "maybe"
        elif invalid == "keyword-correct":
            payload["keyword_outcomes"][0]["correct"] = not payload[
                "keyword_outcomes"
            ][0]["correct"]
        else:
            payload["keyword_outcomes"] = []
        _mutate_append_only_table(
            conn,
            "paper_settlement_outbox",
            "update",
            "UPDATE paper_settlement_outbox SET payload_json=?",
            (json.dumps(payload, separators=(",", ":"), sort_keys=True),),
        )
    elif invalid == "created-at":
        _mutate_append_only_table(
            conn,
            "paper_settlement_outbox",
            "update",
            "UPDATE paper_settlement_outbox "
            "SET created_at='2026-07-14T12:01:00+00:00'",
        )
    elif invalid == "missing-requirement":
        _mutate_append_only_table(
            conn,
            "paper_settlement_outbox_requirements",
            "delete",
            "DELETE FROM paper_settlement_outbox_requirements "
            "WHERE outbox_id=? AND consumer_name='keyword_outcomes'",
            (outbox_id,),
        )
    else:
        conn.execute(
            "INSERT INTO paper_settlement_outbox_requirements VALUES (?, ?)",
            (outbox_id, "unknown_consumer"),
        )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert any(expected_failure in failure for failure in result.failures)
        assert result.metrics[metric] >= 1


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE paper_settlement_observations SET venue='evil'",
        "UPDATE paper_settlement_observations SET venue_market_id='   '",
        "UPDATE paper_settlement_observations SET alias='   '",
    ],
    ids=["venue", "venue-market-id", "alias"],
)
def test_observation_identity_schema_checks_reject_invalid_writes(
    tmp_path,
    mutation,
):
    db = tmp_path / "observation-identity-schema.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    _insert_observation(conn)

    with pytest.raises(sqlite3.IntegrityError):
        _mutate_observation(conn, mutation)
    conn.close()


@pytest.mark.parametrize(
    "mutations",
    [
        (
            "UPDATE paper_settlement_observations SET venue='evil'",
            "UPDATE paper_trades SET venue='evil' WHERE trade_id='t1'",
        ),
        (
            "UPDATE paper_settlement_observations SET venue_market_id='   '",
            "UPDATE paper_trades SET venue_market_id='   ' WHERE trade_id='t1'",
        ),
        (
            "UPDATE paper_settlement_observations SET alias='   '",
            "UPDATE paper_trades SET ticker='   ' WHERE trade_id='t1'",
        ),
    ],
    ids=["forged-venue", "blank-venue-market-id", "blank-aliases"],
)
def test_conservation_rejects_forged_observation_and_trade_identity(
    tmp_path,
    mutations,
):
    db = tmp_path / "forged-settlement-identity.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA ignore_check_constraints=ON")
    for mutation in mutations:
        if "paper_settlement_observations" in mutation:
            _mutate_observation(conn, mutation)
        else:
            conn.execute(mutation)
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert f"observation_identity:{OBSERVATION_SHA}" in result.failures
        assert f"trade_identity:{OBSERVATION_SHA}:t1" in result.failures
        assert result.metrics["invalid_observation_identities"] == 1
        assert result.metrics["invalid_linked_trade_identity"] == 1


@pytest.mark.parametrize(
    ("mutations", "expected_failure", "metric"),
    [
        (
            ("UPDATE paper_trades SET settled_at='not-a-time' WHERE trade_id='t1'",),
            f"trade_timestamps:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_timestamps",
        ),
        (
            (
                "UPDATE paper_trades SET resolved_ts='2026-07-14T12:00:00' "
                "WHERE trade_id='t1'",
            ),
            f"trade_timestamps:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_timestamps",
        ),
        (
            (
                "UPDATE paper_trades SET resolved_ts='2026-07-14T12:01:00+00:00' "
                "WHERE trade_id='t1'",
            ),
            f"trade_timestamps:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_timestamps",
        ),
        (
            (
                "UPDATE paper_settlement_observations "
                "SET applied_at='2026-07-14T12:01:00+00:00'",
            ),
            f"trade_timestamps:{OBSERVATION_SHA}:t1",
            "invalid_linked_trade_timestamps",
        ),
        (
            ("UPDATE paper_settlement_observations SET observed_at='not-a-time'",),
            f"observation_timestamps:{OBSERVATION_SHA}",
            "invalid_observation_timestamps",
        ),
        (
            (
                "UPDATE paper_settlement_observations "
                "SET effective_at='2026-07-14T11:59:00'",
            ),
            f"observation_timestamps:{OBSERVATION_SHA}",
            "invalid_observation_timestamps",
        ),
        (
            ("UPDATE paper_settlement_observations SET applied_at='not-a-time'",),
            f"observation_timestamps:{OBSERVATION_SHA}",
            "invalid_observation_timestamps",
        ),
        (
            (
                "UPDATE paper_settlement_observations "
                "SET effective_at='2026-07-14T12:01:00+00:00'",
            ),
            f"observation_timestamps:{OBSERVATION_SHA}",
            "invalid_observation_timestamps",
        ),
        (
            (
                "UPDATE paper_settlement_observations "
                "SET observed_at='2026-07-14T12:01:00+00:00'",
            ),
            f"observation_timestamps:{OBSERVATION_SHA}",
            "invalid_observation_timestamps",
        ),
    ],
    ids=[
        "malformed-settled-at",
        "naive-resolved-ts",
        "mismatched-resolved-ts",
        "mismatched-applied-at",
        "malformed-observed-at",
        "naive-effective-at",
        "malformed-applied-at",
        "effective-after-observed",
        "observed-after-applied",
    ],
)
def test_conservation_rejects_timestamp_lineage_corruption(
    tmp_path,
    mutations,
    expected_failure,
    metric,
):
    db = tmp_path / "settlement-timestamp-lineage.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    conn = sqlite3.connect(db)
    for mutation in mutations:
        if "paper_settlement_observations" in mutation:
            _mutate_observation(conn, mutation)
        else:
            conn.execute(mutation)
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert expected_failure in result.failures
        assert result.metrics[metric] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        (
            "UPDATE paper_settlement_observations "
            "SET canonical_payload_json='{\"settled\": true}'"
        ),
        (
            "UPDATE paper_settlement_observations "
            f"SET payload_sha256='{'d' * 64}'"
        ),
        (
            "UPDATE paper_settlement_observations "
            "SET authoritative_outcome_json='{\"outcome\":\"no\"}'"
        ),
    ],
    ids=["noncanonical-payload-json", "payload-hash", "semantic-outcome"],
)
def test_conservation_rejects_corrupt_observation_provenance(tmp_path, mutation):
    db = tmp_path / "corrupt-observation-provenance.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    conn = sqlite3.connect(db)
    _mutate_observation(conn, mutation)
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert f"observation_semantics:{OBSERVATION_SHA}" in result.failures
        assert result.metrics["invalid_observation_semantics"] == 1


def test_conservation_rejects_corrupt_observation_sha256(tmp_path):
    db = tmp_path / "corrupt-observation-sha.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)
    forged_sha = "d" * 64

    conn = sqlite3.connect(db)
    _mutate_observation(
        conn,
        "UPDATE paper_settlement_observations SET observation_sha256=?",
        (forged_sha,),
    )
    conn.execute(
        "UPDATE paper_trades SET settlement_observation_sha256=? WHERE trade_id='t1'",
        (forged_sha,),
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert f"observation_semantics:{forged_sha}" in result.failures
        assert result.metrics["invalid_observation_semantics"] == 1


@pytest.mark.parametrize(
    "invalid",
    ["missing-target", "wrong-market", "applied-order", "effective-order"],
)
def test_conservation_rejects_invalid_observation_supersession(tmp_path, invalid):
    db = tmp_path / "invalid-observation-supersession.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)

    first = _insert_observation(
        conn,
        observed_at=NOW - timedelta(minutes=6),
        effective_at=NOW - timedelta(minutes=7),
        applied_at=NOW - timedelta(minutes=5),
        authoritative_payload={"revision": 1},
    )
    _seed_settled_trade(
        conn,
        observation_sha256=first.observation_sha256,
        settled_at=(NOW - timedelta(minutes=5)).isoformat(),
        resolved_ts=(NOW - timedelta(minutes=5)).isoformat(),
    )

    successor_effective_at = NOW - timedelta(minutes=2)
    if invalid == "effective-order":
        successor_effective_at = NOW - timedelta(minutes=8)
    successor = _insert_observation(
        conn,
        observed_at=NOW - timedelta(minutes=1),
        effective_at=successor_effective_at,
        applied_at=NOW,
        authoritative_payload={"revision": 2},
    )
    _mutate_observation(
        conn,
        "UPDATE paper_settlement_observations "
        "SET supersedes_observation_sha256=? WHERE observation_sha256=?",
        (first.observation_sha256, successor.observation_sha256),
    )
    _seed_settled_trade(
        conn,
        trade_id="t2",
        observation_sha256=successor.observation_sha256,
    )

    if invalid == "missing-target":
        _mutate_observation(
            conn,
            "UPDATE paper_settlement_observations "
            "SET supersedes_observation_sha256=? WHERE observation_sha256=?",
            ("f" * 64, successor.observation_sha256),
        )
    elif invalid == "wrong-market":
        other_ref = MarketRef(Venue.KALSHI, "KX-other", "KX-other")
        other = _insert_observation(
            conn,
            market_ref=other_ref,
            observed_at=NOW - timedelta(minutes=4),
            effective_at=NOW - timedelta(minutes=4),
            applied_at=NOW - timedelta(minutes=3),
            authoritative_payload={"revision": "other"},
        )
        _seed_settled_trade(
            conn,
            trade_id="t3",
            market_ref=other_ref,
            observation_sha256=other.observation_sha256,
            settled_at=(NOW - timedelta(minutes=3)).isoformat(),
            resolved_ts=(NOW - timedelta(minutes=3)).isoformat(),
        )
        _mutate_observation(
            conn,
            "UPDATE paper_settlement_observations "
            "SET supersedes_observation_sha256=? WHERE observation_sha256=?",
            (other.observation_sha256, successor.observation_sha256),
        )
    elif invalid == "applied-order":
        later = (NOW + timedelta(minutes=1)).isoformat()
        _mutate_observation(
            conn,
            "UPDATE paper_settlement_observations SET applied_at=? "
            "WHERE observation_sha256=?",
            (later, first.observation_sha256),
        )
        conn.execute(
            "UPDATE paper_trades SET settled_at=?, resolved_ts=? WHERE trade_id='t1'",
            (later, later),
        )

    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert not result.ok
        assert (
            f"observation_supersession:{successor.observation_sha256}"
            in result.failures
        )
        assert result.metrics["invalid_observation_supersessions"] >= 1


@pytest.mark.parametrize(
    (
        "outcome",
        "side",
        "terminal_state",
        "resolved_yes",
        "payout",
        "gross_pnl",
        "pnl_dollars",
        "refund_cents_per_contract",
        "bankroll_after",
    ),
    [
        ("yes", "yes", "won", 1, "100", "60", 0.6, None, "1100"),
        ("yes", "no", "lost", 1, "0", "-40", -0.4, None, "1000"),
        ("no", "yes", "lost", 0, "0", "-40", -0.4, None, "1000"),
        ("no", "no", "won", 0, "100", "60", 0.6, None, "1100"),
        ("void", "yes", "void", None, "50", "10", 0.1, "50", "1050"),
    ],
)
def test_conservation_accepts_coherent_trade_outcomes(
    tmp_path,
    outcome,
    side,
    terminal_state,
    resolved_yes,
    payout,
    gross_pnl,
    pnl_dollars,
    refund_cents_per_contract,
    bankroll_after,
):
    db = tmp_path / "coherent-outcome.db"
    _create_legacy_db(db)
    _migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    observation = _insert_observation(
        conn,
        outcome=outcome,
        payout=payout,
        bankroll_after=bankroll_after,
        refund_cents_per_contract=refund_cents_per_contract,
        refunds_entry_fee=(0 if outcome == "void" else None),
    )
    _seed_settled_trade(
        conn,
        payout=payout,
        gross_pnl=gross_pnl,
        side=side,
        terminal_state=terminal_state,
        resolved_yes=resolved_yes,
        pnl_dollars=pnl_dollars,
        observation_sha256=observation.observation_sha256,
    )
    _seed_canonical_outbox(conn, observation)
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert result.ok
        assert result.metrics["invalid_linked_trade_outcomes"] == 0
        assert result.metrics["invalid_linked_trade_financials"] == 0


@pytest.mark.parametrize(
    ("processed_at", "forged_result_sha256", "failure_prefix"),
    [
        (
            "not-a-time",
            None,
            "receipt_processed_at",
        ),
        (
            NOW.replace(tzinfo=None).isoformat(),
            None,
            "receipt_processed_at",
        ),
        (
            NOW.isoformat(),
            "f" * 64,
            "receipt_result_sha256",
        ),
    ],
    ids=["malformed-timestamp", "naive-timestamp", "forged-result-hash"],
)
def test_conservation_rejects_forged_consumer_receipts(
    tmp_path,
    processed_at,
    forged_result_sha256,
    failure_prefix,
):
    db = tmp_path / "forged-receipt.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    requirements = conn.execute(
        "SELECT outbox_id, consumer_name "
        "FROM paper_settlement_outbox_requirements"
    ).fetchall()
    outbox_id = requirements[0][0]
    conn.executemany(
        "INSERT INTO paper_settlement_consumer_receipts VALUES (?, ?, ?, ?)",
        [
            (
                consumer_name,
                outbox_id,
                processed_at if consumer_name == "paper_trade_log" else NOW.isoformat(),
                (
                    forged_result_sha256
                    if consumer_name == "paper_trade_log"
                    and forged_result_sha256 is not None
                    else settlement_result_sha256(outbox_id, consumer_name)
                ),
            )
            for _, consumer_name in requirements
        ],
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        assert store.pending_requirements() == ()
        result = store.conservation(now=NOW)
        assert not result.ok
        assert (
            f"{failure_prefix}:paper_trade_log:{outbox_id}" in result.failures
        )
        assert result.metrics["consumer_receipts"] == 4
        assert result.metrics["invalid_consumer_receipts"] == 1


def test_conservation_accepts_exact_valid_accounting(tmp_path):
    db = tmp_path / "paper.db"
    _create_legacy_db(db)
    _migrate(db)
    _seed_valid_accounting(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    outbox_id = conn.execute(
        "SELECT outbox_id FROM paper_settlement_outbox"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO paper_settlement_consumer_receipts VALUES (?, ?, ?, ?)",
        (
            "paper_trade_log",
            outbox_id,
            NOW.isoformat(),
            settlement_result_sha256(outbox_id, "paper_trade_log"),
        ),
    )
    conn.commit()
    conn.close()

    with SettlementStore(db) as store:
        result = store.conservation(now=NOW)
        assert result.ok
        assert result.failures == ()
        assert result.metrics["foreign_key_violations"] == 0
        assert result.metrics["consumer_receipts"] == 1
        assert result.metrics["invalid_consumer_receipts"] == 0
        assert result.metrics["linked_trades"] == 1
        assert result.metrics["invalid_linked_trade_identity"] == 0
        assert result.metrics["linked_trade_alias_drifts"] == 0
        assert result.metrics["invalid_linked_trade_financials"] == 0
        assert result.metrics["invalid_linked_trade_outcomes"] == 0
        assert result.metrics["settlement_outboxes"] == 1
        assert result.metrics["invalid_settlement_outboxes"] == 0
        assert result.metrics["invalid_settlement_outbox_requirements"] == 0
