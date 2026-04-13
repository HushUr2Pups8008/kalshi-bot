import json
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.freshness_diagnostics import (
    age_bucket,
    borderline_candidate,
    parse_date_end,
    parse_date_start,
    print_summary,
    strategy_misaligned_candidate,
    summarize,
)


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_freshness_diagnostics"
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


def test_summarize_empty_file():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        path.write_text("", encoding="utf-8")

        stats = summarize(path, since=None, until=None)

        assert stats["lines_total"] == 0
        assert stats["lines_malformed"] == 0
        assert stats["records_kept"] == 0
        assert stats["age_bearing_early_stale_records"] == 0
        assert stats["sources"] == {}
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_skips_malformed_lines():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                '{"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 400, "ts": "2026-04-11T00:00:00+00:00"}',
                '{"type": "EARLY_STALE_DROP"',
                '["not", "a", "dict"]',
            ],
        )

        stats = summarize(path, since=None, until=None)

        assert stats["lines_total"] == 3
        assert stats["lines_malformed"] == 2
        assert stats["records_kept"] == 1
        assert stats["sources"]["Reuters"]["early_stale_drops"] == 1
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_source_freshness_metrics():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {"type": "EARLY_FRESH_PASS", "source": "Reuters", "age_seconds": 50, "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "EARLY_FRESH_PASS", "source": "Reuters", "age_seconds": 200, "ts": "2026-04-11T00:01:00+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 50, "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 200, "ts": "2026-04-11T00:01:00+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 700, "ts": "2026-04-11T00:02:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "ts": "2026-04-11T00:03:00+00:00"},
                {"type": "EARLY_FRESH_PASS", "source": "AP", "age_seconds": 100, "ts": "2026-04-11T00:04:00+00:00"},
                {"type": "SIGNAL", "source": "AP", "ts": "2026-04-11T00:04:00+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "AP", "age_seconds": 1500, "ts": "2026-04-11T00:05:00+00:00"},
            ],
        )

        stats = summarize(path, since=None, until=None)
        reuters = stats["sources"]["Reuters"]
        ap = stats["sources"]["AP"]

        assert reuters["observed_records"] == 6
        assert reuters["early_stale_drops"] == 3
        assert reuters["fresh_passes"] == 2
        assert reuters["stale_rate"] == 0.5
        assert reuters["age_samples_count"] == 5
        assert reuters["median_age_seconds"] == 200
        assert reuters["freshest_age_seconds"] == 50
        assert reuters["within_60s"] == 2
        assert reuters["within_300s"] == 4
        assert reuters["over_300s"] == 1
        assert reuters["over_900s"] == 0

        assert ap["observed_records"] == 3
        assert ap["early_stale_drops"] == 1
        assert ap["fresh_passes"] == 1
        assert ap["stale_rate"] == pytest.approx(1 / 3)
        assert ap["median_age_seconds"] == 800
        assert ap["within_60s"] == 0
        assert ap["within_300s"] == 1
        assert ap["over_300s"] == 1
        assert ap["over_900s"] == 1
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_handles_missing_age_honestly():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {"type": "EARLY_FRESH_PASS", "source": "Reuters", "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "ts": "2026-04-11T00:01:00+00:00"},
            ],
        )

        stats = summarize(path, since=None, until=None)
        row = stats["sources"]["Reuters"]

        assert row["observed_records"] == 3
        assert row["early_stale_drops"] == 1
        assert row["fresh_passes"] == 1
        assert row["age_samples_count"] == 0
        assert row["median_age_seconds"] is None
        assert row["p90_age_seconds"] is None
        assert row["freshest_age_seconds"] is None
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_applies_date_filter():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 400, "ts": "2026-04-09T23:59:59+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 500, "ts": "2026-04-10T12:00:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "ts": "2026-04-11T23:59:59+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 600, "ts": "2026-04-12T00:00:00+00:00"},
            ],
        )

        stats = summarize(
            path,
            since=parse_date_start("2026-04-10"),
            until=parse_date_end("2026-04-11"),
        )

        row = stats["sources"]["Reuters"]
        assert row["observed_records"] == 2
        assert row["early_stale_drops"] == 1
        assert row["median_age_seconds"] == 500
    finally:
        _cleanup_tmp_dir(tmp)


