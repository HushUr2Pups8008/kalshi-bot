import sqlite3
from pathlib import Path

from scripts import paper_performance_drilldown
from scripts.daily_review import build_daily_review
from scripts.edge_replay.post_fix_new_readiness_status import collect_readiness


def _paper_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO bot_state (key, value)
            VALUES ('p0_price_fix_deployed_ts', '2026-05-13T00:00:00+00:00');

            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                ticker TEXT NOT NULL,
                venue TEXT NOT NULL DEFAULT 'kalshi',
                side TEXT NOT NULL DEFAULT 'yes',
                contracts INTEGER NOT NULL DEFAULT 1,
                price_cents INTEGER NOT NULL DEFAULT 50,
                cost_dollars REAL NOT NULL DEFAULT 0.5,
                estimated_prob REAL NOT NULL DEFAULT 0.6,
                entry_price_cents REAL NOT NULL DEFAULT 50,
                edge REAL NOT NULL DEFAULT 0.1,
                kelly_dollars REAL NOT NULL DEFAULT 1.0,
                capped_dollars REAL NOT NULL DEFAULT 1.0,
                signal_headline TEXT NOT NULL DEFAULT '',
                signal_source TEXT NOT NULL DEFAULT 'Reuters',
                keywords_matched TEXT NOT NULL DEFAULT '[]',
                reasoning TEXT NOT NULL DEFAULT '',
                resolved INTEGER DEFAULT 0,
                resolved_yes INTEGER,
                pnl_dollars REAL,
                series_ticker TEXT DEFAULT 'KX-SERIES',
                signal_type TEXT DEFAULT 'news',
                news_class TEXT DEFAULT 'news',
                market_family TEXT DEFAULT 'politics',
                llm_confidence REAL DEFAULT 0.8,
                readiness_admitted INTEGER DEFAULT 1
            );
            """
        )
        rows = [
            (
                "k-win",
                "2026-05-13T00:05:00+00:00",
                "KX-KALSHI-1",
                "kalshi",
                1,
                1,
                2.5,
            ),
            (
                "k-open",
                "2026-05-13T00:06:00+00:00",
                "KX-KALSHI-2",
                "kalshi",
                0,
                None,
                None,
            ),
            (
                "pm-loss",
                "2026-05-13T00:07:00+00:00",
                "will-example-happen-2026",
                "polymarket_us",
                1,
                0,
                -1.5,
            ),
        ]
        conn.executemany(
            """
            INSERT INTO paper_trades
            (trade_id, ts, ticker, venue, resolved, resolved_yes, pnl_dollars)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_paper_performance_summarize_splits_mixed_venue_rows(tmp_path):
    db = tmp_path / "paper_trades.db"
    _paper_db(db)

    stats = paper_performance_drilldown.summarize(db)

    by_venue = {row["name"]: row for row in stats["venues"]}
    assert by_venue["kalshi"]["trades"] == 2
    assert by_venue["kalshi"]["resolved"] == 1
    assert by_venue["kalshi"]["pnl"] == 2.5
    assert by_venue["polymarket_us"]["trades"] == 1
    assert by_venue["polymarket_us"]["resolved"] == 1
    assert by_venue["polymarket_us"]["pnl"] == -1.5


def test_daily_review_renders_side_by_side_venue_sections(monkeypatch, tmp_path):
    db = tmp_path / "paper_trades.db"
    _paper_db(db)
    summarize = paper_performance_drilldown.summarize

    monkeypatch.setattr(
        "scripts.daily_review.paper_performance_drilldown.summarize",
        lambda *args, **kwargs: summarize(db),
    )

    lines = build_daily_review(
        trades_path=tmp_path / "trades.jsonl",
        paper_db_path=db,
        since=None,
        until=None,
        top=2,
        exclude_test=False,
    )

    rendered = "\n".join(lines)
    assert "Drilldown: paper performance by venue" in rendered
    assert "kalshi: trades=2 resolved=1 win_rate=100.0% pnl=+$2.50" in rendered
    assert "polymarket_us: trades=1 resolved=1 win_rate=0.0% pnl=$-1.50" in rendered


def test_post_fix_new_readiness_can_filter_by_venue(tmp_path):
    db = tmp_path / "paper_trades.db"
    _paper_db(db)

    kalshi = collect_readiness(
        db_path=db,
        clean_start_ts="2026-05-13T00:00:00+00:00",
        min_trades=2,
        min_tickers=2,
        venue="kalshi",
    )
    polymarket = collect_readiness(
        db_path=db,
        clean_start_ts="2026-05-13T00:00:00+00:00",
        min_trades=1,
        min_tickers=1,
        venue="polymarket_us",
    )

    assert kalshi["venue"] == "kalshi"
    assert kalshi["post_clean_start_row_count"] == 2
    assert kalshi["post_clean_start_distinct_tickers"] == 2
    assert polymarket["venue"] == "polymarket_us"
    assert polymarket["post_clean_start_row_count"] == 1
    assert polymarket["post_clean_start_distinct_tickers"] == 1
