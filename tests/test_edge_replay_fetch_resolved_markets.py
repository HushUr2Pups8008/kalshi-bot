import json
import sqlite3

from scripts.edge_replay.fetch_resolved_markets import (
    fetch_live_kalshi_markets,
    fetch_live_kalshi_markets_by_ticker,
    load_evidence_store_tickers,
    load_markets_from_paper_trades_db,
    normalize_market,
    normalize_markets,
)


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
                entry_price_cents REAL,
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


def test_load_markets_from_paper_trades_db_accepts_legacy_price_column(tmp_path):
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
                'KXLEG-26MAY01', 'Legacy price?', 'KXLEG', 1, 0, 49.0,
                '2026-05-01T00:00:00+00:00'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    rows = load_markets_from_paper_trades_db(db_path)

    assert rows[0]["yes_price"] == 49.0


def test_load_evidence_store_tickers_reads_distinct_market_tickers(tmp_path):
    db_path = tmp_path / "evidence_store.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE evidence (market_ticker TEXT)")
        conn.executemany("INSERT INTO evidence VALUES (?)", [("KX1",), ("KX2",), ("KX1",)])
        conn.commit()
    finally:
        conn.close()

    assert load_evidence_store_tickers(db_path) == {"KX1", "KX2"}


def test_fetch_live_kalshi_markets_queries_settled_and_finalized_and_intersects_evidence():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def get_markets(self, *, status, limit, cursor, min_close_ts=None, max_close_ts=None):
            self.calls.append((status, limit, cursor, min_close_ts, max_close_ts))
            rows = {
                "settled": [
                    type("Market", (), {"ticker": "KX1", "title": "one", "series_ticker": "KX", "status": "settled", "result": "yes", "yes_price": 50, "close_time": "2026-05-01"})(),
                    type("Market", (), {"ticker": "KXIGNORE", "title": "ignore", "series_ticker": "KX", "status": "settled", "result": "yes", "yes_price": 50, "close_time": "2026-05-01"})(),
                ],
                "finalized": [
                    type("Market", (), {"ticker": "KX2", "title": "two", "series_ticker": "KX", "status": "finalized", "result": "no", "yes_price": 50, "close_time": "2026-05-02"})(),
                ],
            }[status]
            return rows, None

    client = FakeClient()

    rows = fetch_live_kalshi_markets(client=client, statuses=["settled", "finalized"], evidence_tickers={"KX1", "KX2"}, page_limit=10)

    assert [row["ticker"] for row in rows] == ["KX1", "KX2"]
    assert [call[0] for call in client.calls] == ["settled", "finalized"]


def test_fetch_live_kalshi_markets_by_ticker_filters_unresolved():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def get_market(self, ticker):
            self.calls.append(ticker)
            if ticker == "KX1":
                return type("Market", (), {"ticker": "KX1", "title": "one", "series_ticker": "KX", "status": "settled", "result": "yes", "yes_price": 50, "close_time": "2026-05-01"})()
            return type("Market", (), {"ticker": ticker, "title": "open", "series_ticker": "KX", "status": "open", "result": "", "yes_price": 50, "close_time": "2026-05-01"})()

    client = FakeClient()

    rows = fetch_live_kalshi_markets_by_ticker(client=client, tickers={"KX1", "KX2"}, sleep_seconds=0.0)

    assert rows == [
        {
            "ticker": "KX1",
            "title": "one",
            "series_ticker": "KX",
            "status": "settled",
            "resolved_yes": True,
            "result": "yes",
            "yes_price": 50.0,
            "close_time": "2026-05-01",
        }
    ]
