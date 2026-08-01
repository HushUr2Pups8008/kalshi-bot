"""Behavior contract for the unwired canonical paper-settlement path."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from polymarket.settlement_reconciler import PersistedPositionReconciler
from tests.test_paper_trader import _cfg_module, _make_mock_analysis
from trading.paper_accounting import (
    PAPER_ACCOUNTING_VERSION,
    PaperAccountingAdmissionError,
    PaperAccountingHandlers,
    PaperAccountingRecord,
)
from trading.executor import TradeExecutor
from trading.portfolio import Portfolio, Position
from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    SettlementObservation,
    VoidRefundContract,
    build_settlement_observation,
)
from trading.settlement_store import (
    PAPER_TRADE_FEE_NET_SETTLED_EVENT_KIND,
    SettlementStore,
    settlement_schema_contract_matches,
)
from trading.settlement_economics import (
    KALSHI_FIX_MISC_FEE_RECEIPT_V1,
    SettlementEconomicsBinding,
    SettlementEconomicsContract,
    SettlementEconomicsEvidence,
    canonical_json,
    derive_settlement_cashflows,
    derive_settlement_fee_receipt,
)
from trading.venue import MarketRef, Venue


OBSERVED_AT = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
EFFECTIVE_AT = datetime(2026, 7, 14, 17, 55, tzinfo=timezone.utc)
RULES_VERSION = "official-rules-v1"
SOURCE_ID = "official-api:test"


@pytest.fixture()
def trader_factory(monkeypatch, tmp_path):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)
    monkeypatch.setattr(
        _cfg_module.cfg,
        "enable_fee_net_paper_accounting",
        False,
    )

    import trading.paper_trader as paper_trader_module

    credibility = MagicMock()
    credibility.get_multiplier.return_value = 1.0
    credibility_factory = MagicMock(return_value=credibility)
    monkeypatch.setattr(paper_trader_module, "SourceCredibility", credibility_factory)
    monkeypatch.setattr(paper_trader_module, "trade_log", MagicMock())

    traders = []

    def _make(name: str, **kwargs):
        trader = paper_trader_module.PaperTrader(
            db_path=tmp_path / f"{name}.db",
            startup_context="test",
            **kwargs,
        )
        trader._set_state("notional_bankroll", "500.0")
        traders.append(trader)
        return trader

    yield _make

    for trader in traders:
        trader._conn.close()


def _observation(
    market_ref: MarketRef,
    outcome: MarketOutcome,
    *,
    payload: dict[str, object] | None = None,
    observed_at: datetime = OBSERVED_AT,
    effective_at: datetime = EFFECTIVE_AT,
    void_refund: VoidRefundContract | None = None,
    previous: SettlementObservation | None = None,
    supersedes: str | None = None,
    source_id: str = SOURCE_ID,
) -> SettlementObservation:
    authoritative_payload = payload or {
        "id": market_ref.venue_market_id,
        "result": outcome.value,
        "settled": True,
    }
    return build_settlement_observation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome=outcome.value,
        authoritative_payload=authoritative_payload,
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version=RULES_VERSION,
        source_id=source_id,
        void_refund=void_refund,
        previous_observation=previous,
        supersedes_observation_sha256=supersedes,
    )


def _fee_net_settlement_payload(
    market_ref: MarketRef,
    outcome: MarketOutcome,
    *,
    settlement_fee: str = "0.0137",
) -> dict[str, object]:
    account_party_id = "test-customer-account"
    message = {
        "MarketSettlementReportID": "test-settlement-report-1",
        "NoMarketSettlementPartyIDs": [
            {
                "LongQty": "5",
                "MarketSettlementPartyID": account_party_id,
                "MarketSettlementPartyRole": "24",
                "MiscFees": [
                    {
                        "MiscFeeAmt": settlement_fee,
                        "MiscFeeBasis": "0",
                        "MiscFeeCurr": "USD",
                        "MiscFeeType": "4",
                    }
                ],
                "NoMiscFees": "1",
                "ShortQty": "0",
            }
        ],
        "Symbol": market_ref.venue_market_id,
    }
    message_json = canonical_json(message)
    return {
        "market_id": market_ref.venue_market_id,
        "result": outcome.value,
        "settlement_fee_receipt": {
            "message": message,
            "message_sha256": hashlib.sha256(message_json.encode("utf-8")).hexdigest(),
        },
    }


def _fee_net_evidence(
    trader,
    trade_id: str,
    observation: SettlementObservation,
) -> SettlementEconomicsEvidence:
    accounting_row = trader._conn.execute(
        "SELECT * FROM paper_trade_accounting WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    assert accounting_row is not None
    accounting = PaperAccountingRecord.from_database_row(accounting_row)
    trade = trader._conn.execute(
        "SELECT side FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    assert trade is not None
    contract = SettlementEconomicsContract(
        settlement_fee_receipt_profile=KALSHI_FIX_MISC_FEE_RECEIPT_V1,
        void_refund_policy=None,
    )
    binding = SettlementEconomicsBinding(
        venue=observation.market_ref.venue,
        venue_market_id=observation.market_ref.venue_market_id,
        account_party_id_sha256=hashlib.sha256(
            b"test-customer-account"
        ).hexdigest(),
        contract_fingerprint="contract-v1",
        rules_fingerprint="rules-v1",
        settlement_fingerprint="settlement-v1",
        authoritative_observation_sha256=observation.observation_sha256,
        authoritative_payload_sha256=observation.payload_sha256,
        source_id=observation.source_id,
    )
    receipt = derive_settlement_fee_receipt(
        contract=contract,
        binding=binding,
        source_payload_json=observation.canonical_payload_json,
    )
    cashflows = derive_settlement_cashflows(
        contract=contract,
        binding=binding,
        outcome=observation.outcome,
        held_side=str(trade["side"]).lower(),
        quantity=accounting.quantity,
        entry_price=accounting.price,
        entry_fee=accounting.quote.net_fee,
        void_refund=observation.void_refund,
        fee_receipt=receipt,
    )
    return SettlementEconomicsEvidence(
        contract=contract,
        binding=binding,
        fee_receipt=receipt,
        cashflows=cashflows,
    )


def _record_trade(
    trader,
    market_ref: MarketRef,
    *,
    trade_id: str,
    side: str = "yes",
    yes_price: float = 40.0,
) -> str:
    assert len(trade_id) == 12
    analysis = _make_mock_analysis(
        ticker=market_ref.alias,
        side=side,
        yes_price=yes_price,
        capped_dollars=10.0,
        kelly_dollars=10.0,
    )
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id
    return _record_analysis(trader, analysis, trade_id=trade_id)


def _record_analysis(
    trader,
    analysis,
    *,
    trade_id: str,
    entry_request_id: str | None = None,
) -> str:
    assert len(trade_id) == 12
    with (
        patch("trading.paper_trader.uuid.uuid4", return_value=trade_id),
        patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}),
    ):
        return trader.record_trade(analysis, entry_request_id=entry_request_id)


def test_fresh_database_installs_disabled_accounting_beside_unchanged_gross_v1(
    trader_factory,
):
    trader = trader_factory("accounting-fresh")

    assert trader.paper_accounting_schema_present is True
    assert settlement_schema_contract_matches(trader._conn) is True
    meta = trader._conn.execute(
        """
        SELECT schema_version, accounting_version
        FROM paper_accounting_schema_meta
        """
    ).fetchone()
    assert tuple(meta) == (1, PAPER_ACCOUNTING_VERSION)
    assert trader._paper_accounting_handlers is not None


def test_false_mode_keeps_gross_entry_and_leaves_accounting_table_empty(
    trader_factory,
):
    trader = trader_factory("accounting-false-parity")
    market_ref = MarketRef(Venue.KALSHI, "KX-ACCT-FALSE", "KX-ACCT-FALSE")

    trade_id = _record_trade(trader, market_ref, trade_id="acctfalse001")

    assert trade_id == "acctfalse001"
    assert trader.get_notional_bankroll() == pytest.approx(490.0)
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 0


def test_false_mode_existing_database_does_not_auto_install_accounting_schema(
    trader_factory,
):
    first = trader_factory("accounting-legacy")
    with first._conn:
        first._conn.execute("DROP TABLE paper_trade_accounting")
        first._conn.execute("DROP TABLE paper_accounting_schema_meta")
    first._conn.close()

    reopened = trader_factory("accounting-legacy")

    assert reopened.paper_accounting_schema_present is False
    assert reopened._conn.execute(
        """
        SELECT COUNT(*) FROM sqlite_schema
        WHERE name IN ('paper_trade_accounting', 'paper_accounting_schema_meta')
        """
    ).fetchone()[0] == 0
    assert settlement_schema_contract_matches(reopened._conn) is True


def test_enabled_accounting_dispatches_complete_entry_before_commit(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-enabled")
    seeded._conn.close()
    monkeypatch.setattr(
        _cfg_module.cfg,
        "enable_fee_net_paper_accounting",
        True,
    )

    entries = []
    handlers = PaperAccountingHandlers(
        entry={PAPER_ACCOUNTING_VERSION: entries.append},
        settlement={PAPER_ACCOUNTING_VERSION: lambda _record: None},
    )
    trader = trader_factory(
        "accounting-enabled",
        paper_accounting_handlers=handlers,
    )
    analysis = _make_mock_analysis(ticker="KX-ACCT-ENABLED")
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.capped_dollars = 11.0
    before_bankroll = trader.get_notional_bankroll()

    with pytest.raises(PaperAccountingAdmissionError, match="entry_request_id"):
        trader.record_trade(analysis)

    trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctenabled1",
        entry_request_id="candidate-stable-request-1",
    )

    assert trade_id == "acctenabled1"
    assert len(entries) == 1
    record = entries[0]
    assert record.entry_request_id == "candidate-stable-request-1"
    assert record.trade_id == trade_id
    assert record.order_id == "paper-order:candidate-stable-request-1"
    assert record.fill_id == "paper-fill:candidate-stable-request-1:0"
    assert record.gross_entry_debit > 0
    assert record.net_entry_debit > record.gross_entry_debit
    assert trader.get_notional_bankroll() == pytest.approx(
        before_bankroll - float(record.net_entry_debit)
    )
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 1


def test_enabled_accounting_installs_connection_bound_sqlite_handlers(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-auto-handlers")
    seeded._conn.close()
    monkeypatch.setattr(
        _cfg_module.cfg,
        "enable_fee_net_paper_accounting",
        True,
    )

    trader = trader_factory("accounting-auto-handlers")
    analysis = _make_mock_analysis(ticker="KX-ACCT-AUTO")
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.capped_dollars = 11.0

    trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctenabled2",
        entry_request_id="candidate-stable-request-2",
    )

    assert trade_id == "acctenabled2"
    row = trader._conn.execute(
        "SELECT * FROM paper_trade_accounting WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    assert row is not None
    assert row["entry_request_id"] == "candidate-stable-request-2"


def test_fee_net_entry_rejects_second_open_trade_for_same_canonical_market(
    trader_factory,
    monkeypatch,
):
    trader = trader_factory("accounting-one-fee-net-trade-per-market")
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    market_ref = MarketRef(
        Venue.KALSHI,
        "KX-ACCT-ONE-FEE-NET",
        "KX-ACCT-ONE-FEE-NET",
    )
    analysis = _make_mock_analysis(ticker=market_ref.alias, capped_dollars=11.0)
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id
    first_trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctfee00006",
        entry_request_id="candidate-stable-request-one-market-1",
    )

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="one open trade per canonical market",
    ):
        _record_analysis(
            trader,
            analysis,
            trade_id="acctfee00007",
            entry_request_id="candidate-stable-request-one-market-2",
        )

    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trades"
    ).fetchone()[0] == 1
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 1
    assert trader._conn.execute(
        "SELECT trade_id FROM paper_trades"
    ).fetchone()[0] == first_trade_id


def test_fee_net_parent_marker_quarantines_nonpersisting_entry_handler(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-parent-marker")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    entries = []
    handlers = PaperAccountingHandlers(
        entry={PAPER_ACCOUNTING_VERSION: entries.append},
        settlement={PAPER_ACCOUNTING_VERSION: lambda _record: None},
    )
    trader = trader_factory(
        "accounting-parent-marker",
        paper_accounting_handlers=handlers,
    )
    market_ref = MarketRef(
        Venue.KALSHI,
        "KX-ACCT-PARENT-MARKER",
        "KX-ACCT-PARENT-MARKER",
    )
    analysis = _make_mock_analysis(ticker=market_ref.alias, capped_dollars=11.0)
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id

    trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctmarker01",
        entry_request_id="paper-entry:v1:active-test:lc-" + "k" * 32,
    )

    assert [record.trade_id for record in entries] == [trade_id]
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 0
    marker = trader._conn.execute(
        "SELECT fee_net_accounting_version FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    assert marker["fee_net_accounting_version"] == PAPER_ACCOUNTING_VERSION
    with pytest.raises(
        sqlite3.IntegrityError,
        match="fee_net_accounting_version is immutable",
    ):
        trader._conn.execute(
            """
            UPDATE paper_trades
            SET fee_net_accounting_version=NULL
            WHERE trade_id=?
            """,
            (trade_id,),
        )
    trader._conn.rollback()
    assert trader._conn.execute(
        "SELECT fee_net_accounting_version FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()["fee_net_accounting_version"] == PAPER_ACCOUNTING_VERSION
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", False)
    before = _financial_snapshot(trader)
    observation = _observation(market_ref, MarketOutcome.YES)

    assert _resolve(trader, observation) is False

    _assert_quarantined_without_financial_change(trader, observation, before)
    quarantine = trader._conn.execute(
        "SELECT reason_code, details_json FROM paper_settlement_quarantine"
    ).fetchone()
    assert quarantine["reason_code"] == "fee_net_settlement_evidence_unavailable"
    assert json.loads(quarantine["details_json"]) == {"trade_ids": [trade_id]}


def test_fee_net_parent_marker_rejects_non_immutable_trigger(
    trader_factory,
):
    import trading.paper_trader as paper_trader_module

    seeded = trader_factory("accounting-parent-marker-trigger")
    trigger_name = paper_trader_module._FEE_NET_ACCOUNTING_MARKER_TRIGGER_NAME
    seeded._conn.execute(f"DROP TRIGGER {trigger_name}")
    seeded._conn.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE OF fee_net_accounting_version ON paper_trades
        WHEN NEW.fee_net_accounting_version IS NOT OLD.fee_net_accounting_version
        BEGIN
            SELECT 'fee_net_accounting_version is immutable';
        END
        """
    )
    seeded._conn.commit()
    seeded._conn.close()

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="marker immutability trigger does not match contract",
    ):
        trader_factory("accounting-parent-marker-trigger")


