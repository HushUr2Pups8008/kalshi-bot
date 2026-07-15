from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock

import pytest

from config import BotConfig
import scripts.migrate_paper_accounting_schema as migration_module
from scripts.migrate_paper_accounting_schema import (
    apply_paper_accounting_schema,
    open_readonly,
    plan_paper_accounting_schema,
)
from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    KALSHI_GENERAL_2026_07_07,
    NON_DIRECT_ACCOUNT_PRECISION,
    POLYMARKET_US_2026_07_01,
    FeeContext,
    FeeRole,
    FeeScheduleId,
    fee_coefficient_for,
    fee_type_for_schedule,
    quote_fee,
)
from trading.paper_accounting import (
    PAPER_ACCOUNTING_DDL_SHA256,
    PAPER_ACCOUNTING_SCHEMA_VERSION,
    PAPER_ACCOUNTING_TARGET_STATEMENTS,
    PAPER_ACCOUNTING_VERSION,
    PaperAccountingAdmissionError,
    PaperAccountingHandlers,
    PaperAccountingRecord,
    PaperAccountingSchemaError,
    accounting_schema_state,
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


def _seed_trade(
    conn: sqlite3.Connection,
    trade_id: str = "trade-1",
    venue: str = "kalshi",
) -> None:
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ts, ticker, venue, venue_market_id, identity_status,
            market_title, side, contracts, price_cents, cost_dollars,
            estimated_prob, entry_price_cents, edge, kelly_dollars,
            capped_dollars, signal_headline, signal_source,
            keywords_matched, reasoning
        ) VALUES (?, ?, 'KXTEST-1', ?, 'KXTEST-1', 'mapped',
                  'Test market', 'yes', 1, 33, 0.33, 0.55, 33, 0.22,
                  0.33, 0.33, 'headline', 'source', '[]', 'reason')
        """,
        (trade_id, NOW.isoformat(), venue),
    )


def _seed_settlement_observation(
    conn: sqlite3.Connection,
    observation_sha256: str = "b" * 64,
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
        ) VALUES (?, 'kalshi', 'KXTEST-1', 'KXTEST-1', 'yes',
                  '{}', '{}', ?, ?, ?, 'test-v1', 'test',
                  NULL, NULL, NULL, 1, '0', '0', '0', ?)
        """,
        (
            observation_sha256,
            "c" * 64,
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
        ),
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
        fee_type=fee_type_for_schedule(context.schedule_id),
        fee_multiplier_provenance_sha256="d" * 64,
        fee_multiplier_effective_at=NOW - timedelta(minutes=1),
        coefficient=context.coefficient,
        account_precision=context.account_precision,
        account_precision_mode="direct",
        quote=quote,
        gross_entry_debit=gross_entry_debit,
        net_entry_debit=gross_entry_debit + quote.net_fee,
        recorded_at=NOW,
    )
    return replace(record, **changes)


def _polymarket_entry_record(
    entry: PaperAccountingRecord | None = None,
    **changes: object,
) -> PaperAccountingRecord:
    record = entry or _entry_record()
    coefficient = fee_coefficient_for(POLYMARKET_US_2026_07_01, FeeRole.TAKER)
    context = FeeContext(
        schedule_id=POLYMARKET_US_2026_07_01,
        role=FeeRole.TAKER,
        quantity=record.quantity,
        price=record.price,
        signed_revenue=record.signed_revenue,
        order_id=record.order_id,
        accumulator=D("0"),
        multiplier=record.multiplier,
        coefficient=coefficient,
        account_precision=None,
        timestamp=record.filled_at,
    )
    quote = quote_fee(context)
    polymarket = replace(
        record,
        schedule_id=context.schedule_id,
        role=context.role,
        coefficient=context.coefficient,
        account_precision=None,
        account_precision_mode="not_applicable",
        quote=quote,
        net_entry_debit=record.gross_entry_debit + quote.net_fee,
    )
    return replace(polymarket, **changes)


