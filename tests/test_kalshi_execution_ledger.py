from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trading.kalshi_execution_ledger import (
    ExecutionLedgerSchemaError,
    KALSHI_EXECUTION_LEDGER_DB,
    KalshiExecutionLedger,
    LedgerPageResult,
)
from utils.output_paths import DB_STATE_DIR


NOW = "2026-07-24T10:40:00Z"


def _order(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "order_id": "order-1",
        "user_id": "user-1",
        "client_order_id": "client-1",
        "ticker": "KXTEST-26JUL-T1",
        "outcome_side": "yes",
        "book_side": "bid",
        "type": "limit",
        "status": "resting",
        "yes_price_dollars": "0.560000",
        "no_price_dollars": "0.440000",
        "fill_count_fp": "0.00",
        "remaining_count_fp": "2.00",
        "initial_count_fp": "2.00",
        "taker_fees_dollars": "0.000000",
        "maker_fees_dollars": "0.000000",
        "taker_fill_cost_dollars": "0.000000",
        "maker_fill_cost_dollars": "0.000000",
        "created_time": "2026-07-24T10:39:00Z",
        "last_update_time": "2026-07-24T10:39:30Z",
        "subaccount_number": 2,
    }
    payload.update(overrides)
    return payload


def _fill(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "fill_id": "fill-1",
        "trade_id": "fill-1",
        "order_id": "order-1",
        "ticker": "KXTEST-26JUL-T1",
        "market_ticker": "KXTEST-26JUL-T1",
        "outcome_side": "yes",
        "book_side": "bid",
        "count_fp": "2.00",
        "yes_price_dollars": "0.560000",
        "no_price_dollars": "0.440000",
        "is_taker": True,
        "fee_cost": "0.012500",
        "created_time": "2026-07-24T10:39:59Z",
        "ts": 1_753_355_999,
        "subaccount_number": 2,
    }
    payload.update(overrides)
    return payload


