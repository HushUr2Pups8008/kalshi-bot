import logging
import sqlite3

import pytest

from polymarket.settlement_reconciler import (
    PolymarketPublicSettlementSource,
    SettlementDriftError,
    SettlementNotFound,
    SettlementReconciler,
    _resolved_yes_from_payload,
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, True), ("1", True), (0, False), ("0", False)],
)
def test_authoritative_settlement_values(value, expected):
    assert _resolved_yes_from_payload(
        "will-example-happen", {"settlement": value}
    ) is expected


@pytest.mark.parametrize(
    "value", [0.5, "0.5", float("nan"), float("inf"), -1, 2]
)
def test_nonbinary_settlement_values_fail_closed(value):
    with pytest.raises(SettlementDriftError, match="nonbinary settlement"):
        _resolved_yes_from_payload(
            "will-example-happen", {"settlement": value}
        )


@pytest.mark.parametrize("value", [True, False])
def test_boolean_settlement_values_fail_closed(value):
    with pytest.raises(SettlementDriftError, match="boolean settlement"):
        _resolved_yes_from_payload(
            "will-example-happen", {"settlement": value}
        )


def test_unrepresentable_settlement_value_is_hard_drift():
    with pytest.raises(SettlementDriftError, match="nonnumeric settlement"):
        _resolved_yes_from_payload(
            "will-example-happen", {"settlement": 10**10000}
        )


def test_outcome_prices_without_authoritative_result_fail_closed():
    payload = {
        "closed": True,
        "outcomes": '["No","Yes"]',
        "outcomePrices": '["0","1"]',
    }

    with pytest.raises(SettlementDriftError, match="authoritative"):
        _resolved_yes_from_payload("will-example-happen", payload)


def test_missing_settlement_payload_fails_closed():
    with pytest.raises(SettlementDriftError, match="authoritative"):
        _resolved_yes_from_payload("will-example-happen", {})


def test_explicit_resolved_outcome_compatibility_is_preserved():
    assert _resolved_yes_from_payload(
        "will-example-happen", {"settled": True, "resolvedOutcome": "YES"}
    ) is True


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


def test_reconciler_isolates_unexpected_error_and_settles_later_ticker(conn, caplog):
    # WHY: a single get_settlement raising an UNEXPECTED (non-SettlementNotFound)
    # error -- e.g. the public_client Value('... not found') leaking pre-fix --
    # must NOT abort the whole remaining Polymarket batch for the cycle. The
    # latent risk is the first real PM resolution being silently skipped because
    # it shares a cycle behind an unfound long-dated midterm slug. The bad
    # ticker is isolated, LOGGED LOUDLY with its ticker (settlement state +
    # observability path -- never silently swallowed), and the LATER ticker
    # still resolves YES.
    _insert_trade(conn, "pm-bad", "ewc-usse-me-2026-11-03-dem")
    _insert_trade(conn, "pm-good", "will-real-resolution-2026")
    source = FakeSettlementSource(
        {
            "ewc-usse-me-2026-11-03-dem": ValueError(
                "Polymarket market 'ewc-usse-me-2026-11-03-dem' not found"
            ),
            "will-real-resolution-2026": {"settled": True, "resolvedOutcome": "YES"},
        }
    )
    resolver = FakeResolver(conn)

    with caplog.at_level(logging.WARNING, logger="polymarket_settlement"):
        result = SettlementReconciler(source=source, resolver=resolver).reconcile()

    # Both tickers were attempted -- the bad one did not short-circuit the loop.
    assert source.calls == [
        "ewc-usse-me-2026-11-03-dem",
        "will-real-resolution-2026",
    ]
    # The later, genuinely-settled ticker STILL resolves YES.
    assert result.resolved == 1
    assert resolver.resolved == [("will-real-resolution-2026", True)]
    good_row = conn.execute(
        "SELECT resolved, resolved_yes FROM paper_trades WHERE ticker = ?",
        ("will-real-resolution-2026",),
    ).fetchone()
    assert good_row["resolved"] == 1
    assert good_row["resolved_yes"] == 1
    # The bad ticker is counted as an isolated error, not as not_found.
    assert result.errors == 1
    assert result.not_found == 0
    # LOUD log carries the ticker so operators can see the isolated failure.
    assert any(
        record.levelno >= logging.ERROR
        and "ewc-usse-me-2026-11-03-dem" in record.getMessage()
        for record in caplog.records
    )