def _settled_record(
    entry: PaperAccountingRecord | None = None,
    **changes: object,
) -> PaperAccountingRecord:
    record = entry or _entry_record()
    settled = replace(
        record,
        settlement_observation_sha256="b" * 64,
        settled_at=NOW,
        settlement_fee=D("0.01"),
        settlement_refund=D("0"),
        gross_settlement_payout=D("0.10"),
        net_settlement_payout=D("0.09"),
        fee_net_pnl=D("0.09") - record.net_entry_debit,
    )
    return replace(settled, **changes)


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


def test_record_rejects_causally_impossible_timestamps_direct_and_persisted() -> None:
    entry = _entry_record()
    with pytest.raises(ValueError, match="recorded_at must not precede filled_at"):
        replace(entry, recorded_at=entry.filled_at - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="settled_at must not precede recorded_at"):
        _settled_record(entry, settled_at=entry.recorded_at - timedelta(microseconds=1))

    values = entry.to_database_values()
    values["recorded_at"] = (entry.filled_at - timedelta(microseconds=1)).isoformat()
    with pytest.raises(ValueError, match="recorded_at must not precede filled_at"):
        PaperAccountingRecord.from_database_row(values)

    settled_values = _settled_record(entry).to_database_values()
    settled_values["settled_at"] = (entry.recorded_at - timedelta(microseconds=1)).isoformat()
    with pytest.raises(ValueError, match="settled_at must not precede recorded_at"):
        PaperAccountingRecord.from_database_row(settled_values)


def test_record_requires_exact_fee_multiplier_provenance_direct_and_persisted() -> None:
    entry = _entry_record()
    assert entry.fee_type == fee_type_for_schedule(entry.schedule_id)
    with pytest.raises(ValueError, match="fee_type is required"):
        replace(entry, fee_type="")
    for invalid_fee_type in ("bogus", "linear"):
        with pytest.raises(ValueError, match="fee_type does not match pinned fee formula"):
            replace(entry, fee_type=invalid_fee_type)
    with pytest.raises(ValueError, match="fee_multiplier_provenance_sha256"):
        replace(entry, fee_multiplier_provenance_sha256="D" * 64)
    with pytest.raises(
        ValueError,
        match="fee_multiplier_effective_at must not follow filled_at",
    ):
        replace(
            entry,
            fee_multiplier_effective_at=entry.filled_at + timedelta(microseconds=1),
        )

    for column, value, message in (
        ("fee_type", "", "fee_type is required"),
        (
            "fee_multiplier_provenance_sha256",
            "D" * 64,
            "fee_multiplier_provenance_sha256",
        ),
        (
            "fee_multiplier_effective_at",
            (entry.filled_at + timedelta(microseconds=1)).isoformat(),
            "fee_multiplier_effective_at must not follow filled_at",
        ),
    ):
        values = entry.to_database_values()
        values[column] = value
        with pytest.raises(ValueError, match=message):
            PaperAccountingRecord.from_database_row(values)


def test_fee_multiplier_effective_at_must_be_inside_schedule_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry_record()
    with pytest.raises(ValueError, match="must not precede fee schedule effective_from"):
        replace(
            entry,
            fee_multiplier_effective_at=entry.schedule_id.effective_from - timedelta(microseconds=1),
        )

    bounded_schedule = replace(
        entry.schedule_id,
        effective_to=entry.filled_at - timedelta(seconds=1),
    )
    monkeypatch.setattr(
        "trading.paper_accounting.fee_coefficient_for",
        lambda _schedule_id, _role: entry.coefficient,
    )
    bounded = replace(
        entry,
        schedule_id=bounded_schedule,
        fee_multiplier_effective_at=bounded_schedule.effective_to,
        validate=False,
    )
    with pytest.raises(ValueError, match="must precede fee schedule effective_to"):
        bounded.validate_record()


