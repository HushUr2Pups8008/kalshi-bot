"""Behavior contract for the unwired canonical paper-settlement path."""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.test_paper_trader import _cfg_module, _make_mock_analysis
from trading.portfolio import Portfolio
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
    with (
        patch("trading.paper_trader.uuid.uuid4", return_value=trade_id),
        patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}),
    ):
        return trader.record_trade(analysis)


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
    alias = "shared-display-alias"
    target = MarketRef(Venue.POLYMARKET_US, "8594", alias)
    other_venue = MarketRef(Venue.KALSHI, alias, alias)
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
        "BOT_STATE" in statement and "NOTIONAL_BANKROLL" in statement
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
    market_ref = MarketRef(Venue.POLYMARKET_US, "8594", "void-market")
    trade_id = _record_mapped_trade(
        trader, market_ref, trade_id="void00000001"
    )
    observation = _observation(
        market_ref,
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
        "SELECT outbox_id, payload_json FROM paper_settlement_outbox"
    ).fetchone()
    assert json.loads(outbox["payload_json"])["outcome"] == "void"
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
    assert all("calibr" not in name and "credib" not in name for name in consumers)


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
