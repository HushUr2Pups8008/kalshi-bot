import json
import shutil
import uuid
from pathlib import Path

from scripts.match_quality_diagnostics import (
    parse_date_end,
    parse_date_start,
    print_backfill_summary,
    print_summary,
    summarize,
    summarize_backfill,
)


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_match_quality_diagnostics"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _cleanup_tmp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for record in records:
        if isinstance(record, str):
            lines.append(record)
        else:
            lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_summarize_collects_low_quality_rates_and_examples():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "source": "Reuters",
                    "headline": "Iran threatens Strait of Hormuz closure",
                    "ticker": "KXIRAN-1",
                    "market_title": "Will Iran close the Strait of Hormuz in 2026?",
                    "match_score": 0.18,
                    "matched_tokens": ["hormuz", "iran", "strait"],
                    "token_overlap_count": 3,
                    "geo_overlap_count": 1,
                    "generic_overlap_count": 2,
                    "headline_token_count": 5,
                    "market_title_token_count": 6,
                    "overlap_ratio": 0.6,
                    "low_match_quality": False,
                    "heuristic_flags": [],
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "source": "NYT > World News",
                    "headline": "Trump says Iran talks have collapsed again",
                    "ticker": "KXTRUMP-25A",
                    "market_title": "Will Trump invoke the 25th Amendment this year?",
                    "match_score": 0.061,
                    "matched_tokens": ["trump"],
                    "token_overlap_count": 1,
                    "geo_overlap_count": 1,
                    "generic_overlap_count": 0,
                    "headline_token_count": 5,
                    "market_title_token_count": 6,
                    "overlap_ratio": 0.1667,
                    "low_match_quality": True,
                    "heuristic_flags": ["near_threshold_score", "single_named_entity_only", "minimal_overlap"],
                    "ts": "2026-04-12T10:01:00+00:00",
                },
            ],
        )

        stats = summarize(path, since=None, until=None)

        assert stats["match_records"] == 2
        assert stats["low_quality_matches"] == 1
        assert stats["by_source"]["NYT > World News"] == 1
        assert stats["by_ticker"]["KXTRUMP-25A"] == 1
        assert stats["heuristic_flags"]["single_named_entity_only"] == 1
        assert stats["examples_bad"][0]["ticker"] == "KXTRUMP-25A"
        assert stats["examples_good"][0]["ticker"] == "KXIRAN-1"
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_respects_date_filter_and_missing_score_note():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "source": "Reuters",
                    "headline": "Old item",
                    "ticker": "KXOLD",
                    "market_title": "Old market",
                    "low_match_quality": True,
                    "ts": "2026-04-10T23:59:59+00:00",
                },
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "source": "Reuters",
                    "headline": "New item",
                    "ticker": "KXNEW",
                    "market_title": "New market",
                    "low_match_quality": False,
                    "ts": "2026-04-11T12:00:00+00:00",
                },
            ],
        )

        stats = summarize(
            path,
            since=parse_date_start("2026-04-11"),
            until=parse_date_end("2026-04-11"),
        )

        assert stats["match_records"] == 1
        assert stats["low_quality_matches"] == 0
        assert stats["match_score_available"] is False
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_reads_partitioned_trade_root():
    tmp = _make_tmp_dir()
    try:
        root = tmp / "trades"
        _write_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-11.jsonl",
            [
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "source": "Reuters",
                    "headline": "Old item",
                    "ticker": "KXOLD",
                    "market_title": "Old market",
                    "low_match_quality": False,
                    "ts": "2026-04-11T12:00:00+00:00",
                },
            ],
        )
        _write_jsonl(
            root / "live" / "trades.jsonl",
            [
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "source": "AP",
                    "headline": "New item",
                    "ticker": "KXNEW",
                    "market_title": "New market",
                    "low_match_quality": True,
                    "heuristic_flags": ["minimal_overlap"],
                    "ts": "2026-04-12T12:00:00+00:00",
                },
            ],
        )

        stats = summarize(root, since=None, until=None)

        assert stats["lines_total"] == 2
        assert stats["match_records"] == 2
        assert stats["low_quality_matches"] == 1
    finally:
        _cleanup_tmp_dir(tmp)