def test_zero_fee_multiplier_remains_valid_with_exact_provenance() -> None:
    entry = _entry_record()
    zero_context = replace(
        FeeContext(
            schedule_id=entry.schedule_id,
            role=entry.role,
            quantity=entry.quantity,
            price=entry.price,
            signed_revenue=entry.signed_revenue,
            order_id=entry.order_id,
            accumulator=entry.quote.previous_accumulator,
            multiplier=entry.multiplier,
            coefficient=entry.coefficient,
            account_precision=entry.account_precision,
            timestamp=entry.filled_at,
        ),
        multiplier=D("0"),
    )
    quote = quote_fee(zero_context)
    zero = replace(
        entry,
        multiplier=D("0"),
        quote=quote,
        net_entry_debit=entry.gross_entry_debit + quote.net_fee,
    )
    assert zero.multiplier == D("0")
    assert PaperAccountingRecord.from_database_row(zero.to_database_values()) == zero


def test_entry_debit_rejects_coherent_positive_signed_revenue() -> None:
    entry = _entry_record()
    context = FeeContext(
        schedule_id=entry.schedule_id,
        role=entry.role,
        quantity=entry.quantity,
        price=entry.price,
        signed_revenue=abs(entry.signed_revenue),
        order_id=entry.order_id,
        accumulator=entry.quote.previous_accumulator,
        multiplier=entry.multiplier,
        coefficient=entry.coefficient,
        account_precision=entry.account_precision,
        timestamp=entry.filled_at,
    )
    quote = quote_fee(context)
    with pytest.raises(ValueError, match="signed_revenue must be negative"):
        replace(
            entry,
            signed_revenue=context.signed_revenue,
            quote=quote,
            net_entry_debit=entry.gross_entry_debit + quote.net_fee,
        )


def test_account_precision_mode_matches_venue_and_exact_quantum() -> None:
    direct = _entry_record()
    with pytest.raises(ValueError, match="account_precision_mode"):
        replace(direct, account_precision_mode="non_direct")

    non_direct_context = FeeContext(
        schedule_id=direct.schedule_id,
        role=direct.role,
        quantity=direct.quantity,
        price=direct.price,
        signed_revenue=direct.signed_revenue,
        order_id=direct.order_id,
        accumulator=direct.quote.previous_accumulator,
        multiplier=direct.multiplier,
        coefficient=direct.coefficient,
        account_precision=NON_DIRECT_ACCOUNT_PRECISION,
        timestamp=direct.filled_at,
    )
    non_direct_quote = quote_fee(non_direct_context)
    non_direct = replace(
        direct,
        account_precision=NON_DIRECT_ACCOUNT_PRECISION,
        account_precision_mode="non_direct",
        quote=non_direct_quote,
        net_entry_debit=direct.gross_entry_debit + non_direct_quote.net_fee,
    )
    assert PaperAccountingRecord.from_database_row(non_direct.to_database_values()) == non_direct

    polymarket = _polymarket_entry_record(direct)
    assert PaperAccountingRecord.from_database_row(polymarket.to_database_values()) == polymarket
    with pytest.raises(ValueError, match="account_precision_mode"):
        replace(polymarket, account_precision_mode="direct")

    values = direct.to_database_values()
    values["account_precision_mode"] = "not_applicable"
    with pytest.raises(ValueError, match="account_precision_mode"):
        PaperAccountingRecord.from_database_row(values)


def test_settlement_fields_are_all_null_or_complete_and_exact() -> None:
    entry = _entry_record()
    with pytest.raises(ValueError, match="settlement fields must be all null or complete"):
        replace(entry, settlement_fee=D("0"))

    settled = _settled_record(entry)
    values = settled.to_database_values()
    assert PaperAccountingRecord.from_database_row(values) == settled
    with pytest.raises(ValueError, match="net_settlement_payout"):
        replace(settled, net_settlement_payout=D("0.08"))


