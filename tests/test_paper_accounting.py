from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from config import BotConfig
from scripts.migrate_paper_accounting_schema import (
    apply_paper_accounting_schema,
    open_readonly,
    plan_paper_accounting_schema,
)
from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    KALSHI_GENERAL_2026_07_07,
    FeeContext,
    FeeRole,
    FeeScheduleId,
    fee_coefficient_for,
    quote_fee,
)
from trading.paper_accounting import (
    PAPER_ACCOUNTING_DDL_SHA256,
    PAPER_ACCOUNTING_VERSION,
    PaperAccountingAdmissionError,
    PaperAccountingHandlers,
    PaperAccountingRecord,
    PaperAccountingSchemaError,
    initialize_fresh_paper_accounting_schema,
    paper_accounting_schema_contract_matches,
    require_paper_accounting_admission,
)
from trading.paper_trader import _DDL
from trading.settlement_store import (
    SETTLEMENT_DDL_SHA256,
    SETTLEMENT_SCHEMA_VERSION,
    SETTLEMENT_TARGET_STATEMENTS,
    enable_and_verify_foreign_keys,
    initialize_fresh_settlement_schema,
)


D = Decimal
UTC = timezone.utc
NOW = datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
PLAN_SHA = "a" * 64


def _create_gross_v1_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        enable_and_verify_foreign_keys(conn)
        for statement in _DDL.split(";"):
            if statement.strip():
                conn.execute(statement)
        initialize_fresh_settlement_schema(conn, applied_at=NOW.isoformat())
        conn.commit()
    finally:
        conn.close()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    enable_and_verify_foreign_keys(conn)
    return conn


