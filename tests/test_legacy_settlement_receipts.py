from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.profit_evidence_report import summarize_paper_expectancy
from trading.legacy_settlement_receipts import build_legacy_settlement_receipt
from trading.paper_trader import PaperTrader
from trading.settlement import MarketOutcome, build_settlement_observation
from trading.settlement_store import LegacyReceiptApplicationError, SettlementStore
from trading.venue import MarketRef, Venue


OBSERVED_AT = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
APPLIED_AT = OBSERVED_AT + timedelta(minutes=1)


def _legacy_root(path: Path) -> None:
    trader = PaperTrader(db_path=path, startup_context="test")
    trader._conn.close()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            UPDATE bot_state
            SET value='100.00'
            WHERE key='notional_bankroll'
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, venue, venue_market_id, identity_status,
                market_title, side, contracts, price_cents, cost_dollars,
                estimated_prob, entry_price_cents, edge, kelly_dollars,
                capped_dollars, signal_headline, signal_source, keywords_matched,
                reasoning
            ) VALUES (
                'legacy-trade-1', '2026-07-30T12:00:00+00:00',
                'legacy-exact-market', 'polymarket_us', '42', 'mapped',
                'Legacy exact market', 'yes', 2, 40, 0.80,
                0.61, 40, 0.21, 1.0, 1.0, 'legacy signal', 'legacy:test',
                '[]', 'legacy receipt fixture'
            )
            """
        )


def _receipt(
    *,
    outcome: MarketOutcome = MarketOutcome.YES,
    alias: str = "legacy-exact-market",
    effective_at: datetime | None = None,
):
    market_ref = MarketRef(
        Venue.POLYMARKET_US,
        "42",
        alias,
    )
    observation = build_settlement_observation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome={"outcome": outcome.value},
        authoritative_payload={"id": "42", "settled": True, "slug": market_ref.alias},
        observed_at=OBSERVED_AT,
        effective_at=effective_at or OBSERVED_AT - timedelta(minutes=1),
        rules_version="test-rules-v1",
        source_id="test-authoritative-source",
    )
    return build_legacy_settlement_receipt("legacy-trade-1", observation)


def _database_fingerprint(path: Path) -> tuple[object, ...]:
    tables = (
        "bot_state",
        "paper_trades",
        "paper_settlement_observations",
        "paper_legacy_settlement_receipt_applications",
        "paper_settlement_outbox",
        "paper_settlement_outbox_requirements",
        "paper_settlement_delivery_claims",
        "paper_settlement_consumer_receipts",
    )
    with sqlite3.connect(path) as conn:
        schema = tuple(
            conn.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        )
        rows: list[tuple[str, tuple[object, ...]]] = []
        for table in tables:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is not None:
                rows.append(
                    (
                        table,
                        tuple(conn.execute(f"SELECT * FROM {table} ORDER BY rowid")),
                    )
                )
    return schema, tuple(rows)


def _insert_second_market_lot(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, venue, venue_market_id, identity_status,
                market_title, side, contracts, price_cents, cost_dollars,
                estimated_prob, entry_price_cents, edge, kelly_dollars,
                capped_dollars, signal_headline, signal_source, keywords_matched,
                reasoning
            ) VALUES (
                'legacy-trade-2', '2026-07-30T12:01:00+00:00',
                'legacy-exact-market', 'polymarket_us', '42', 'mapped',
                'Legacy exact market', 'no', 1, 60, 0.60,
                0.39, 60, 0.21, 1.0, 1.0, 'legacy signal', 'legacy:test',
                '[]', 'legacy receipt fixture'
            )
            """
        )


