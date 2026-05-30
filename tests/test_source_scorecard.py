import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.source_scorecard import (
    auto_disable_candidate,
    classify_source,
    classify_source_family,
    disabled_status,
    format_group_rows,
    has_lifetime_yield,
    is_disabled_source,
    parse_date_end,
    parse_date_start,
    print_summary,
    recommendation_tier,
    summarize,
    top_performer,
    watchlist_candidate,
)
from tasks.stats.source_stats import read_lifetime_totals
from tests._helpers import cleanup_tmp_dir, make_tmp_dir, write_jsonl

_SOURCE_STATS_DDL = (
    "CREATE TABLE source_stats ("
    "source TEXT PRIMARY KEY, posts_seen INTEGER, signals INTEGER, "
    "opportunities INTEGER, trades INTEGER, last_signal TEXT, last_updated TEXT)"
)


def _write_source_stats(db_path: Path, rows: list[tuple]) -> None:
    # Each row: (source, posts_seen, signals, opportunities, trades[, last_signal]).
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_SOURCE_STATS_DDL)
        for r in rows:
            r = tuple(r)
            source, posts, signals, opps, trades = r[:5]
            last_signal = r[5] if len(r) > 5 else ""
            conn.execute(
                "INSERT INTO source_stats "
                "(source, posts_seen, signals, opportunities, trades, last_signal, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source, posts, signals, opps, trades, last_signal, last_signal),
            )
        conn.commit()
    finally:
        conn.close()

_REAL_SQLITE_CONNECT = sqlite3.connect


def _create_shared_memory_db(uri: str, rows: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                ticker TEXT NOT NULL,
                signal_source TEXT NOT NULL,
                resolved INTEGER DEFAULT 0,
                pnl_dollars REAL,
                signal_type TEXT DEFAULT 'news'
            )
            """
        )
        for row in rows:
            conn.execute(
                """
                INSERT INTO paper_trades (trade_id, ts, ticker, signal_source, resolved, pnl_dollars, signal_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["trade_id"],
                    row["ts"],
                    row["ticker"],
                    row["signal_source"],
                    row.get("resolved", 0),
                    row.get("pnl_dollars"),
                    row.get("signal_type", "news"),
                ),
            )
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def test_summarize_reads_partitioned_trade_root():
    tmp = make_tmp_dir("source_scorecard")
    try:
        logs_root = tmp / "trades"
        write_jsonl(
            logs_root / "archive" / "2026" / "04" / "2026-04-11.jsonl",
            [
                {
                    "type": "SIGNAL",
                    "source": "Reuters",
                    "ticker": "KX1",
                    "ts": "2026-04-11T00:00:00+00:00",
                },
            ],
        )
        write_jsonl(
            logs_root / "live" / "trades.jsonl",
            [
                {
                    "type": "EARLY_STALE_DROP",
                    "reason": "stale_by_source_policy",
                    "source": "Reuters",
                    "ticker": "KX1",
                    "ts": "2026-04-12T00:00:00+00:00",
                },
            ],
        )

        db_path = tmp / "missing_paper_trades.db"

        stats = summarize(logs_root, db_path, since=None, until=None, exclude_test=False)

        assert stats["log_meta"]["lines_total"] == 2
        reuters = next(row for row in stats["rows"] if row["source"] == "Reuters")
        assert reuters["signals"] == 1
        assert reuters["early_stale_drops"] == 1
    finally:
        cleanup_tmp_dir(tmp)


def test_summarize_combines_log_and_db_metrics():
    tmp = make_tmp_dir("source_scorecard")
    try:
        log_path = tmp / "trades.jsonl"
        db_path = tmp / "paper_trades.db"
        db_uri = f"file:source_scorecard_{uuid.uuid4().hex}?mode=memory&cache=shared"
        write_jsonl(
            log_path,
            [
                {"type": "EARLY_STALE_DROP", "reason": "stale_by_source_policy", "source": "Reuters", "headline": "old", "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "source": "Reuters", "headline": "stale", "ticker": "KX1", "ts": "2026-04-11T00:01:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "headline": "signal", "ts": "2026-04-11T00:02:00+00:00"},
                {"type": "SIGNAL", "source": "AP", "headline": "signal", "ts": "2026-04-11T00:03:00+00:00"},
            ],
        )
        conn = _create_shared_memory_db(
            db_uri,
            [
                {"trade_id": "t1", "ts": "2026-04-11T00:05:00+00:00", "ticker": "KX1", "signal_source": "Reuters", "resolved": 1, "pnl_dollars": 2.5},
                {"trade_id": "t2", "ts": "2026-04-11T00:06:00+00:00", "ticker": "KX2", "signal_source": "Reuters", "resolved": 1, "pnl_dollars": -1.0},
                {"trade_id": "t3", "ts": "2026-04-11T00:07:00+00:00", "ticker": "KX3", "signal_source": "AP", "resolved": 0, "pnl_dollars": None},
            ],
        )
        db_path.write_text("placeholder", encoding="utf-8")

        with patch("scripts.source_scorecard.sqlite3.connect", side_effect=lambda *args, **kwargs: _REAL_SQLITE_CONNECT(db_uri, uri=True)):
            stats = summarize(log_path, db_path, since=None, until=None, exclude_test=False)
        rows = {row["source"]: row for row in stats["rows"]}

        assert rows["Reuters"]["observed_records"] == 3
        assert rows["Reuters"]["early_stale_drops"] == 1
        assert rows["Reuters"]["analysis_stale_rejections"] == 1
        assert rows["Reuters"]["signals"] == 1
        assert rows["Reuters"]["paper_trades"] == 2
        assert rows["Reuters"]["resolved_paper_trades"] == 2
        assert rows["Reuters"]["win_rate"] == 0.5
        assert rows["Reuters"]["total_pnl"] == 1.5

        assert rows["AP"]["observed_records"] == 1
        assert rows["AP"]["signals"] == 1
        assert rows["AP"]["paper_trades"] == 1
        assert rows["AP"]["resolved_paper_trades"] == 0
        conn.close()
    finally:
        cleanup_tmp_dir(tmp)