def test_fee_net_entry_rolls_back_if_handler_replaces_parent_marker(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-parent-marker-replace")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)

    def replace_parent_without_marker(record):
        columns = [
            str(row["name"])
            for row in trader._conn.execute("PRAGMA table_info(paper_trades)")
        ]
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        selected_columns = ", ".join(
            "NULL"
            if column == "fee_net_accounting_version"
            else f'"{column}"'
            for column in columns
        )
        trader._conn.execute(
            f"""
            INSERT OR REPLACE INTO paper_trades ({quoted_columns})
            SELECT {selected_columns}
            FROM paper_trades
            WHERE trade_id=?
            """,
            (record.trade_id,),
        )

    handlers = PaperAccountingHandlers(
        entry={PAPER_ACCOUNTING_VERSION: replace_parent_without_marker},
        settlement={PAPER_ACCOUNTING_VERSION: lambda _record: None},
    )
    trader = trader_factory(
        "accounting-parent-marker-replace",
        paper_accounting_handlers=handlers,
    )
    analysis = _make_mock_analysis(ticker="KX-ACCT-PARENT-REPLACE", capped_dollars=11.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.market.market_id = analysis.market.ticker
    analysis.market.venue_market_id = analysis.market.ticker
    before = _financial_snapshot(trader)

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="parent marker changed during accounting dispatch",
    ):
        _record_analysis(
            trader,
            analysis,
            trade_id="acctreplace1",
            entry_request_id="paper-entry:v1:active-test:lc-" + "r" * 32,
        )

    assert _financial_snapshot(trader) == before
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("transaction_control", ("commit", "sql", "clear_authorizer"))
def test_fee_net_entry_rejects_handler_transaction_control_before_parent_rollback(
    trader_factory,
    monkeypatch,
    transaction_control,
):
    seeded = trader_factory("accounting-parent-marker-commit")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)

    def replace_parent_without_marker_and_control_transaction(record):
        columns = [
            str(row["name"])
            for row in trader._conn.execute("PRAGMA table_info(paper_trades)")
        ]
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        selected_columns = ", ".join(
            "NULL"
            if column == "fee_net_accounting_version"
            else f'"{column}"'
            for column in columns
        )
        trader._conn.execute(
            f"""
            INSERT OR REPLACE INTO paper_trades ({quoted_columns})
            SELECT {selected_columns}
            FROM paper_trades
            WHERE trade_id=?
            """,
            (record.trade_id,),
        )
        if transaction_control == "commit":
            trader._conn.commit()
        elif transaction_control == "sql":
            trader._conn.execute("COMMIT")
        else:
            trader._conn.set_authorizer(None)
            trader._conn.commit()

    handlers = PaperAccountingHandlers(
        entry={
            PAPER_ACCOUNTING_VERSION: replace_parent_without_marker_and_control_transaction,
        },
        settlement={PAPER_ACCOUNTING_VERSION: lambda _record: None},
    )
    trader = trader_factory(
        "accounting-parent-marker-commit",
        paper_accounting_handlers=handlers,
    )
    analysis = _make_mock_analysis(ticker="KX-ACCT-PARENT-COMMIT", capped_dollars=11.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.market.market_id = analysis.market.ticker
    analysis.market.venue_market_id = analysis.market.ticker
    before = _financial_snapshot(trader)

    with pytest.raises(PaperAccountingAdmissionError):
        _record_analysis(
            trader,
            analysis,
            trade_id="acctcommit01",
            entry_request_id="paper-entry:v1:active-test:lc-" + "s" * 32,
        )

    assert _financial_snapshot(trader) == before
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 0
    db_path = trader.db_path
    trader._conn.close()
    with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True) as reader:
        assert reader.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0


