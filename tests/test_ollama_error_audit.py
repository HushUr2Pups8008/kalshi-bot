"""Tests for scripts/ollama_error_audit.py.

Tests are lightweight, synthetic, and require no live Ollama, no network,
and no runtime bot infrastructure.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path


from scripts.ollama_error_audit import (
    BUCKET_BAD_REQUEST,
    BUCKET_MODEL_NOT_FOUND,
    BUCKET_OTHER,
    BUCKET_SERVER_ERROR,
    BUCKET_TIMEOUT,
    OllamaErrorEvent,
    build_report,
    classify,
    collect,
    gap_analysis,
    parse_log_file,
    parse_log_line,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_NEW_FORMAT_404 = (
    "2026-04-15 15:49:56,122 UTC WARNING  signal_analyzer"
    "      Ollama HTTP 404 from /v1/chat/completions"
    ' -- body: {"error":{"message":"model \'qwen2.5:3b\' not found",'
    '"type":"not_found_error","param":null,"code":null}}'
)

_OLD_FORMAT_404 = (
    "2026-04-15 12:22:47,529 UTC DEBUG    signal_analyzer"
    "      Ollama returned HTTP 404"
)

_TIMEOUT_LINE = (
    "2026-04-15 08:00:00,000 UTC WARNING  signal_analyzer"
    "      Ollama estimation failed: timed out after 60s"
    " (model loading or CPU overloaded)"
)

_500_LINE = (
    "2026-04-15 09:00:00,000 UTC WARNING  signal_analyzer"
    "      Ollama HTTP 500 from /v1/chat/completions"
    " -- body: internal server error"
)

_400_LINE = (
    "2026-04-15 10:00:00,000 UTC WARNING  signal_analyzer"
    "      Ollama HTTP 400 from /v1/chat/completions"
    ' -- body: {"error":"bad field"}'
)

_OTHER_LINE = (
    "2026-04-15 11:00:00,000 UTC WARNING  signal_analyzer"
    "      Ollama HTTP 403 from /v1/chat/completions"
    " -- body: forbidden"
)


def _make_tmp(suffix: str = "") -> Path:
    root = Path(__file__).resolve().parent / "_tmp_ollama_error_audit"
    path = root / (uuid.uuid4().hex + suffix)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup(path: Path) -> None:
    shutil.rmtree(path.parent if path.is_file() else path, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. parse_log_line: new WARNING format
# ---------------------------------------------------------------------------


class TestParseLogLine:
    def test_new_format_404_parsed_correctly(self):
        event = parse_log_line(_NEW_FORMAT_404)
        assert event is not None
        assert event.status == 404
        assert event.ts == "2026-04-15 15:49:56,122 UTC"
        assert "not found" in event.body.lower()

    def test_old_format_404_parsed_no_body(self):
        event = parse_log_line(_OLD_FORMAT_404)
        assert event is not None
        assert event.status == 404
        assert event.ts == "2026-04-15 12:22:47,529 UTC"
        # old format has no body snippet
        assert event.body == ""

    def test_500_line_parsed(self):
        event = parse_log_line(_500_LINE)
        assert event is not None
        assert event.status == 500

    def test_400_line_parsed(self):
        event = parse_log_line(_400_LINE)
        assert event is not None
        assert event.status == 400

    def test_non_ollama_line_returns_none(self):
        assert parse_log_line("2026-04-15 12:00:00,000 UTC INFO main normal startup") is None

    def test_timeout_warning_line_parsed(self):
        event = parse_log_line(_TIMEOUT_LINE)
        assert event is not None
        assert event.bucket == BUCKET_TIMEOUT
        assert event.status is None

    def test_raw_truncated_to_300_chars(self):
        long_line = _NEW_FORMAT_404 + "x" * 400
        event = parse_log_line(long_line)
        assert event is not None
        assert len(event.raw) <= 300


# ---------------------------------------------------------------------------
# 2. classify: bucket assignment
# ---------------------------------------------------------------------------


class TestClassify:
    def test_404_not_found_is_model_not_found(self):
        assert classify(404, "model 'qwen2.5:3b' not found", "") == BUCKET_MODEL_NOT_FOUND

    def test_404_without_not_found_body_is_other(self):
        # A 404 without "not found" in body is ambiguous -- classify as OTHER
        assert classify(404, "some other body", "") == BUCKET_OTHER

    def test_timeout_in_body_is_timeout_bucket(self):
        assert classify(None, "request timed out", "") == BUCKET_TIMEOUT

    def test_timeout_in_line_is_timeout_bucket(self):
        assert classify(200, "ok", "timed out after 60s") == BUCKET_TIMEOUT

    def test_500_is_server_error(self):
        assert classify(500, "", "") == BUCKET_SERVER_ERROR

    def test_503_is_server_error(self):
        assert classify(503, "service unavailable", "") == BUCKET_SERVER_ERROR

    def test_400_is_bad_request(self):
        assert classify(400, "", "") == BUCKET_BAD_REQUEST

    def test_403_is_other(self):
        assert classify(403, "forbidden", "") == BUCKET_OTHER

    def test_none_status_no_timeout_is_other(self):
        assert classify(None, "unknown error", "") == BUCKET_OTHER


# ---------------------------------------------------------------------------
# 3. parse_log_file: reads a file
# ---------------------------------------------------------------------------


class TestParseLogFile:
    def test_reads_synthetic_log(self):
        tmp_path = _make_tmp()
        log = tmp_path / "bot.log"
        log.write_text(
            "\n".join([_NEW_FORMAT_404, _OLD_FORMAT_404, _500_LINE, "unrelated line"]),
            encoding="utf-8",
        )
        events = parse_log_file(log)
        assert len(events) == 3
        _cleanup(tmp_path)

    def test_missing_file_returns_empty(self):
        tmp_path = _make_tmp()
        events = parse_log_file(tmp_path / "nonexistent.log")
        assert events == []
        _cleanup(tmp_path)

    def test_returns_events_in_order(self):
        tmp_path = _make_tmp()
        log = tmp_path / "bot.log"
        log.write_text("\n".join([_500_LINE, _400_LINE, _NEW_FORMAT_404]), encoding="utf-8")
        events = parse_log_file(log)
        assert [e.status for e in events] == [500, 400, 404]
        _cleanup(tmp_path)


# ---------------------------------------------------------------------------
# 4. Summary aggregation on synthetic sample
# ---------------------------------------------------------------------------


class TestAggregation:
    def _make_events(self) -> list[OllamaErrorEvent]:
        lines = [
            _NEW_FORMAT_404,   # model_not_found
            _OLD_FORMAT_404,   # model_not_found (old format -- no body, so BUCKET_OTHER)
            _TIMEOUT_LINE,     # timeout_or_connection
            _500_LINE,         # server_error
            _400_LINE,         # bad_request
        ]
        return [e for line in lines if (e := parse_log_line(line)) is not None]

    def test_events_count_matches(self):
        events = self._make_events()
        assert len(events) == 5

    def test_dominant_bucket_correct(self):
        # new-format 404 -> model_not_found (1)
        # old-format 404 -> OTHER (no body, no "not found" text)
        # timeout -> timeout_or_connection (1)
        # 500 -> server_error (1)
        # 400 -> bad_request (1)
        events = self._make_events()
        from collections import Counter
        bucket_counts = Counter(e.bucket for e in events)
        # Each bucket has exactly 1 event; model_not_found and other both have 1
        assert bucket_counts[BUCKET_MODEL_NOT_FOUND] == 1
        assert bucket_counts[BUCKET_TIMEOUT] == 1
        assert bucket_counts[BUCKET_SERVER_ERROR] == 1
        assert bucket_counts[BUCKET_BAD_REQUEST] == 1

    def test_status_counts(self):
        events = self._make_events()
        from collections import Counter
        sc = Counter(e.status for e in events if e.status is not None)
        assert sc[404] == 2  # new + old format 404
        assert sc[500] == 1
        assert sc[400] == 1


# ---------------------------------------------------------------------------
# 5. Report rendering on fixture log
# ---------------------------------------------------------------------------


class TestBuildReport:
    def _fixture_events(self) -> list[OllamaErrorEvent]:
        return [e for line in [_NEW_FORMAT_404, _500_LINE, _400_LINE] if (e := parse_log_line(line)) is not None]

    def test_report_contains_header(self):
        events = self._fixture_events()
        report = build_report(events)
        combined = "\n".join(report)
        assert "OLLAMA ERROR AUDIT" in combined

    def test_report_contains_total_count(self):
        events = self._fixture_events()
        report = build_report(events)
        combined = "\n".join(report)
        assert "3" in combined  # total events

    def test_report_contains_bucket_names(self):
        events = self._fixture_events()
        report = build_report(events)
        combined = "\n".join(report)
        assert "model_not_found" in combined
        assert "server_error" in combined
        assert "bad_request" in combined

    def test_report_contains_most_recent_section(self):
        events = self._fixture_events()
        report = build_report(events)
        combined = "\n".join(report)
        assert "MOST RECENT" in combined

    def test_report_contains_interpretation(self):
        events = self._fixture_events()
        report = build_report(events)
        combined = "\n".join(report)
        assert "INTERPRETATION" in combined

    def test_empty_events_produces_safe_report(self):
        report = build_report([])
        combined = "\n".join(report)
        assert "No Ollama error events found" in combined

    def test_max_examples_limits_output(self):
        # Build 10 identical model_not_found events
        events = [parse_log_line(_NEW_FORMAT_404)] * 10
        events = [e for e in events if e is not None]
        report = build_report(events, max_examples=3)
        combined = "\n".join(report)
        assert "and 7 more" in combined

    def test_interpretation_is_model_not_found_for_all_404s(self):
        events = [parse_log_line(_NEW_FORMAT_404)] * 5
        events = [e for e in events if e is not None]
        report = build_report(events)
        combined = "\n".join(report)
        assert "model_not_found" in combined.lower()
        assert "configuration issue" in combined.lower()


# ---------------------------------------------------------------------------
# 6. Gap analysis
# ---------------------------------------------------------------------------


class TestGapAnalysis:
    def test_returns_empty_for_single_event(self):
        event = parse_log_line(_NEW_FORMAT_404)
        assert event is not None
        result = gap_analysis([event])
        assert result == {}

    def test_computes_gap_for_two_events(self):
        e1 = parse_log_line(_NEW_FORMAT_404)   # 2026-04-15 15:49:56
        e2 = parse_log_line(
            "2026-04-15 15:50:56,122 UTC WARNING  signal_analyzer"
            "      Ollama HTTP 404 from /v1 -- body: model not found"
        )
        assert e1 is not None and e2 is not None
        result = gap_analysis([e1, e2])
        assert result["count"] == 2
        assert abs(result["avg_gap"] - 60.0) < 1.0

    def test_burst_detection(self):
        # Three events within 2s of each other = one burst of size 3
        lines = [
            "2026-04-15 12:00:00,000 UTC WARNING  signal_analyzer      Ollama HTTP 404 from /v1 -- body: model not found",
            "2026-04-15 12:00:01,000 UTC WARNING  signal_analyzer      Ollama HTTP 404 from /v1 -- body: model not found",
            "2026-04-15 12:00:02,000 UTC WARNING  signal_analyzer      Ollama HTTP 404 from /v1 -- body: model not found",
        ]
        events = [e for line in lines if (e := parse_log_line(line)) is not None]
        assert len(events) == 3
        result = gap_analysis(events)
        # All gaps < 5s -> one burst group of size 3
        assert result["burst_groups"] == [3]

    def test_two_separate_bursts(self):
        lines = [
            "2026-04-15 12:00:00,000 UTC WARNING  signal_analyzer      Ollama HTTP 404 from /v1 -- body: model not found",
            "2026-04-15 12:00:01,000 UTC WARNING  signal_analyzer      Ollama HTTP 404 from /v1 -- body: model not found",
            # 10-minute gap
            "2026-04-15 12:10:00,000 UTC WARNING  signal_analyzer      Ollama HTTP 404 from /v1 -- body: model not found",
            "2026-04-15 12:10:01,000 UTC WARNING  signal_analyzer      Ollama HTTP 404 from /v1 -- body: model not found",
        ]
        events = [e for line in lines if (e := parse_log_line(line)) is not None]
        result = gap_analysis(events)
        assert result["burst_groups"] == [2, 2]


# ---------------------------------------------------------------------------
# 7. collect: multi-file path
# ---------------------------------------------------------------------------


class TestCollect:
    def test_collects_from_multiple_files(self):
        tmp_path = _make_tmp()
        f1 = tmp_path / "bot.log.1"
        f2 = tmp_path / "bot.log"
        f1.write_text(_NEW_FORMAT_404 + "\n", encoding="utf-8")
        f2.write_text(_500_LINE + "\n", encoding="utf-8")
        events = collect([f1, f2])
        assert len(events) == 2
        # f1 first, f2 second (given path order)
        assert events[0].status == 404
        assert events[1].status == 500
        _cleanup(tmp_path)

    def test_skips_missing_file(self):
        tmp_path = _make_tmp()
        f1 = tmp_path / "missing.log"
        f2 = tmp_path / "bot.log"
        f2.write_text(_400_LINE + "\n", encoding="utf-8")
        events = collect([f1, f2])
        assert len(events) == 1
        assert events[0].status == 400
        _cleanup(tmp_path)

    def test_glob_pattern_reads_archives_then_active(self):
        tmp_path = _make_tmp()
        f1 = tmp_path / "bot.log.2026-04-15"
        f2 = tmp_path / "bot.log"
        f1.write_text(_NEW_FORMAT_404 + "\n", encoding="utf-8")
        f2.write_text(_500_LINE + "\n", encoding="utf-8")

        events = collect([tmp_path / "bot.log*"])
        assert [event.status for event in events] == [404, 500]
        _cleanup(tmp_path)