def _table_rows(db_path: Path, query: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(db_path) as conn:
        return list(conn.execute(query))


def test_constructor_is_io_free_and_initialize_creates_isolated_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "live_execution_ledger.db"
    ledger = KalshiExecutionLedger(db_path)

    assert not db_path.exists()
    ledger.initialize(applied_at=NOW)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {
            "execution_ledger_schema_meta",
            "execution_orders",
            "execution_order_snapshots",
            "execution_fill_receipts",
            "execution_conflicts",
            "execution_quarantines",
        }
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    ledger_connection = ledger._connect()
    try:
        assert ledger_connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        ledger_connection.close()


def test_default_and_injected_ledger_paths_are_canonicalized() -> None:
    assert KALSHI_EXECUTION_LEDGER_DB == DB_STATE_DIR / "live_execution_ledger.db"

    ledger = KalshiExecutionLedger("relative-ledger.db")

    assert ledger.db_path.is_absolute()


def test_page_persists_immutable_order_and_fixed_point_fill_values(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)

    result = ledger.apply_page(_order(), [_fill()], collected_at=NOW)

    assert result == LedgerPageResult(
        order_status="inserted",
        fill_statuses=("inserted",),
    )
    assert _table_rows(
        ledger.db_path,
        "SELECT order_id, user_id, client_order_id, ticker, outcome_side, book_side, order_type, status, "
        "subaccount_number, fill_coverage_state FROM execution_orders",
    ) == [
        (
            "order-1",
            "user-1",
            "client-1",
            "KXTEST-26JUL-T1",
            "yes",
            "bid",
            "limit",
            "resting",
            2,
            "historical_cutoff_unknown",
        )
    ]
    assert _table_rows(
        ledger.db_path,
        "SELECT fill_id, trade_id, order_id, ticker, market_ticker, outcome_side, book_side, "
        "count_fp, yes_price_dollars, no_price_dollars, fee_cost_dollars, is_taker, "
        "created_time, ts, subaccount_number "
        "FROM execution_fill_receipts",
    ) == [
        (
            "fill-1",
            "fill-1",
            "order-1",
            "KXTEST-26JUL-T1",
            "KXTEST-26JUL-T1",
            "yes",
            "bid",
            "2",
            "0.56",
            "0.44",
            "0.0125",
            1,
            "2026-07-24T10:39:59Z",
            1_753_355_999,
            2,
        )
    ]


def test_page_replay_is_idempotent(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)

    first = ledger.apply_page(_order(), [_fill()], collected_at=NOW)
    replay = ledger.apply_page(_order(), [_fill()], collected_at=NOW)

    assert first.fill_statuses == ("inserted",)
    assert replay == LedgerPageResult(
        order_status="identical",
        fill_statuses=("identical",),
    )
    assert _table_rows(ledger.db_path, "SELECT COUNT(*) FROM execution_order_snapshots") == [(1,)]
    assert _table_rows(ledger.db_path, "SELECT COUNT(*) FROM execution_fill_receipts") == [(1,)]


def test_conflicting_fill_id_is_quarantined_without_overwriting_receipt(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)
    ledger.apply_page(_order(), [_fill()], collected_at=NOW)

    result = ledger.apply_page(_order(), [_fill(fee_cost="0.025")], collected_at=NOW)

    assert result.fill_statuses == ("conflict",)
    assert _table_rows(
        ledger.db_path,
        "SELECT fee_cost_dollars FROM execution_fill_receipts WHERE fill_id = 'fill-1'",
    ) == [("0.0125",)]
    assert _table_rows(
        ledger.db_path,
        "SELECT kind, external_id, reason FROM execution_conflicts",
    ) == [("fill", "fill-1", "payload_hash_conflict")]


def test_malformed_fill_is_quarantined_and_not_accounted(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)

    result = ledger.apply_page(_order(), [_fill(fee_cost="not-a-decimal")], collected_at=NOW)

    assert result.fill_statuses == ("quarantined",)
    assert _table_rows(ledger.db_path, "SELECT COUNT(*) FROM execution_fill_receipts") == [(0,)]
    assert _table_rows(
        ledger.db_path,
        "SELECT receipt_kind, external_id, reason FROM execution_quarantines",
    ) == [("fill", "fill-1", "invalid_fee_cost")]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"count_fp": None}, "invalid_count_fp"),
        ({"yes_price_dollars": 0.56}, "invalid_yes_price_dollars"),
        ({"trade_id": "different-trade"}, "fill_trade_id_mismatch"),
        ({"market_ticker": "KXOTHER-26JUL-T1"}, "fill_market_ticker_mismatch"),
        ({"outcome_side": "maybe"}, "invalid_outcome_side"),
        ({"book_side": "hold"}, "invalid_book_side"),
        ({"outcome_side": "yes", "book_side": "ask"}, "outcome_book_side_mismatch"),
        ({"subaccount_number": 64}, "invalid_subaccount_number"),
    ],
)
def test_current_kalshi_fill_contract_violations_are_quarantined(
    tmp_path: Path,
    overrides: dict[str, object],
    reason: str,
) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)

    result = ledger.apply_page(_order(), [_fill(**overrides)], collected_at=NOW)

    assert result.fill_statuses == ("quarantined",)
    assert _table_rows(ledger.db_path, "SELECT COUNT(*) FROM execution_fill_receipts") == [(0,)]
    assert _table_rows(
        ledger.db_path,
        "SELECT receipt_kind, external_id, reason FROM execution_quarantines",
    ) == [("fill", "fill-1", reason)]


