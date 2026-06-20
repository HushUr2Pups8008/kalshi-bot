import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.simulations.sizing_policy_replay import (
    PolicyParams,
    evaluate_grid,
    load_paper_db,
    load_evidence,
)


def _trade(
    *,
    ts: str,
    ticker: str,
    prefix: str,
    venue: str = "kalshi",
    price_cents: float = 50.0,
    probability: float = 0.62,
    pnl: float = 1.0,
    source_class: str = "official",
    side: str = "yes",
    contracts: int = 1,
    floor_clamped: bool = False,
) -> dict:
    return {
        "ts": ts,
        "ticker": ticker,
        "prefix": prefix,
        "venue": venue,
        "side": side,
        "price_cents": price_cents,
        "estimated_probability": probability,
        "edge": probability - (price_cents / 100.0),
        "pnl": pnl,
        "contracts": contracts,
        "source_class": source_class,
        "confidence": 1.0,
        "source_multiplier": 1.0,
        "floor_clamped": floor_clamped,
        "days_to_close": 1.0,
        "resolution_ts": "2026-06-20T23:00:00Z",
    }


def test_evaluate_grid_reports_required_metrics_and_guardrails():
    records = [
        _trade(ts="2026-06-20T00:00:00Z", ticker="KXALPHA-1", prefix="KXALPHA", pnl=1.0),
        _trade(ts="2026-06-20T00:10:00Z", ticker="KXALPHA-1", prefix="KXALPHA", pnl=1.0),
        _trade(ts="2026-06-20T01:00:00Z", ticker="KXALPHA-2", prefix="KXALPHA", pnl=-0.5),
        _trade(
            ts="2026-06-20T02:00:00Z",
            ticker="PM-SPORT-1",
            prefix="PM-SPORT",
            venue="polymarket_us",
            pnl=1.0,
        ),
    ]
    policies = [
        PolicyParams(
            kelly_fraction=0.5,
            floor_clamp_kelly_multiplier=0.5,
            max_ticker_exposure_pct=0.50,
            paper_ticker_cooldown=3600,
            max_open_positions_per_prefix=1,
        ),
        PolicyParams(
            kelly_fraction=0.25,
            floor_clamp_kelly_multiplier=1.0,
            max_ticker_exposure_pct=0.01,
            paper_ticker_cooldown=0,
            max_open_positions_per_prefix=10,
        ),
    ]

    report = evaluate_grid(records, policies, bankroll=100.0, max_bet_dollars=20.0, min_edge=0.01)

    assert report["read_only"] is True
    assert report["no_live_policy_change"] is True
    assert report["input"]["records"] == 4
    assert len(report["results"]) == 2

    strict = report["results"][0]
    assert strict["simulated_trade_count"] == 2
    assert strict["skipped_by_category"] == {"concentration": 1, "cooldown": 1}
    assert strict["net_pnl"] == pytest.approx(48.0)
    assert strict["expectancy"] == pytest.approx(24.0)
    assert strict["max_drawdown"] == 0.0
    assert strict["venue_split"]["kalshi"]["trades"] == 1
    assert strict["venue_split"]["polymarket_us"]["trades"] == 1
    assert strict["concentration_hits"] == [
        {
            "ticker": "KXALPHA-2",
            "prefix": "KXALPHA",
            "category": "concentration",
            "reason": "per-prefix cap: 1 open in KXALPHA (cap=1)",
        }
    ]

    exposure_limited = report["results"][1]
    assert exposure_limited["simulated_trade_count"] == 0
    assert exposure_limited["skipped_by_category"] == {"concentration": 4}


def test_floor_clamp_multiplier_reduces_simulated_contracts():
    records = [
        _trade(
            ts="2026-06-20T00:00:00Z",
            ticker="KXFLOOR-1",
            prefix="KXFLOOR",
            price_cents=50.0,
            probability=0.80,
            pnl=1.0,
            floor_clamped=True,
        )
    ]

    report = evaluate_grid(
        records,
        [
            PolicyParams(0.5, 1.0, 1.0, 0, 10),
            PolicyParams(0.5, 0.25, 1.0, 0, 10),
        ],
        bankroll=100.0,
        max_bet_dollars=50.0,
        min_edge=0.01,
    )

    full = report["results"][0]["simulated_trades"][0]
    clamped = report["results"][1]["simulated_trades"][0]
    assert full["contracts"] == 60
    assert clamped["contracts"] == 15
    assert clamped["pnl"] == pytest.approx(15.0)


def test_load_evidence_accepts_json_array_and_jsonl(tmp_path):
    json_array = tmp_path / "trades.json"
    jsonl = tmp_path / "trades.jsonl"
    first = _trade(ts="2026-06-20T00:00:00Z", ticker="KXLOAD-1", prefix="KXLOAD")
    second = _trade(ts="2026-06-20T01:00:00Z", ticker="KXLOAD-2", prefix="KXLOAD")
    json_array.write_text(json.dumps([first]), encoding="utf-8")
    jsonl.write_text(json.dumps(second) + "\nnot-json\n", encoding="utf-8")

    assert [r["ticker"] for r in load_evidence([json_array, jsonl])] == ["KXLOAD-1", "KXLOAD-2"]


