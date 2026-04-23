import shutil
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.source_scorecard import (
    auto_disable_candidate,
    classify_source,
    classify_source_family,
    disabled_status,
    is_disabled_source,
    parse_date_end,
    parse_date_start,
    print_summary,
    recommendation_tier,
    summarize,
    top_performer,
    watchlist_candidate,
)
from tests._helpers import write_jsonl

_REAL_SQLITE_CONNECT = sqlite3.connect


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_source_scorecard"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _cleanup_tmp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


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
    tmp = _make_tmp_dir()
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
        _cleanup_tmp_dir(tmp)


def test_summarize_combines_log_and_db_metrics():
    tmp = _make_tmp_dir()
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
        _cleanup_tmp_dir(tmp)


def test_summarize_respects_date_filter():
    tmp = _make_tmp_dir()
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
        _cleanup_tmp_dir(tmp)


def test_summarize_excludes_test_records():
    tmp = _make_tmp_dir()
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
        _cleanup_tmp_dir(tmp)


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
    assert is_disabled_source("BBC News") is True
    assert is_disabled_source("bbc news") is True
    assert is_disabled_source("Reuters") is False


def test_disabled_status_checks_source_and_family():
    assert disabled_status("BBC News", "publisher_rss") == (True, "source")
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
    tmp = _make_tmp_dir()
    try:
        log_path = tmp / "trades.jsonl"
        db_path = tmp / "paper_trades.db"
        db_uri = f"file:source_scorecard_{uuid.uuid4().hex}?mode=memory&cache=shared"
        write_jsonl(
            log_path,
            [
                {"type": "SIGNAL", "source": "BBC News", "headline": "disabled", "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "headline": "active", "ts": "2026-04-11T00:01:00+00:00"},
            ],
        )
        conn = _create_shared_memory_db(
            db_uri,
            [
                {"trade_id": "t1", "ts": "2026-04-11T00:05:00+00:00", "ticker": "KX1", "signal_source": "BBC News", "resolved": 1, "pnl_dollars": -1.0},
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

        assert "BBC News" in disabled_sources
        assert "BBC News" not in active_sources
        assert "Reuters" in active_sources
        conn.close()
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_separates_family_disabled_sources_from_active_groups():
    tmp = _make_tmp_dir()
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
        _cleanup_tmp_dir(tmp)


def test_summarize_assigns_each_active_source_to_one_recommendation_tier():
    tmp = _make_tmp_dir()
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
        _cleanup_tmp_dir(tmp)


def test_print_summary_mentions_non_attributable_metrics(capsys):
    tmp = _make_tmp_dir()
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
        _cleanup_tmp_dir(tmp)


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