def test_apply_legacy_directional_receipt_is_archival_and_conserves(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    receipt = _receipt()

    with SettlementStore(db_path) as store:
        result = store.apply_legacy_directional_receipt(
            receipt,
            applied_at=APPLIED_AT,
        )
        check = store.conservation(now=APPLIED_AT + timedelta(minutes=1))

    assert result.applied is True
    assert result.trade_id == "legacy-trade-1"
    assert result.observation_sha256 == receipt.observation.observation_sha256
    assert check.ok is True
    with sqlite3.connect(db_path) as conn:
        trade = conn.execute(
            """
            SELECT resolved, resolved_yes, terminal_state, pnl_dollars,
                   gross_payout_cents, gross_pnl_cents,
                   settlement_observation_sha256
            FROM paper_trades WHERE trade_id='legacy-trade-1'
            """
        ).fetchone()
        application = conn.execute(
            """
            SELECT trade_id, observation_sha256, receipt_schema_version,
                   receipt_json, receipt_sha256
            FROM paper_legacy_settlement_receipt_applications
            """
        ).fetchone()
        outbox_count = conn.execute(
            "SELECT COUNT(*) FROM paper_settlement_outbox"
        ).fetchone()[0]

    assert trade == (
        1,
        1,
        "won",
        1.20,
        "200",
        "120",
        receipt.observation.observation_sha256,
    )
    assert application is not None
    assert application[:3] == (
        "legacy-trade-1",
        receipt.observation.observation_sha256,
        1,
    )
    assert json.loads(application[3]) == receipt.to_dict()
    assert application[4] == receipt.receipt_sha256
    assert outbox_count == 0


def test_exact_replay_is_idempotent_without_rewriting_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    receipt = _receipt()

    with SettlementStore(db_path) as store:
        first = store.apply_legacy_directional_receipt(receipt, applied_at=APPLIED_AT)
    before = _database_fingerprint(db_path)
    with SettlementStore(db_path) as store:
        replay = store.apply_legacy_directional_receipt(receipt, applied_at=APPLIED_AT)
        check = store.conservation(now=APPLIED_AT + timedelta(minutes=1))
    after = _database_fingerprint(db_path)

    assert first.applied is True
    assert replay.applied is False
    assert replay.trade_id == first.trade_id
    assert replay.observation_sha256 == first.observation_sha256
    assert after == before
    assert check.ok is True


def test_no_outcome_is_gross_only_and_leaves_bankroll_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    receipt = _receipt(outcome=MarketOutcome.NO)

    with SettlementStore(db_path) as store:
        result = store.apply_legacy_directional_receipt(receipt, applied_at=APPLIED_AT)
        check = store.conservation(now=APPLIED_AT + timedelta(minutes=1))

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute(
            """
            SELECT resolved_yes, terminal_state, pnl_dollars,
                   gross_payout_cents, gross_pnl_cents
            FROM paper_trades WHERE trade_id='legacy-trade-1'
            """
        ).fetchone()
        bankroll = conn.execute(
            "SELECT value FROM bot_state WHERE key='notional_bankroll'"
        ).fetchone()[0]

    assert result.gross_payout_cents == "0"
    assert result.gross_pnl_cents == "-80"
    assert trade == (0, "lost", -0.8, "0", "-80")
    assert bankroll == "100"
    assert check.ok is True


def test_archival_receipt_is_excluded_from_profit_attested_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    with SettlementStore(db_path) as store:
        store.apply_legacy_directional_receipt(_receipt(), applied_at=APPLIED_AT)

    summary = summarize_paper_expectancy(db_path)

    assert summary.total_trades == 1
    assert summary.resolved_trades == 0
    assert summary.canonical_delivery_complete_resolved_trades == 0
    assert summary.profit_attested_resolved_trades == 0
    assert summary.net_pnl == 0.0


def test_naive_application_time_rejects_before_creating_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    before = _database_fingerprint(db_path)

    with SettlementStore(db_path) as store:
        with pytest.raises(LegacyReceiptApplicationError, match="application time"):
            store.apply_legacy_directional_receipt(
                _receipt(),
                applied_at=APPLIED_AT.replace(tzinfo=None),
            )

    assert _database_fingerprint(db_path) == before


def test_identity_mismatch_rolls_back_companion_schema_and_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    before = _database_fingerprint(db_path)

    with SettlementStore(db_path) as store:
        with pytest.raises(LegacyReceiptApplicationError, match="trade identity"):
            store.apply_legacy_directional_receipt(
                _receipt(alias="wrong-market-alias"),
                applied_at=APPLIED_AT,
            )

    assert _database_fingerprint(db_path) == before


def test_second_market_lot_rejects_ambiguous_receipt_without_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    _insert_second_market_lot(db_path)
    before = _database_fingerprint(db_path)

    with SettlementStore(db_path) as store:
        with pytest.raises(LegacyReceiptApplicationError, match="exactly one trade"):
            store.apply_legacy_directional_receipt(_receipt(), applied_at=APPLIED_AT)

    assert _database_fingerprint(db_path) == before


def test_archival_receipt_conservation_rejects_later_second_market_lot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    receipt = _receipt()
    with SettlementStore(db_path) as store:
        store.apply_legacy_directional_receipt(receipt, applied_at=APPLIED_AT)
    _insert_second_market_lot(db_path)

    with SettlementStore(db_path) as store:
        check = store.conservation(now=APPLIED_AT + timedelta(minutes=1))

    assert check.ok is False
    assert (
        f"legacy_receipt_application:{receipt.observation.observation_sha256}:legacy-trade-1"
        in check.failures
    )


def test_observation_effective_before_trade_entry_rolls_back(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    before = _database_fingerprint(db_path)
    stale_receipt = _receipt(effective_at=OBSERVED_AT - timedelta(days=2))

    with SettlementStore(db_path) as store:
        with pytest.raises(LegacyReceiptApplicationError, match="predates trade entry"):
            store.apply_legacy_directional_receipt(stale_receipt, applied_at=APPLIED_AT)

    assert _database_fingerprint(db_path) == before


def test_conflicting_replay_rejects_without_mutating_prior_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    with SettlementStore(db_path) as store:
        store.apply_legacy_directional_receipt(_receipt(), applied_at=APPLIED_AT)
    before = _database_fingerprint(db_path)

    with SettlementStore(db_path) as store:
        with pytest.raises(LegacyReceiptApplicationError, match="conflicts"):
            store.apply_legacy_directional_receipt(
                _receipt(outcome=MarketOutcome.NO),
                applied_at=APPLIED_AT,
            )

    assert _database_fingerprint(db_path) == before


def test_trade_update_failure_rolls_back_observation_and_receipt_schema(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_legacy_trade_update
            BEFORE UPDATE ON paper_trades
            WHEN NEW.trade_id='legacy-trade-1'
            BEGIN
                SELECT RAISE(ABORT, 'test rollback');
            END
            """
        )
    before = _database_fingerprint(db_path)

    with SettlementStore(db_path) as store:
        with pytest.raises(LegacyReceiptApplicationError, match="transaction failed"):
            store.apply_legacy_directional_receipt(_receipt(), applied_at=APPLIED_AT)

    assert _database_fingerprint(db_path) == before


def test_archival_receipt_application_is_immutable(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    with SettlementStore(db_path) as store:
        store.apply_legacy_directional_receipt(_receipt(), applied_at=APPLIED_AT)
    before = _database_fingerprint(db_path)

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """
                UPDATE paper_legacy_settlement_receipt_applications
                SET applied_at='2026-07-31T12:02:00+00:00'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM paper_legacy_settlement_receipt_applications")

    assert _database_fingerprint(db_path) == before


def test_archival_receipt_with_normal_outbox_fails_as_unlinked(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    receipt = _receipt()
    with SettlementStore(db_path) as store:
        store.apply_legacy_directional_receipt(receipt, applied_at=APPLIED_AT)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO paper_settlement_outbox (
                outbox_id, event_version, event_kind, observation_sha256,
                trade_id, payload_json, created_at
            ) VALUES ('unexpected-legacy-outbox', 1, 'paper_trade_settled', ?, ?, '{}', ?)
            """,
            (
                receipt.observation.observation_sha256,
                receipt.trade_id,
                APPLIED_AT.isoformat(),
            ),
        )

    with SettlementStore(db_path) as store:
        check = store.conservation(now=APPLIED_AT + timedelta(minutes=1))

    assert check.ok is False
    assert (
        "outbox_unlinked:unexpected-legacy-outbox" in check.failures
    )


def test_apply_rolls_back_when_trigger_creates_normal_outbox(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    receipt = _receipt()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TRIGGER inject_legacy_receipt_outbox
            AFTER INSERT ON paper_settlement_observations
            WHEN NEW.observation_sha256='{receipt.observation.observation_sha256}'
            BEGIN
                INSERT INTO paper_settlement_outbox (
                    outbox_id, event_version, event_kind, observation_sha256,
                    trade_id, payload_json, created_at
                ) VALUES (
                    'injected-legacy-outbox', 1, 'paper_trade_settled',
                    NEW.observation_sha256, 'legacy-trade-1', '{{}}',
                    '{APPLIED_AT.isoformat()}'
                );
            END
            """
        )
    before = _database_fingerprint(db_path)

    with SettlementStore(db_path) as store:
        with pytest.raises(LegacyReceiptApplicationError, match="normal outbox"):
            store.apply_legacy_directional_receipt(receipt, applied_at=APPLIED_AT)

    assert _database_fingerprint(db_path) == before


def test_apply_rolls_back_on_new_conservation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "legacy-paper.db"
    _legacy_root(db_path)
    before = _database_fingerprint(db_path)
    original_conservation = SettlementStore.conservation
    calls = 0

    def inject_post_apply_failure(self, *, now: datetime):
        nonlocal calls
        check = original_conservation(self, now=now)
        calls += 1
        if calls == 2:
            return type(check)(
                ok=False,
                failures=(*check.failures, "injected_post_apply_failure"),
                metrics=check.metrics,
            )
        return check

    monkeypatch.setattr(SettlementStore, "conservation", inject_post_apply_failure)
    with SettlementStore(db_path) as store:
        with pytest.raises(LegacyReceiptApplicationError, match="conservation postcondition"):
            store.apply_legacy_directional_receipt(_receipt(), applied_at=APPLIED_AT)

    assert calls == 2
    assert _database_fingerprint(db_path) == before
