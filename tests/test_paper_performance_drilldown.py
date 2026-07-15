import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import paper_performance_drilldown
from scripts.paper_performance_drilldown import print_summary, summarize

BASE_COLUMNS = [
    ("trade_id", "TEXT PRIMARY KEY"),
    ("ts", "TEXT"),
    ("ticker", "TEXT"),
    ("signal_source", "TEXT"),
    ("resolved", "INTEGER"),
    ("pnl_dollars", "REAL"),
]


OPTIONAL_COLUMNS = [
    ("resolved_ts", "TEXT"),
    ("signal_type", "TEXT"),
    ("series_ticker", "TEXT"),
    ("venue", "TEXT"),
    ("side", "TEXT"),
    ("contracts", "INTEGER"),
    ("cost_dollars", "REAL"),
    ("estimated_prob", "REAL"),
    ("entry_price_cents", "REAL"),
    ("llm_confidence", "REAL"),
    ("market_snapshot", "TEXT"),
]


@pytest.fixture
def local_db_case():
    root = Path(__file__).resolve().parent / "_tmp_paper_performance_drilldown"
    root.mkdir(parents=True, exist_ok=True)
    fake_path = root / f"paper-performance-{uuid.uuid4().hex}.db"
    fake_path.write_text("", encoding="utf-8")
    db_uri = f"file:{fake_path.stem}?mode=memory&cache=shared"
    keeper = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
    keeper.row_factory = sqlite3.Row

    real_connect = sqlite3.connect

    def _connect(target, *args, **kwargs):
        if str(target) == str(fake_path):
            conn = real_connect(db_uri, uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        conn = real_connect(target, *args, **kwargs)
        conn.row_factory = sqlite3.Row
        return conn

    try:
        yield fake_path, keeper, _connect
    finally:
        keeper.close()
        try:
            fake_path.unlink()
        except (FileNotFoundError, PermissionError):
            pass


def _make_db(conn: sqlite3.Connection, include_optional: bool = True, rows=None) -> None:
    rows = rows or []
    columns = list(BASE_COLUMNS)
    if include_optional:
        columns.extend(OPTIONAL_COLUMNS)

    ddl = ", ".join(f"{name} {decl}" for name, decl in columns)
    conn.execute(f"CREATE TABLE paper_trades ({ddl})")
    if rows:
        names = [name for name, _decl in columns]
        placeholders = ", ".join("?" for _ in names)
        conn.executemany(
            f"INSERT INTO paper_trades ({', '.join(names)}) VALUES ({placeholders})",
            [tuple(row.get(name) for name in names) for row in rows],
        )
    conn.commit()


def test_missing_db_handling():
    path = Path("paper-performance-missing.db")

    stats = summarize(path)

    assert stats["exists"] is False
    assert stats["total_trades"] == 0
    assert stats["resolved_trades"] == 0
    assert stats["open_trades"] == 0


def test_empty_table_handling(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(keeper)

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    assert stats["exists"] is True
    assert stats["total_trades"] == 0
    assert stats["resolved_trades"] == 0
    assert stats["open_trades"] == 0
    assert stats["sources"] == []
    assert stats["tickers"] == []


def test_resolved_vs_open_trade_counting(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 2.5,
            },
            {
                "trade_id": "t2",
                "ts": "2026-04-11T00:00:00+00:00",
                "ticker": "KX2",
                "signal_source": "AP",
                "resolved": 0,
                "pnl_dollars": None,
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    assert stats["total_trades"] == 2
    assert stats["resolved_trades"] == 1
    assert stats["open_trades"] == 1


def test_win_rate_and_pnl_summary(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 3.0,
            },
            {
                "trade_id": "t2",
                "ts": "2026-04-10T01:00:00+00:00",
                "ticker": "KX2",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": -2.0,
            },
            {
                "trade_id": "t3",
                "ts": "2026-04-10T02:00:00+00:00",
                "ticker": "KX3",
                "signal_source": "AP",
                "resolved": 1,
                "pnl_dollars": 1.0,
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    assert stats["win_rate"] == pytest.approx(2 / 3)
    assert stats["total_pnl"] == pytest.approx(2.0)
    assert stats["avg_pnl"] == pytest.approx(2.0 / 3.0)


def test_average_win_and_average_loss(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 4.0,
            },
            {
                "trade_id": "t2",
                "ts": "2026-04-10T01:00:00+00:00",
                "ticker": "KX2",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 2.0,
            },
            {
                "trade_id": "t3",
                "ts": "2026-04-10T02:00:00+00:00",
                "ticker": "KX3",
                "signal_source": "AP",
                "resolved": 1,
                "pnl_dollars": -3.0,
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    assert stats["avg_win"] == pytest.approx(3.0)
    assert stats["avg_loss"] == pytest.approx(-3.0)


def test_high_confidence_full_loss_rows_are_flagged(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "bad",
                "ts": "2026-06-16T13:18:29+00:00",
                "ticker": "enwc-ushrp-ny07-2026-06-23-dem-claval",
                "signal_source": "qns.com",
                "resolved": 1,
                "resolved_ts": "2026-06-24T18:53:04+00:00",
                "pnl_dollars": -4.15,
                "cost_dollars": 4.15,
                "estimated_prob": 0.898,
                "entry_price_cents": 83,
                "llm_confidence": 0.85,
                "venue": "polymarket_us",
            },
            {
                "trade_id": "small",
                "ts": "2026-06-16T14:00:00+00:00",
                "ticker": "KXSMALL",
                "signal_source": "Reuters",
                "resolved": 1,
                "resolved_ts": "2026-06-17T14:00:00+00:00",
                "pnl_dollars": -0.10,
                "cost_dollars": 1.00,
                "estimated_prob": 0.90,
                "entry_price_cents": 10,
                "llm_confidence": 0.90,
                "venue": "kalshi",
            },
            {
                "trade_id": "no-side-low-confidence",
                "ts": "2026-06-16T15:00:00+00:00",
                "ticker": "KXNO",
                "signal_source": "Reuters",
                "resolved": 1,
                "resolved_ts": "2026-06-17T15:00:00+00:00",
                "pnl_dollars": -0.98,
                "cost_dollars": 0.98,
                "estimated_prob": 0.95,
                "entry_price_cents": 2,
                "llm_confidence": 0.90,
                "side": "no",
                "venue": "kalshi",
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    rows = stats["high_confidence_full_losses"]
    assert len(rows) == 1
    assert rows[0]["ticker"] == "enwc-ushrp-ny07-2026-06-23-dem-claval"
    assert rows[0]["pnl_dollars"] == pytest.approx(-4.15)
    assert rows[0]["estimated_prob"] == pytest.approx(0.898)
    assert rows[0]["llm_confidence"] == pytest.approx(0.85)


def test_source_breakdown(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 2.0,
            },
            {
                "trade_id": "t2",
                "ts": "2026-04-10T01:00:00+00:00",
                "ticker": "KX2",
                "signal_source": "Reuters",
                "resolved": 0,
                "pnl_dollars": None,
            },
            {
                "trade_id": "t3",
                "ts": "2026-04-10T02:00:00+00:00",
                "ticker": "KX3",
                "signal_source": "AP",
                "resolved": 1,
                "pnl_dollars": -1.0,
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    assert stats["sources"][0]["name"] == "Reuters"
    assert stats["sources"][0]["trades"] == 2
    assert stats["sources"][0]["resolved"] == 1
    assert stats["sources"][0]["pnl"] == pytest.approx(2.0)


def test_ticker_breakdown(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 1.0,
            },
            {
                "trade_id": "t2",
                "ts": "2026-04-10T01:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "AP",
                "resolved": 1,
                "pnl_dollars": -1.0,
            },
            {
                "trade_id": "t3",
                "ts": "2026-04-10T02:00:00+00:00",
                "ticker": "KX2",
                "signal_source": "AP",
                "resolved": 0,
                "pnl_dollars": None,
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    assert stats["tickers"][0]["name"] == "KX1"
    assert stats["tickers"][0]["trades"] == 2
    assert stats["tickers"][0]["resolved"] == 2
    assert stats["tickers"][0]["pnl"] == pytest.approx(0.0)


def test_signal_type_breakdown_when_column_exists(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 1.0,
                "signal_type": "news",
            },
            {
                "trade_id": "t2",
                "ts": "2026-04-10T01:00:00+00:00",
                "ticker": "KX2",
                "signal_source": "AP",
                "resolved": 1,
                "pnl_dollars": 2.0,
                "signal_type": "fade_tweet",
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    names = {row["name"] for row in stats["signal_types"]}
    assert names == {"news", "fade_tweet"}


def test_series_breakdown_normalizes_legacy_polymarket_series(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "pm1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "ewc-usse-me-2026-11-03-dem",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 1.0,
                "series_ticker": "polymarket_us",
                "venue": "polymarket_us",
            },
            {
                "trade_id": "pm2",
                "ts": "2026-04-10T01:00:00+00:00",
                "ticker": "ewc-usse-me-2026-11-03-rep",
                "signal_source": "AP",
                "resolved": 1,
                "pnl_dollars": -1.0,
                "series_ticker": "polymarket_us",
                "venue": "polymarket_us",
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    series = {row["name"]: row for row in stats["series"]}
    assert "polymarket_us:ewc-usse-me" in series
    assert "polymarket_us" not in series
    assert series["polymarket_us:ewc-usse-me"]["trades"] == 2


def test_graceful_degradation_without_optional_columns(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        include_optional=False,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 2.0,
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    assert stats["signal_types"][0]["name"] == "news (default/legacy schema)"
    assert stats["series"] == []
    assert stats["holding_period_count"] == 0


def test_holding_period_summary_only_when_derivable(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-04-10T00:00:00+00:00",
                "ticker": "KX1",
                "signal_source": "Reuters",
                "resolved": 1,
                "pnl_dollars": 2.0,
                "resolved_ts": "2026-04-10T12:00:00+00:00",
            },
            {
                "trade_id": "t2",
                "ts": "2026-04-11T00:00:00+00:00",
                "ticker": "KX2",
                "signal_source": "AP",
                "resolved": 1,
                "pnl_dollars": -1.0,
                "resolved_ts": "2026-04-12T00:00:00+00:00",
            },
            {
                "trade_id": "t3",
                "ts": "2026-04-11T05:00:00+00:00",
                "ticker": "KX3",
                "signal_source": "AP",
                "resolved": 1,
                "pnl_dollars": 1.0,
                "resolved_ts": None,
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    assert stats["holding_period_count"] == 2
    assert stats["holding_period_avg_hours"] == pytest.approx(18.0)
    assert stats["holding_period_median_hours"] == pytest.approx(18.0)


def test_open_resolution_buckets_use_market_snapshot_close_time(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "t1",
                "ts": "2026-06-19T00:00:00+00:00",
                "ticker": "KXFAST",
                "signal_source": "Reuters",
                "resolved": 0,
                "pnl_dollars": None,
                "venue": "polymarket",
                "cost_dollars": 12.5,
                "market_snapshot": json.dumps({"close_time": "2026-06-21T00:00:00+00:00"}),
            },
            {
                "trade_id": "t2",
                "ts": "2026-06-19T01:00:00+00:00",
                "ticker": "KXSLOW",
                "signal_source": "AP",
                "resolved": 0,
                "pnl_dollars": None,
                "venue": "polymarket",
                "cost_dollars": 7.5,
                "market_snapshot": json.dumps({"market": {"close_time": "2026-07-25T00:00:00+00:00"}}),
            },
            {
                "trade_id": "t3",
                "ts": "2026-06-19T02:00:00+00:00",
                "ticker": "KXUNKNOWN",
                "signal_source": "AP",
                "resolved": 0,
                "pnl_dollars": None,
                "venue": "kalshi",
                "cost_dollars": 3.0,
                "market_snapshot": "{}",
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path, now=datetime(2026, 6, 19, tzinfo=timezone.utc))

    rows = {(row["bucket"], row["venue"]): row for row in stats["open_resolution_buckets"]}
    assert rows[("0-3d", "polymarket")]["trades"] == 1
    assert rows[("0-3d", "polymarket")]["exposure"] == pytest.approx(12.5)
    assert rows[(">30d", "polymarket")]["trades"] == 1
    assert rows[(">30d", "polymarket")]["exposure"] == pytest.approx(7.5)
    assert rows[("unknown", "kalshi")]["trades"] == 1
    assert rows[("unknown", "kalshi")]["exposure"] == pytest.approx(3.0)


def test_open_mark_summary_marks_kalshi_bid_and_tracks_unknowns(local_db_case):
    path, keeper, connect = local_db_case
    _make_db(
        keeper,
        rows=[
            {
                "trade_id": "marked",
                "ts": "2026-06-19T00:00:00+00:00",
                "ticker": "KXMARKED",
                "signal_source": "Reuters",
                "resolved": 0,
                "pnl_dollars": None,
                "venue": "kalshi",
                "side": "yes",
                "contracts": 5,
                "cost_dollars": 0.50,
                "market_snapshot": json.dumps({"yes_bid": 5}),
            },
            {
                "trade_id": "unknown",
                "ts": "2026-06-19T01:00:00+00:00",
                "ticker": "PMUNKNOWN",
                "signal_source": "AP",
                "resolved": 0,
                "pnl_dollars": None,
                "venue": "polymarket",
                "side": "yes",
                "contracts": 5,
                "cost_dollars": 2.00,
                "market_snapshot": "{}",
            },
        ],
    )

    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path, now=datetime(2026, 6, 19, tzinfo=timezone.utc))

    mark = stats["open_mark_summary"]
    assert mark["open_cost_dollars"] == pytest.approx(2.50)
    assert mark["marked_kalshi_cost_dollars"] == pytest.approx(0.50)
    assert mark["marked_kalshi_bid_value_dollars"] == pytest.approx(0.25)
    assert mark["marked_kalshi_unrealized_pnl_dollars"] == pytest.approx(-0.25)
    assert mark["unknown_mark_cost_dollars"] == pytest.approx(2.00)


def test_summarize_injects_one_provider_and_timestamp_for_liquidation(
    monkeypatch, local_db_case
):
    path, conn, connect = local_db_case
    _make_db(conn)
    provider = object()
    as_of = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    expected = {
        "as_of": as_of.isoformat(),
        "report_net_liquidation_value": 4.25,
    }
    captured = {}

    def fake_marks(path, *, provider, as_of):
        captured.update(path=path, provider=provider, as_of=as_of)
        return expected

    monkeypatch.setattr(
        paper_performance_drilldown, "compute_open_position_marks", fake_marks
    )

    with patch("sqlite3.connect", connect):
        stats = summarize(
            path,
            mark_provider=provider,
            as_of=as_of,
        )

    assert stats["executable_liquidation"] is expected
    assert captured == {
        "path": path,
        "provider": provider,
        "as_of": as_of,
    }


def test_print_summary_missing_db(capsys):
    path = Path("paper-performance-missing.db")
    stats = summarize(path)

    print_summary(stats, top=5)
    output = capsys.readouterr().out

    assert "PAPER TRADING PERFORMANCE DRILLDOWN" in output
    assert "Database file not found." in output


def test_print_summary_empty_db(capsys, local_db_case):
    path, keeper, connect = local_db_case
    _make_db(keeper)
    with patch("scripts.paper_performance_drilldown.sqlite3.connect", side_effect=connect):
        stats = summarize(path)

    print_summary(stats, top=5)
    output = capsys.readouterr().out

    assert "PAPER TRADING PERFORMANCE DRILLDOWN" in output
    assert "No paper trades found." in output
