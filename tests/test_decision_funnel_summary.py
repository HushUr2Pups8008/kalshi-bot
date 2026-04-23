import shutil
import uuid
from pathlib import Path

import pytest

from scripts.decision_funnel_summary import (
    infer_signal_type,
    parse_date_end,
    parse_date_start,
    print_summary,
    stale_age_bucket,
    summarize,
)
from tests._helpers import write_jsonl


@pytest.fixture
def local_tmp_dir():
    root = Path(__file__).resolve().parent / "_tmp_decision_funnel_summary"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_summarize_empty_file(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    path.write_text("", encoding="utf-8")

    stats = summarize(path, since=None, until=None)

    assert stats["lines_total"] == 0
    assert stats["lines_malformed"] == 0
    assert stats["records_kept"] == 0
    assert not stats["event_counts"]
    assert not stats["skip_reasons"]
    assert not stats["analysis_rejected_reasons"]
    assert not stats["stale_news_age_buckets"]
    assert not stats["stale_news_sources"]
    assert not stats["stale_news_tickers"]
    assert not stats["path_counts"]
    assert not stats["execution_paths"]


def test_summarize_skips_malformed_json_lines(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            '{"type": "SIGNAL", "ts": "2026-04-11T00:00:00+00:00"}',
            '{"type": "SIGNAL"',
            '["not", "a", "dict"]',
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["lines_total"] == 3
    assert stats["lines_malformed"] == 2
    assert stats["records_kept"] == 1
    assert stats["event_counts"]["SIGNAL"] == 1
    assert stats["path_counts"]["news"] == 1


def test_summarize_reads_partitioned_trade_root(local_tmp_dir):
    root = local_tmp_dir / "trades"
    write_jsonl(
        root / "archive" / "2026" / "04" / "2026-04-11.jsonl",
        [
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "source": "Reuters", "ticker": "KXOLD", "age_seconds": 600, "ts": "2026-04-11T00:00:00+00:00"},
        ],
    )
    write_jsonl(
        root / "live" / "trades.jsonl",
        [
            {"type": "PAPER_TRADE", "signal_source": "Reuters", "ticker": "KXNEW", "ts": "2026-04-12T00:00:00+00:00"},
        ],
    )

    stats = summarize(root, since=None, until=None)

    assert stats["lines_total"] == 2
    assert stats["records_kept"] == 2
    assert stats["event_counts"]["ANALYSIS_REJECTED"] == 1
    assert stats["event_counts"]["PAPER_TRADE"] == 1


def test_summarize_handles_missing_optional_fields_gracefully(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SKIPPED", "ts": "2026-04-11T00:00:00+00:00"},
            {"ts": "2026-04-11T01:00:00+00:00"},
            {"type": "", "ts": "2026-04-11T02:00:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["records_kept"] == 3
    assert stats["event_counts"]["SKIPPED"] == 1
    assert stats["event_counts"]["UNKNOWN"] == 2
    assert stats["skip_reasons"]["unknown"] == 1
    assert not stats["analysis_rejected_reasons"]
    assert stats["path_counts"]["news"] == 1
    assert not stats["execution_paths"]


def test_summarize_counts_funnel_stages(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "SIGNAL", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "OPPORTUNITY", "ts": "2026-04-11T00:01:00+00:00"},
            {"type": "SKIPPED", "reason": "cooldown", "ts": "2026-04-11T00:02:00+00:00"},
            {"type": "PAPER_TRADE", "ts": "2026-04-11T00:03:00+00:00"},
            {"type": "LIVE_ORDER", "ts": "2026-04-11T00:04:00+00:00"},
            {"type": "PAPER_RESOLUTION", "ts": "2026-04-11T00:05:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["event_counts"]["ANALYSIS_REJECTED"] == 1
    assert stats["event_counts"]["SIGNAL"] == 1
    assert stats["event_counts"]["OPPORTUNITY"] == 1
    assert stats["event_counts"]["SKIPPED"] == 1
    assert stats["event_counts"]["PAPER_TRADE"] == 1
    assert stats["event_counts"]["LIVE_ORDER"] == 1
    assert stats["event_counts"]["PAPER_RESOLUTION"] == 1
    assert stats["path_counts"]["news"] == 6


def test_summarize_aggregates_skip_reasons(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SKIPPED", "reason": "cooldown", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "SKIPPED", "reason": "cooldown", "ts": "2026-04-11T00:01:00+00:00"},
            {"type": "SKIPPED", "reason": "same-signal duplicate", "ts": "2026-04-11T00:02:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["skip_reasons"]["cooldown"] == 2
    assert stats["skip_reasons"]["same-signal duplicate"] == 1


def test_summarize_aggregates_analysis_rejected_reasons(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "ts": "2026-04-11T00:01:00+00:00"},
            {"type": "ANALYSIS_REJECTED", "reason": "no_keywords", "ts": "2026-04-11T00:02:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["event_counts"]["ANALYSIS_REJECTED"] == 3
    assert stats["analysis_rejected_reasons"]["stale_news"] == 2
    assert stats["analysis_rejected_reasons"]["no_keywords"] == 1
    assert not stats["skip_reasons"]


def test_summarize_tracks_stale_news_breakdowns(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "Reuters",
                "ticker": "KX1",
                "age_seconds": 60,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "Reuters",
                "ticker": "KX2",
                "age_seconds": 600,
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "AP",
                "ticker": "KX1",
                "age_seconds": 2400,
                "ts": "2026-04-11T00:02:00+00:00",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "AP",
                "ticker": "KX3",
                "age_seconds": 5400,
                "ts": "2026-04-11T00:03:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["stale_news_age_buckets"]["0-5m"] == 1
    assert stats["stale_news_age_buckets"]["5-15m"] == 1
    assert stats["stale_news_age_buckets"]["30-60m"] == 1
    assert stats["stale_news_age_buckets"]["60m+"] == 1
    assert stats["stale_news_sources"]["Reuters"] == 2
    assert stats["stale_news_sources"]["AP"] == 2
    assert stats["stale_news_tickers"]["KX1"] == 2
    assert stats["stale_news_tickers"]["KX2"] == 1
    assert stats["stale_news_tickers"]["KX3"] == 1


@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [
        (0, "0-5m"),
        (300, "5-15m"),
        (900, "15-30m"),
        (1800, "30-60m"),
        (3600, "60m+"),
        (-1, None),
        (None, None),
    ],
)
def test_stale_age_bucket_boundaries(age_seconds, expected):
    assert stale_age_bucket(age_seconds) == expected


def test_summarize_infers_path_contribution(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SIGNAL", "source": "Reuters", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "OPPORTUNITY", "reasoning": "[FADE/GEO/@Kalshi] bullish: hype", "ts": "2026-04-11T00:01:00+00:00"},
            {"type": "SKIPPED", "source": "price_fade", "ts": "2026-04-11T00:02:00+00:00"},
            {"type": "PAPER_RESOLUTION", "reasoning": "[FADE/GEO/@Kalshi] old", "ts": "2026-04-11T00:03:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["path_counts"]["news"] == 1
    assert stats["path_counts"]["fade_tweet"] == 1
    assert stats["path_counts"]["price_fade"] == 1
    assert "fade_tweet" not in stats["event_counts"]


def test_summarize_tracks_execution_contribution(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "PAPER_TRADE",
                "signal_type": "fade_tweet",
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "LIVE_ORDER",
                "source": "price_fade",
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "PAPER_TRADE",
                "signal_source": "Reuters",
                "ts": "2026-04-11T00:02:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["execution_paths"]["fade_tweet"] == 1
    assert stats["execution_paths"]["price_fade"] == 1
    assert stats["execution_paths"]["news"] == 1


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"signal_type": "fade_tweet"}, "fade_tweet"),
        ({"source": "price_fade"}, "price_fade"),
        ({"url": "kalshi://price_fade/KXTEST"}, "price_fade"),
        ({"reasoning": "[PRICE_FADE] KXTEST high_cross"}, "price_fade"),
        ({"reasoning": "[FADE/GEO/@Kalshi] bullish: hype"}, "fade_tweet"),
        ({"source": "Reuters"}, "news"),
    ],
)
def test_infer_signal_type_classification(record, expected):
    assert infer_signal_type(record) == expected


def test_summarize_applies_date_filter(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SIGNAL", "ts": "2026-04-09T23:59:59+00:00"},
            {"type": "OPPORTUNITY", "ts": "2026-04-10T12:00:00+00:00"},
            {"type": "PAPER_TRADE", "ts": "2026-04-11T23:59:59+00:00"},
            {"type": "SKIPPED", "ts": "2026-04-12T00:00:00+00:00"},
            {"type": "LIVE_ORDER"},
        ],
    )

    stats = summarize(
        path,
        since=parse_date_start("2026-04-10"),
        until=parse_date_end("2026-04-11"),
    )

    assert stats["records_kept"] == 2
    assert stats["event_counts"]["OPPORTUNITY"] == 1
    assert stats["event_counts"]["PAPER_TRADE"] == 1
    assert "SIGNAL" not in stats["event_counts"]
    assert "SKIPPED" not in stats["event_counts"]
    assert "LIVE_ORDER" not in stats["event_counts"]


def test_print_summary_handles_empty_stats(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    path.write_text("", encoding="utf-8")
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "DECISION FUNNEL SUMMARY" in output
    assert "No matching log records found." in output


def test_print_summary_handles_no_observable_funnel_stages(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "NEW_MARKET", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "PAPER_RESOLUTION", "ts": "2026-04-11T00:01:00+00:00"},
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "DECISION FUNNEL SUMMARY" in output
    assert "Signals logged                : 0" in output
    assert "Opportunities logged          : 0 (n/a of signals)" in output
    assert "Executor skips               : 0" in output
    assert "Executions                   : 0" in output
    assert "Path Contribution (decision-stage records)" in output
    assert "  (none)" in output


def test_print_summary_includes_pre_signal_rejections_when_present(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "ANALYSIS_REJECTED", "reason": "no_keywords", "ts": "2026-04-11T00:01:00+00:00"},
            {"type": "SIGNAL", "ts": "2026-04-11T00:02:00+00:00"},
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "Pre-signal rejections         : 2" in output
    assert "No-signal exits               : partially observable via ANALYSIS_REJECTED" in output
    assert "Pre-Signal Rejection Reasons" in output
    assert "stale_news" in output
    assert "no_keywords" in output


def test_print_summary_includes_stale_news_sections(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "Reuters",
                "ticker": "KX1",
                "age_seconds": 120,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "AP",
                "ticker": "KX2",
                "age_seconds": 4200,
                "ts": "2026-04-11T00:01:00+00:00",
            },
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "Stale-News Age Buckets" in output
    assert "Stale-News Sources" in output
    assert "Stale-News Tickers" in output
    assert "0-5m" in output
    assert "60m+" in output
    assert "Reuters" in output
    assert "KX1" in output


def test_print_summary_handles_absent_analysis_rejected_events_gracefully(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SIGNAL", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "OPPORTUNITY", "ts": "2026-04-11T00:01:00+00:00"},
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "Pre-signal rejections         : 0" in output
    assert "No-signal exits               : not directly observable in trades.jsonl" in output
    assert "Pre-Signal Rejection Reasons" in output
