from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from utils.app_log_reader import iter_app_log_records
from tests._helpers import make_tmp_dir


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_reads_valid_logger_format_line():
    tmp_path = make_tmp_dir("app_log_reader")
    path = tmp_path / "bot.log"
    _write_lines(
        path,
        ["2026-04-16 09:30:45,123 UTC ERROR    ws_task               Connection refused; retrying"],
    )

    record = next(iter_app_log_records(path))
    assert record["ts"] == datetime(2026, 4, 16, 9, 30, 45, 123000, tzinfo=timezone.utc)
    assert record["level"] == "ERROR"
    assert record["task"] == "ws_task"
    assert record["message"] == "Connection refused; retrying"
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_reads_bracket_task_format_line():
    tmp_path = make_tmp_dir("app_log_reader")
    path = tmp_path / "bot.log"
    _write_lines(
        path,
        ["2026-04-16 09:30:45 ERROR: [ws_task] Connection refused; retrying"],
    )

    record = next(iter_app_log_records(path))
    assert record["ts"] == datetime(2026, 4, 16, 9, 30, 45, tzinfo=timezone.utc)
    assert record["level"] == "ERROR"
    assert record["task"] == "ws_task"
    assert record["message"] == "Connection refused; retrying"
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_malformed_line_is_safe():
    tmp_path = make_tmp_dir("app_log_reader")
    path = tmp_path / "bot.log"
    _write_lines(path, ["not a log line"])

    record = next(iter_app_log_records(path))
    assert record["ts"] is None
    assert record["level"] is None
    assert record["task"] is None
    assert record["message"] == "not a log line"
    assert record["raw"] == "not a log line"
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_date_filter_keeps_undated_lines():
    tmp_path = make_tmp_dir("app_log_reader")
    path = tmp_path / "bot.log"
    _write_lines(
        path,
        [
            "2026-04-16 09:30:45,000 UTC INFO     main                  inside window",
            "2026-04-14 09:30:45,000 UTC INFO     main                  before window",
            "malformed line without timestamp",
        ],
    )

    records = list(
        iter_app_log_records(
            path,
            since=datetime(2026, 4, 15, tzinfo=timezone.utc),
            until=datetime(2026, 4, 16, 23, 59, 59, tzinfo=timezone.utc),
        )
    )

    assert [record["message"] for record in records] == ["inside window", "malformed line without timestamp"]
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_level_filter_is_case_insensitive():
    tmp_path = make_tmp_dir("app_log_reader")
    path = tmp_path / "bot.log"
    _write_lines(
        path,
        [
            "2026-04-16 09:30:45,000 UTC INFO     main                  keep me",
            "2026-04-16 09:31:45,000 UTC ERROR    main                  skip me",
            "malformed line without level",
        ],
    )

    records = list(iter_app_log_records(path, levels={"info"}))
    assert [record["message"] for record in records] == ["keep me", "malformed line without level"]
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_directory_reads_archives_oldest_to_newest_then_active():
    tmp_path = make_tmp_dir("app_log_reader")
    log_dir = tmp_path / "app"
    _write_lines(log_dir / "bot.log.2026-04-14", ["2026-04-14 09:00:00,000 UTC INFO     main                  oldest"])
    _write_lines(log_dir / "bot.log.2026-04-15", ["2026-04-15 09:00:00,000 UTC INFO     main                  newer"])
    _write_lines(log_dir / "bot.log", ["2026-04-16 09:00:00,000 UTC INFO     main                  active"])

    records = list(iter_app_log_records(log_dir))
    assert [record["message"] for record in records] == ["oldest", "newer", "active"]
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_glob_pattern_reads_matching_files():
    tmp_path = make_tmp_dir("app_log_reader")
    log_dir = tmp_path / "app"
    _write_lines(log_dir / "bot.log.2026-04-15", ["2026-04-15 09:00:00,000 UTC INFO     main                  archive"])
    _write_lines(log_dir / "bot.log", ["2026-04-16 09:00:00,000 UTC INFO     main                  active"])

    records = list(iter_app_log_records(log_dir / "bot.log*"))
    assert [record["message"] for record in records] == ["archive", "active"]
    shutil.rmtree(tmp_path, ignore_errors=True)