def test_fee_net_entry_rejects_unguarded_sqlite_connection(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-unguarded-connection")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    trader = trader_factory("accounting-unguarded-connection")
    original_connection = trader._conn
    replacement_connection = sqlite3.connect(trader.db_path)
    replacement_connection.row_factory = sqlite3.Row
    replacement_connection.execute("PRAGMA foreign_keys=ON")
    original_connection.close()
    trader._conn = replacement_connection

    def replace_parent_without_marker_and_commit(record):
        columns = [
            str(row["name"])
            for row in trader._conn.execute("PRAGMA table_info(paper_trades)")
        ]
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        selected_columns = ", ".join(
            "NULL"
            if column == "fee_net_accounting_version"
            else f'"{column}"'
            for column in columns
        )
        trader._conn.execute(
            f"""
            INSERT OR REPLACE INTO paper_trades ({quoted_columns})
            SELECT {selected_columns}
            FROM paper_trades
            WHERE trade_id=?
            """,
            (record.trade_id,),
        )
        trader._conn.set_authorizer(None)
        trader._conn.commit()

    trader._paper_accounting_handlers = PaperAccountingHandlers(
        entry={
            PAPER_ACCOUNTING_VERSION: replace_parent_without_marker_and_commit,
        },
        settlement={PAPER_ACCOUNTING_VERSION: lambda _record: None},
    )
    analysis = _make_mock_analysis(ticker="KX-ACCT-UNGUARDED", capped_dollars=11.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.market.market_id = analysis.market.ticker
    analysis.market.venue_market_id = analysis.market.ticker
    before = _financial_snapshot(trader)

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="guarded SQLite connection",
    ):
        _record_analysis(
            trader,
            analysis,
            trade_id="acctguard001",
            entry_request_id="paper-entry:v1:active-test:lc-" + "u" * 32,
        )

    assert _financial_snapshot(trader) == before
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0


def test_fee_net_entry_preserves_existing_connection_authorizer(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-authorizer")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    trader = trader_factory("accounting-authorizer")
    observed_actions: list[int] = []

    def existing_authorizer(action_code, _arg1, _arg2, _database, _trigger):
        observed_actions.append(action_code)
        return sqlite3.SQLITE_OK

    trader._conn.set_authorizer(existing_authorizer)
    analysis = _make_mock_analysis(ticker="KX-ACCT-AUTHORIZER", capped_dollars=11.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.market.market_id = analysis.market.ticker
    analysis.market.venue_market_id = analysis.market.ticker

    _record_analysis(
        trader,
        analysis,
        trade_id="acctauth0001",
        entry_request_id="paper-entry:v1:active-test:lc-" + "a" * 32,
    )
    assert observed_actions
    observed_actions.clear()
    trader._conn.execute("SELECT value FROM bot_state WHERE key='notional_bankroll'")

    assert observed_actions


def test_runtime_cli_rejects_injected_fee_net_handlers_before_opening_database(
    monkeypatch,
    tmp_path: Path,
):
    import trading.paper_trader as paper_trader_module

    handlers = PaperAccountingHandlers(
        entry={PAPER_ACCOUNTING_VERSION: lambda _record: None},
        settlement={PAPER_ACCOUNTING_VERSION: lambda _record: None},
    )

    with patch(
        "trading.paper_trader.sqlite3.connect",
        side_effect=AssertionError("SQLite opened"),
    ):
        with pytest.raises(ValueError, match="custom paper accounting handlers"):
            paper_trader_module.PaperTrader(
                db_path=tmp_path / "injected-handler.db",
                startup_context="cli",
                paper_accounting_handlers=handlers,
                paper_cohort_storage_root=tmp_path,
            )


def test_fee_net_entry_retry_is_idempotent_without_second_debit_or_log(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-entry-retry")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    trader = trader_factory("accounting-entry-retry")
    analysis = _make_mock_analysis(ticker="KX-ACCT-RETRY", capped_dollars=12.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    entry_request_id = "paper-entry:v1:active-test:lc-" + "a" * 32

    first_trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctretry001",
        entry_request_id=entry_request_id,
    )
    bankroll_after_first = trader.get_notional_bankroll()

    with (
        patch("trading.paper_trader.uuid.uuid4", return_value="acctretry002"),
        patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}),
    ):
        replay = trader.record_trade_result(
            analysis,
            entry_request_id=entry_request_id,
        )

    assert replay.trade_id == first_trade_id
    assert replay.created is False
    assert trader.get_notional_bankroll() == pytest.approx(bankroll_after_first)
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 1
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 1
    assert [position.trade_id for position in trader.portfolio.open_positions()] == [
        first_trade_id
    ]
    import trading.paper_trader as paper_trader_module

    paper_trader_module.trade_log.log_paper_trade.assert_called_once()


def test_fee_net_entry_rejects_reused_identity_with_changed_terms(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-entry-conflict")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    trader = trader_factory("accounting-entry-conflict")
    analysis = _make_mock_analysis(ticker="KX-ACCT-CONFLICT", capped_dollars=12.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    entry_request_id = "paper-entry:v1:active-test:lc-" + "b" * 32

    first_trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctconflict",
        entry_request_id=entry_request_id,
    )
    before_bankroll = trader.get_notional_bankroll()
    analysis.executed_price_cents += 1

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="immutable execution intent",
    ):
        _record_analysis(
            trader,
            analysis,
            trade_id="acctconflic2",
            entry_request_id=entry_request_id,
        )

    assert trader.get_notional_bankroll() == pytest.approx(before_bankroll)
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 1
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 1
    assert [position.trade_id for position in trader.portfolio.open_positions()] == [
        first_trade_id
    ]


def test_fee_net_entry_requires_active_cohort_outside_test_context(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-entry-cohort-gate")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    trader = trader_factory("accounting-entry-cohort-gate")
    trader._startup_context = "runtime"

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="manifest-bound active paper cohort",
    ):
        trader._require_fee_net_active_cohort(
            "paper-entry:v1:active-test:lc-" + "i" * 32,
        )

    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 0


def test_fee_net_runtime_entry_requires_matching_active_cohort_identity(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-entry-active-cohort")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    monkeypatch.setattr(_cfg_module.cfg, "paper_cohort_id", "active-test")
    trader = trader_factory("accounting-entry-active-cohort")
    trader._startup_context = "runtime"
    trader._paper_cohort_binding = SimpleNamespace(
        cohort_type="active",
        cohort=SimpleNamespace(cohort_id="active-test"),
    )
    analysis = _make_mock_analysis(ticker="KX-ACCT-ACTIVE-COHORT")
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.capped_dollars = 11.0

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="identity does not match its active cohort",
    ):
        trader._require_fee_net_active_cohort(
            "paper-entry:v1:other-cohort:lc-" + "j" * 32,
        )

    trader._require_fee_net_active_cohort(
        "paper-entry:v1:active-test:lc-" + "j" * 32,
    )

    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 0


def test_fee_net_runtime_entry_stays_blocked_after_post_init_flag_change(
    trader_factory,
    monkeypatch,
):
    trader = trader_factory("accounting-runtime-entry-block")
    trader._startup_context = "runtime"
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    analysis = _make_mock_analysis(ticker="KX-ACCT-RUNTIME-BLOCK", capped_dollars=11.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    before = _financial_snapshot(trader)

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="authoritative settlement fee evidence",
    ):
        _record_analysis(
            trader,
            analysis,
            trade_id="acctblock001",
            entry_request_id="paper-entry:v1:active-test:lc-" + "j" * 32,
        )

    assert _financial_snapshot(trader) == before
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 0


def test_fee_net_runtime_startup_stays_blocked_until_receipt_settlement_exists(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    from trading.paper_trader import PaperTrader

    db_path = tmp_path / "paper-trades.db"
    with pytest.raises(
        PaperAccountingAdmissionError,
        match="authoritative settlement fee evidence",
    ):
        PaperTrader(
            db_path=db_path,
            startup_context="runtime",
        )
    assert not db_path.exists()


def test_fee_net_entry_handler_failure_rolls_back_parent_trade_and_bankroll(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-entry-rollback")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    observed_parent_row_counts = []

    def fail_after_parent_insert(_record):
        observed_parent_row_counts.append(
            trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        )
        raise RuntimeError("injected entry handler failure")

    handlers = PaperAccountingHandlers(
        entry={PAPER_ACCOUNTING_VERSION: fail_after_parent_insert},
        settlement={PAPER_ACCOUNTING_VERSION: lambda _record: None},
    )
    trader = trader_factory(
        "accounting-entry-rollback",
        paper_accounting_handlers=handlers,
    )
    analysis = _make_mock_analysis(ticker="KX-ACCT-ROLLBACK", capped_dollars=12.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    before_bankroll = trader.get_notional_bankroll()

    trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctrollback",
        entry_request_id="paper-entry:v1:active-test:lc-" + "c" * 32,
    )

    assert trade_id == ""
    assert observed_parent_row_counts == [1]
    assert trader.get_notional_bankroll() == pytest.approx(before_bankroll)
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_trade_accounting"
    ).fetchone()[0] == 0
    assert trader.portfolio.open_positions() == []


def test_executor_persists_lifecycle_key_and_hides_fee_net_replay(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-executor-path")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    monkeypatch.setattr(_cfg_module.cfg, "paper_cohort_id", "active-test")
    trader = trader_factory("accounting-executor-path")
    executor = TradeExecutor(MagicMock(), trader)
    analysis = _make_mock_analysis(ticker="KX-ACCT-EXECUTOR", capped_dollars=12.0)
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.signal_meta = {"lifecycle_id": "lc-" + "d" * 32}

    with (
        patch("trading.paper_trader.uuid.uuid4", return_value="acctexecutor"),
        patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}),
    ):
        first_trade_id = asyncio.run(executor._execute_paper(analysis))
    replay_trade_id = asyncio.run(executor._execute_paper(analysis))

    assert first_trade_id == "acctexecutor"
    assert replay_trade_id == ""
    row = trader._conn.execute(
        """
        SELECT entry_request_id, trade_id
        FROM paper_trade_accounting
        """
    ).fetchone()
    assert tuple(row) == (
        "paper-entry:v1:active-test:lc-" + "d" * 32,
        first_trade_id,
    )
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 1


def test_fee_net_entry_survives_disabled_restart_without_new_ledger_rows(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-disabled-restart")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    enabled = trader_factory("accounting-disabled-restart")
    fee_net_analysis = _make_mock_analysis(
        ticker="KX-ACCT-RESTART-FEE",
        capped_dollars=12.0,
    )
    fee_net_analysis.venue = Venue.KALSHI.value
    fee_net_analysis.market.venue = Venue.KALSHI.value
    fee_net_trade_id = _record_analysis(
        enabled,
        fee_net_analysis,
        trade_id="acctrestart1",
        entry_request_id="paper-entry:v1:active-test:lc-" + "e" * 32,
    )
    enabled._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", False)

    restarted = trader_factory("accounting-disabled-restart")
    gross_analysis = _make_mock_analysis(
        ticker="KX-ACCT-RESTART-GROSS",
        capped_dollars=12.0,
    )
    gross_analysis.venue = Venue.KALSHI.value
    gross_analysis.market.venue = Venue.KALSHI.value
    gross_trade_id = _record_analysis(
        restarted,
        gross_analysis,
        trade_id="acctrestart2",
        entry_request_id="paper-entry:v1:active-test:lc-" + "f" * 32,
    )

    assert fee_net_trade_id == "acctrestart1"
    assert gross_trade_id == "acctrestart2"
    accounting_rows = restarted._conn.execute(
        "SELECT trade_id FROM paper_trade_accounting ORDER BY trade_id"
    ).fetchall()
    assert [row["trade_id"] for row in accounting_rows] == [fee_net_trade_id]
    assert restarted._paper_accounting_handlers is not None


def test_fee_net_entry_quarantines_after_disabled_restart_without_gross_settlement(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-settlement-disabled-restart")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    enabled = trader_factory("accounting-settlement-disabled-restart")
    market_ref = MarketRef(
        Venue.KALSHI,
        "KX-ACCT-SETTLE-FEE",
        "KX-ACCT-SETTLE-FEE",
    )
    analysis = _make_mock_analysis(
        ticker=market_ref.alias,
        capped_dollars=12.0,
    )
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id
    fee_net_trade_id = _record_analysis(
        enabled,
        analysis,
        trade_id="acctfee00001",
        entry_request_id="paper-entry:v1:active-test:lc-" + "f" * 32,
    )
    enabled._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", False)

    restarted = trader_factory("accounting-settlement-disabled-restart")
    gross_analysis = _make_mock_analysis(
        ticker=market_ref.alias,
        capped_dollars=10.0,
    )
    gross_analysis.venue = market_ref.venue.value
    gross_analysis.market.venue = market_ref.venue.value
    gross_analysis.market.market_id = market_ref.alias
    gross_analysis.market.venue_market_id = market_ref.venue_market_id
    gross_trade_id = _record_analysis(
        restarted,
        gross_analysis,
        trade_id="acctfee00002",
    )
    before = _financial_snapshot(restarted)
    observation = _observation(market_ref, MarketOutcome.YES)

    assert _resolve(restarted, observation) is False

    _assert_quarantined_without_financial_change(restarted, observation, before)
    assert {row[0] for row in before["trades"]} == {
        fee_net_trade_id,
        gross_trade_id,
    }
    accounting = restarted._conn.execute(
        """
        SELECT settlement_observation_sha256, settled_at, settlement_fee_dollars,
               settlement_refund_dollars, gross_settlement_payout_dollars,
               net_settlement_payout_dollars, fee_net_pnl_dollars
        FROM paper_trade_accounting
        WHERE trade_id=?
        """,
        (fee_net_trade_id,),
    ).fetchone()
    assert tuple(accounting) == (None, None, None, None, None, None, None)
    quarantine = restarted._conn.execute(
        """
        SELECT reason_code, details_json
        FROM paper_settlement_quarantine
        """
    ).fetchone()
    assert quarantine["reason_code"] == "fee_net_settlement_evidence_unavailable"
    assert json.loads(quarantine["details_json"]) == {"trade_ids": [fee_net_trade_id]}


def test_fee_net_entry_blocks_legacy_gross_settlement_after_disabled_restart(
    trader_factory,
    monkeypatch,
):
    seeded = trader_factory("accounting-legacy-settlement-disabled-restart")
    seeded._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    enabled = trader_factory("accounting-legacy-settlement-disabled-restart")
    market_ref = MarketRef(
        Venue.KALSHI,
        "KX-ACCT-LEGACY-FEE",
        "KX-ACCT-LEGACY-FEE",
    )
    analysis = _make_mock_analysis(
        ticker=market_ref.alias,
        capped_dollars=12.0,
    )
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id
    fee_net_trade_id = _record_analysis(
        enabled,
        analysis,
        trade_id="acctfee00003",
        entry_request_id="paper-entry:v1:active-test:lc-" + "g" * 32,
    )
    enabled._conn.close()
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", False)

    restarted = trader_factory("accounting-legacy-settlement-disabled-restart")
    before = _financial_snapshot(restarted)

    with pytest.raises(
        PaperAccountingAdmissionError,
        match="fee-net paper settlements require authoritative fee evidence",
    ):
        asyncio.run(restarted.resolve_market(market_ref.alias, True))

    assert _financial_snapshot(restarted) == before
    accounting = restarted._conn.execute(
        """
        SELECT settlement_observation_sha256, settled_at, settlement_fee_dollars,
               settlement_refund_dollars, gross_settlement_payout_dollars,
               net_settlement_payout_dollars, fee_net_pnl_dollars
        FROM paper_trade_accounting
        WHERE trade_id=?
        """,
        (fee_net_trade_id,),
    ).fetchone()
    assert tuple(accounting) == (None, None, None, None, None, None, None)


def test_fee_net_entry_settles_atomically_with_exact_typed_evidence(
    trader_factory,
    monkeypatch,
):
    trader = trader_factory("accounting-settlement-evidence")
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    market_ref = MarketRef(
        Venue.KALSHI,
        "KX-ACCT-SETTLE-EVIDENCE",
        "KX-ACCT-SETTLE-EVIDENCE",
    )
    analysis = _make_mock_analysis(ticker=market_ref.alias, capped_dollars=12.0)
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id
    trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctfee00004",
        entry_request_id="candidate-stable-request-settlement-evidence",
    )
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", False)
    observation = _observation(
        market_ref,
        MarketOutcome.YES,
        payload=_fee_net_settlement_payload(market_ref, MarketOutcome.YES),
        source_id="kalshi-fix-market-settlement-v1",
    )
    evidence = _fee_net_evidence(trader, trade_id, observation)
    before = _financial_snapshot(trader)

    assert _resolve(
        trader,
        observation,
        fee_net_evidence_by_trade_id={trade_id: evidence},
    ) is True

    accounting = trader._conn.execute(
        """
        SELECT settlement_observation_sha256, settlement_fee_dollars,
               settlement_refund_dollars, gross_settlement_payout_dollars,
               net_settlement_payout_dollars, fee_net_pnl_dollars
        FROM paper_trade_accounting
        WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    assert accounting["settlement_observation_sha256"] == observation.observation_sha256
    assert accounting["settlement_fee_dollars"] == str(evidence.cashflows.settlement_fee)
    assert accounting["settlement_refund_dollars"] == str(evidence.cashflows.settlement_refund)
    assert accounting["gross_settlement_payout_dollars"] == str(evidence.cashflows.gross_payout)
    assert accounting["net_settlement_payout_dollars"] == str(evidence.cashflows.net_payout)
    entry = PaperAccountingRecord.from_database_row(
        trader._conn.execute(
            "SELECT * FROM paper_trade_accounting WHERE trade_id=?",
            (trade_id,),
        ).fetchone()
    )
    assert Decimal(accounting["fee_net_pnl_dollars"]) == (
        evidence.cashflows.net_payout - entry.net_entry_debit
    )
    observation_row = trader._conn.execute(
        """
        SELECT gross_payout_cents
        FROM paper_settlement_observations
        WHERE observation_sha256=?
        """,
        (observation.observation_sha256,),
    ).fetchone()
    assert Decimal(observation_row["gross_payout_cents"]) == (
        evidence.cashflows.gross_payout * Decimal("100")
    )
    assert _bankroll_cents(trader) == (
        before["bankroll_cents"] + evidence.cashflows.net_payout * Decimal("100")
    )
    parent = trader._conn.execute(
        """
        SELECT cost_dollars, pnl_dollars, gross_payout_cents, gross_pnl_cents
        FROM paper_trades
        WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    assert Decimal(str(parent["cost_dollars"])) == entry.net_entry_debit
    assert Decimal(str(parent["pnl_dollars"])) == entry.fee_net_pnl
    assert Decimal(parent["gross_payout_cents"]) == (
        evidence.cashflows.gross_payout * Decimal("100")
    )
    assert Decimal(parent["gross_pnl_cents"]) == (
        evidence.cashflows.gross_payout - entry.gross_entry_debit
    ) * Decimal("100")
    outbox = trader._conn.execute(
        """
        SELECT event_version, event_kind, payload_json
        FROM paper_settlement_outbox
        WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    assert outbox["event_version"] == 1
    assert outbox["event_kind"] == PAPER_TRADE_FEE_NET_SETTLED_EVENT_KIND
    payload = json.loads(outbox["payload_json"])
    assert Decimal(payload["net_settlement_payout_cents"]) == (
        evidence.cashflows.net_payout * Decimal("100")
    )
    assert Decimal(payload["fee_net_pnl_cents"]) == entry.fee_net_pnl * Decimal("100")
    assert Decimal(payload["net_entry_debit_cents"]) == entry.net_entry_debit * Decimal("100")
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_settlement_quarantine").fetchone()[0] == 0
    with SettlementStore(trader.db_path, read_only=True) as settlement_store:
        check = settlement_store.conservation(now=datetime.now(timezone.utc))
    assert check.ok, check.failures

    from tasks.calibration_task import CalibrationTask
    from tasks.settlement_outbox_task import SettlementOutboxTask

    trade_logger = MagicMock()
    outbox_task = SettlementOutboxTask(
        db_path=trader.db_path,
        calibration_task=CalibrationTask(),
        trade_logger=trade_logger,
        clock=lambda: datetime.now(timezone.utc),
        token_factory=lambda: "fee-net-settlement-worker",
        lease_seconds=60,
    )
    assert asyncio.run(outbox_task.run_once(limit=100)) == 4
    resolution_kwargs = trade_logger.log_paper_resolution.call_args.kwargs
    assert resolution_kwargs["pnl_dollars"] == pytest.approx(float(entry.fee_net_pnl))
    assert resolution_kwargs["bankroll_delta_dollars"] == pytest.approx(
        float(entry.net_settlement_payout)
    )
    with SettlementStore(trader.db_path, read_only=True) as settlement_store:
        assert settlement_store.canonical_delivery_complete_trade_ids(
            now=datetime.now(timezone.utc)
        ) == (trade_id,)


def test_fee_net_typed_evidence_rejects_wrong_observation_binding(
    trader_factory,
    monkeypatch,
):
    trader = trader_factory("accounting-settlement-evidence-binding")
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", True)
    market_ref = MarketRef(
        Venue.KALSHI,
        "KX-ACCT-SETTLE-BINDING",
        "KX-ACCT-SETTLE-BINDING",
    )
    analysis = _make_mock_analysis(ticker=market_ref.alias, capped_dollars=12.0)
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id
    trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctfee00005",
        entry_request_id="candidate-stable-request-settlement-binding",
    )
    monkeypatch.setattr(_cfg_module.cfg, "enable_fee_net_paper_accounting", False)
    observation = _observation(
        market_ref,
        MarketOutcome.YES,
        payload=_fee_net_settlement_payload(market_ref, MarketOutcome.YES),
        source_id="kalshi-fix-market-settlement-v1",
    )
    evidence = _fee_net_evidence(trader, trade_id, observation)
    invalid_evidence = replace(
        evidence,
        binding=replace(
            evidence.binding,
            authoritative_observation_sha256="b" * 64,
        ),
    )
    before = _financial_snapshot(trader)

    assert _resolve(
        trader,
        observation,
        fee_net_evidence_by_trade_id={trade_id: invalid_evidence},
    ) is False

    _assert_quarantined_without_financial_change(trader, observation, before)
    quarantine = trader._conn.execute(
        "SELECT reason_code FROM paper_settlement_quarantine"
    ).fetchone()
    assert quarantine["reason_code"] == "fee_net_settlement_evidence_invalid"


def test_constructor_closes_connection_when_foreign_key_setup_fails(
    monkeypatch,
    tmp_path,
):
    import trading.paper_trader as paper_trader_module

    conn = MagicMock()
    monkeypatch.setattr(paper_trader_module.sqlite3, "connect", MagicMock(return_value=conn))
    monkeypatch.setattr(
        paper_trader_module,
        "enable_and_verify_foreign_keys",
        MagicMock(side_effect=RuntimeError("foreign keys unavailable")),
    )

    with pytest.raises(RuntimeError, match="foreign keys unavailable"):
        paper_trader_module.PaperTrader(
            db_path=tmp_path / "fk-failure.db",
            startup_context="test",
        )

    conn.close.assert_called_once_with()


def _attempt_record_trade(
    trader,
    market_ref: MarketRef,
    *,
    trade_id: str,
) -> tuple[str, Exception | None]:
    try:
        return _record_trade(trader, market_ref, trade_id=trade_id), None
    except Exception as exc:  # noqa: BLE001 - durable state is the rejection contract.
        return "", exc


def _record_mapped_trade(
    trader,
    market_ref: MarketRef,
    *,
    trade_id: str,
    side: str = "yes",
    yes_price: float = 40.0,
) -> str:
    recorded_id = _record_trade(
        trader,
        market_ref,
        trade_id=trade_id,
        side=side,
        yes_price=yes_price,
    )
    trader._conn.execute(
        """
        UPDATE paper_trades
        SET venue=?, venue_market_id=?, identity_status='mapped', quarantine_reason=NULL
        WHERE trade_id=?
        """,
        (market_ref.venue.value, market_ref.venue_market_id, recorded_id),
    )
    trader._conn.commit()
    portfolio = Portfolio()
    portfolio.load_from_db(trader._conn)
    trader.portfolio = portfolio
    return recorded_id


def _resolve(trader, observation: SettlementObservation, **kwargs):
    result = trader.resolve_observation(observation, **kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _attempt_resolution(trader, observation: SettlementObservation) -> Exception | None:
    try:
        _resolve(trader, observation)
    except Exception as exc:  # Rejections may return a status or raise after quarantine.
        return exc
    return None


def _bankroll_cents(trader) -> Decimal:
    value = trader._conn.execute(
        "SELECT value FROM bot_state WHERE key='notional_bankroll'"
    ).fetchone()[0]
    return Decimal(str(value)) * Decimal("100")


def test_mapped_open_market_refs_deduplicate_exact_two_venue_identities(trader_factory):
    trader = trader_factory("mapped-open-refs")
    kalshi = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "KXGDP-26JUL31")
    polymarket = MarketRef(Venue.POLYMARKET_US, "104982", "KXGDP-26JUL31")
    _record_mapped_trade(trader, kalshi, trade_id="mapref000001")
    _record_mapped_trade(trader, kalshi, trade_id="mapref000002")
    _record_mapped_trade(trader, polymarket, trade_id="mapref000003")

    market_refs = trader.mapped_open_market_refs()

    assert market_refs == (kalshi, polymarket)


def test_mapped_open_market_refs_fail_closed_on_invalid_identity(trader_factory):
    trader = trader_factory("invalid-open-ref")
    market_ref = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "KXGDP-26JUL31")
    trade_id = _record_mapped_trade(
        trader,
        market_ref,
        trade_id="badref000001",
    )
    trader._conn.execute(
        """
        UPDATE paper_trades
        SET identity_status='quarantined', quarantine_reason='identity_drift'
        WHERE trade_id=?
        """,
        (trade_id,),
    )
    trader._conn.commit()

    with pytest.raises(SettlementDriftError, match="mapped"):
        trader.mapped_open_market_refs()

    alias_check = trader.legacy_settlement_alias_check()
    assert not alias_check.ok
    assert alias_check.metrics["invalid_identity_count"] == 1


def test_legacy_alias_check_allows_same_ref_duplicates_and_blocks_cross_ref_alias(
    trader_factory,
):
    trader = trader_factory("legacy-alias-check")
    first = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "KXGDP-26JUL31")
    second = MarketRef(Venue.POLYMARKET_US, "104982", "KXGDP-26JUL31")
    _record_mapped_trade(trader, first, trade_id="alias0000001")
    _record_mapped_trade(trader, first, trade_id="alias0000002")

    allowed = trader.legacy_settlement_alias_check()

    assert allowed.ok
    assert allowed.metrics["alias_collision_count"] == 0

    _record_mapped_trade(trader, second, trade_id="alias0000003")

    blocked = trader.legacy_settlement_alias_check()
    assert not blocked.ok
    assert blocked.failures == ("alias_collision:KXGDP-26JUL31",)
    assert blocked.metrics["alias_collision_count"] == 1


def test_observation_recovers_post_commit_portfolio_sync_in_same_call(
    trader_factory,
):
    trader = trader_factory("portfolio-recovery")
    market_ref = MarketRef(Venue.KALSHI, "KX-RECOVER", "KX-RECOVER")
    trade_id = _record_mapped_trade(
        trader,
        market_ref,
        trade_id="recovr000001",
    )
    observation = _observation(market_ref, MarketOutcome.YES)
    original_resolve = trader.portfolio.resolve
    resolve_calls = 0

    def fail_once_then_resolve(ref):
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls == 1:
            raise RuntimeError("injected post-commit portfolio failure")
        return original_resolve(ref)

    failed_portfolio = trader.portfolio
    failed_portfolio.resolve = fail_once_then_resolve

    assert _resolve(trader, observation) is True

    row = trader._conn.execute(
        "SELECT resolved, settlement_observation_sha256 FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    assert tuple(row) == (1, observation.observation_sha256)
    assert trader.portfolio.open_positions(market_ref.alias) == []
    assert trader.portfolio is not failed_portfolio
    assert resolve_calls == 1


def test_reconciler_recovers_post_commit_portfolio_sync_without_second_cycle(
    trader_factory,
):
    trader = trader_factory("reconciler-portfolio-recovery")
    market_ref = MarketRef(Venue.KALSHI, "KX-RECONCILE", "KX-RECONCILE")
    _record_mapped_trade(
        trader,
        market_ref,
        trade_id="reconcl00001",
    )
    observation = _observation(market_ref, MarketOutcome.YES)
    bankroll_before = _bankroll_cents(trader)
    failed_portfolio = trader.portfolio
    failed_portfolio.resolve = MagicMock(
        side_effect=RuntimeError("injected post-commit portfolio failure")
    )
    source = MagicMock()
    source.get_settlement.return_value = observation
    reconciler = PersistedPositionReconciler(source=source, resolver=trader)

    first = reconciler.reconcile()

    assert first.checked == 1
    assert first.resolved == 1
    assert first.errors == 0
    assert trader.portfolio is not failed_portfolio
    assert trader.portfolio.open_positions(market_ref.alias) == []
    bankroll_after = _bankroll_cents(trader)
    assert bankroll_after > bankroll_before
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_observations"
    ).fetchone()[0] == 1
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_outbox"
    ).fetchone()[0] == 1

    second = reconciler.reconcile()

    assert second.checked == 0
    assert second.resolved == 0
    assert second.errors == 0
    assert _bankroll_cents(trader) == bankroll_after
    assert source.get_settlement.call_count == 1
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_observations"
    ).fetchone()[0] == 1
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_outbox"
    ).fetchone()[0] == 1


def test_schema_present_legacy_resolution_rechecks_alias_under_write_lock(
    trader_factory,
):
    trader = trader_factory("legacy-alias-transaction")
    kalshi = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "KXGDP-26JUL31")
    polymarket = MarketRef(Venue.POLYMARKET_US, "104982", "KXGDP-26JUL31")
    first_trade = _record_mapped_trade(
        trader,
        kalshi,
        trade_id="guard0000001",
    )
    second_trade = _record_mapped_trade(
        trader,
        polymarket,
        trade_id="guard0000002",
    )
    bankroll_before = _bankroll_cents(trader)

    with pytest.raises(SettlementDriftError, match="alias"):
        asyncio.run(trader.resolve_market(kalshi.alias, True))

    rows = trader._conn.execute(
        """
        SELECT trade_id, resolved, pnl_dollars
        FROM paper_trades
        WHERE trade_id IN (?, ?)
        ORDER BY trade_id
        """,
        (first_trade, second_trade),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (first_trade, 0, None),
        (second_trade, 0, None),
    ]
    assert _bankroll_cents(trader) == bankroll_before


def _financial_snapshot(trader) -> dict[str, object]:
    trades = trader._conn.execute(
        """
        SELECT trade_id, resolved, resolved_yes, pnl_dollars, terminal_state,
               settlement_observation_sha256, gross_payout_cents, gross_pnl_cents
        FROM paper_trades ORDER BY trade_id
        """
    ).fetchall()
    return {
        "bankroll_cents": _bankroll_cents(trader),
        "trades": [tuple(row) for row in trades],
        "observations": trader._conn.execute(
            "SELECT COUNT(*) FROM paper_settlement_observations"
        ).fetchone()[0],
        "outbox": trader._conn.execute(
            "SELECT COUNT(*) FROM paper_settlement_outbox"
        ).fetchone()[0],
    }


def _assert_quarantined_without_financial_change(
    trader,
    observation: SettlementObservation,
    before: dict[str, object],
) -> None:
    assert _financial_snapshot(trader) == before
    rows = trader._conn.execute(
        """
        SELECT observation_sha256, venue, venue_market_id, alias
        FROM paper_settlement_quarantine
        """
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["observation_sha256"] == observation.observation_sha256
    assert rows[0]["venue"] == observation.market_ref.venue.value
    assert rows[0]["venue_market_id"] == observation.market_ref.venue_market_id
    assert rows[0]["alias"] == observation.market_ref.alias


@pytest.mark.parametrize(
    "market_ref",
    [
        MarketRef(Venue.KALSHI, "KX-CANONICAL", "KX-CANONICAL"),
        MarketRef(
            Venue.POLYMARKET_US,
            "8594",
            "numeric-id-is-separate-from-this-slug",
        ),
    ],
)
def test_schema_present_record_trade_persists_mapped_canonical_identity(
    trader_factory,
    market_ref,
):
    trader = trader_factory(f"canonical-entry-{market_ref.venue.value}")

    trade_id = _record_trade(trader, market_ref, trade_id="entry0000001")

    row = trader._conn.execute(
        """
        SELECT venue, venue_market_id, identity_status
        FROM paper_trades WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    assert tuple(row) == (
        market_ref.venue.value,
        market_ref.venue_market_id,
        "mapped",
    )
    position = trader.portfolio.open_positions(market_ref.alias)[0]
    assert position.venue_market_id == market_ref.venue_market_id


def test_canonical_record_trade_rechecks_bankroll_inside_transaction(
    trader_factory,
):
    trader = trader_factory("canonical-entry-bankroll-race")
    market_ref = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "KXGDP-26JUL31")
    trader._set_state("notional_bankroll", "0.40")

    with patch.object(trader, "get_notional_bankroll", return_value=500.0):
        trade_id = _record_trade(trader, market_ref, trade_id="budget000001")

    assert trade_id == ""
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert _bankroll_cents(trader) == Decimal("40")


def test_record_trade_rejects_exact_identity_after_canonical_settlement(
    trader_factory,
):
    trader = trader_factory("reject-late-entry-after-settlement")
    settled_ref = MarketRef(
        Venue.POLYMARKET_US,
        "8594",
        "original-market-alias",
    )
    settled_trade_id = _record_trade(
        trader,
        settled_ref,
        trade_id="settle000001",
    )
    assert _resolve(trader, _observation(settled_ref, MarketOutcome.YES)) is True
    before = _financial_snapshot(trader)
    assert len(before["trades"]) == 1
    assert before["trades"][0][:2] == (settled_trade_id, 1)
    assert trader.portfolio.open_positions() == []
    late_ref = MarketRef(
        Venue.POLYMARKET_US,
        settled_ref.venue_market_id,
        "renamed-market-alias",
    )

    late_trade_id = _record_trade(
        trader,
        late_ref,
        trade_id="late00000001",
    )

    assert late_trade_id == ""
    assert _financial_snapshot(trader) == before
    assert trader.portfolio.open_positions() == []


def test_record_trade_waits_for_canonical_settlement_transaction(trader_factory, monkeypatch):
    trader = trader_factory("serialized-entry-and-settlement")
    settled_ref = MarketRef(
        Venue.KALSHI,
        "KXSETTLE-26JUL31",
        "KXSETTLE-26JUL31",
    )
    entry_ref = MarketRef(
        Venue.KALSHI,
        "KXENTRY-26JUL31",
        "KXENTRY-26JUL31",
    )
    settled_trade_id = _record_trade(
        trader,
        settled_ref,
        trade_id="settle000001",
    )
    observation = _observation(settled_ref, MarketOutcome.YES)
    original_candidate_rows = trader._canonical_candidate_rows
    settlement_began = threading.Event()
    release_settlement = threading.Event()
    entry_started = threading.Event()
    entry_finished = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def paused_candidate_rows(current_observation):
        settlement_began.set()
        if not release_settlement.wait(timeout=5):
            raise TimeoutError("test did not release settlement transaction")
        return original_candidate_rows(current_observation)

    def settle() -> None:
        try:
            results["settlement"] = _resolve(trader, observation)
        except BaseException as exc:  # noqa: BLE001 - asserted after both threads join.
            errors["settlement"] = exc

    def enter() -> None:
        entry_started.set()
        try:
            results["entry"] = _record_trade(
                trader,
                entry_ref,
                trade_id="entry0000001",
            )
        except BaseException as exc:  # noqa: BLE001 - asserted after both threads join.
            errors["entry"] = exc
        finally:
            entry_finished.set()

    monkeypatch.setattr(trader, "_canonical_candidate_rows", paused_candidate_rows)
    settlement_thread = threading.Thread(target=settle)
    entry_thread = threading.Thread(target=enter)
    settlement_thread.start()
    assert settlement_began.wait(timeout=5)
    entry_thread.start()
    assert entry_started.wait(timeout=5)
    entry_finished_while_settlement_paused = entry_finished.wait(timeout=0.2)
    release_settlement.set()
    settlement_thread.join(timeout=5)
    entry_thread.join(timeout=5)

    assert not settlement_thread.is_alive()
    assert not entry_thread.is_alive()
    assert entry_finished_while_settlement_paused is False
    assert errors == {}
    assert results == {"settlement": True, "entry": "entry0000001"}
    rows = trader._conn.execute(
        "SELECT trade_id, resolved FROM paper_trades ORDER BY trade_id"
    ).fetchall()
    assert {row["trade_id"]: row["resolved"] for row in rows} == {
        settled_trade_id: 1,
        "entry0000001": 0,
    }
    assert {position.trade_id for position in trader.portfolio.open_positions()} == {
        "entry0000001"
    }


def test_schema_present_kalshi_ticker_is_authoritative_over_separate_id(
    trader_factory,
):
    trader = trader_factory("kalshi-ticker-authoritative")
    analysis = _make_mock_analysis(ticker="KX-AUTHORITATIVE")
    analysis.venue = Venue.KALSHI.value
    analysis.market.venue = Venue.KALSHI.value
    analysis.market.venue_market_id = "KX-DIFFERENT-ID"

    trade_id = _record_analysis(trader, analysis, trade_id="kxauth000001")

    row = trader._conn.execute(
        "SELECT venue_market_id, identity_status FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    assert tuple(row) == ("KX-AUTHORITATIVE", "mapped")


@pytest.mark.parametrize("canonical_id", [None, "", "   "])
def test_schema_present_record_trade_without_canonical_id_fails_closed(
    trader_factory,
    canonical_id,
):
    trader = trader_factory(f"missing-canonical-{canonical_id!r}")
    analysis = _make_mock_analysis(ticker="missing-canonical-id")
    analysis.venue = Venue.POLYMARKET_US.value
    analysis.market.venue = Venue.POLYMARKET_US.value
    analysis.market.market_id = "missing-canonical-id"
    analysis.market.venue_market_id = canonical_id
    before = _bankroll_cents(trader)

    try:
        with patch("dataclasses.asdict", return_value={"series_ticker": ""}):
            result = trader.record_trade(analysis)
    except ValueError:
        result = ""

    assert result == ""
    assert trader._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert _bankroll_cents(trader) == before
    assert trader.portfolio.open_positions() == []


@pytest.mark.parametrize("missing_column", ["venue_market_id", "identity_status"])
def test_canonical_entry_requires_complete_identity_schema_before_debit(
    trader_factory,
    missing_column,
):
    trader = trader_factory(f"incomplete-identity-{missing_column}")
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_schema_meta"
    ).fetchone()[0] == 1
    trader._conn.execute(f"ALTER TABLE paper_trades DROP COLUMN {missing_column}")
    trader._conn.commit()
    assert missing_column not in {
        row[1] for row in trader._conn.execute("PRAGMA table_info(paper_trades)")
    }
    before = _financial_snapshot(trader)
    assert before["bankroll_cents"] == Decimal("50000")
    market_ref = MarketRef(Venue.POLYMARKET_US, "8594", "complete-schema")

    result, _error = _attempt_record_trade(
        trader,
        market_ref,
        trade_id="broken000001",
    )

    assert result == ""
    assert _financial_snapshot(trader) == before
    assert trader.portfolio.open_positions() == []


def test_canonical_entry_insert_failure_rolls_back_debit_and_row(trader_factory):
    trader = trader_factory("canonical-insert-failure")
    trader._conn.execute(
        """
        CREATE TRIGGER inject_canonical_entry_failure
        BEFORE INSERT ON paper_trades
        WHEN NEW.identity_status='mapped'
        BEGIN
            SELECT RAISE(ABORT, 'injected canonical entry failure');
        END
        """
    )
    trader._conn.commit()
    before = _financial_snapshot(trader)
    assert before["bankroll_cents"] == Decimal("50000")
    market_ref = MarketRef(Venue.POLYMARKET_US, "8594", "insert-failure")

    result, _error = _attempt_record_trade(
        trader,
        market_ref,
        trade_id="insertfail01",
    )

    assert result == ""
    assert _financial_snapshot(trader) == before
    assert trader.portfolio.open_positions() == []


def test_schema_present_defaulted_kalshi_rejects_non_kx_ticker_before_debit(
    trader_factory,
):
    trader = trader_factory("defaulted-kalshi-slug")
    analysis = _make_mock_analysis(ticker="will-example-happen-2026")
    analysis.venue = None
    analysis.market.venue = None
    analysis.market.venue_market_id = None
    before = _financial_snapshot(trader)
    assert before["bankroll_cents"] == Decimal("50000")

    try:
        result = _record_analysis(trader, analysis, trade_id="nonkx0000001")
    except Exception:  # noqa: BLE001 - durable state is the rejection contract.
        result = ""

    assert result == ""
    assert _financial_snapshot(trader) == before
    assert trader.portfolio.open_positions() == []


def test_schema_absent_existing_database_keeps_legacy_record_trade_insert(
    trader_factory,
):
    trader = trader_factory("legacy-schema-absent")
    trader._conn.commit()
    trader._conn.execute("PRAGMA foreign_keys=OFF")
    trader._conn.execute("DROP INDEX paper_trades_settlement_observation_idx")
    for column in (
        "terminal_state",
        "settlement_observation_sha256",
        "settled_at",
        "gross_payout_cents",
        "gross_pnl_cents",
    ):
        trader._conn.execute(f"ALTER TABLE paper_trades DROP COLUMN {column}")
    for table in (
        "paper_settlement_delivery_claims",
        "paper_settlement_consumer_receipts",
        "paper_settlement_outbox_requirements",
        "paper_settlement_outbox",
        "paper_settlement_quarantine",
        "paper_settlement_observations",
        "paper_settlement_schema_meta",
    ):
        trader._conn.execute(f"DROP TABLE {table}")
    trader._conn.commit()
    trader._conn.close()

    trader = trader_factory("legacy-schema-absent")
    assert trader._conn.execute(
        """
        SELECT 1 FROM sqlite_schema
        WHERE type='table' AND name='paper_settlement_schema_meta'
        """
    ).fetchone() is None
    analysis = _make_mock_analysis(ticker="legacy-polymarket-slug")
    analysis.venue = Venue.POLYMARKET_US.value
    analysis.market.venue = Venue.POLYMARKET_US.value
    analysis.market.market_id = "legacy-polymarket-slug"
    analysis.market.venue_market_id = None
    statements: list[str] = []
    trader._conn.set_trace_callback(statements.append)

    with (
        patch("trading.paper_trader.uuid.uuid4", return_value="legacyinsert"),
        patch("dataclasses.asdict", return_value={"series_ticker": ""}),
    ):
        trade_id = trader.record_trade(analysis)
    trader._conn.set_trace_callback(None)

    assert trade_id == "legacyinsert"
    row = trader._conn.execute(
        """
        SELECT ticker, venue, venue_market_id, identity_status
        FROM paper_trades WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    assert tuple(row) == (
        "legacy-polymarket-slug",
        Venue.POLYMARKET_US.value,
        None,
        None,
    )
    assert _bankroll_cents(trader) == Decimal("49000")
    paper_inserts = [
        " ".join(statement.upper().split())
        for statement in statements
        if "INSERT INTO PAPER_TRADES" in statement.upper()
    ]
    assert len(paper_inserts) == 1
    for canonical_column in (
        "VENUE_MARKET_ID",
        "IDENTITY_STATUS",
        "TERMINAL_STATE",
        "SETTLEMENT_OBSERVATION_SHA256",
    ):
        assert canonical_column not in paper_inserts[0]


def test_exact_market_ref_settles_only_matching_rows_and_closes_exact_portfolio_position(
    trader_factory,
):
    trader = trader_factory("exact-market-ref")
    alias = "KX-SHARED-ALIAS"
    target = MarketRef(Venue.POLYMARKET_US, "8594", alias)
    other_venue = MarketRef(Venue.KALSHI, "KX-SHARED-ALIAS", alias)
    other_id = MarketRef(Venue.POLYMARKET_US, "9999", alias)
    target_trade = _record_mapped_trade(
        trader, target, trade_id="target000001"
    )
    other_venue_trade = _record_mapped_trade(
        trader, other_venue, trade_id="kalshi000001"
    )
    other_id_trade = _record_mapped_trade(
        trader, other_id, trade_id="otherid00001"
    )

    _resolve(trader, _observation(target, MarketOutcome.YES))

    rows = trader._conn.execute(
        "SELECT trade_id, resolved FROM paper_trades ORDER BY trade_id"
    ).fetchall()
    assert {row["trade_id"]: row["resolved"] for row in rows} == {
        target_trade: 1,
        other_venue_trade: 0,
        other_id_trade: 0,
    }
    assert {position.trade_id for position in trader.portfolio.open_positions(alias)} == {
        other_venue_trade,
        other_id_trade,
    }


def test_alias_drift_closes_exact_canonical_portfolio_position(trader_factory):
    trader = trader_factory("canonical-alias-drift")
    stored_alias = "KX-SHARED-ALIAS"
    target = MarketRef(Venue.POLYMARKET_US, "8594", stored_alias)
    wrong_id = MarketRef(Venue.POLYMARKET_US, "9999", stored_alias)
    cross_venue = MarketRef(Venue.KALSHI, stored_alias, stored_alias)
    target_trade = _record_mapped_trade(
        trader,
        target,
        trade_id="aliastgt0001",
    )
    wrong_id_trade = _record_mapped_trade(
        trader,
        wrong_id,
        trade_id="aliasbad0001",
    )
    cross_venue_trade = _record_mapped_trade(
        trader,
        cross_venue,
        trade_id="aliasxvn0001",
    )
    observation = _observation(
        MarketRef(Venue.POLYMARKET_US, "8594", "renamed-display-alias"),
        MarketOutcome.YES,
    )

    assert _resolve(trader, observation) is True

    rows = trader._conn.execute(
        "SELECT trade_id, resolved FROM paper_trades ORDER BY trade_id"
    ).fetchall()
    assert {row["trade_id"]: row["resolved"] for row in rows} == {
        target_trade: 1,
        wrong_id_trade: 0,
        cross_venue_trade: 0,
    }
    assert _bankroll_cents(trader) == Decimal("49500")
    assert {position.trade_id for position in trader.portfolio.open_positions()} == {
        wrong_id_trade,
        cross_venue_trade,
    }


def test_alias_drift_skips_unrelated_invalid_legacy_portfolio_venue(trader_factory):
    trader = trader_factory("canonical-invalid-legacy-venue")
    stored_ref = MarketRef(
        Venue.POLYMARKET_US,
        "8594",
        "stored-target-alias",
    )
    target_trade = _record_mapped_trade(
        trader,
        stored_ref,
        trade_id="target000001",
    )
    target_position = trader.portfolio.open_positions(stored_ref.alias)[0]
    invalid_position = Position(
        trade_id="invalid00001",
        ticker="invalid-legacy-alias",
        side="yes",
        contracts=1,
        cost_dollars=1.0,
        price_cents=50,
        estimated_prob=0.5,
        entry_price_cents=50.0,
        ts="2026-01-01T00:00:00+00:00",
        venue="unsupported_legacy",
        venue_market_id="unrelated-market-id",
    )
    portfolio = Portfolio()
    portfolio.add(invalid_position)
    portfolio.add(target_position)
    trader.portfolio = portfolio
    observation = _observation(
        MarketRef(
            Venue.POLYMARKET_US,
            "8594",
            "renamed-target-alias",
        ),
        MarketOutcome.YES,
    )

    assert _resolve(trader, observation) is True

    row = trader._conn.execute(
        """
        SELECT resolved, settlement_observation_sha256
        FROM paper_trades
        WHERE trade_id = ?
        """,
        (target_trade,),
    ).fetchone()
    assert row["resolved"] == 1
    assert row["settlement_observation_sha256"] == observation.observation_sha256
    assert _bankroll_cents(trader) == Decimal("51500")
    assert trader.portfolio.open_positions() == [invalid_position]

    after_first = _financial_snapshot(trader)
    assert _resolve(trader, observation) is False
    assert _financial_snapshot(trader) == after_first
    assert trader.portfolio.open_positions() == [invalid_position]


def test_resolution_uses_one_immediate_transaction_direct_bankroll_update_and_per_trade_cas(
    trader_factory, monkeypatch
):
    trader = trader_factory("one-financial-transaction")
    market_ref = MarketRef(Venue.KALSHI, "KX-CAS", "KX-CAS")
    _record_mapped_trade(trader, market_ref, trade_id="castrade0001")
    _record_mapped_trade(trader, market_ref, trade_id="castrade0002")
    monkeypatch.setattr(
        trader,
        "_credit_bankroll",
        lambda _amount: pytest.fail("canonical settlement used nested bankroll commit"),
    )
    statements: list[str] = []
    trader._conn.set_trace_callback(statements.append)

    _resolve(trader, _observation(market_ref, MarketOutcome.YES))
    trader._conn.set_trace_callback(None)

    normalized = [" ".join(statement.upper().split()) for statement in statements]
    assert sum(statement == "BEGIN IMMEDIATE" for statement in normalized) == 1
    assert sum(statement == "COMMIT" for statement in normalized) == 1
    assert not any(statement == "ROLLBACK" for statement in normalized)
    assert sum(
        statement.startswith("UPDATE PAPER_TRADES") for statement in normalized
    ) == 2
    assert sum(
        statement.startswith("UPDATE BOT_STATE") and "NOTIONAL_BANKROLL" in statement
        for statement in normalized
    ) == 1
    assert _bankroll_cents(trader) == Decimal("53000")


@pytest.mark.parametrize(
    ("side", "outcome", "expected_payout", "expected_pnl", "expected_bankroll"),
    [
        ("yes", MarketOutcome.YES, Decimal("2500"), Decimal("1500"), Decimal("51500")),
        ("no", MarketOutcome.NO, Decimal("2500"), Decimal("1500"), Decimal("51500")),
        ("yes", MarketOutcome.NO, Decimal("0"), Decimal("-1000"), Decimal("49000")),
        ("no", MarketOutcome.YES, Decimal("0"), Decimal("-1000"), Decimal("49000")),
    ],
)
def test_yes_no_gross_settlement_parity(
    trader_factory,
    side,
    outcome,
    expected_payout,
    expected_pnl,
    expected_bankroll,
):
    trader = trader_factory(f"gross-{side}-{outcome.value}")
    market_ref = MarketRef(Venue.KALSHI, "KX-GROSS", "KX-GROSS")
    yes_price = 40.0 if side == "yes" else 60.0
    trade_id = _record_mapped_trade(
        trader,
        market_ref,
        trade_id="gross0000001",
        side=side,
        yes_price=yes_price,
    )

    observation = _observation(market_ref, outcome)
    _resolve(trader, observation)

    row = trader._conn.execute(
        """
        SELECT terminal_state, gross_payout_cents, gross_pnl_cents
        FROM paper_trades WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    won = (side == "yes") == (outcome is MarketOutcome.YES)
    assert row["terminal_state"] == ("won" if won else "lost")
    assert Decimal(row["gross_payout_cents"]) == expected_payout
    assert Decimal(row["gross_pnl_cents"]) == expected_pnl
    assert _bankroll_cents(trader) == expected_bankroll


def test_void_credits_exact_refund_and_emits_no_directional_feedback_requirement(
    trader_factory,
):
    trader = trader_factory("void-refund")
    stored_ticker = "stored-void-alias"
    market_ref = MarketRef(Venue.POLYMARKET_US, "8594", stored_ticker)
    analysis = _make_mock_analysis(
        ticker=stored_ticker,
        series_ticker="PMVOID",
        side="yes",
        yes_price=40.0,
        estimated_prob=0.61,
        source="wire:void-source",
        keywords=["missile strike", "ceasefire"],
        llm_direction="yes",
        llm_magnitude="small",
        llm_confidence=0.73,
    )
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = stored_ticker
    analysis.market.venue_market_id = market_ref.venue_market_id
    analysis.signal_meta = {
        "fast_lane_p": 0.71,
        "fast_lane_confidence": 0.91,
        "accumulation_p": 0.66,
        "accumulation_confidence": 0.81,
        "structural_p": 0.61,
        "structural_confidence": 0.71,
    }
    trade_id = _record_analysis(trader, analysis, trade_id="void00000001")
    observation = _observation(
        MarketRef(
            market_ref.venue,
            market_ref.venue_market_id,
            "drifted-void-alias",
        ),
        MarketOutcome.VOID,
        void_refund=VoidRefundContract(
            refund_cents_per_contract=Decimal("50"),
            refunds_entry_fee=False,
        ),
    )

    _resolve(trader, observation)

    row = trader._conn.execute(
        """
        SELECT resolved, resolved_yes, terminal_state,
               gross_payout_cents, gross_pnl_cents
        FROM paper_trades WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    assert row["resolved"] == 1
    assert row["resolved_yes"] is None
    assert row["terminal_state"] == "void"
    assert Decimal(row["gross_payout_cents"]) == Decimal("1250")
    assert Decimal(row["gross_pnl_cents"]) == Decimal("250")
    assert _bankroll_cents(trader) == Decimal("50250")
    outbox = trader._conn.execute(
        """
        SELECT outbox_id, event_version, event_kind, observation_sha256,
               trade_id, payload_json
        FROM paper_settlement_outbox
        """
    ).fetchone()
    trade = trader._conn.execute(
        "SELECT ticker, ts, settled_at FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    payload = json.loads(outbox["payload_json"])
    consumers = {
        row[0]
        for row in trader._conn.execute(
            """
            SELECT consumer_name FROM paper_settlement_outbox_requirements
            WHERE outbox_id=?
            """,
            (outbox["outbox_id"],),
        )
    }
    assert consumers == {"paper_trade_log"}
    assert payload["outbox_id"] == outbox["outbox_id"]
    assert payload["event_version"] == outbox["event_version"] == 1
    assert payload["event_kind"] == outbox["event_kind"] == "paper_trade_settled"
    assert payload["observation_sha256"] == outbox["observation_sha256"]
    assert payload["trade_id"] == outbox["trade_id"] == trade_id
    assert payload["ticker"] == trade["ticker"] == stored_ticker
    assert payload["ticker"] != observation.market_ref.alias
    assert payload["venue"] == market_ref.venue.value
    assert payload["venue_market_id"] == market_ref.venue_market_id
    assert payload["alias"] == observation.market_ref.alias
    assert payload["outcome"] == "void"
    assert payload["side"] == "yes"
    assert payload["resolved_yes"] is None
    assert payload["terminal_state"] == "void"
    assert payload["won"] is None
    assert payload["settled_at"] == trade["settled_at"]
    assert payload["signal_source"] == "wire:void-source"
    assert payload["series_ticker"] == "PMVOID"
    assert payload["entry_ts"] == trade["ts"]
    assert payload["estimated_prob"] == pytest.approx(0.61)
    assert payload["entry_price_cents"] == pytest.approx(40.0)
    assert payload["cost_dollars"] == pytest.approx(10.0)
    assert payload["llm_magnitude"] == "small"
    assert payload["llm_confidence"] == pytest.approx(0.73)
    assert payload["keyword_outcomes"] == [
        {"keyword": "missile strike", "direction": "yes", "correct": None},
        {"keyword": "ceasefire", "direction": "no", "correct": None},
    ]
    assert payload["lane_estimates"] == {
        "fast": pytest.approx(0.71),
        "accumulation": pytest.approx(0.66),
        "structural": pytest.approx(0.61),
    }
    assert payload["gross_payout_cents"] == "1250"
    assert payload["gross_pnl_cents"] == "250"


def test_duplicate_observation_is_a_financial_and_outbox_noop(trader_factory):
    trader = trader_factory("duplicate-observation")
    market_ref = MarketRef(Venue.KALSHI, "KX-DUP", "KX-DUP")
    _record_mapped_trade(trader, market_ref, trade_id="duplicate001")
    observation = _observation(market_ref, MarketOutcome.YES)
    _resolve(trader, observation)
    after_first = _financial_snapshot(trader)

    _resolve(trader, observation)

    assert _financial_snapshot(trader) == after_first
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_quarantine"
    ).fetchone()[0] == 0


def test_terminal_correction_preserves_financial_state_then_quarantines_separately(
    trader_factory,
):
    trader = trader_factory("terminal-correction")
    market_ref = MarketRef(Venue.KALSHI, "KX-CORRECT", "KX-CORRECT")
    _record_mapped_trade(trader, market_ref, trade_id="correct00001")
    first = _observation(market_ref, MarketOutcome.YES)
    _resolve(trader, first)
    before = _financial_snapshot(trader)
    correction = _observation(
        market_ref,
        MarketOutcome.NO,
        payload={"id": market_ref.venue_market_id, "result": "no", "revision": 2},
        observed_at=OBSERVED_AT + timedelta(minutes=5),
        effective_at=EFFECTIVE_AT + timedelta(minutes=5),
        previous=first,
        supersedes=first.observation_sha256,
    )

    _attempt_resolution(trader, correction)

    _assert_quarantined_without_financial_change(trader, correction, before)


@pytest.mark.parametrize("supersedes_kind", ["unknown", "cross-market"])
def test_first_observation_rejects_unowned_supersedes_hash(
    trader_factory,
    supersedes_kind,
):
    trader = trader_factory(f"unowned-supersedes-{supersedes_kind}")
    target_ref = MarketRef(Venue.POLYMARKET_US, "8594", "supersedes-target")
    _record_mapped_trade(
        trader,
        target_ref,
        trade_id="supertrg0001",
    )
    if supersedes_kind == "cross-market":
        other_ref = MarketRef(Venue.POLYMARKET_US, "7777", "supersedes-other")
        _record_mapped_trade(
            trader,
            other_ref,
            trade_id="superoth0001",
        )
        other_observation = _observation(other_ref, MarketOutcome.YES)
        assert _resolve(trader, other_observation) is True
        supersedes_sha256 = other_observation.observation_sha256
    else:
        supersedes_sha256 = "f" * 64
    observation = replace(
        _observation(target_ref, MarketOutcome.YES),
        supersedes_observation_sha256=supersedes_sha256,
    )
    before = _financial_snapshot(trader)

    _attempt_resolution(trader, observation)

    _assert_quarantined_without_financial_change(trader, observation, before)
    assert {position.trade_id for position in trader.portfolio.open_positions()} == {
        "supertrg0001"
    }


def test_per_trade_cas_row_count_drift_rolls_back_then_quarantines(trader_factory):
    trader = trader_factory("cas-drift")
    market_ref = MarketRef(Venue.KALSHI, "KX-DRIFT", "KX-DRIFT")
    _record_mapped_trade(trader, market_ref, trade_id="drift0000001")
    blocked_trade = _record_mapped_trade(
        trader, market_ref, trade_id="drift0000002"
    )
    trader._conn.execute(
        f"""
        CREATE TRIGGER inject_cas_drift
        BEFORE UPDATE OF resolved ON paper_trades
        WHEN OLD.trade_id='{blocked_trade}'
        BEGIN
            SELECT RAISE(IGNORE);
        END
        """
    )
    trader._conn.commit()
    observation = _observation(market_ref, MarketOutcome.YES)
    before = _financial_snapshot(trader)

    _attempt_resolution(trader, observation)

    _assert_quarantined_without_financial_change(trader, observation, before)
    assert len(trader.portfolio.open_positions(market_ref.alias)) == 2


def test_legacy_null_identity_preserves_financial_state_then_quarantines(trader_factory):
    trader = trader_factory("legacy-null")
    market_ref = MarketRef(Venue.POLYMARKET_US, "8594", "legacy-null")
    trade_id = _record_mapped_trade(
        trader, market_ref, trade_id="legacy000001"
    )
    trader._conn.execute(
        """
        UPDATE paper_trades
        SET venue_market_id=NULL, identity_status=NULL
        WHERE trade_id=?
        """,
        (trade_id,),
    )
    trader._conn.commit()
    portfolio = Portfolio()
    portfolio.load_from_db(trader._conn)
    trader.portfolio = portfolio
    observation = _observation(market_ref, MarketOutcome.YES)
    before = _financial_snapshot(trader)

    _attempt_resolution(trader, observation)

    _assert_quarantined_without_financial_change(trader, observation, before)
    assert trader.portfolio.open_positions(market_ref.alias)[0].venue_market_id is None


def test_different_alias_unmapped_canonical_collision_rolls_back_and_quarantines(
    trader_factory,
):
    trader = trader_factory("unmapped-canonical-collision")
    market_ref = MarketRef(Venue.POLYMARKET_US, "8594", "mapped-target-alias")
    mapped_trade = _record_mapped_trade(
        trader,
        market_ref,
        trade_id="mapped000001",
    )
    legacy_ref = MarketRef(Venue.POLYMARKET_US, "8594", "older-legacy-alias")
    legacy_trade = _record_mapped_trade(
        trader,
        legacy_ref,
        trade_id="unmapped0001",
    )
    trader._conn.execute(
        "UPDATE paper_trades SET identity_status=NULL WHERE trade_id=?",
        (legacy_trade,),
    )
    trader._conn.commit()
    portfolio = Portfolio()
    portfolio.load_from_db(trader._conn)
    trader.portfolio = portfolio
    observation = _observation(market_ref, MarketOutcome.YES)
    before = _financial_snapshot(trader)
    assert before["bankroll_cents"] == Decimal("48000")

    _attempt_resolution(trader, observation)

    _assert_quarantined_without_financial_change(trader, observation, before)
    first_quarantine = trader._conn.execute(
        """
        SELECT quarantine_id, reason_code, details_json
        FROM paper_settlement_quarantine
        """
    ).fetchone()
    assert first_quarantine["reason_code"] == "legacy_null_identity"
    assert json.loads(first_quarantine["details_json"])["trade_ids"] == [legacy_trade]
    _attempt_resolution(trader, observation)
    quarantine_ids = [
        row[0]
        for row in trader._conn.execute(
            "SELECT quarantine_id FROM paper_settlement_quarantine"
        )
    ]
    assert quarantine_ids == [first_quarantine["quarantine_id"]]
    assert _financial_snapshot(trader) == before
    assert {position.trade_id for position in trader.portfolio.open_positions()} == {
        mapped_trade,
        legacy_trade,
    }


def test_late_keyword_requirement_failure_rolls_back_outbox_and_finances(
    trader_factory,
):
    trader = trader_factory("mid-transaction-failure")
    market_ref = MarketRef(Venue.KALSHI, "KX-FAIL", "KX-FAIL")
    _record_mapped_trade(trader, market_ref, trade_id="failure00001")
    trader._conn.execute(
        """
        CREATE TRIGGER inject_keyword_requirement_failure
        BEFORE INSERT ON paper_settlement_outbox_requirements
        WHEN NEW.consumer_name = 'keyword_outcomes'
        BEGIN
            SELECT RAISE(ABORT, 'injected keyword requirement failure');
        END
        """
    )
    trader._conn.commit()
    observation = _observation(market_ref, MarketOutcome.YES)
    before = _financial_snapshot(trader)
    statements: list[str] = []
    trader._conn.set_trace_callback(statements.append)

    _attempt_resolution(trader, observation)
    trader._conn.set_trace_callback(None)

    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_outbox"
    ).fetchone()[0] == 0
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_outbox_requirements"
    ).fetchone()[0] == 0
    _assert_quarantined_without_financial_change(trader, observation, before)
    normalized = [" ".join(statement.upper().split()) for statement in statements]
    rollback_index = normalized.index("ROLLBACK")
    quarantine_index = next(
        index
        for index, statement in enumerate(normalized)
        if statement.startswith("INSERT INTO PAPER_SETTLEMENT_QUARANTINE")
    )
    assert rollback_index < quarantine_index
    assert "COMMIT" in normalized[quarantine_index + 1 :]


def test_observation_and_outbox_ids_are_deterministic_across_databases(trader_factory):
    market_ref = MarketRef(Venue.POLYMARKET_US, "8594", "deterministic-market")
    first_observation = _observation(
        market_ref,
        MarketOutcome.YES,
        payload={"settled": True, "result": "yes", "id": "8594"},
    )
    second_observation = _observation(
        market_ref,
        MarketOutcome.YES,
        payload={"id": "8594", "result": "yes", "settled": True},
    )
    outbox_ids = []
    for name, observation in (
        ("deterministic-one", first_observation),
        ("deterministic-two", second_observation),
    ):
        trader = trader_factory(name)
        _record_mapped_trade(
            trader, market_ref, trade_id="stable000001"
        )
        _resolve(trader, observation)
        outbox_ids.append(
            trader._conn.execute(
                "SELECT outbox_id FROM paper_settlement_outbox"
            ).fetchone()[0]
        )

    assert first_observation.observation_sha256 == second_observation.observation_sha256
    assert outbox_ids[0] == outbox_ids[1]
    assert len(outbox_ids[0]) == 64


@pytest.mark.parametrize(
    (
        "outcome",
        "trade_id",
        "side",
        "yes_price",
        "resolved_yes",
        "terminal_state",
        "won",
        "payout",
        "pnl",
    ),
    [
        (
            MarketOutcome.YES,
            "outboxyes001",
            "yes",
            40.0,
            True,
            "won",
            True,
            "2500",
            "1500",
        ),
        (
            MarketOutcome.NO,
            "outboxno0001",
            "no",
            60.0,
            False,
            "won",
            True,
            "2500",
            "1500",
        ),
        (
            MarketOutcome.YES,
            "outboxloss01",
            "no",
            60.0,
            True,
            "lost",
            False,
            "0",
            "-1000",
        ),
    ],
)
def test_directional_outbox_v1_is_complete_immutable_and_exactly_routed(
    trader_factory,
    outcome,
    trade_id,
    side,
    yes_price,
    resolved_yes,
    terminal_state,
    won,
    payout,
    pnl,
):
    trader = trader_factory(f"directional-outbox-{outcome.value}")
    stored_ticker = "stored-polymarket-alias"
    market_ref = MarketRef(Venue.POLYMARKET_US, "8594", stored_ticker)
    analysis = _make_mock_analysis(
        ticker=stored_ticker,
        series_ticker="PMOUTBOX",
        side=side,
        yes_price=yes_price,
        estimated_prob=0.67,
        source="wire:test-source",
        keywords=["missile strike", "ceasefire"],
        llm_direction="yes",
        llm_magnitude="moderate",
        llm_confidence=0.81,
    )
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = stored_ticker
    analysis.market.venue_market_id = market_ref.venue_market_id
    analysis.signal_meta = {
        "fast_lane_p": 0.70,
        "fast_lane_confidence": 0.90,
        "accumulation_p": 0.65,
        "accumulation_confidence": 0.80,
        "structural_p": 0.60,
        "structural_confidence": 0.70,
    }
    _record_analysis(trader, analysis, trade_id=trade_id)
    observation = _observation(
        MarketRef(
            Venue.POLYMARKET_US,
            market_ref.venue_market_id,
            "drifted-observation-alias",
        ),
        outcome,
    )

    assert _resolve(trader, observation) is True

    outer = trader._conn.execute(
        """
        SELECT outbox_id, event_version, event_kind, observation_sha256,
               trade_id, payload_json
        FROM paper_settlement_outbox
        """
    ).fetchone()
    trade = trader._conn.execute(
        """
        SELECT ticker, ts, settled_at
        FROM paper_trades
        WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    payload = json.loads(outer["payload_json"])
    consumers = {
        row[0]
        for row in trader._conn.execute(
            """
            SELECT consumer_name
            FROM paper_settlement_outbox_requirements
            WHERE outbox_id=?
            """,
            (outer["outbox_id"],),
        )
    }

    assert consumers == {
        "paper_trade_log",
        "source_credibility",
        "calibration_state",
        "keyword_outcomes",
    }
    assert payload["outbox_id"] == outer["outbox_id"]
    assert payload["event_version"] == outer["event_version"] == 1
    assert payload["event_kind"] == outer["event_kind"] == "paper_trade_settled"
    assert payload["observation_sha256"] == outer["observation_sha256"]
    assert payload["trade_id"] == outer["trade_id"] == trade_id
    assert payload["ticker"] == trade["ticker"] == stored_ticker
    assert payload["ticker"] != observation.market_ref.alias
    assert payload["venue"] == market_ref.venue.value
    assert payload["venue_market_id"] == market_ref.venue_market_id
    assert payload["alias"] == observation.market_ref.alias
    assert payload["outcome"] == outcome.value
    assert payload["side"] == side
    assert payload["resolved_yes"] is resolved_yes
    assert payload["terminal_state"] == terminal_state
    assert payload["won"] is won
    assert payload["settled_at"] == trade["settled_at"]
    assert payload["signal_source"] == "wire:test-source"
    assert payload["series_ticker"] == "PMOUTBOX"
    assert payload["entry_ts"] == trade["ts"]
    assert payload["estimated_prob"] == pytest.approx(0.67)
    assert payload["entry_price_cents"] == pytest.approx(40.0)
    assert payload["cost_dollars"] == pytest.approx(10.0)
    assert payload["llm_magnitude"] == "moderate"
    assert payload["llm_confidence"] == pytest.approx(0.81)
    assert payload["keyword_outcomes"] == [
        {"keyword": "missile strike", "direction": "yes", "correct": resolved_yes},
        {"keyword": "ceasefire", "direction": "no", "correct": not resolved_yes},
    ]
    assert payload["lane_estimates"] == {
        "fast": pytest.approx(0.70),
        "accumulation": pytest.approx(0.65),
        "structural": pytest.approx(0.60),
    }
    assert payload["gross_payout_cents"] == payout
    assert payload["gross_pnl_cents"] == pnl


def test_legacy_resolve_market_behavior_remains_separate_from_canonical_store(
    trader_factory,
):
    trader = trader_factory("legacy-resolver")
    market_ref = MarketRef(Venue.KALSHI, "KX-LEGACY", "KX-LEGACY")
    trade_id = _record_mapped_trade(
        trader, market_ref, trade_id="legacy000002"
    )

    asyncio.run(trader.resolve_market(market_ref.alias, True))

    row = trader._conn.execute(
        """
        SELECT resolved, resolved_yes, terminal_state, settlement_observation_sha256
        FROM paper_trades WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()
    assert tuple(row) == (1, 1, None, None)
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_observations"
    ).fetchone()[0] == 0
    assert trader._conn.execute(
        "SELECT COUNT(*) FROM paper_settlement_outbox"
    ).fetchone()[0] == 0


def test_runtime_entrypoint_keeps_canonical_resolver_behind_shared_reconciler():
    repo_root = Path(__file__).resolve().parents[1]

    def _called_attributes(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

    main_calls = _called_attributes(repo_root / "main.py")
    polymarket_calls = _called_attributes(
        repo_root / "polymarket" / "settlement_reconciler.py"
    )
    assert "resolve_market" in main_calls
    assert "_resolve_market_sync" in polymarket_calls
    assert "resolve_observation" not in main_calls
    assert "resolve_observation" in polymarket_calls