def test_reconciler_isolates_arbitrary_unexpected_exception_and_logs(conn, caplog):
    # WHY: the safety net is for ANY unexpected exception, not just the known
    # not-found ValueError. A surprise RuntimeError on one ticker must be
    # isolated + logged (never silent) so one bad ticker cannot abort the batch.
    _insert_trade(conn, "pm-boom", "surprise-explosion-2026")
    _insert_trade(conn, "pm-ok", "will-still-resolve-2026")
    source = FakeSettlementSource(
        {
            "surprise-explosion-2026": RuntimeError("totally unexpected boom"),
            "will-still-resolve-2026": {"settled": True, "resolvedOutcome": "NO"},
        }
    )
    resolver = FakeResolver(conn)

    with caplog.at_level(logging.WARNING, logger="polymarket_settlement"):
        result = SettlementReconciler(source=source, resolver=resolver).reconcile()

    assert result.errors == 1
    assert result.resolved == 1
    assert resolver.resolved == [("will-still-resolve-2026", False)]
    # Not silent: the surprise error is logged with the offending ticker.
    assert any(
        record.levelno >= logging.ERROR
        and "surprise-explosion-2026" in record.getMessage()
        for record in caplog.records
    )


def test_public_source_translates_not_found_valueerror_to_settlement_not_found():
    # WHY: PolymarketPublicSettlementSource.get_settlement documents
    # "raise SettlementNotFound" but the underlying public_client raises
    # ValueError('... not found') for unfound slugs. The KNOWN not-found case
    # must flow through reconcile()'s existing not_found path, not escape as a
    # raw ValueError that aborts the batch.
    class NotFoundClient:
        def get_market_settlement(self, market_id):
            raise ValueError(f"Polymarket market {market_id!r} not found")

    source = PolymarketPublicSettlementSource(client=NotFoundClient())

    with pytest.raises(SettlementNotFound):
        source.get_settlement("ewc-usse-me-2026-11-03-dem")


def test_public_source_does_not_mask_unrelated_valueerror():
    # WHY: only the not-found ValueError is translated. A different ValueError
    # (a real defect, e.g. malformed payload shape) must NOT be masked as
    # SettlementNotFound -- it must propagate so the batch-level safety net can
    # log it loudly instead of silently counting it as "not settled yet".
    class BrokenClient:
        def get_market_settlement(self, market_id):
            raise ValueError("Polymarket market payload must be an object")

    source = PolymarketPublicSettlementSource(client=BrokenClient())

    with pytest.raises(ValueError) as excinfo:
        source.get_settlement("will-example-happen-2026")
    assert not isinstance(excinfo.value, SettlementNotFound)


def test_public_source_uses_dedicated_settlement_endpoint():
    class SettlementClient:
        def __init__(self):
            self.calls = []

        def get_market_settlement(self, market_id):
            self.calls.append(market_id)
            return {"slug": market_id, "settlement": 1}

    client = SettlementClient()

    payload = PolymarketPublicSettlementSource(client=client).get_settlement(
        "will-example-happen-2026"
    )

    assert payload["settlement"] == 1
    assert client.calls == ["will-example-happen-2026"]


def test_reconciler_still_halts_on_settlement_drift_error(conn):
    # WHY: SettlementDriftError is an explicit payload-shape-violation HARD HALT
    # (a settled payload we cannot interpret is a data-integrity problem, not a
    # transient per-ticker miss). The new per-ticker safety net must NOT degrade
    # this into a swallowed, isolated error -- drift still propagates out of
    # reconcile() so the operator sees a hard failure. Pins the boundary between
    # "isolate + continue" (unexpected) and "halt" (drift).
    _insert_trade(conn, "pm-drift", "will-drift-2026")
    source = FakeSettlementSource(
        {"will-drift-2026": {"settled": True, "resolvedOutcome": "MAYBE"}}
    )
    resolver = FakeResolver(conn)

    with pytest.raises(SettlementDriftError, match="resolvedOutcome"):
        SettlementReconciler(source=source, resolver=resolver).reconcile()