def test_age_bucket_boundaries():
    assert age_bucket(60) == "<=60s"
    assert age_bucket(61) == "<=300s"
    assert age_bucket(300) == "<=300s"
    assert age_bucket(301) == "301-900s"
    assert age_bucket(900) == "301-900s"
    assert age_bucket(901) == ">900s"


def test_print_summary_handles_empty_stats(capsys):
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        path.write_text("", encoding="utf-8")
        stats = summarize(path, since=None, until=None)

        print_summary(stats, top=5, min_observations=5, since=None, until=None)
        output = capsys.readouterr().out

        assert "FRESHNESS DIAGNOSTICS" in output
        assert "No source-attributable freshness records found." in output
    finally:
        _cleanup_tmp_dir(tmp)


def test_print_summary_includes_ranked_sections(capsys):
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {"type": "EARLY_FRESH_PASS", "source": "Reuters", "age_seconds": 120, "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 400, "ts": "2026-04-11T00:00:00+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "Reuters", "age_seconds": 800, "ts": "2026-04-11T00:01:00+00:00"},
                {"type": "SIGNAL", "source": "Reuters", "ts": "2026-04-11T00:02:00+00:00"},
                {"type": "EARLY_FRESH_PASS", "source": "AP", "age_seconds": 100, "ts": "2026-04-11T00:03:00+00:00"},
                {"type": "EARLY_STALE_DROP", "source": "AP", "age_seconds": 1200, "ts": "2026-04-11T00:03:00+00:00"},
                {"type": "SIGNAL", "source": "AP", "ts": "2026-04-11T00:04:00+00:00"},
                {"type": "SIGNAL", "source": "AP", "ts": "2026-04-11T00:05:00+00:00"},
                {"type": "SIGNAL", "source": "AP", "ts": "2026-04-11T00:06:00+00:00"},
                {"type": "SIGNAL", "source": "AP", "ts": "2026-04-11T00:07:00+00:00"},
            ],
        )
        stats = summarize(path, since=None, until=None)

        print_summary(stats, top=5, min_observations=1, since=None, until=None)
        output = capsys.readouterr().out

        assert "Source Freshness Metrics" in output
        assert "Worst Offenders" in output
        assert "Borderline Sources" in output
        assert "Strategy-Misaligned Sources" in output
        assert "Freshest Sources" in output
        assert "Reuters" in output
        assert "AP" in output
        assert "EARLY_STALE_DROP and EARLY_FRESH_PASS" in output
        assert "fresh_pass=" in output
        assert "label=" in output
    finally:
        _cleanup_tmp_dir(tmp)


def test_borderline_and_strategy_misaligned_candidates():
    borderline = {
        "observed_records": 100,
        "early_stale_drops": 90,
        "stale_rate": 0.90,
        "age_samples_count": 10,
        "median_age_seconds": 1200,
        "p90_age_seconds": 2400,
        "freshest_age_seconds": 340,
        "within_60s": 0,
        "within_300s": 0,
        "over_300s": 10,
        "over_900s": 8,
        "interpretation": "near-threshold",
    }
    misaligned = {
        "observed_records": 100,
        "early_stale_drops": 100,
        "stale_rate": 1.0,
        "age_samples_count": 10,
        "median_age_seconds": 7200,
        "p90_age_seconds": 14400,
        "freshest_age_seconds": 1200,
        "within_60s": 0,
        "within_300s": 0,
        "over_300s": 10,
        "over_900s": 10,
        "interpretation": "chronically late",
    }
    insufficient = {
        "observed_records": 100,
        "early_stale_drops": 70,
        "stale_rate": 0.70,
        "age_samples_count": 1,
        "median_age_seconds": 400,
        "p90_age_seconds": 400,
        "freshest_age_seconds": 400,
        "within_60s": 0,
        "within_300s": 0,
        "over_300s": 1,
        "over_900s": 0,
        "interpretation": "insufficient data",
    }

    assert borderline_candidate(borderline) is True
    assert strategy_misaligned_candidate(borderline) is False
    assert strategy_misaligned_candidate(misaligned) is True
    assert borderline_candidate(misaligned) is False
    assert borderline_candidate(insufficient) is False
    assert strategy_misaligned_candidate(insufficient) is False