def _seed_trade(conn: sqlite3.Connection, trade_id: str = "trade-1") -> None:
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ts, ticker, venue, venue_market_id, identity_status,
            market_title, side, contracts, price_cents, cost_dollars,
            estimated_prob, entry_price_cents, edge, kelly_dollars,
            capped_dollars, signal_headline, signal_source,
            keywords_matched, reasoning
        ) VALUES (?, ?, 'KXTEST-1', 'kalshi', 'KXTEST-1', 'mapped',
                  'Test market', 'yes', 1, 33, 0.33, 0.55, 33, 0.22,
                  0.33, 0.33, 'headline', 'source', '[]', 'reason')
        """,
        (trade_id, NOW.isoformat()),
    )


def _entry_record(**changes: object) -> PaperAccountingRecord:
    quantity = D("0.125")
    price = D("0.3333")
    signed_revenue = -(quantity * price)
    coefficient = fee_coefficient_for(KALSHI_GENERAL_2026_07_07, FeeRole.TAKER)
    context = FeeContext(
        schedule_id=KALSHI_GENERAL_2026_07_07,
        role=FeeRole.TAKER,
        quantity=quantity,
        price=price,
        signed_revenue=signed_revenue,
        order_id="paper-order:req-1",
        accumulator=D("0.0003"),
        multiplier=D("1"),
        coefficient=coefficient,
        account_precision=DIRECT_ACCOUNT_PRECISION,
        timestamp=NOW,
    )
    quote = quote_fee(context)
    gross_entry_debit = abs(signed_revenue)
    record = PaperAccountingRecord(
        accounting_version=PAPER_ACCOUNTING_VERSION,
        entry_request_id="req-1",
        trade_id="trade-1",
        order_id=context.order_id,
        fill_id="paper-fill:req-1:0",
        filled_at=NOW,
        schedule_id=context.schedule_id,
        role=context.role,
        quantity=context.quantity,
        price=context.price,
        signed_revenue=context.signed_revenue,
        multiplier=context.multiplier,
        coefficient=context.coefficient,
        account_precision=context.account_precision,
        quote=quote,
        gross_entry_debit=gross_entry_debit,
        net_entry_debit=gross_entry_debit + quote.net_fee,
        recorded_at=NOW,
    )
    return replace(record, **changes)


def _install_accounting(conn: sqlite3.Connection) -> None:
    initialize_fresh_paper_accounting_schema(
        conn,
        migration_plan_sha256=PLAN_SHA,
        applied_at=NOW.isoformat(),
    )


def _insert_record(conn: sqlite3.Connection, record: PaperAccountingRecord) -> None:
    values = record.to_database_values()
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO paper_trade_accounting ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


def test_fee_net_paper_accounting_config_defaults_false_and_parses_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_FEE_NET_PAPER_ACCOUNTING", raising=False)
    assert BotConfig().enable_fee_net_paper_accounting is False
    monkeypatch.setenv("ENABLE_FEE_NET_PAPER_ACCOUNTING", "true")
    assert BotConfig().enable_fee_net_paper_accounting is True


def test_record_round_trip_preserves_fractional_quantity_and_subpenny_price(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        _seed_trade(conn)
        record = _entry_record()
        _insert_record(conn, record)
        row = conn.execute("SELECT * FROM paper_trade_accounting WHERE entry_request_id='req-1'").fetchone()
        assert PaperAccountingRecord.from_database_row(row) == record
        assert row["fill_quantity"] == "0.125"
        assert row["fill_price_dollars"] == "0.3333"

        declared_types = {
            item[1]: str(item[2]).upper() for item in conn.execute("PRAGMA table_info(paper_trade_accounting)")
        }
        assert "REAL" not in declared_types.values()
        for column in PaperAccountingRecord.decimal_database_columns():
            assert declared_types[column] == "TEXT"
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"quantity": D("NaN")}, "quantity must be finite"),
        ({"price": 0.5}, "price must be Decimal"),
        ({"entry_request_id": ""}, "entry_request_id is required"),
        ({"net_entry_debit": D("9")}, "net_entry_debit"),
    ],
)
def test_record_rejects_inexact_or_incoherent_values(
    change: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_entry_record(), **change)


def test_record_rejects_noncanonical_persisted_decimal_text() -> None:
    values = _entry_record().to_database_values()
    values["fill_quantity"] = "1e-1"
    with pytest.raises(ValueError, match="fill_quantity must be canonical Decimal text"):
        PaperAccountingRecord.from_database_row(values)


def test_settlement_fields_are_all_null_or_complete_and_exact() -> None:
    entry = _entry_record()
    with pytest.raises(ValueError, match="settlement fields must be all null or complete"):
        replace(entry, settlement_fee=D("0"))

    settled = replace(
        entry,
        settlement_observation_sha256="b" * 64,
        settled_at=NOW,
        settlement_fee=D("0.01"),
        settlement_refund=D("0"),
        gross_settlement_payout=D("0.10"),
        net_settlement_payout=D("0.09"),
        fee_net_pnl=D("0.09") - entry.net_entry_debit,
    )
    values = settled.to_database_values()
    assert PaperAccountingRecord.from_database_row(values) == settled
    with pytest.raises(ValueError, match="net_settlement_payout"):
        replace(settled, net_settlement_payout=D("0.08"))


def test_schema_requires_exact_gross_settlement_v1(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "missing-gross.db")
    try:
        conn.execute("CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY)")
        with pytest.raises(PaperAccountingSchemaError, match="gross settlement v1"):
            _install_accounting(conn)
    finally:
        conn.close()


def test_schema_contract_meta_uniques_and_immutable_version(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        assert paper_accounting_schema_contract_matches(conn)
        meta = conn.execute(
            "SELECT schema_version, accounting_version, ddl_sha256, "
            "migration_plan_sha256 FROM paper_accounting_schema_meta"
        ).fetchone()
        assert tuple(meta) == (
            1,
            PAPER_ACCOUNTING_VERSION,
            PAPER_ACCOUNTING_DDL_SHA256,
            PLAN_SHA,
        )

        _seed_trade(conn)
        _insert_record(conn, _entry_record())
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE paper_trade_accounting SET accounting_version=2 WHERE entry_request_id='req-1'")
        for field, value in (
            ("entry_request_id", "req-1"),
            ("trade_id", "trade-1"),
            ("fill_id", "paper-fill:req-1:0"),
        ):
            replacements = {
                "entry_request_id": "req-2",
                "trade_id": "trade-2",
                "fill_id": "paper-fill:req-2:0",
            }
            replacements[field] = value
            duplicate = replace(_entry_record(), **replacements)
            _seed_trade(conn, "trade-2")
            with pytest.raises(sqlite3.IntegrityError):
                _insert_record(conn, duplicate)
            conn.execute("DELETE FROM paper_trades WHERE trade_id='trade-2'")
    finally:
        conn.close()


def test_contract_rejects_tampered_accounting_object(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        conn.execute("DROP INDEX paper_trade_accounting_accumulator_idx")
        assert not paper_accounting_schema_contract_matches(conn)
    finally:
        conn.close()


def test_handlers_dispatch_by_persisted_version() -> None:
    calls: list[tuple[str, str]] = []
    handlers = PaperAccountingHandlers(
        entry={1: lambda record: calls.append(("entry", record.entry_request_id))},
        settlement={1: lambda record: calls.append(("settlement", record.trade_id))},
    )
    record = _entry_record()
    handlers.dispatch_entry(record)
    handlers.dispatch_settlement(record)
    assert calls == [("entry", "req-1"), ("settlement", "trade-1")]

    unsupported = replace(record, accounting_version=2, validate=False)
    with pytest.raises(PaperAccountingAdmissionError, match="entry handler.*version 2"):
        handlers.dispatch_entry(unsupported)


def test_admission_requires_schema_both_handlers_request_and_pinned_schedule(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    handlers = PaperAccountingHandlers(
        entry={1: lambda _record: None},
        settlement={1: lambda _record: None},
    )
    try:
        with pytest.raises(PaperAccountingAdmissionError, match="schema"):
            require_paper_accounting_admission(conn, handlers, "req-1", KALSHI_GENERAL_2026_07_07)
        _install_accounting(conn)
        with pytest.raises(PaperAccountingAdmissionError, match="entry_request_id"):
            require_paper_accounting_admission(conn, handlers, "", KALSHI_GENERAL_2026_07_07)
        with pytest.raises(PaperAccountingAdmissionError, match="both entry and settlement"):
            require_paper_accounting_admission(
                conn,
                PaperAccountingHandlers(entry={1: lambda _record: None}, settlement={}),
                "req-1",
                KALSHI_GENERAL_2026_07_07,
            )
        unknown = FeeScheduleId(
            name="unknown",
            venue=KALSHI_GENERAL_2026_07_07.venue,
            effective_from=NOW,
            effective_to=None,
            artifact_sha256="c" * 64,
        )
        with pytest.raises(PaperAccountingAdmissionError, match="pinned fee schedule"):
            require_paper_accounting_admission(conn, handlers, "req-1", unknown)
        require_paper_accounting_admission(conn, handlers, "req-1", KALSHI_GENERAL_2026_07_07)
    finally:
        conn.close()


def test_migration_plan_apply_noop_and_preserves_gross_v1(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        gross_sql_before = {
            name: conn.execute("SELECT sql FROM sqlite_schema WHERE name=?", (name,)).fetchone()[0]
            for name, _sql in SETTLEMENT_TARGET_STATEMENTS
        }
        gross_meta_before = tuple(
            conn.execute(
                "SELECT schema_version, ddl_sha256, migration_plan_sha256, applied_at FROM paper_settlement_schema_meta"
            ).fetchone()
        )
    finally:
        conn.close()

    with open_readonly(db) as readonly:
        assert readonly.execute("PRAGMA query_only").fetchone()[0] == 1
        plan = plan_paper_accounting_schema(readonly, db)
    assert plan.action == "apply"
    apply_paper_accounting_schema(db, plan, reviewed_plan_fingerprint=plan.fingerprint)

    conn = _connect(db)
    try:
        assert paper_accounting_schema_contract_matches(conn)
        assert (
            tuple(
                conn.execute(
                    "SELECT schema_version, ddl_sha256, migration_plan_sha256, applied_at "
                    "FROM paper_settlement_schema_meta"
                ).fetchone()
            )
            == gross_meta_before
        )
        assert {
            name: conn.execute("SELECT sql FROM sqlite_schema WHERE name=?", (name,)).fetchone()[0]
            for name, _sql in SETTLEMENT_TARGET_STATEMENTS
        } == gross_sql_before
        assert SETTLEMENT_SCHEMA_VERSION == 1
        assert SETTLEMENT_DDL_SHA256 == gross_meta_before[1]
    finally:
        conn.close()

    with open_readonly(db) as readonly:
        noop = plan_paper_accounting_schema(readonly, db)
    assert noop.action == "noop"
    apply_paper_accounting_schema(db, noop, reviewed_plan_fingerprint=noop.fingerprint)


def test_migration_rechecks_fingerprint_and_database_drift(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    with open_readonly(db) as conn:
        plan = plan_paper_accounting_schema(conn, db)
    with pytest.raises(ValueError, match="reviewed plan fingerprint"):
        apply_paper_accounting_schema(db, plan, reviewed_plan_fingerprint="0" * 64)

    conn = _connect(db)
    conn.execute("CREATE TABLE unrelated_drift (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="sqlite_schema drift"):
        apply_paper_accounting_schema(db, plan, reviewed_plan_fingerprint=plan.fingerprint)


def test_migration_rechecks_all_paper_trade_rows_under_writer_lock(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    with open_readonly(db) as conn:
        plan = plan_paper_accounting_schema(conn, db)

    conn = _connect(db)
    _seed_trade(conn)
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="paper_trades row drift"):
        apply_paper_accounting_schema(db, plan, reviewed_plan_fingerprint=plan.fingerprint)


def test_noop_migration_rechecks_accounting_meta_under_writer_lock(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    with open_readonly(db) as conn:
        apply_plan = plan_paper_accounting_schema(conn, db)
    apply_paper_accounting_schema(
        db,
        apply_plan,
        reviewed_plan_fingerprint=apply_plan.fingerprint,
    )
    with open_readonly(db) as conn:
        noop_plan = plan_paper_accounting_schema(conn, db)

    conn = _connect(db)
    conn.execute(
        "UPDATE paper_accounting_schema_meta SET migration_plan_sha256=?",
        ("f" * 64,),
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="paper-accounting state drift"):
        apply_paper_accounting_schema(
            db,
            noop_plan,
            reviewed_plan_fingerprint=noop_plan.fingerprint,
        )


def test_planner_rejects_corrupt_gross_settlement_meta(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    conn.execute(
        "UPDATE paper_settlement_schema_meta SET ddl_sha256=?",
        ("0" * 64,),
    )
    conn.commit()
    conn.close()
    with open_readonly(db) as conn:
        with pytest.raises(PaperAccountingSchemaError, match="gross settlement v1"):
            plan_paper_accounting_schema(conn, db)


def test_migration_rolls_back_and_rejects_partial_state(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    with open_readonly(db) as conn:
        plan = plan_paper_accounting_schema(conn, db)

    def fail_after_table(stage: str) -> None:
        if stage == "after_ddl:paper_trade_accounting":
            raise RuntimeError("fault")

    with pytest.raises(RuntimeError, match="fault"):
        apply_paper_accounting_schema(
            db,
            plan,
            reviewed_plan_fingerprint=plan.fingerprint,
            fault_hook=fail_after_table,
        )
    conn = _connect(db)
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_accounting%' "
                "OR name LIKE 'paper_trade_accounting%'"
            ).fetchone()[0]
            == 0
        )
        conn.execute("CREATE TABLE paper_accounting_schema_meta (schema_version INTEGER)")
        conn.commit()
    finally:
        conn.close()
    with open_readonly(db) as conn:
        with pytest.raises(PaperAccountingSchemaError, match="partial"):
            plan_paper_accounting_schema(conn, db)


def test_plan_json_is_deterministic_and_binds_action(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    with open_readonly(db) as conn:
        first = plan_paper_accounting_schema(conn, db)
    with open_readonly(db) as conn:
        second = plan_paper_accounting_schema(conn, db)
    assert first == second
    payload = json.loads(first.to_json())
    assert payload["action"] == "apply"
    assert payload["accounting_version"] == PAPER_ACCOUNTING_VERSION
    assert payload["ddl_sha256"] == PAPER_ACCOUNTING_DDL_SHA256
    assert len(payload["paper_trades_rows_sha256"]) == 64
    assert len(payload["fingerprint"]) == 64
