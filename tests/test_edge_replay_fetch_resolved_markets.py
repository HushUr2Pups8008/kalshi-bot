import json
import sqlite3

from scripts.edge_replay.fetch_resolved_markets import load_markets_from_paper_trades_db, normalize_market, normalize_markets


def test_normalize_market_keeps_resolved_yes_no_market():
    market = normalize_market(
        {
            "ticker": "KXTEST-26MAY01",
            "title": "Will the test pass?",
            "status": "settled",
            "result": "yes",
            "yes_bid": 41,
            "yes_ask": 43,
            "yes_price": 42,
            "series_ticker": "KXTEST",
            "close_time": "2026-05-01T00:00:00Z",
        }
    )

    assert market == {
        "ticker": "KXTEST-26MAY01",
        "title": "Will the test pass?",
        "series_ticker": "KXTEST",
        "status": "settled",
        "resolved_yes": True,
        "result": "yes",
        "yes_price": 42.0,
        "close_time": "2026-05-01T00:00:00Z",
    }


def test_normalize_markets_filters_unresolved_and_bad_outcomes():
    payload = json.dumps(
        {
            "markets": [
                {"ticker": "KX1", "status": "settled", "result": "no", "yes_price": 55},
                {"ticker": "KX2", "status": "open", "result": "yes", "yes_price": 50},
                {"ticker": "KX3", "status": "finalized", "result": "draw", "yes_price": 50},
            ]
        }
    )

    rows = normalize_markets(json.loads(payload))

    assert [row["ticker"] for row in rows] == ["KX1"]
    assert rows[0]["resolved_yes"] is False


def test_load_markets_from_paper_trades_db_extracts_resolved_rows(tmp_path):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                ticker TEXT,
                market_title TEXT,
                series_ticker TEXT,
                resolved INTEGER,
                resolved_yes INTEGER,
                market_yes_price REAL,
                resolved_ts TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades VALUES (
                'KXTEST-26MAY01', 'Will the test pass?', 'KXTEST', 1, 1, 52.0,
                '2026-05-01T00:00:00+00:00'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    rows = load_markets_from_paper_trades_db(db_path)

    assert rows == [
        {
            "ticker": "KXTEST-26MAY01",
            "title": "Will the test pass?",
            "series_ticker": "KXTEST",
            "status": "settled",
            "resolved_yes": True,
            "result": "yes",
            "yes_price": 52.0,
            "close_time": "2026-05-01T00:00:00+00:00",
        }
    ]
