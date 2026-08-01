from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from scripts import performance_analysis as performance_analysis
from scripts.performance_analysis import section_golive_readiness, section_placed_performance
from tests.test_paper_canonical_settlement import (
    _active_runtime_fee_net_trader,
    _kalshi_market_api_binary_payload,
    _make_mock_analysis,
    _observation,
    _record_analysis,
    _resolve,
)
from trading.venue import MarketRef, Venue
from trading.settlement import MarketOutcome
from trading.settlement_store import settlement_result_sha256


def test_placed_performance_separates_delivery_complete_paper_from_profit_evidence():
    report = section_placed_performance(
        [],
        [
            {
                "trade_id": "attested",
                "ticker": "KX-ATTESTED",
                "side": "yes",
                "ts": "2026-07-28T12:00:00Z",
                "resolved": 1,
                "pnl_dollars": 0.50,
                "cost_dollars": 0.40,
                "edge": 0.10,
                "settlement_observation_sha256": "a" * 64,
                "settlement_canonical_delivery_complete": True,
                "settlement_profit_receipt_attested": False,
            },
            {
                "trade_id": "legacy",
                "ticker": "KX-LEGACY",
                "side": "no",
                "ts": "2026-06-22T12:00:00Z",
                "resolved": 1,
                "pnl_dollars": -0.40,
                "cost_dollars": 0.40,
                "edge": 0.10,
                "settlement_observation_sha256": None,
                "settlement_canonical_delivery_complete": False,
                "settlement_profit_receipt_attested": False,
            },
        ],
        {},
    )

    assert "Canonical delivery-complete resolved: 1 | Paper net P&L: +$0.50" in report
    assert "Delivery-incomplete resolved: 1 | Paper net P&L: $-0.40" in report
    assert "Independent profit-attested resolved: 0 | Realized P&L: unavailable" in report
    assert "not independent realized-profit evidence" in report


def test_placed_performance_does_not_treat_a_bare_observation_hash_as_delivery_complete():
    report = section_placed_performance(
        [],
        [
            {
                "trade_id": "dangling",
                "ticker": "KX-DANGLING",
                "side": "yes",
                "ts": "2026-07-28T12:00:00Z",
                "resolved": 1,
                "pnl_dollars": 0.50,
                "cost_dollars": 0.40,
                "edge": 0.10,
                "settlement_observation_sha256": "a" * 64,
                "settlement_canonical_delivery_complete": False,
                "settlement_profit_receipt_attested": False,
            }
        ],
        {},
    )

    assert "Canonical delivery-complete resolved: 0 | Paper net P&L: +$0.00" in report
    assert "Delivery-incomplete resolved: 1 | Paper net P&L: +$0.50" in report


def test_golive_readiness_fails_closed_when_legacy_winners_are_delivery_incomplete(monkeypatch):
    monkeypatch.setenv("GO_LIVE_MIN_RESOLVED", "20")
    monkeypatch.setenv("GO_LIVE_MIN_WIN_RATE", "0.52")
    trades = [
        {
            "trade_id": f"legacy-{index}",
            "ts": f"2026-07-{index + 1:02d}T12:00:00+00:00",
            "resolved": 1,
            "pnl_dollars": 1.0,
            "notional_bankroll_before": 100.0,
            "settlement_canonical_delivery_complete": False,
            "settlement_profit_receipt_attested": False,
        }
        for index in range(20)
    ]

    report = section_golive_readiness(trades, {"notional_bankroll": "100.00"})

    assert "Canonical delivery-complete resolved: 0 / 20 required  [FAIL]" in report
    assert "20 delivery-incomplete resolved outcomes block paper-readiness" in report
    assert "Independent realized-profit evidence: unavailable  [FAIL]" in report
    assert "OVERALL: READY FOR LIVE TRADING" not in report


