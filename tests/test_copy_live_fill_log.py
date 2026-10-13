"""The daily live-fill copy must not wipe the committed report."""

from __future__ import annotations

from scripts.copy_live_fill_log import copy_live_fill_log
from trading.executor import LIVE_FILL_LOG_ARCHIVE_PATH, LIVE_FILL_LOG_PATH


def test_runtime_fill_log_is_gitignored_and_the_report_stays_in_docs():
    assert LIVE_FILL_LOG_PATH.name == "live-fill-log.md"
    assert LIVE_FILL_LOG_PATH.parent.name == "state"
    assert LIVE_FILL_LOG_PATH.parent.parent.name == "logs"
    assert LIVE_FILL_LOG_ARCHIVE_PATH.parts[-3:] == (
        "docs",
        "reports",
        "live-fill-log.md",
    )


def test_missing_or_empty_runtime_log_does_not_replace_the_report(tmp_path):
    report = tmp_path / "docs" / "live-fill-log.md"
    report.parent.mkdir()
    report.write_text("kept\n", encoding="utf-8")
    missing = tmp_path / "logs" / "state" / "live-fill-log.md"
    assert copy_live_fill_log(missing, report) is False
    assert report.read_text(encoding="utf-8") == "kept\n"

    missing.parent.mkdir(parents=True)
    missing.write_text(" \n", encoding="utf-8")
    assert copy_live_fill_log(missing, report) is False
    assert report.read_text(encoding="utf-8") == "kept\n"


def test_daily_copy_replaces_the_report_with_the_runtime_log(tmp_path):
    source = tmp_path / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "2026-10-12T19:11:24Z, KXTRUMPSAY-26OCT19-GOLF, yes, 89, 8, abc, 201\n",
        encoding="utf-8",
    )
    report = tmp_path / "docs" / "reports" / "live-fill-log.md"
    report.parent.mkdir(parents=True)
    report.write_text("old\n", encoding="utf-8")
    assert copy_live_fill_log(source, report) is True
    assert report.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert "KALSHI-ACCESS" not in report.read_text(encoding="utf-8")
