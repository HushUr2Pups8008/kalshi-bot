import json
import shutil
import uuid
from pathlib import Path

from scripts.keyword_feedback import (
    parse_date_end,
    parse_date_start,
    print_summary,
    summarize,
)


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_keyword_feedback"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _cleanup_tmp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _write_jsonl(path: Path, records) -> None:
    lines = []
    for record in records:
        if isinstance(record, str):
            lines.append(record)
        else:
            lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_summarize_collects_no_keyword_misses_and_candidate_phrases():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "NYT > World News",
                    "ticker": "KXIRAN-1",
                    "headline": "Iran war live updates as talks collapse again",
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "Reuters",
                    "ticker": "KXIRAN-2",
                    "headline": "Iran war talks collapse after blockade threat",
                    "ts": "2026-04-12T10:01:00+00:00",
                },
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "method": "keyword_gate",
                    "keywords": [],
                    "source": "Reuters",
                    "ticker": "KXIRAN-2",
                    "headline": "Iran war talks collapse after blockade threat",
                    "ts": "2026-04-12T10:01:00+00:00",
                },
            ],
        )

        stats = summarize(path, since=None, until=None)

        assert stats["no_keyword_misses"] == 2
        assert stats["corroborating_keyword_gate_records"] == 1
        phrases = {row["phrase"]: row for row in stats["phrases"]}
        assert phrases["iran war"]["count"] == 2
        assert phrases["talks collapse"]["count"] == 2
        assert "NYT > World News" in phrases["iran war"]["sources"]
        assert phrases["talks collapse"]["category"] == "specific event phrase"
        assert phrases["talks collapse"]["review_bucket"] == "strongest specific candidates"
        assert phrases["talks collapse"]["candidate_score"] > phrases["iran war"]["candidate_score"]
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_respects_date_filter_and_excludes_test_records():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "r/testfeed",
                    "ticker": "KXTEST-1",
                    "headline": "Test phrase should be filtered",
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "Reuters",
                    "ticker": "KXREAL-1",
                    "headline": "Real phrase survives filter",
                    "ts": "2026-04-13T10:00:00+00:00",
                },
            ],
        )

        stats = summarize(
            path,
            since=parse_date_start("2026-04-13"),
            until=parse_date_end("2026-04-13"),
            exclude_test=True,
        )

        assert stats["no_keyword_misses"] == 1
        phrases = {row["phrase"] for row in stats["phrases"]}
        assert "real phrase" in phrases
    finally:
        _cleanup_tmp_dir(tmp)


def test_generic_phrase_gets_risk_flags():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "Reuters",
                    "ticker": "KXONE",
                    "headline": "Watch live updates on peace talks",
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "Reuters",
                    "ticker": "KXTWO",
                    "headline": "Watch live updates on peace talks again",
                    "ts": "2026-04-12T10:01:00+00:00",
                },
            ],
        )

        stats = summarize(path, since=None, until=None)
        phrases = {row["phrase"]: row for row in stats["phrases"]}

        assert "generic_terms" in phrases["watch peace"]["risk_flags"]
        assert "broad_false_positive_risk" in phrases["watch peace"]["risk_flags"]
        assert phrases["watch peace"]["category"] == "generic / high false-positive risk"
        assert phrases["watch peace"]["review_bucket"] == "likely reject / too broad"
    finally:
        _cleanup_tmp_dir(tmp)


def test_named_entity_phrase_lands_on_watchlist():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "Reuters",
                    "ticker": "KXTRUMP-1",
                    "headline": "Donald Trump says nothing new",
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "AP",
                    "ticker": "KXTRUMP-2",
                    "headline": "Donald Trump visits Europe",
                    "ts": "2026-04-12T10:01:00+00:00",
                },
            ],
        )

        stats = summarize(path, since=None, until=None)
        phrases = {row["phrase"]: row for row in stats["phrases"]}
        assert phrases["donald trump"]["category"] == "named-entity phrase"
        assert "overly_broad_named_entity" in phrases["donald trump"]["risk_flags"]
        assert phrases["donald trump"]["review_bucket"] == "watchlist / ambiguous candidates"
    finally:
        _cleanup_tmp_dir(tmp)


def test_print_summary_includes_expected_sections(capsys):
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "Reuters",
                    "ticker": "KXIRAN-1",
                    "headline": "Iran war talks collapse after blockade threat",
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "ANALYSIS_REJECTED",
                    "reason": "no_keywords",
                    "source": "Reuters",
                    "ticker": "KXIRAN-2",
                    "headline": "Iran war talks collapse after sanctions threat",
                    "ts": "2026-04-12T10:01:00+00:00",
                },
            ],
        )

        stats = summarize(path, since=None, until=None)
        print_summary(stats, top=5, min_count=2, max_examples=2, since=None, until=None)
        output = capsys.readouterr().out

        assert "KEYWORD FEEDBACK AUDIT" in output
        assert "Available Miss Corpus" in output
        assert "Strongest Specific Candidates" in output
        assert "Watchlist / Ambiguous Candidates" in output
        assert "Likely Reject / Too Broad" in output
        assert "Risk Notes" in output
        assert "iran war" in output
    finally:
        _cleanup_tmp_dir(tmp)