def test_load_db_trades_marks_hashes_unattested_when_settlement_state_is_unavailable(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "paper.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                resolved INTEGER NOT NULL,
                settlement_observation_sha256 TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO paper_trades VALUES ('dangling', '2026-07-28T12:00:00Z', 1, ?)",
            ("a" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(performance_analysis, "DB_PATH", db)

    trades = performance_analysis.load_db_trades()

    assert trades == [
        {
            "trade_id": "dangling",
            "ts": "2026-07-28T12:00:00Z",
            "resolved": 1,
            "settlement_observation_sha256": "a" * 64,
            "settlement_canonical_delivery_complete": False,
            "settlement_profit_receipt_attested": False,
        }
    ]


def test_golive_readiness_never_promotes_paper_delivery_to_realized_profit(monkeypatch):
    monkeypatch.setenv("GO_LIVE_MIN_RESOLVED", "20")
    monkeypatch.setenv("GO_LIVE_MIN_WIN_RATE", "0.52")
    trades = [
        {
            "trade_id": f"canonical-{index}",
            "ts": f"2026-07-{index + 1:02d}T12:00:00+00:00",
            "resolved": 1,
            "pnl_dollars": 1.0,
            "notional_bankroll_before": 100.0,
            "settlement_canonical_delivery_complete": True,
            "settlement_profit_receipt_attested": False,
        }
        for index in range(20)
    ]

    report = section_golive_readiness(trades, {"notional_bankroll": "100.00"})

    assert "Canonical delivery-complete resolved: 20 / 20 required  [PASS]" in report
    assert "Independent realized-profit evidence: unavailable  [FAIL]" in report
    assert "OVERALL: READY FOR LIVE TRADING" not in report


def test_load_db_trades_keeps_modeled_fee_net_delivery_unattested(
    monkeypatch,
    tmp_path,
):
    trader = _active_runtime_fee_net_trader(
        monkeypatch,
        tmp_path,
        cohort_id="active-modeled-readiness",
    )
    market_ref = MarketRef(
        Venue.KALSHI,
        "KX-ACCT-MODELED-READINESS",
        "KX-ACCT-MODELED-READINESS",
    )
    analysis = _make_mock_analysis(ticker=market_ref.alias, capped_dollars=12.0)
    analysis.venue = market_ref.venue.value
    analysis.market.venue = market_ref.venue.value
    analysis.market.market_id = market_ref.alias
    analysis.market.venue_market_id = market_ref.venue_market_id
    trade_id = _record_analysis(
        trader,
        analysis,
        trade_id="acctfee00011",
        entry_request_id="paper-entry:v1:active-modeled-readiness:lc-" + "r" * 32,
    )
    observation = _observation(
        market_ref,
        MarketOutcome.YES,
        payload=_kalshi_market_api_binary_payload(market_ref, MarketOutcome.YES),
        source_id="kalshi-market-api",
    )
    assert _resolve(trader, observation) is True
    with sqlite3.connect(trader.db_path) as conn:
        conn.row_factory = sqlite3.Row
        outbox = conn.execute(
            """
            SELECT outbox_id, created_at
            FROM paper_settlement_outbox
            WHERE trade_id=?
            """,
            (trade_id,),
        ).fetchone()
        assert outbox is not None
        created_at = datetime.fromisoformat(str(outbox["created_at"]))
        processed_at = (created_at + timedelta(seconds=1)).isoformat()
        requirements = conn.execute(
            """
            SELECT consumer_name
            FROM paper_settlement_outbox_requirements
            WHERE outbox_id=?
            ORDER BY consumer_name
            """,
            (outbox["outbox_id"],),
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO paper_settlement_consumer_receipts (
                consumer_name, outbox_id, processed_at, result_sha256
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    str(row["consumer_name"]),
                    str(outbox["outbox_id"]),
                    processed_at,
                    settlement_result_sha256(
                        str(outbox["outbox_id"]),
                        str(row["consumer_name"]),
                    ),
                )
                for row in requirements
            ],
        )
        conn.commit()
    monkeypatch.setattr(performance_analysis, "DB_PATH", trader.db_path)

    trades = performance_analysis.load_db_trades()

    assert len(trades) == 1
    assert trades[0]["trade_id"] == trade_id
    assert trades[0]["settlement_canonical_delivery_complete"] is True
    assert trades[0]["settlement_profit_receipt_attested"] is False

    report = section_golive_readiness(
        trades,
        {"notional_bankroll": "125.00"},
    )
    assert "Independent realized-profit evidence: unavailable  [FAIL]" in report
    assert "OVERALL: READY FOR LIVE TRADING" not in report
