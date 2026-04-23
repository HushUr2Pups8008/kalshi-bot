import shutil
import uuid
from pathlib import Path

from scripts.match_suppression_audit import (
    classify_candidate,
    parse_date_end,
    parse_date_start,
    print_summary,
    summarize,
)
from tests._helpers import write_jsonl


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_match_suppression_audit"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _cleanup_tmp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def test_classify_candidate_safe_vs_risky():
    risky = {
        "ticker": "KXTRUMPIRAN-26MAY01",
        "matched_tokens": ["iran"],
    }
    safe = {
        "ticker": "KXMOCTRUMP25-26-APR24",
        "matched_tokens": ["iran"],
    }
    assert classify_candidate(risky) == "risky"
    assert classify_candidate(safe) == "safe"


def test_summarize_collects_safe_and_risky_counts():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        write_jsonl(
            path,
            [
                {
                    "type": "MATCH_SUPPRESSION_CANDIDATE",
                    "source": "NYT > World News",
                    "headline": "Iran update",
                    "ticker": "KXTRUMPIRAN-26MAY01",
                    "match_score": 0.0753,
                    "overlap_count": 1,
                    "overlap_ratio": 0.25,
                    "heuristic_flags": ["single_named_entity_only"],
                    "matched_tokens": ["iran"],
                    "ts": "2026-04-12T22:21:10+00:00",
                },
                {
                    "type": "MATCH_SUPPRESSION_CANDIDATE",
                    "source": "NYT > World News",
                    "headline": "Iran update",
                    "ticker": "KXMOCTRUMP25-26-APR24",
                    "match_score": 0.0610,
                    "overlap_count": 1,
                    "overlap_ratio": 0.08,
                    "heuristic_flags": ["minimal_overlap"],
                    "matched_tokens": ["iran"],
                    "ts": "2026-04-12T22:22:10+00:00",
                },
            ],
        )

        stats = summarize(path, since=None, until=None)
        assert stats["total_candidates"] == 2
        assert stats["safe_count"] == 1
        assert stats["risky_count"] == 1
        assert stats["by_source"]["NYT > World News"]["safe"] == 1
        assert stats["by_source"]["NYT > World News"]["risky"] == 1
        assert stats["safe_examples"][0]["ticker"] == "KXMOCTRUMP25-26-APR24"
        assert stats["risky_examples"][0]["ticker"] == "KXTRUMPIRAN-26MAY01"
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_reads_partitioned_trade_root():
    tmp = _make_tmp_dir()
    try:
        root = tmp / "trades"
        write_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-11.jsonl",
            [
                {
                    "type": "MATCH_SUPPRESSION_CANDIDATE",
                    "source": "Reuters",
                    "headline": "Iran update",
                    "ticker": "KXSAFE",
                    "matched_tokens": ["iran"],
                    "ts": "2026-04-11T00:00:00+00:00",
                },
            ],
        )
        write_jsonl(
            root / "live" / "trades.jsonl",
            [
                {
                    "type": "MATCH_SUPPRESSION_CANDIDATE",
                    "source": "Reuters",
                    "headline": "Trump Iran update",
                    "ticker": "KXIRAN",
                    "matched_tokens": ["iran"],
                    "ts": "2026-04-12T00:00:00+00:00",
                },
            ],
        )

        stats = summarize(root, since=None, until=None)

        assert stats["lines_total"] == 2
        assert stats["total_candidates"] == 2
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_applies_date_filter():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        write_jsonl(
            path,
            [
                {
                    "type": "MATCH_SUPPRESSION_CANDIDATE",
                    "source": "Reuters",
                    "headline": "Old",
                    "ticker": "KXOLDIRAN",
                    "matched_tokens": ["iran"],
                    "ts": "2026-04-10T23:59:59+00:00",
                },
                {
                    "type": "MATCH_SUPPRESSION_CANDIDATE",
                    "source": "Reuters",
                    "headline": "New",
                    "ticker": "KXNEW",
                    "matched_tokens": ["iran"],
                    "ts": "2026-04-11T12:00:00+00:00",
                },
            ],
        )

        stats = summarize(
            path,
            since=parse_date_start("2026-04-11"),
            until=parse_date_end("2026-04-11"),
        )
        assert stats["total_candidates"] == 1
        assert stats["safe_count"] == 1
    finally:
        _cleanup_tmp_dir(tmp)


def test_print_summary_includes_sections(capsys):
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        write_jsonl(
            path,
            [
                {
                    "type": "MATCH_SUPPRESSION_CANDIDATE",
                    "source": "NYT > World News",
                    "headline": "Iran update",
                    "ticker": "KXMOCTRUMP25-26-APR24",
                    "match_score": 0.0610,
                    "overlap_count": 1,
                    "overlap_ratio": 0.08,
                    "heuristic_flags": ["minimal_overlap"],
                    "matched_tokens": ["iran"],
                    "ts": "2026-04-12T22:22:10+00:00",
                },
            ],
        )
        stats = summarize(path, since=None, until=None)
        print_summary(stats, top=5, recent=5, since=None, until=None)
        output = capsys.readouterr().out
        assert "MATCH SUPPRESSION AUDIT" in output
        assert "By Source" in output
        assert "By Ticker" in output
        assert "Recent Safe Examples" in output
        assert "Recent Risky Examples" in output
        assert "heuristic only" in output
    finally:
        _cleanup_tmp_dir(tmp)