def test_direct_page_rejects_fill_for_any_order_other_than_the_page_order(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)

    result = ledger.apply_page(_order(), [_fill(order_id="order-2")], collected_at=NOW)

    assert result.fill_statuses == ("quarantined",)
    assert _table_rows(ledger.db_path, "SELECT COUNT(*) FROM execution_fill_receipts") == [(0,)]
    assert _table_rows(
        ledger.db_path,
        "SELECT receipt_kind, external_id, reason FROM execution_quarantines",
    ) == [("fill", "fill-1", "fill_order_id_mismatch")]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {
                "ticker": "KXOTHER-26JUL-T1",
                "market_ticker": "KXOTHER-26JUL-T1",
            },
            "fill_order_identity_mismatch",
        ),
        (
            {"outcome_side": "no", "book_side": "ask"},
            "fill_order_identity_mismatch",
        ),
        ({"subaccount_number": 3}, "fill_subaccount_mismatch"),
    ],
)
def test_fill_must_match_the_immutable_order_identity(
    tmp_path: Path,
    overrides: dict[str, object],
    reason: str,
) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)

    result = ledger.apply_page(_order(), [_fill(**overrides)], collected_at=NOW)

    assert result.fill_statuses == ("quarantined",)
    assert _table_rows(ledger.db_path, "SELECT COUNT(*) FROM execution_fill_receipts") == [(0,)]
    assert _table_rows(
        ledger.db_path,
        "SELECT receipt_kind, external_id, reason FROM execution_quarantines",
    ) == [("fill", "fill-1", reason)]


def test_order_identity_drift_conflicts_without_overwriting_the_projection(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)
    ledger.apply_page(_order(), [_fill()], collected_at=NOW)

    result = ledger.apply_page(
        _order(ticker="KXOTHER-26JUL-T1"),
        [_fill()],
        collected_at="2026-07-24T10:41:00Z",
    )

    assert result.order_status == "conflict"
    assert result.fill_statuses == ()
    assert _table_rows(ledger.db_path, "SELECT ticker FROM execution_orders") == [
        ("KXTEST-26JUL-T1",)
    ]
    assert _table_rows(
        ledger.db_path,
        "SELECT kind, external_id, reason FROM execution_conflicts",
    ) == [("order", "order-1", "immutable_order_identity_conflict")]


def test_identical_order_replay_refreshes_observation_time_without_mutating_receipts(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)
    ledger.apply_page(_order(), [_fill()], collected_at=NOW)

    result = ledger.apply_page(
        _order(),
        [_fill()],
        collected_at="2026-07-24T10:41:00Z",
    )

    assert result == LedgerPageResult(order_status="identical", fill_statuses=("identical",))
    assert _table_rows(
        ledger.db_path,
        "SELECT first_collected_at, last_collected_at FROM execution_orders",
    ) == [(NOW, "2026-07-24T10:41:00Z")]


def test_altered_expected_schema_object_is_rejected(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)

    with sqlite3.connect(ledger.db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE execution_fill_receipts")
        conn.execute("CREATE TABLE execution_fill_receipts (fill_id TEXT PRIMARY KEY)")

    with pytest.raises(ExecutionLedgerSchemaError, match="execution ledger schema drift"):
        ledger.initialize(applied_at=NOW)


def test_receipt_tables_reject_direct_mutation_after_collection(tmp_path: Path) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)
    ledger.apply_page(_order(), [_fill()], collected_at=NOW)

    with sqlite3.connect(ledger.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="execution fill receipts are immutable"):
            conn.execute("UPDATE execution_fill_receipts SET fee_cost_dollars = '0.1'")
        with pytest.raises(sqlite3.IntegrityError, match="execution order identity is immutable"):
            conn.execute("UPDATE execution_orders SET ticker = 'KXOTHER-26JUL-T1'")


def test_failed_page_transaction_rolls_back_order_and_fill_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)

    def fail_after_order(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("simulated fill write failure")

    monkeypatch.setattr(ledger, "_record_fill_transaction", fail_after_order)

    with pytest.raises(RuntimeError, match="simulated fill write failure"):
        ledger.apply_page(_order(), [_fill()], collected_at=NOW)

    assert _table_rows(ledger.db_path, "SELECT COUNT(*) FROM execution_orders") == [(0,)]
    assert _table_rows(ledger.db_path, "SELECT COUNT(*) FROM execution_order_snapshots") == [(0,)]