def test_print_summary_includes_sections(capsys):
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "source": "Reuters",
                    "headline": "Iran threatens Strait of Hormuz closure",
                    "ticker": "KXIRAN-1",
                    "market_title": "Will Iran close the Strait of Hormuz in 2026?",
                    "match_score": 0.18,
                    "matched_tokens": ["hormuz", "iran", "strait"],
                    "token_overlap_count": 3,
                    "geo_overlap_count": 1,
                    "generic_overlap_count": 2,
                    "headline_token_count": 5,
                    "market_title_token_count": 6,
                    "overlap_ratio": 0.6,
                    "low_match_quality": False,
                    "heuristic_flags": [],
                    "ts": "2026-04-12T10:00:00+00:00",
                }
            ],
        )
        stats = summarize(path, since=None, until=None)
        print_summary(stats, top=5, recent=5, since=None, until=None)
        output = capsys.readouterr().out

        assert "MATCH QUALITY DIAGNOSTICS" in output
        assert "Low-Quality Match Flags" in output
        assert "Low-Quality Matches by Source" in output
        assert "Recent Low-Quality Examples" in output
        assert "Recent Higher-Quality Examples" in output
        assert "approximate and non-blocking" in output
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_backfill_reconstructs_titles_and_rates():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "OPPORTUNITY",
                    "ticker": "KXTRUMP-25A",
                    "market_title": "Will Trump order military action under the 25th Amendment this year?",
                    "ts": "2026-04-12T10:00:03+00:00",
                },
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "source": "NYT > World News",
                    "headline": "Trump says Iran talks have collapsed again",
                    "ticker": "KXTRUMP-25A",
                    "final_probability": 0.50,
                    "market_price": 0.50,
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "SKIPPED",
                    "ticker": "KXTRUMP-25A",
                    "headline": "Trump says Iran talks have collapsed again",
                    "reason": "edge +0.0000 below min_edge 0.02",
                    "edge": 0.0,
                    "ts": "2026-04-12T10:00:04+00:00",
                },
                {
                    "type": "PAPER_TRADE",
                    "signal_source": "Reuters",
                    "signal_headline": "Iran threatens Strait of Hormuz closure",
                    "ticker": "KXIRAN-1",
                    "market_title": "Will Iran close the Strait of Hormuz in 2026?",
                    "edge": 0.08,
                    "ts": "2026-04-12T10:01:00+00:00",
                },
            ],
        )

        stats = summarize_backfill(path, since=None, until=None, event_limit=10)

        assert stats["evaluated_events"] == 2
        assert stats["low_quality_matches"] == 1
        assert stats["rows_missing_title"] == 0
        assert stats["rows_missing_score"] == 2
        low = [row for row in stats["rows"] if row["low_match_quality"]]
        high = [row for row in stats["rows"] if not row["low_match_quality"]]
        assert low[0]["ticker"] == "KXTRUMP-25A"
        assert low[0]["skip_reason"] == "edge +0.0000 below min_edge 0.02"
        assert high[0]["ticker"] == "KXIRAN-1"
        assert stats["by_source"][0]["source"] == "NYT > World News"
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_backfill_honestly_excludes_missing_titles():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "source": "Reuters",
                    "headline": "Title missing",
                    "ticker": "KXMISS",
                    "final_probability": 0.55,
                    "market_price": 0.50,
                    "ts": "2026-04-12T10:00:00+00:00",
                },
            ],
        )

        stats = summarize_backfill(path, since=None, until=None, event_limit=10)
        assert stats["evaluated_events"] == 0
        assert stats["rows_missing_title"] == 1
    finally:
        _cleanup_tmp_dir(tmp)


def test_print_backfill_summary_includes_sections(capsys):
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "PAPER_TRADE",
                    "signal_source": "Reuters",
                    "signal_headline": "Iran threatens Strait of Hormuz closure",
                    "ticker": "KXIRAN-1",
                    "market_title": "Will Iran close the Strait of Hormuz in 2026?",
                    "edge": 0.08,
                    "ts": "2026-04-12T10:01:00+00:00",
                },
            ],
        )
        stats = summarize_backfill(path, since=None, until=None, event_limit=10)
        print_backfill_summary(stats, top=5, recent=5, event_limit=10, since=None, until=None)
        output = capsys.readouterr().out

        assert "MATCH QUALITY BACKFILL" in output
        assert "Edge vs Match Quality" in output
        assert "Top Low-Quality Examples" in output
        assert "Low-Quality Rate by Source" in output
        assert "approximate overlap heuristics" in output
    finally:
        _cleanup_tmp_dir(tmp)