def test_summarize_respects_date_filter():
    tmp = make_tmp_dir("source_scorecard")
    try:
        log_path = tmp / "trades.jsonl"
        db_path = tmp / "paper_trades.db"
        db_uri = f"file:source_scorecard_{uuid.uuid4().hex}?mode=memory&cache=shared"
        write_jsonl(
            log_path,
            [
                {"type": "SIGNAL", "source": "Reuters", "headline": "old", "ts": "2026-04-09T00:00:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "headline": "keep", "ts": "2026-04-10T12:00:00+00:00"},
            ],
        )
        conn = _create_shared_memory_db(
            db_uri,
            [
                {"trade_id": "t1", "ts": "2026-04-09T00:05:00+00:00", "ticker": "KX1", "signal_source": "Reuters", "resolved": 1, "pnl_dollars": 1.0},
                {"trade_id": "t2", "ts": "2026-04-11T00:05:00+00:00", "ticker": "KX2", "signal_source": "Reuters", "resolved": 1, "pnl_dollars": 2.0},
            ],
        )
        db_path.write_text("placeholder", encoding="utf-8")

        with patch("scripts.source_scorecard.sqlite3.connect", side_effect=lambda *args, **kwargs: _REAL_SQLITE_CONNECT(db_uri, uri=True)):
            stats = summarize(
                log_path,
                db_path,
                since=parse_date_start("2026-04-10"),
                until=parse_date_end("2026-04-11"),
                exclude_test=False,
            )
        row = stats["rows"][0]
        assert row["signals"] == 1
        assert row["paper_trades"] == 1
        assert row["total_pnl"] == 2.0
        conn.close()
    finally:
        cleanup_tmp_dir(tmp)


def test_summarize_excludes_test_records():
    tmp = make_tmp_dir("source_scorecard")
    try:
        log_path = tmp / "trades.jsonl"
        db_path = tmp / "paper_trades.db"
        db_uri = f"file:source_scorecard_{uuid.uuid4().hex}?mode=memory&cache=shared"
        write_jsonl(
            log_path,
            [
                {"type": "SIGNAL", "source": "r/test", "headline": "x", "ticker": "KXTEST-1", "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "headline": "y", "ticker": "KX1", "ts": "2026-04-11T00:01:00+00:00"},
            ],
        )
        conn = _create_shared_memory_db(
            db_uri,
            [
                {"trade_id": "t1", "ts": "2026-04-11T00:05:00+00:00", "ticker": "KXTEST-1", "signal_source": "r/test", "resolved": 1, "pnl_dollars": 5.0},
                {"trade_id": "t2", "ts": "2026-04-11T00:06:00+00:00", "ticker": "KX1", "signal_source": "Reuters", "resolved": 1, "pnl_dollars": 1.0},
            ],
        )
        db_path.write_text("placeholder", encoding="utf-8")

        with patch("scripts.source_scorecard.sqlite3.connect", side_effect=lambda *args, **kwargs: _REAL_SQLITE_CONNECT(db_uri, uri=True)):
            stats = summarize(log_path, db_path, since=None, until=None, exclude_test=True)
        assert len(stats["rows"]) == 1
        assert stats["rows"][0]["source"] == "Reuters"
        conn.close()
    finally:
        cleanup_tmp_dir(tmp)


def test_classify_source_heuristics():
    keep = classify_source({
        "resolved_paper_trades": 2,
        "total_pnl": 3.0,
        "win_rate": 1.0,
        "observed_records": 3,
        "signals": 2,
        "paper_trades": 2,
        "early_stale_drops": 0,
        "analysis_stale_rejections": 0,
    })
    prune_negative = classify_source({
        "resolved_paper_trades": 2,
        "total_pnl": -1.0,
        "win_rate": 0.0,
        "observed_records": 3,
        "signals": 1,
        "paper_trades": 2,
        "early_stale_drops": 0,
        "analysis_stale_rejections": 0,
    })
    prune_stale = classify_source({
        "resolved_paper_trades": 0,
        "total_pnl": 0.0,
        "win_rate": None,
        "observed_records": 8,
        "signals": 0,
        "paper_trades": 0,
        "early_stale_drops": 4,
        "analysis_stale_rejections": 1,
    })
    investigate = classify_source({
        "resolved_paper_trades": 1,
        "total_pnl": 1.0,
        "win_rate": 1.0,
        "observed_records": 2,
        "signals": 1,
        "paper_trades": 1,
        "early_stale_drops": 0,
        "analysis_stale_rejections": 0,
    })

    assert keep == "likely keep"
    assert prune_negative == "likely prune"
    assert prune_stale == "likely prune"
    assert investigate == "investigate"


def test_is_disabled_source_matches_case_insensitively():
    # Foreign Policy is in DISABLED_NEWS_SOURCES as of 2026-04-23 (BBC/France24/DW
    # were re-enabled in the B3 source-integration batch).
    assert is_disabled_source("Foreign Policy") is True
    assert is_disabled_source("foreign policy") is True
    assert is_disabled_source("Reuters") is False


def test_disabled_status_checks_source_and_family():
    assert disabled_status("Foreign Policy", "publisher_rss") == (True, "source")
    assert disabled_status("query one - BingNews", "bing_news_query") == (True, "family")
    assert disabled_status("Reuters", "publisher_rss") == (False, None)


def test_classify_source_family_rules():
    assert classify_source_family("query one - BingNews") == "bing_news_query"
    assert classify_source_family("query two - Google News") == "google_news_query"
    assert classify_source_family("r/worldnews") == "reddit"
    assert classify_source_family("BBC News") == "publisher_rss"
    assert classify_source_family("Just In News") == "direct_feed"
    assert classify_source_family("Unknown Feed") == "other"


def test_summarize_separates_disabled_sources_from_active_groups():
    tmp = make_tmp_dir("source_scorecard")
    try:
        log_path = tmp / "trades.jsonl"
        db_path = tmp / "paper_trades.db"
        db_uri = f"file:source_scorecard_{uuid.uuid4().hex}?mode=memory&cache=shared"
        write_jsonl(
            log_path,
            [
                {"type": "SIGNAL", "source": "Foreign Policy", "headline": "disabled", "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "headline": "active", "ts": "2026-04-11T00:01:00+00:00"},
            ],
        )
        conn = _create_shared_memory_db(
            db_uri,
            [
                {"trade_id": "t1", "ts": "2026-04-11T00:05:00+00:00", "ticker": "KX1", "signal_source": "Foreign Policy", "resolved": 1, "pnl_dollars": -1.0},
                {"trade_id": "t2", "ts": "2026-04-11T00:06:00+00:00", "ticker": "KX2", "signal_source": "Reuters", "resolved": 1, "pnl_dollars": 2.0},
            ],
        )
        db_path.write_text("placeholder", encoding="utf-8")

        with patch("scripts.source_scorecard.sqlite3.connect", side_effect=lambda *args, **kwargs: _REAL_SQLITE_CONNECT(db_uri, uri=True)):
            stats = summarize(log_path, db_path, since=None, until=None, exclude_test=False)

        disabled_sources = {row["source"] for row in stats["grouped"]["disabled by source"]}
        active_sources = {
            row["source"]
            for bucket in ("remove immediately", "prune", "watch / investigate", "keep", "top performers")
            for row in stats["grouped"][bucket]
        }

        assert "Foreign Policy" in disabled_sources
        assert "Foreign Policy" not in active_sources
        assert "Reuters" in active_sources
        conn.close()
    finally:
        cleanup_tmp_dir(tmp)


def test_summarize_separates_family_disabled_sources_from_active_groups():
    tmp = make_tmp_dir("source_scorecard")
    try:
        log_path = tmp / "trades.jsonl"
        db_path = tmp / "paper_trades.db"
        write_jsonl(
            log_path,
            [
                {"type": "SIGNAL", "source": "query one - BingNews", "headline": "disabled by family", "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "headline": "active", "ts": "2026-04-11T00:01:00+00:00"},
            ],
        )

        stats = summarize(log_path, db_path, since=None, until=None, exclude_test=False)

        disabled_rows = {row["source"]: row for row in stats["grouped"]["disabled by family"]}
        active_sources = {
            row["source"]
            for bucket in ("remove immediately", "prune", "watch / investigate", "keep", "top performers")
            for row in stats["grouped"][bucket]
        }

        assert "query one - BingNews" in disabled_rows
        assert disabled_rows["query one - BingNews"]["disabled_reason"] == "family"
        assert "query one - BingNews" not in active_sources
        assert "Reuters" in active_sources
    finally:
        cleanup_tmp_dir(tmp)


def test_summarize_assigns_each_active_source_to_one_recommendation_tier():
    tmp = make_tmp_dir("source_scorecard")
    try:
        log_path = tmp / "trades.jsonl"
        db_path = tmp / "paper_trades.db"
        write_jsonl(
            log_path,
            [
                {
                    "type": "EARLY_STALE_DROP",
                    "reason": "stale_by_source_policy",
                    "source": "Active Remove",
                    "headline": f"h{i}",
                    "ts": f"2026-04-11T00:{i:02d}:00+00:00",
                }
                for i in range(15)
            ] + [
                {
                    "type": "EARLY_STALE_DROP",
                    "reason": "stale_by_source_policy",
                    "source": "Active Watch",
                    "headline": f"w{i}",
                    "ts": f"2026-04-11T01:{i:02d}:00+00:00",
                }
                for i in range(7)
            ] + [
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "stale_news",
                    "source": "Active Watch",
                    "headline": f"a{i}",
                    "ticker": "KX1",
                    "ts": f"2026-04-11T02:{i:02d}:00+00:00",
                }
                for i in range(3)
            ] + [
                {
                    "type": "SIGNAL",
                    "source": "Active Watch",
                    "headline": "borderline signal",
                    "ts": "2026-04-11T02:30:00+00:00",
                }
            ] + [
                {
                    "type": "SIGNAL",
                    "source": "Active Keep",
                    "headline": "signal",
                    "ts": "2026-04-11T03:00:00+00:00",
                }
            ] + [
                {
                    "type": "SIGNAL",
                    "source": "Active Top",
                    "headline": "signal",
                    "ts": "2026-04-11T03:10:00+00:00",
                }
            ],
        )
        db_uri = f"file:source_scorecard_{uuid.uuid4().hex}?mode=memory&cache=shared"
        conn = _create_shared_memory_db(
            db_uri,
                [
                    {"trade_id": "t1", "ts": "2026-04-11T03:05:00+00:00", "ticker": "KX1", "signal_source": "Active Keep", "resolved": 1, "pnl_dollars": 1.5},
                    {"trade_id": "t2", "ts": "2026-04-11T03:06:00+00:00", "ticker": "KX2", "signal_source": "Active Keep", "resolved": 1, "pnl_dollars": -0.5},
                    {"trade_id": "t3", "ts": "2026-04-11T03:07:00+00:00", "ticker": "KX3", "signal_source": "Active Top", "resolved": 1, "pnl_dollars": 2.5},
                    {"trade_id": "t4", "ts": "2026-04-11T03:08:00+00:00", "ticker": "KX4", "signal_source": "Active Top", "resolved": 1, "pnl_dollars": 2.0},
                ],
            )
        db_path.write_text("placeholder", encoding="utf-8")
        with patch("scripts.source_scorecard.sqlite3.connect", side_effect=lambda *args, **kwargs: _REAL_SQLITE_CONNECT(db_uri, uri=True)):
            stats = summarize(log_path, db_path, since=None, until=None, exclude_test=False)
        rows = {row["source"]: row for row in stats["rows"]}

        assert rows["Active Keep"]["source_family"] == "other"
        assert rows["Active Top"]["source_family"] == "other"
        memberships = {}
        for bucket in ("remove immediately", "prune", "watch / investigate", "keep", "top performers"):
            for row in stats["grouped"][bucket]:
                memberships.setdefault(row["source"], []).append(bucket)

        assert memberships["Active Remove"] == ["remove immediately"]
        assert memberships["Active Watch"] == ["watch / investigate"]
        assert memberships["Active Keep"] == ["keep"]
        assert memberships["Active Top"] == ["top performers"]
        conn.close()
    finally:
        cleanup_tmp_dir(tmp)


def test_print_summary_mentions_non_attributable_metrics(capsys):
    tmp = make_tmp_dir("source_scorecard")
    try:
        log_path = tmp / "trades.jsonl"
        db_path = tmp / "paper_trades.db"
        write_jsonl(log_path, [])
        stats = summarize(log_path, db_path, since=None, until=None, exclude_test=False)

        print_summary(stats, top=5)
        output = capsys.readouterr().out

        assert "SOURCE SCORECARD" in output
        assert "Opportunities produced: not reliably attributable" in output
        assert "Executor skips: not reliably attributable" in output
    finally:
        cleanup_tmp_dir(tmp)


def test_recommendation_heuristics():
    auto_row = {
        "observed_records": 15,
        "signals": 0,
        "paper_trades": 0,
        "stale_drop_ratio": 0.8,
    }
    watch_row = {
        "observed_records": 10,
        "signals": 1,
        "paper_trades": 0,
        "stale_drop_ratio": 0.6,
    }
    neutral_row = {
        "observed_records": 9,
        "signals": 0,
        "paper_trades": 0,
        "stale_drop_ratio": 1.0,
    }

    assert auto_disable_candidate(auto_row) is True
    assert watchlist_candidate(auto_row) is False
    assert auto_disable_candidate(watch_row) is False
    assert watchlist_candidate(watch_row) is True
    assert auto_disable_candidate(neutral_row) is False
    assert watchlist_candidate(neutral_row) is False


def test_print_summary_shows_disabled_section_by_default(capsys):
    stats = {
        "logs_path": Path("logs/trades/trades.jsonl"),
        "db_path": Path("data/paper_trades.db"),
        "since": None,
        "until": None,
        "log_meta": {"lines_total": 1, "lines_malformed": 0, "records_kept": 1},
        "db_exists": True,
        "db_columns": set(),
        "rows": [{"source": "BBC News"}],
        "grouped": {
            "remove immediately": [],
            "prune": [],
            "watch / investigate": [],
            "keep": [],
            "top performers": [],
            "disabled by source": [
                {
                    "source": "BBC News",
                    "source_family": "publisher_rss",
                    "disabled_reason": "source",
                    "is_disabled": True,
                    "observed_records": 10,
                    "early_stale_drops": 4,
                    "analysis_stale_rejections": 2,
                    "signals": 0,
                    "paper_trades": 0,
                    "resolved_paper_trades": 0,
                    "win_rate": None,
                    "total_pnl": 0.0,
                    "stale_drop_ratio": 0.6,
                }
            ],
            "disabled by family": [],
        },
        "disabled_sources": ["BBC News"],
        "opportunities_attributable": False,
        "executor_skips_attributable": False,
    }

    print_summary(stats, top=5)
    output = capsys.readouterr().out

    assert "Disabled Sources" in output
    assert "Disabled By Source" in output
    assert "BBC News" in output
    assert "disabled_by=source" in output


def test_print_summary_hides_disabled_section_when_requested(capsys):
    stats = {
        "logs_path": Path("logs/trades/trades.jsonl"),
        "db_path": Path("data/paper_trades.db"),
        "since": None,
        "until": None,
        "log_meta": {"lines_total": 1, "lines_malformed": 0, "records_kept": 1},
        "db_exists": True,
        "db_columns": set(),
        "rows": [{"source": "BBC News"}],
        "grouped": {
            "remove immediately": [],
            "prune": [],
            "watch / investigate": [],
            "keep": [],
            "top performers": [],
            "disabled by source": [
                {
                    "source": "BBC News",
                    "source_family": "publisher_rss",
                    "disabled_reason": "source",
                    "is_disabled": True,
                    "observed_records": 10,
                    "early_stale_drops": 4,
                    "analysis_stale_rejections": 2,
                    "signals": 0,
                    "paper_trades": 0,
                    "resolved_paper_trades": 0,
                    "win_rate": None,
                    "total_pnl": 0.0,
                    "stale_drop_ratio": 0.6,
                }
            ],
            "disabled by family": [],
        },
        "disabled_sources": ["BBC News"],
        "opportunities_attributable": False,
        "executor_skips_attributable": False,
    }

    print_summary(stats, top=5, hide_disabled=True)
    output = capsys.readouterr().out

    assert "Disabled Sources" not in output


def test_print_summary_includes_tiered_recommendation_sections(capsys):
    stats = {
        "logs_path": Path("logs/trades/trades.jsonl"),
        "db_path": Path("data/paper_trades.db"),
        "since": None,
        "until": None,
        "log_meta": {"lines_total": 1, "lines_malformed": 0, "records_kept": 1},
        "db_exists": True,
        "db_columns": set(),
        "rows": [{"source": "query one - BingNews"}],
        "grouped": {
            "remove immediately": [
                {
                    "source": "query one - BingNews",
                    "source_family": "bing_news_query",
                    "disabled_reason": None,
                    "is_disabled": False,
                    "observed_records": 15,
                    "early_stale_drops": 12,
                    "analysis_stale_rejections": 0,
                    "signals": 0,
                    "paper_trades": 0,
                    "resolved_paper_trades": 0,
                    "win_rate": None,
                    "total_pnl": 0.0,
                    "stale_drop_ratio": 0.8,
                }
            ],
            "prune": [],
            "watch / investigate": [],
            "keep": [],
            "top performers": [],
            "disabled by source": [],
            "disabled by family": [],
        },
        "disabled_sources": [],
        "opportunities_attributable": False,
        "executor_skips_attributable": False,
    }

    print_summary(stats, top=5)
    output = capsys.readouterr().out

    assert "Active Source Recommendations" in output
    assert "Remove Immediately" in output
    assert "Top Performers" in output
    assert "family=bing_news_query" in output


def test_print_summary_shows_family_disabled_reason(capsys):
    stats = {
        "logs_path": Path("logs/trades/trades.jsonl"),
        "db_path": Path("data/paper_trades.db"),
        "since": None,
        "until": None,
        "log_meta": {"lines_total": 1, "lines_malformed": 0, "records_kept": 1},
        "db_exists": True,
        "db_columns": set(),
        "rows": [{"source": "query one - BingNews"}],
        "grouped": {
            "remove immediately": [],
            "prune": [],
            "watch / investigate": [],
            "keep": [],
            "top performers": [],
            "disabled by source": [],
            "disabled by family": [
                {
                    "source": "query one - BingNews",
                    "source_family": "bing_news_query",
                    "disabled_reason": "family",
                    "is_disabled": True,
                    "observed_records": 20,
                    "early_stale_drops": 18,
                    "analysis_stale_rejections": 0,
                    "signals": 0,
                    "paper_trades": 0,
                    "resolved_paper_trades": 0,
                    "win_rate": None,
                    "total_pnl": 0.0,
                    "stale_drop_ratio": 0.9,
                }
            ],
        },
        "disabled_sources": ["BBC News"],
        "opportunities_attributable": False,
        "executor_skips_attributable": False,
    }

    print_summary(stats, top=5)
    output = capsys.readouterr().out

    assert "Disabled By Family" in output
    assert "query one - BingNews" in output
    assert "disabled_by=family" in output


def test_top_performer_and_recommendation_tier_heuristics():
    top_row = {
        "classification": "likely keep",
        "observed_records": 20,
        "signals": 2,
        "paper_trades": 2,
        "resolved_paper_trades": 2,
        "wins": 2,
        "win_rate": 1.0,
        "total_pnl": 4.0,
        "early_stale_drops": 1,
        "analysis_stale_rejections": 0,
        "stale_drop_ratio": 0.05,
    }
    keep_row = {
        "classification": "likely keep",
        "observed_records": 20,
        "signals": 2,
        "paper_trades": 2,
        "resolved_paper_trades": 2,
        "wins": 1,
        "win_rate": 0.5,
        "total_pnl": 1.0,
        "early_stale_drops": 1,
        "analysis_stale_rejections": 0,
        "stale_drop_ratio": 0.05,
    }
    prune_row = {
        "classification": "likely prune",
        "observed_records": 8,
        "signals": 0,
        "paper_trades": 0,
        "resolved_paper_trades": 0,
        "wins": 0,
        "win_rate": None,
        "total_pnl": 0.0,
        "early_stale_drops": 4,
        "analysis_stale_rejections": 1,
        "stale_drop_ratio": 0.625,
    }
    remove_row = {
        "classification": "investigate",
        "observed_records": 15,
        "signals": 0,
        "paper_trades": 0,
        "resolved_paper_trades": 0,
        "wins": 0,
        "win_rate": None,
        "total_pnl": 0.0,
        "early_stale_drops": 12,
        "analysis_stale_rejections": 0,
        "stale_drop_ratio": 0.8,
    }

    assert top_performer(top_row) is True
    assert recommendation_tier(top_row) == "top performers"
    assert top_performer(keep_row) is False
    assert recommendation_tier(keep_row) == "keep"
    assert recommendation_tier(prune_row) == "prune"
    assert recommendation_tier(remove_row) == "remove immediately"


# ---------------------------------------------------------------------------
# A(i) lifetime-yield veto + A(ii) wide recommendation window + B funnel render.
#
# Why: the 24h display window flagged the bot's two largest lifetime opportunity
# producers ("Middle East and north Africa | The Guardian" = 140 sig / 123 opp,
# "NYT > World News" = 62 sig / 54 opp) as "remove immediately" because they
# emitted zero SIGNAL events during a single starved day. The bot emits ~158
# SIGNAL events over its entire lifetime (~1/day across ALL sources), so judging
# any single source over 24h is structurally broken. These tests pin the fix:
# a source with demonstrated lifetime yield is never auto-removed, and the
# zero-signal judgment uses a horizon long enough to be meaningful.
# ---------------------------------------------------------------------------


def test_has_lifetime_yield_detects_any_funnel_output():
    assert has_lifetime_yield({"lifetime_signals": 1}) is True
    assert has_lifetime_yield({"lifetime_opportunities": 1}) is True
    assert has_lifetime_yield({"lifetime_trades": 1}) is True
    assert has_lifetime_yield({"lifetime_signals": 0, "lifetime_opportunities": 0,
                               "lifetime_trades": 0}) is False
    # Missing lifetime fields (legacy callers) must not crash and must read as
    # "no proven yield" so existing behavior is preserved.
    assert has_lifetime_yield({}) is False


def test_auto_disable_vetoed_by_lifetime_yield():
    # The NYT/MENA case: stale + zero recent signals, but a proven lifetime
    # producer. Must NOT be auto-removed; falls through to watch / investigate.
    proven_but_dry = {
        "observed_records": 30,
        "signals": 0,
        "paper_trades": 0,
        "resolved_paper_trades": 0,
        "win_rate": None,
        "total_pnl": 0.0,
        "stale_drop_ratio": 0.95,
        "lifetime_signals": 62,
        "lifetime_opportunities": 54,
        "lifetime_trades": 1,
        "classification": "investigate",
    }
    assert auto_disable_candidate(proven_but_dry) is False
    assert recommendation_tier(proven_but_dry) == "watch / investigate"


def test_auto_disable_still_fires_for_zero_lifetime_yield():
    # A source with high staleness, zero recent signals AND zero lifetime yield is
    # genuinely useless -- removal must stay recommended.
    genuinely_dead = {
        "observed_records": 30,
        "signals": 0,
        "paper_trades": 0,
        "resolved_paper_trades": 0,
        "win_rate": None,
        "total_pnl": 0.0,
        "stale_drop_ratio": 0.95,
        "lifetime_signals": 0,
        "lifetime_opportunities": 0,
        "lifetime_trades": 0,
        "classification": "investigate",
    }
    assert auto_disable_candidate(genuinely_dead) is True
    assert recommendation_tier(genuinely_dead) == "remove immediately"


def test_stale_prune_path_vetoed_by_lifetime_yield():
    # classify_source's stale-prune branch must also honor lifetime yield: a proven
    # source in a dry window is "investigate" (-> watch), never "likely prune".
    proven = {
        "resolved_paper_trades": 0,
        "total_pnl": 0.0,
        "win_rate": None,
        "observed_records": 8,
        "signals": 0,
        "paper_trades": 0,
        "early_stale_drops": 4,
        "analysis_stale_rejections": 1,
        "lifetime_opportunities": 123,
    }
    assert classify_source(proven) == "investigate"
    # Same shape, no lifetime track record -> still prunes (unchanged behavior).
    unproven = dict(proven)
    unproven.pop("lifetime_opportunities")
    assert classify_source(unproven) == "likely prune"


def test_recommendation_window_widens_signal_horizon():
    # A(ii): zero-signal is judged over recommendation_window_days, not the 1-day
    # display window. A source whose only SIGNAL fell 10 days ago (outside the
    # display window, inside the rec window) and which has no source_stats lifetime
    # record must NOT be removed -- the wide window sees the signal.
    tmp = make_tmp_dir("source_scorecard")
    try:
        logs_root = tmp / "trades"
        records = [
            {
                "type": "EARLY_STALE_DROP",
                "reason": "stale_by_source_policy",
                "source": "Quiet Wire",
                "ticker": "KX1",
                "ts": f"2026-05-28T00:{i:02d}:00+00:00",
            }
            for i in range(20)
        ]
        records.append(
            {
                "type": "SIGNAL",
                "source": "Quiet Wire",
                "headline": "fired 10 days before the display window",
                "ts": "2026-05-19T00:00:00+00:00",
            }
        )
        write_jsonl(logs_root / "live" / "trades.jsonl", records)
        db_path = tmp / "missing_paper_trades.db"

        # Display window = single day; rec window = 45d back -> includes the signal.
        stats = summarize(
            logs_root,
            db_path,
            since=parse_date_start("2026-05-28"),
            until=parse_date_end("2026-05-29"),
            exclude_test=False,
            recommendation_window_days=45,
        )
        row = next(r for r in stats["rows"] if r["source"] == "Quiet Wire")
        assert row["signals"] == 0  # display-window value unchanged
        assert row["rec_signals"] == 1  # wide window sees the older signal
        removed = {r["source"] for r in stats["grouped"]["remove immediately"]}
        assert "Quiet Wire" not in removed
    finally:
        cleanup_tmp_dir(tmp)


def test_summarize_attaches_lifetime_funnel_and_vetoes_remove():
    # End-to-end: stale + signal-less in the window, but a proven lifetime producer
    # in source_stats. Its lifetime funnel is attached to the row and it is NOT
    # recommended for removal -- it lands in watch / investigate instead.
    tmp = make_tmp_dir("source_scorecard")
    try:
        logs_root = tmp / "trades"
        write_jsonl(
            logs_root / "live" / "trades.jsonl",
            [
                {
                    "type": "EARLY_STALE_DROP",
                    "reason": "stale_by_source_policy",
                    "source": "NYT > World News",
                    "ticker": "KX1",
                    "ts": f"2026-05-28T00:{i:02d}:00+00:00",
                }
                for i in range(20)
            ],
        )
        stats_db = tmp / "paper_trades.db"
        _write_source_stats(stats_db, [("NYT > World News", 1287, 62, 54, 1)])

        stats = summarize(
            logs_root,
            tmp / "missing_paper_trades.db",
            since=None,
            until=None,
            exclude_test=False,
            source_stats_db_path=stats_db,
        )
        nyt = next(r for r in stats["rows"] if r["source"] == "NYT > World News")
        assert nyt["lifetime_signals"] == 62
        assert nyt["lifetime_opportunities"] == 54
        assert nyt["lifetime_posts"] == 1287
        assert nyt["lifetime_trades"] == 1

        removed = {r["source"] for r in stats["grouped"]["remove immediately"]}
        watch = {r["source"] for r in stats["grouped"]["watch / investigate"]}
        assert "NYT > World News" not in removed
        assert "NYT > World News" in watch
    finally:
        cleanup_tmp_dir(tmp)


def test_format_group_rows_includes_lifetime_funnel():
    # B: every recommendation row prints its lifetime funnel so "remove immediately"
    # can never appear without the operator seeing the source's real yield.
    rows = [
        {
            "source": "NYT > World News",
            "source_family": "publisher_rss",
            "disabled_reason": None,
            "observed_records": 20,
            "early_stale_drops": 20,
            "analysis_stale_rejections": 0,
            "signals": 0,
            "paper_trades": 0,
            "resolved_paper_trades": 0,
            "win_rate": None,
            "total_pnl": 0.0,
            "stale_drop_ratio": 1.0,
            "lifetime_posts": 1287,
            "lifetime_signals": 62,
            "lifetime_opportunities": 54,
            "lifetime_trades": 1,
        }
    ]
    out = "\n".join(format_group_rows(rows, top=5))
    assert "life_posts=1287" in out
    assert "life_sig=62" in out
    assert "life_opp=54" in out
    assert "life_trade=1" in out


def test_read_lifetime_totals_reads_source_stats_table(tmp_path):
    db = tmp_path / "paper_trades.db"
    _write_source_stats(db, [("NYT > World News", 1287, 62, 54, 1)])
    totals = read_lifetime_totals(db)
    assert totals["NYT > World News"]["signals"] == 62
    assert totals["NYT > World News"]["opportunities"] == 54
    assert totals["NYT > World News"]["posts_seen"] == 1287
    assert totals["NYT > World News"]["trades"] == 1


def test_read_lifetime_totals_missing_db_or_table_returns_empty(tmp_path):
    assert read_lifetime_totals(tmp_path / "nope.db") == {}
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()
    assert read_lifetime_totals(empty) == {}


def test_read_lifetime_totals_coerces_null_counts(tmp_path):
    # Load-bearing: a partially-written/legacy source_stats row with NULL counts
    # must coerce to 0, never propagate None into has_lifetime_yield (None > 0
    # raises TypeError and crashes the whole scorecard render).
    db = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(_SOURCE_STATS_DDL)
        conn.execute("INSERT INTO source_stats (source) VALUES ('NullSrc')")
        conn.commit()
    finally:
        conn.close()
    totals = read_lifetime_totals(db)
    assert totals["NullSrc"] == {
        "posts_seen": 0,
        "signals": 0,
        "opportunities": 0,
        "trades": 0,
        "last_signal": None,
    }
    # And the coerced row must not crash has_lifetime_yield.
    assert has_lifetime_yield({"lifetime_signals": 0, "lifetime_opportunities": 0,
                               "lifetime_trades": 0}) is False


def _tier_of(stats: dict, source: str) -> str | None:
    for tier, rows in stats["grouped"].items():
        if any(r["source"] == source for r in rows):
            return tier
    return None


def test_lifetime_veto_expires_when_signal_dead_past_window():
    # A(i) recency bound (PROFIT-ROT-002): a source proven long ago but signal-dead
    # beyond the recommendation window LOSES veto immunity and is flagged again.
    # Without the recency gate one ancient signal grants permanent immunity -- the
    # inverse of the original bug, hiding "proven once, dead now" sources forever.
    tmp = make_tmp_dir("source_scorecard")
    try:
        logs_root = tmp / "trades"
        records = []
        for src in ("Faded Wire", "Fresh Wire"):
            records += [
                {
                    "type": "EARLY_STALE_DROP",
                    "reason": "stale_by_source_policy",
                    "source": src,
                    "ticker": "KX1",
                    "ts": f"2026-05-29T00:{i:02d}:00+00:00",
                }
                for i in range(20)
            ]
        write_jsonl(logs_root / "live" / "trades.jsonl", records)
        stats_db = tmp / "paper_trades.db"
        _write_source_stats(
            stats_db,
            [
                # proven (5 signals) but last signal 80 days before `until` -> stale
                ("Faded Wire", 500, 5, 0, 0, "2026-03-10T00:00:00+00:00"),
                # proven and last signal 5 days before `until` -> still recent
                ("Fresh Wire", 500, 5, 0, 0, "2026-05-25T00:00:00+00:00"),
            ],
        )
        stats = summarize(
            logs_root,
            tmp / "missing_paper_trades.db",
            since=parse_date_start("2026-05-29"),
            until=parse_date_end("2026-05-30"),
            exclude_test=False,
            recommendation_window_days=45,
            source_stats_db_path=stats_db,
        )
        # Veto expired -> flagged for removal; veto still valid -> protected.
        assert _tier_of(stats, "Faded Wire") == "remove immediately"
        assert _tier_of(stats, "Fresh Wire") == "watch / investigate"
    finally:
        cleanup_tmp_dir(tmp)


def test_rec_window_is_bounded_signal_outside_window_still_flags():
    # A(ii) lookback is genuinely bounded: a SIGNAL older than the recommendation
    # window does NOT rescue a source. A buggy "scan all history" would keep
    # rec_signals>0 and silently hide it. (No source_stats -> no lifetime veto, so
    # only the window math is under test.)
    tmp = make_tmp_dir("source_scorecard")
    try:
        logs_root = tmp / "trades"
        records = [
            {
                "type": "SIGNAL",
                "source": "Old Echo",
                "headline": "ancient signal 90 days before until",
                "ts": "2026-03-01T00:00:00+00:00",
            }
        ]
        records += [
            {
                "type": "EARLY_STALE_DROP",
                "reason": "stale_by_source_policy",
                "source": "Old Echo",
                "ticker": "KX1",
                "ts": f"2026-05-29T00:{i:02d}:00+00:00",
            }
            for i in range(20)
        ]
        write_jsonl(logs_root / "live" / "trades.jsonl", records)
        stats = summarize(
            logs_root,
            tmp / "missing_paper_trades.db",
            since=parse_date_start("2026-05-29"),
            until=parse_date_end("2026-05-30"),
            exclude_test=False,
            recommendation_window_days=45,
        )
        row = next(r for r in stats["rows"] if r["source"] == "Old Echo")
        assert row["rec_signals"] == 0  # ancient signal excluded -> window bounded
        assert _tier_of(stats, "Old Echo") == "remove immediately"
    finally:
        cleanup_tmp_dir(tmp)