def test_settlement_rejects_negative_net_payout_with_coherent_arithmetic() -> None:
    settled = _settled_record()
    negative_net_payout = D("-0.01")
    with pytest.raises(ValueError, match="net_settlement_payout must be non-negative"):
        replace(
            settled,
            settlement_fee=D("0.11"),
            net_settlement_payout=negative_net_payout,
            fee_net_pnl=negative_net_payout - settled.net_entry_debit,
        )


def test_settlement_rejects_over_payout_over_refund_and_mixed_resolution() -> None:
    settled = _settled_record()

    over_payout = settled.quantity + D("0.0001")
    over_payout_net = over_payout - settled.settlement_fee
    with pytest.raises(ValueError, match="gross_settlement_payout must not exceed fill_quantity"):
        replace(
            settled,
            gross_settlement_payout=over_payout,
            net_settlement_payout=over_payout_net,
            fee_net_pnl=over_payout_net - settled.net_entry_debit,
        )

    over_refund = settled.gross_entry_debit + D("0.0001")
    with pytest.raises(ValueError, match="settlement_refund must not exceed gross_entry_debit"):
        replace(
            settled,
            settlement_fee=D("0"),
            settlement_refund=over_refund,
            gross_settlement_payout=D("0"),
            net_settlement_payout=over_refund,
            fee_net_pnl=over_refund - settled.net_entry_debit,
        )

    mixed_refund = D("0.01")
    mixed_net = settled.gross_settlement_payout - settled.settlement_fee + mixed_refund
    with pytest.raises(ValueError, match="payout and refund are mutually exclusive"):
        replace(
            settled,
            settlement_refund=mixed_refund,
            net_settlement_payout=mixed_net,
            fee_net_pnl=mixed_net - settled.net_entry_debit,
        )


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
        table_sql = conn.execute("SELECT sql FROM sqlite_schema WHERE name='paper_trade_accounting'").fetchone()[0]
        assert "CHECK (fee_type = 'quadratic')" in table_sql

        _seed_trade(conn)
        invalid_values = _entry_record().to_database_values()
        invalid_values["fee_type"] = "linear"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO paper_trade_accounting ("
                + ",".join(invalid_values)
                + ") VALUES ("
                + ",".join("?" for _ in invalid_values)
                + ")",
                tuple(invalid_values.values()),
            )
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


