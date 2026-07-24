from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tasks.kalshi_execution_ledger_collector import (
    CollectorProtocolError,
    KalshiExecutionLedgerCollector,
)
from trading.kalshi_execution_ledger import (
    HISTORICAL_CUTOFF_UNKNOWN,
    UNATTRIBUTED_MANUAL_SOURCE,
    KalshiExecutionLedger,
)


NOW = "2026-07-24T10:45:00Z"


def _order() -> dict[str, object]:
    return {
        "order_id": "order-1",
        "user_id": "user-1",
        "client_order_id": "client-1",
        "ticker": "KXTEST-26JUL-T1",
        "outcome_side": "yes",
        "book_side": "bid",
        "type": "limit",
        "status": "executed",
        "yes_price_dollars": "0.500000",
        "no_price_dollars": "0.500000",
        "fill_count_fp": "1.00",
        "remaining_count_fp": "0.00",
        "initial_count_fp": "1.00",
        "taker_fees_dollars": "0.010000",
        "maker_fees_dollars": "0.000000",
        "taker_fill_cost_dollars": "0.010000",
        "maker_fill_cost_dollars": "0.000000",
        "subaccount_number": 0,
    }


def _fill(fill_id: str) -> dict[str, object]:
    return {
        "fill_id": fill_id,
        "trade_id": fill_id,
        "order_id": "order-1",
        "ticker": "KXTEST-26JUL-T1",
        "market_ticker": "KXTEST-26JUL-T1",
        "outcome_side": "yes",
        "book_side": "bid",
        "count_fp": "1.00",
        "yes_price_dollars": "0.500000",
        "no_price_dollars": "0.500000",
        "is_taker": False,
        "fee_cost": "0.010000",
        "created_time": "2026-07-24T10:45:00Z",
        "ts": 1_753_356_300,
        "subaccount_number": 0,
    }


@dataclass
class FakeReceiptClient:
    pages: list[tuple[list[object], str | None]]
    fail_after_page: int | None = None

    def __post_init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def get_order_receipt(self, order_id: str) -> dict[str, object]:
        self.calls.append(("GET_ORDER", order_id))
        return _order()

    def get_fills_page(
        self,
        *,
        order_id: str,
        cursor: str | None = None,
        **_kwargs: object,
    ) -> tuple[list[object], str | None]:
        self.calls.append(("GET_FILLS", order_id, cursor))
        page_index = 0 if cursor is None else 1
        if self.fail_after_page is not None and page_index >= self.fail_after_page:
            raise RuntimeError("page fetch failed")
        return self.pages[page_index]


def _collector(tmp_path: Path, client: FakeReceiptClient) -> tuple[KalshiExecutionLedgerCollector, KalshiExecutionLedger]:
    ledger = KalshiExecutionLedger(tmp_path / "ledger.db")
    ledger.initialize(applied_at=NOW)
    return KalshiExecutionLedgerCollector(client=client, ledger=ledger, now=lambda: NOW), ledger


