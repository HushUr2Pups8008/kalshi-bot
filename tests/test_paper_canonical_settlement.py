"""Behavior contract for the unwired canonical paper-settlement path."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.test_paper_trader import _cfg_module, _make_mock_analysis
from trading.portfolio import Portfolio, Position
from trading.settlement import (
    MarketOutcome,
    SettlementObservation,
    VoidRefundContract,
    build_settlement_observation,
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

    import trading.paper_trader as paper_trader_module

    credibility = MagicMock()
    credibility.get_multiplier.return_value = 1.0
    credibility_factory = MagicMock(return_value=credibility)
    monkeypatch.setattr(paper_trader_module, "SourceCredibility", credibility_factory)
    monkeypatch.setattr(paper_trader_module, "trade_log", MagicMock())

    traders = []

    def _make(name: str):
        trader = paper_trader_module.PaperTrader(
            db_path=tmp_path / f"{name}.db",
            startup_context="test",
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
        source_id=SOURCE_ID,
        void_refund=void_refund,
        previous_observation=previous,
        supersedes_observation_sha256=supersedes,
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


def _record_analysis(trader, analysis, *, trade_id: str) -> str:
    assert len(trade_id) == 12
    with (
        patch("trading.paper_trader.uuid.uuid4", return_value=trade_id),
        patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}),
    ):
        return trader.record_trade(analysis)


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


def _resolve(trader, observation: SettlementObservation):
    result = trader.resolve_observation(observation)
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
    trade_id = _record_mapped_trade(
        trader, market_ref, trade_id="void00000001"
    )
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
        "SELECT ticker, settled_at FROM paper_trades WHERE trade_id=?",
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
    assert payload["resolved_yes"] is None
    assert payload["terminal_state"] == "void"
    assert payload["won"] is None
    assert payload["settled_at"] == trade["settled_at"]
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


def test_mid_transaction_failure_rolls_back_finances_before_separate_quarantine(
    trader_factory,
):
    trader = trader_factory("mid-transaction-failure")
    market_ref = MarketRef(Venue.KALSHI, "KX-FAIL", "KX-FAIL")
    _record_mapped_trade(trader, market_ref, trade_id="failure00001")
    trader._conn.execute(
        """
        CREATE TRIGGER inject_outbox_failure
        BEFORE INSERT ON paper_settlement_outbox
        BEGIN
            SELECT RAISE(ABORT, 'injected outbox failure');
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


def test_runtime_callers_remain_unwired_from_canonical_resolver():
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
    assert "resolve_observation" not in main_calls | polymarket_calls