def test_accounting_entry_meta_and_delete_are_database_immutable(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        _seed_trade(conn)
        _insert_record(conn, _entry_record())
        immutable_changes: dict[str, object] = {
            "accounting_id": 99,
            "entry_request_id": "req-other",
            "trade_id": "trade-other",
            "venue": "polymarket_us",
            "order_id": "paper-order:other",
            "fill_id": "paper-fill:other:0",
            "fee_role": "maker",
            "filled_at": (NOW + timedelta(seconds=1)).isoformat(),
            "fill_quantity": "0.25",
            "fill_price_dollars": "0.25",
            "signed_revenue_dollars": "-0.0625",
            "fee_schedule_name": "other",
            "fee_schedule_effective_from": (NOW - timedelta(days=1)).isoformat(),
            "fee_schedule_effective_to": (NOW + timedelta(days=1)).isoformat(),
            "fee_schedule_artifact_sha256": "c" * 64,
            "fee_schedule_supporting_artifacts_json": "[]",
            "fee_multiplier": "2",
            "fee_type": "other",
            "fee_multiplier_provenance_sha256": "e" * 64,
            "fee_multiplier_effective_at": (NOW - timedelta(minutes=2)).isoformat(),
            "fee_coefficient": "0.06",
            "account_precision_dollars": "0.01",
            "account_precision_mode": "non_direct",
            "base_fee_dollars": "1",
            "trade_fee_dollars": "1",
            "rounding_adjustment_dollars": "1",
            "balance_rounding_fee_dollars": "1",
            "rebate_dollars": "1",
            "net_fee_dollars": "1",
            "accumulator_before_dollars": "1",
            "accumulator_after_dollars": "1",
            "gross_entry_debit_dollars": "1",
            "net_entry_debit_dollars": "1",
            "recorded_at": (NOW + timedelta(seconds=1)).isoformat(),
        }
        for column, value in immutable_changes.items():
            with pytest.raises(sqlite3.IntegrityError, match="entry fields are immutable"):
                conn.execute(
                    f"UPDATE paper_trade_accounting SET {column}=? WHERE entry_request_id='req-1'",
                    (value,),
                )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM paper_trade_accounting WHERE entry_request_id='req-1'")
        with pytest.raises(sqlite3.IntegrityError, match="meta is immutable"):
            conn.execute(
                "UPDATE paper_accounting_schema_meta SET applied_at=?",
                ((NOW + timedelta(seconds=1)).isoformat(),),
            )
        with pytest.raises(sqlite3.IntegrityError, match="meta is immutable"):
            conn.execute("DELETE FROM paper_accounting_schema_meta")
    finally:
        conn.close()


def test_fill_identity_is_unique_within_venue_not_across_venues(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        _seed_trade(conn)
        kalshi = _entry_record()
        _insert_record(conn, kalshi)
        _seed_trade(conn, "trade-2", "polymarket_us")
        polymarket = _polymarket_entry_record(
            kalshi,
            entry_request_id="req-2",
            trade_id="trade-2",
        )
        _insert_record(conn, polymarket)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM paper_trade_accounting WHERE fill_id=?",
                (kalshi.fill_id,),
            ).fetchone()[0]
            == 2
        )

        _seed_trade(conn, "trade-3")
        duplicate_kalshi = replace(
            kalshi,
            entry_request_id="req-3",
            trade_id="trade-3",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_record(conn, duplicate_kalshi)
    finally:
        conn.close()


def test_accounting_insert_requires_parent_trade_venue_match(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        _seed_trade(conn, venue="kalshi")
        mismatched = _polymarket_entry_record()
        with pytest.raises(sqlite3.IntegrityError, match="parent trade venue mismatch"):
            _insert_record(conn, mismatched)
        assert conn.execute("SELECT COUNT(*) FROM paper_trade_accounting").fetchone()[0] == 0
    finally:
        conn.close()


def test_settlement_transitions_once_then_is_database_immutable(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        _seed_trade(conn)
        entry = _entry_record()
        _insert_record(conn, entry)
        settled = _settled_record(entry)
        values = settled.to_database_values()
        settlement_columns = (
            "settlement_observation_sha256",
            "settled_at",
            "settlement_fee_dollars",
            "settlement_refund_dollars",
            "gross_settlement_payout_dollars",
            "net_settlement_payout_dollars",
            "fee_net_pnl_dollars",
        )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                "UPDATE paper_trade_accounting SET "
                + ",".join(f"{column}=?" for column in settlement_columns)
                + " WHERE entry_request_id='req-1'",
                tuple(values[column] for column in settlement_columns),
            )
        _seed_settlement_observation(conn)
        conn.execute(
            "UPDATE paper_trade_accounting SET "
            + ",".join(f"{column}=?" for column in settlement_columns)
            + " WHERE entry_request_id='req-1'",
            tuple(values[column] for column in settlement_columns),
        )
        row = conn.execute("SELECT * FROM paper_trade_accounting WHERE entry_request_id='req-1'").fetchone()
        assert PaperAccountingRecord.from_database_row(row) == settled
        with pytest.raises(sqlite3.IntegrityError, match="settlement is immutable"):
            conn.execute("UPDATE paper_trade_accounting SET fee_net_pnl_dollars='0' WHERE entry_request_id='req-1'")
    finally:
        conn.close()


def test_schema_contract_rejects_noncanonical_meta_applied_at(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        for _name, statement in PAPER_ACCOUNTING_TARGET_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            """
            INSERT INTO paper_accounting_schema_meta (
                schema_version, accounting_version, ddl_sha256,
                migration_plan_sha256, applied_at
            ) VALUES (?, ?, ?, ?, '')
            """,
            (
                PAPER_ACCOUNTING_SCHEMA_VERSION,
                PAPER_ACCOUNTING_VERSION,
                PAPER_ACCOUNTING_DDL_SHA256,
                PLAN_SHA,
            ),
        )
        assert not paper_accounting_schema_contract_matches(conn)
    finally:
        conn.rollback()
        conn.close()


def test_initialize_rejects_noncanonical_applied_at_before_creating_artifacts(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        with pytest.raises(ValueError, match="applied_at"):
            initialize_fresh_paper_accounting_schema(
                conn,
                migration_plan_sha256=PLAN_SHA,
                applied_at="",
            )
        artifacts = conn.execute(
            """
            SELECT name
            FROM sqlite_schema
            WHERE tbl_name IN ('paper_accounting_schema_meta', 'paper_trade_accounting')
               OR name GLOB 'paper_accounting_*'
               OR name GLOB 'paper_trade_accounting_*'
            """
        ).fetchall()
        assert artifacts == []
    finally:
        conn.rollback()
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


def test_parent_venue_guard_is_part_of_exact_schema_contract(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        trigger_name = "paper_trade_accounting_parent_venue_guard"
        target_sql = dict(PAPER_ACCOUNTING_TARGET_STATEMENTS)[trigger_name]
        persisted_sql = conn.execute(
            "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?",
            (trigger_name,),
        ).fetchone()[0]
        assert "NEW.trade_id" in target_sql
        assert "NEW.venue" in target_sql
        assert persisted_sql
        assert paper_accounting_schema_contract_matches(conn)

        conn.execute(f"DROP TRIGGER {trigger_name}")
        assert not paper_accounting_schema_contract_matches(conn)
        with pytest.raises(PaperAccountingSchemaError, match="partial"):
            accounting_schema_state(conn)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "rogue_sql",
    (
        "CREATE INDEX rogue_attached_index ON paper_trade_accounting(order_id)",
        """
        CREATE TRIGGER rogue_attached_trigger
        AFTER INSERT ON paper_trade_accounting
        BEGIN
            SELECT 1;
        END
        """,
        "CREATE TABLE paper_accounting_rogue (id INTEGER PRIMARY KEY)",
        """
        CREATE VIEW paper_trade_accounting_rogue_view
        AS SELECT trade_id FROM paper_trades
        """,
        """
        CREATE TRIGGER paper_accounting_mutate_parent
        AFTER INSERT ON paper_trades
        BEGIN
            UPDATE paper_trades
            SET reasoning = 'mutated'
            WHERE trade_id = NEW.trade_id;
        END
        """,
    ),
)
def test_contract_and_state_reject_rogue_accounting_namespace_objects(
    tmp_path: Path,
    rogue_sql: str,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        _install_accounting(conn)
        conn.execute(rogue_sql)
        assert not paper_accounting_schema_contract_matches(conn)
        with pytest.raises(PaperAccountingSchemaError, match="partial"):
            accounting_schema_state(conn)
    finally:
        conn.close()


def test_initialize_rejects_reserved_namespace_artifact(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    conn = _connect(db)
    try:
        conn.execute("CREATE VIEW paper_accounting_rogue AS SELECT 1 AS value")
        with pytest.raises(PaperAccountingSchemaError, match="partial or existing"):
            initialize_fresh_paper_accounting_schema(
                conn,
                migration_plan_sha256=PLAN_SHA,
                applied_at=NOW.isoformat(),
            )
    finally:
        conn.rollback()
        conn.close()


def test_handlers_dispatch_by_persisted_version() -> None:
    calls: list[tuple[str, str]] = []
    handlers = PaperAccountingHandlers(
        entry={1: lambda record: calls.append(("entry", record.entry_request_id))},
        settlement={1: lambda record: calls.append(("settlement", record.trade_id))},
    )
    record = _entry_record()
    handlers.dispatch_entry(record)
    handlers.dispatch_settlement(_settled_record(record))
    assert calls == [("entry", "req-1"), ("settlement", "trade-1")]

    unsupported = replace(record, accounting_version=2, validate=False)
    with pytest.raises(PaperAccountingAdmissionError, match="entry handler.*version 2"):
        handlers.dispatch_entry(unsupported)


def test_handlers_reject_wrong_phase_and_partial_records() -> None:
    calls: list[str] = []
    handlers = PaperAccountingHandlers(
        entry={1: lambda _record: calls.append("entry")},
        settlement={1: lambda _record: calls.append("settlement")},
    )
    entry = _entry_record()
    settled = _settled_record(entry)
    with pytest.raises(PaperAccountingAdmissionError, match="entry.*unsettled"):
        handlers.dispatch_entry(settled)
    with pytest.raises(PaperAccountingAdmissionError, match="settlement.*complete"):
        handlers.dispatch_settlement(entry)
    partial = replace(entry, settlement_fee=D("0"), validate=False)
    with pytest.raises(PaperAccountingAdmissionError, match="invalid settlement record"):
        handlers.dispatch_settlement(partial)
    assert calls == []


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


def test_noop_migration_rechecks_accounting_rows_under_writer_lock(
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
    conn = _connect(db)
    _seed_trade(conn)
    entry = _entry_record()
    _insert_record(conn, entry)
    conn.commit()
    conn.close()
    with open_readonly(db) as conn:
        noop_plan = plan_paper_accounting_schema(conn, db)

    conn = _connect(db)
    _seed_settlement_observation(conn)
    settled_values = _settled_record(entry).to_database_values()
    settlement_columns = (
        "settlement_observation_sha256",
        "settled_at",
        "settlement_fee_dollars",
        "settlement_refund_dollars",
        "gross_settlement_payout_dollars",
        "net_settlement_payout_dollars",
        "fee_net_pnl_dollars",
    )
    conn.execute(
        "UPDATE paper_trade_accounting SET "
        + ",".join(f"{column}=?" for column in settlement_columns)
        + " WHERE entry_request_id='req-1'",
        tuple(settled_values[column] for column in settlement_columns),
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="paper-accounting state drift"):
        apply_paper_accounting_schema(
            db,
            noop_plan,
            reviewed_plan_fingerprint=noop_plan.fingerprint,
        )


def test_apply_uses_mode_rw_and_does_not_recreate_deleted_reviewed_database(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    with open_readonly(db) as conn:
        plan = plan_paper_accounting_schema(conn, db)
    db.unlink()

    with pytest.raises(sqlite3.OperationalError):
        apply_paper_accounting_schema(
            db,
            plan,
            reviewed_plan_fingerprint=plan.fingerprint,
        )
    assert not db.exists()


def test_connections_close_when_foreign_key_enablement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "paper.db"
    _create_gross_v1_database(db)
    with open_readonly(db) as conn:
        plan = plan_paper_accounting_schema(conn, db)

    connection = MagicMock()
    monkeypatch.setattr(migration_module.sqlite3, "connect", lambda *_args, **_kwargs: connection)

    def fail_foreign_keys(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("foreign keys unavailable")

    monkeypatch.setattr(
        migration_module,
        "enable_and_verify_foreign_keys",
        fail_foreign_keys,
    )
    with pytest.raises(RuntimeError, match="foreign keys unavailable"):
        with migration_module.open_readonly(db):
            pass
    assert connection.close.call_count == 1

    connection.reset_mock()
    with pytest.raises(RuntimeError, match="foreign keys unavailable"):
        apply_paper_accounting_schema(
            db,
            plan,
            reviewed_plan_fingerprint=plan.fingerprint,
        )
    assert connection.close.call_count == 1


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