def test_load_paper_db_maps_resolved_trades_to_replay_records(tmp_path):
    db_path = tmp_path / "paper_trades.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                ts TEXT,
                ticker TEXT,
                venue TEXT,
                side TEXT,
                entry_price_cents REAL,
                estimated_prob REAL,
                edge REAL,
                pnl_dollars REAL,
                contracts INTEGER,
                resolved INTEGER,
                source_class TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades VALUES (
                '2026-06-20T00:00:00Z', 'KXDB-1', 'kalshi', 'YES',
                50.0, 0.62, 0.12, 2.0, 2, 1, 'official'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades VALUES (
                '2026-06-20T01:00:00Z', 'KXDB-OPEN', 'kalshi', 'YES',
                50.0, 0.62, 0.12, NULL, 2, 0, 'official'
            )
            """
        )

    records = load_paper_db(db_path)

    assert records == [
        {
            "ts": "2026-06-20T00:00:00Z",
            "ticker": "KXDB-1",
            "venue": "kalshi",
            "side": "YES",
            "price_cents": 50.0,
            "estimated_probability": 0.62,
            "edge": 0.12,
            "pnl": 2.0,
            "contracts": 2,
            "source_class": "official",
            "resolution_ts": "2026-06-20T00:00:00Z",
        }
    ]


def test_db_rows_without_close_time_do_not_remain_open_forever(tmp_path):
    db_path = tmp_path / "paper_trades.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                ts TEXT,
                ticker TEXT,
                venue TEXT,
                side TEXT,
                entry_price_cents REAL,
                estimated_prob REAL,
                edge REAL,
                pnl_dollars REAL,
                contracts INTEGER,
                resolved INTEGER,
                source_class TEXT
            )
            """
        )
        for ticker, ts in [
            ("KXDB-A", "2026-06-20T00:00:00Z"),
            ("KXDB-B", "2026-06-20T01:00:00Z"),
        ]:
            conn.execute(
                """
                INSERT INTO paper_trades VALUES (
                    ?, ?, 'kalshi', 'YES', 50.0, 0.62, 0.12, 1.0, 1, 1, 'official'
                )
                """,
                (ts, ticker),
            )

    records = load_paper_db(db_path)
    report = evaluate_grid(
        records,
        [PolicyParams(0.5, 1.0, 1.0, 0, 1)],
        bankroll=100.0,
        max_bet_dollars=20.0,
        min_edge=0.01,
    )

    assert report["results"][0]["simulated_trade_count"] == 2
    assert report["results"][0]["skipped_by_category"] == {}


def test_cli_prints_guardrail_lines_and_json(tmp_path):
    evidence = tmp_path / "trades.jsonl"
    evidence.write_text(
        json.dumps(_trade(ts="2026-06-20T00:00:00Z", ticker="KXCLI-1", prefix="KXCLI")) + "\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/simulations/sizing_policy_replay.py",
            "--input",
            str(evidence),
            "--kelly-fraction",
            "0.5",
            "--floor-clamp-kelly-multiplier",
            "0.5",
            "--max-ticker-exposure-pct",
            "1.0",
            "--paper-ticker-cooldown",
            "0",
            "--max-open-positions-per-prefix",
            "10",
            "--json",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "read_only=true\n" in proc.stdout
    assert "no_live_policy_change=true\n" in proc.stdout
    payload = json.loads(proc.stdout[proc.stdout.index("{") :])
    assert payload["read_only"] is True
    assert payload["no_live_policy_change"] is True
    assert payload["results"][0]["simulated_trade_count"] == 1


def test_cli_accepts_paper_db_snapshot(tmp_path):
    db_path = tmp_path / "paper_trades.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                ts TEXT,
                ticker TEXT,
                venue TEXT,
                side TEXT,
                entry_price_cents REAL,
                estimated_prob REAL,
                edge REAL,
                pnl_dollars REAL,
                contracts INTEGER,
                resolved INTEGER,
                source_class TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades VALUES (
                '2026-06-20T00:00:00Z', 'KXDBCLI-1', 'kalshi', 'YES',
                50.0, 0.62, 0.12, 1.0, 1, 1, 'official'
            )
            """
        )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/simulations/sizing_policy_replay.py",
            "--paper-db",
            str(db_path),
            "--paper-ticker-cooldown",
            "0",
            "--max-open-positions-per-prefix",
            "10",
            "--max-ticker-exposure-pct",
            "1.0",
            "--json",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    payload = json.loads(proc.stdout[proc.stdout.index("{") :])
    assert payload["input"]["records"] == 1
    assert payload["results"][0]["simulated_trade_count"] == 1
