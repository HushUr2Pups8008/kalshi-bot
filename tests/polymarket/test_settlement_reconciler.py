import sqlite3

import pytest

from polymarket.settlement_reconciler import (
    SettlementDriftError,
    SettlementNotFound,
    SettlementReconciler,
)


class FakeSettlementSource:
    def __init__(self, settlements):
        self.settlements = settlements
        self.calls = []

    def get_settlement(self, market_id: str):
        self.calls.append(market_id)
        result = self.settlements[market_id]
        if isinstance(result, Exception):
            raise result
        return result


class FakeResolver:
    def __init__(self, conn):
        self._conn = conn
        self.resolved = []

    def _resolve_market_sync(self, ticker: str, resolved_yes: bool):
        self.resolved.append((ticker, resolved_yes))
        self._conn.execute(
            """
            UPDATE paper_trades
            SET resolved = 1, resolved_yes = ?
            WHERE ticker = ? AND resolved = 0
            """,
            (int(resolved_yes), ticker),
        )
        self._conn.commit()
        return []


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            venue TEXT NOT NULL DEFAULT 'kalshi',
            side TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            cost_dollars REAL NOT NULL,
            price_cents INTEGER NOT NULL,
            estimated_prob REAL NOT NULL,
            entry_price_cents REAL NOT NULL,
            ts TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            resolved_yes INTEGER,
            pnl_dollars REAL
        )
        """
    )
    yield db
    db.close()


def _insert_trade(conn, trade_id, ticker, *, venue="polymarket_us", resolved=0):
    conn.execute(
        """
        INSERT INTO paper_trades
        (trade_id, ticker, venue, side, contracts, cost_dollars, price_cents,
         estimated_prob, entry_price_cents, ts, resolved)
        VALUES (?, ?, ?, 'yes', 5, 2.0, 40, 0.6, 40.0,
                '2026-01-01T00:00:00+00:00', ?)
        """,
        (trade_id, ticker, venue, resolved),
    )
    conn.commit()


def test_reconciler_resolves_polymarket_yes_settlement(conn):
    _insert_trade(conn, "pm-yes", "will-example-happen-2026")
    source = FakeSettlementSource(
        {"will-example-happen-2026": {"settled": True, "resolvedOutcome": "YES"}}
    )
    resolver = FakeResolver(conn)

    result = SettlementReconciler(source=source, resolver=resolver).reconcile()

    assert result.checked == 1
    assert result.resolved == 1
    assert resolver.resolved == [("will-example-happen-2026", True)]
    row = conn.execute("SELECT resolved, resolved_yes FROM paper_trades").fetchone()
    assert row["resolved"] == 1
    assert row["resolved_yes"] == 1


def test_reconciler_returns_lane_events_for_calibration_emission(conn):
    class LaneResolver(FakeResolver):
        def _resolve_market_sync(self, ticker: str, resolved_yes: bool):
            super()._resolve_market_sync(ticker, resolved_yes)
            return [("trade-1", "fast", 0.62)]

    _insert_trade(conn, "pm-yes", "will-example-happen-2026")
    source = FakeSettlementSource(
        {"will-example-happen-2026": {"settled": True, "resolvedOutcome": "YES"}}
    )
    resolver = LaneResolver(conn)

    result = SettlementReconciler(source=source, resolver=resolver).reconcile()

    assert result.resolved == 1
    assert result.lane_events == (
        ("will-example-happen-2026", True, "trade-1", "fast", 0.62),
    )


def test_reconciler_resolves_polymarket_no_settlement(conn):
    _insert_trade(conn, "pm-no", "will-example-fail-2026")
    source = FakeSettlementSource(
        {"will-example-fail-2026": {"settled": True, "resolvedOutcome": "NO"}}
    )
    resolver = FakeResolver(conn)

    result = SettlementReconciler(source=source, resolver=resolver).reconcile()

    assert result.checked == 1
    assert result.resolved == 1
    assert resolver.resolved == [("will-example-fail-2026", False)]


def test_reconciler_noops_when_settlement_not_found(conn):
    _insert_trade(conn, "pm-open", "will-stay-open-2026")
    source = FakeSettlementSource(
        {"will-stay-open-2026": SettlementNotFound("Settlement not found")}
    )
    resolver = FakeResolver(conn)

    result = SettlementReconciler(source=source, resolver=resolver).reconcile()

    assert result.checked == 1
    assert result.not_found == 1
    assert result.resolved == 0
    assert resolver.resolved == []
    row = conn.execute("SELECT resolved, resolved_yes FROM paper_trades").fetchone()
    assert row["resolved"] == 0
    assert row["resolved_yes"] is None


def test_reconciler_drift_halts_on_malformed_settled_payload(conn):
    _insert_trade(conn, "pm-bad", "will-bad-payload-2026")
    source = FakeSettlementSource(
        {"will-bad-payload-2026": {"settled": True, "resolvedOutcome": "MAYBE"}}
    )
    resolver = FakeResolver(conn)

    with pytest.raises(SettlementDriftError, match="resolvedOutcome"):
        SettlementReconciler(source=source, resolver=resolver).reconcile()

    assert resolver.resolved == []
    row = conn.execute("SELECT resolved FROM paper_trades").fetchone()
    assert row["resolved"] == 0


def test_reconciler_ignores_kalshi_and_resolved_rows(conn):
    _insert_trade(conn, "kalshi-open", "KXTEST-26DEC31", venue="kalshi")
    _insert_trade(conn, "pm-resolved", "already-settled-2026", resolved=1)
    source = FakeSettlementSource({})
    resolver = FakeResolver(conn)

    result = SettlementReconciler(source=source, resolver=resolver).reconcile()

    assert result.checked == 0
    assert source.calls == []
    assert resolver.resolved == []