def _count(ledger: KalshiExecutionLedger, table: str) -> int:
    import sqlite3

    with sqlite3.connect(ledger.db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _table_rows(ledger: KalshiExecutionLedger, query: str) -> list[tuple[object, ...]]:
    import sqlite3

    with sqlite3.connect(ledger.db_path) as conn:
        return list(conn.execute(query))


def test_collect_order_pages_only_explicit_order_gets_and_never_claims_complete_coverage(
    tmp_path: Path,
) -> None:
    client = FakeReceiptClient(pages=[([_fill("fill-1")], "next"), ([_fill("fill-2")], None)])
    collector, ledger = _collector(tmp_path, client)

    result = collector.collect_order("order-1")

    assert result.order_id == "order-1"
    assert result.pages == 2
    assert result.fill_statuses == ("inserted", "inserted")
    assert result.complete_coverage is False
    assert result.coverage_state == HISTORICAL_CUTOFF_UNKNOWN
    assert result.source_kind == UNATTRIBUTED_MANUAL_SOURCE
    assert result.integrity_ok is True
    assert _count(ledger, "execution_fill_receipts") == 2
    assert _table_rows(
        ledger,
        "SELECT source_kind, fill_coverage_state FROM execution_orders",
    ) == [(UNATTRIBUTED_MANUAL_SOURCE, HISTORICAL_CUTOFF_UNKNOWN)]
    assert client.calls == [
        ("GET_ORDER", "order-1"),
        ("GET_FILLS", "order-1", None),
        ("GET_FILLS", "order-1", "next"),
    ]


def test_collect_order_replay_is_safe_and_never_calls_submission_methods(tmp_path: Path) -> None:
    client = FakeReceiptClient(pages=[([_fill("fill-1")], None)])
    collector, ledger = _collector(tmp_path, client)

    first = collector.collect_order("order-1")
    replay = collector.collect_order("order-1")

    assert first.fill_statuses == ("inserted",)
    assert replay.fill_statuses == ("identical",)
    assert _count(ledger, "execution_fill_receipts") == 1
    assert all(call[0] in {"GET_ORDER", "GET_FILLS"} for call in client.calls)


def test_conflicting_fill_receipt_marks_collection_integrity_invalid(tmp_path: Path) -> None:
    conflicting_fill = _fill("fill-1")
    conflicting_fill["fee_cost"] = "0.020000"
    client = FakeReceiptClient(
        pages=[([_fill("fill-1")], "next"), ([conflicting_fill], None)]
    )
    collector, ledger = _collector(tmp_path, client)

    result = collector.collect_order("order-1")

    assert result.fill_statuses == ("inserted", "conflict")
    assert result.complete_coverage is False
    assert result.integrity_ok is False
    assert _count(ledger, "execution_fill_receipts") == 1
    assert _count(ledger, "execution_conflicts") == 1


def test_collect_order_rejects_unverified_id_without_network_or_hold_mutation(tmp_path: Path) -> None:
    client = FakeReceiptClient(pages=[])
    collector, ledger = _collector(tmp_path, client)

    with pytest.raises(CollectorProtocolError, match="official order ID"):
        collector.collect_order(" ")

    assert client.calls == []
    assert _count(ledger, "execution_orders") == 0


def test_collect_order_rejects_unsupported_attribution_before_network(tmp_path: Path) -> None:
    client = FakeReceiptClient(pages=[])
    collector, ledger = _collector(tmp_path, client)

    with pytest.raises(CollectorProtocolError, match="unsupported order attribution source"):
        collector.collect_order("order-1", source_kind="bot_journal")

    assert client.calls == []
    assert _count(ledger, "execution_orders") == 0


def test_collect_order_quarantines_fill_for_a_different_order(tmp_path: Path) -> None:
    mismatched_fill = _fill("fill-other")
    mismatched_fill["order_id"] = "order-other"
    client = FakeReceiptClient(pages=[([mismatched_fill], None)])
    collector, ledger = _collector(tmp_path, client)

    result = collector.collect_order("order-1")

    assert result.fill_statuses == ("quarantined",)
    assert result.integrity_ok is False
    assert _count(ledger, "execution_orders") == 1
    assert _count(ledger, "execution_fill_receipts") == 0
    assert _table_rows(
        ledger,
        "SELECT receipt_kind, external_id, reason FROM execution_quarantines",
    ) == [("fill", "fill-other", "fill_order_id_mismatch")]


def test_collect_order_quarantines_non_mapping_fill_payload(tmp_path: Path) -> None:
    client = FakeReceiptClient(pages=[(["unexpected-fill-payload"], None)])
    collector, ledger = _collector(tmp_path, client)

    result = collector.collect_order("order-1")

    assert result.fill_statuses == ("quarantined",)
    assert result.integrity_ok is False
    assert _table_rows(
        ledger,
        "SELECT receipt_kind, external_id, reason FROM execution_quarantines",
    ) == [("fill", "", "fill_not_object")]


def test_failed_later_page_never_claims_complete_coverage_and_replay_recovers(tmp_path: Path) -> None:
    client = FakeReceiptClient(
        pages=[([_fill("fill-1")], "next"), ([_fill("fill-2")], None)],
        fail_after_page=1,
    )
    collector, ledger = _collector(tmp_path, client)

    with pytest.raises(RuntimeError, match="page fetch failed"):
        collector.collect_order("order-1")

    assert _count(ledger, "execution_fill_receipts") == 1
    client.fail_after_page = None
    result = collector.collect_order("order-1")

    assert result.fill_statuses == ("identical", "inserted")
    assert result.complete_coverage is False
    assert _count(ledger, "execution_fill_receipts") == 2


def test_repeated_cursor_is_rejected_before_unbounded_fetching(tmp_path: Path) -> None:
    client = FakeReceiptClient(pages=[([_fill("fill-1")], "repeat"), ([_fill("fill-2")], "repeat")])
    collector, _ledger = _collector(tmp_path, client)

    with pytest.raises(CollectorProtocolError, match="repeated fill cursor"):
        collector.collect_order("order-1")

    assert client.calls == [
        ("GET_ORDER", "order-1"),
        ("GET_FILLS", "order-1", None),
        ("GET_FILLS", "order-1", "repeat"),
    ]


def test_opaque_cursor_is_replayed_byte_for_byte(tmp_path: Path) -> None:
    opaque_cursor = " next page cursor "
    client = FakeReceiptClient(
        pages=[([_fill("fill-1")], opaque_cursor), ([_fill("fill-2")], None)]
    )
    collector, _ledger = _collector(tmp_path, client)

    result = collector.collect_order("order-1")

    assert result.fill_statuses == ("inserted", "inserted")
    assert client.calls[-1] == ("GET_FILLS", "order-1", opaque_cursor)
