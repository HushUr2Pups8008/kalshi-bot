import gzip
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from utils.logger import TradeLogStore
from utils import trade_log_reader
from utils.trade_log_reader import iter_trade_records


def _write_jsonl(path: Path, records: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for record in records:
        if isinstance(record, str):
            lines.append(record)
        else:
            lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _gzip_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _tmp_root() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_trade_log_store" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_trade_log_store_rotates_across_day_boundary():
    root = _tmp_root()
    try:
        store = TradeLogStore(
            root=root,
            live_path=root / "live" / "trades.jsonl",
            legacy_path=root / "trades.jsonl",
        )

        day_one = {"type": "SIGNAL", "ts": "2026-04-15T23:59:00+00:00", "ticker": "KX1"}
        day_two = {"type": "SIGNAL", "ts": "2026-04-16T00:01:00+00:00", "ticker": "KX2"}

        store.append(day_one)
        store.append(day_two)

        archive_path = root / "archive" / "2026" / "04" / "2026-04-15.jsonl"
        live_path = root / "live" / "trades.jsonl"

        assert archive_path.exists()
        assert live_path.exists()
        assert _read_jsonl(archive_path) == [day_one]
        assert _read_jsonl(live_path) == [day_two]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_trade_log_store_merges_into_existing_archive_file():
    root = _tmp_root()
    try:
        archive_path = root / "archive" / "2026" / "04" / "2026-04-15.jsonl"
        _write_jsonl(
            archive_path,
            [
                {"type": "SIGNAL", "ts": "2026-04-15T08:00:00+00:00", "ticker": "ARCHIVED"},
            ],
        )
        store = TradeLogStore(
            root=root,
            live_path=root / "live" / "trades.jsonl",
            legacy_path=root / "trades.jsonl",
        )

        store.append({"type": "SIGNAL", "ts": "2026-04-15T23:59:00+00:00", "ticker": "LIVE_OLD"})
        store.append({"type": "SIGNAL", "ts": "2026-04-16T00:01:00+00:00", "ticker": "LIVE_NEW"})

        assert [row["ticker"] for row in _read_jsonl(archive_path)] == ["ARCHIVED", "LIVE_OLD"]
        assert [row["ticker"] for row in _read_jsonl(root / "live" / "trades.jsonl")] == ["LIVE_NEW"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_trade_log_store_routes_older_out_of_order_record_to_archive():
    root = _tmp_root()
    try:
        store = TradeLogStore(
            root=root,
            live_path=root / "live" / "trades.jsonl",
            legacy_path=root / "trades.jsonl",
        )

        store.append({"type": "SIGNAL", "ts": "2026-04-16T10:00:00+00:00", "ticker": "LIVE"})
        store.append({"type": "SIGNAL", "ts": "2026-04-15T23:00:00+00:00", "ticker": "LATE_OLD"})

        archive_path = root / "archive" / "2026" / "04" / "2026-04-15.jsonl"
        assert [row["ticker"] for row in _read_jsonl(root / "live" / "trades.jsonl")] == ["LIVE"]
        assert [row["ticker"] for row in _read_jsonl(archive_path)] == ["LATE_OLD"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_iter_trade_records_reads_live_archive_and_filters_by_date():
    root = _tmp_root()
    try:
        _write_jsonl(
            root / "live" / "trades.jsonl",
            [
                {"type": "SIGNAL", "ts": "2026-04-16T12:00:00+00:00", "ticker": "LIVE"},
            ],
        )
        _write_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-15.jsonl",
            [
                {"type": "PAPER_TRADE", "ts": "2026-04-15T10:00:00+00:00", "ticker": "ARCHIVE"},
            ],
        )
        _gzip_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-14.jsonl.gz",
            [
                {"type": "SIGNAL", "ts": "2026-04-14T09:00:00+00:00", "ticker": "GZ"},
            ],
        )

        since = datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc)
        until = datetime(2026, 4, 16, 23, 59, tzinfo=timezone.utc)
        rows = list(iter_trade_records(root, since=since, until=until))

        assert [row["ticker"] for row in rows] == ["ARCHIVE", "LIVE"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_iter_trade_records_reads_directory_in_chronological_order():
    root = _tmp_root()
    try:
        _gzip_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-14.jsonl.gz",
            [
                {"type": "SIGNAL", "ts": "2026-04-14T09:00:00+00:00", "ticker": "GZ"},
            ],
        )
        _write_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-15.jsonl",
            [
                {"type": "SIGNAL", "ts": "2026-04-15T09:00:00+00:00", "ticker": "ARCHIVE"},
            ],
        )
        _write_jsonl(
            root / "live" / "trades.jsonl",
            [
                {"type": "SIGNAL", "ts": "2026-04-16T09:00:00+00:00", "ticker": "LIVE"},
            ],
        )

        rows = list(iter_trade_records(root))

        assert [row["ticker"] for row in rows] == ["GZ", "ARCHIVE", "LIVE"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_iter_trade_records_skips_legacy_overlap_when_live_exists():
    root = _tmp_root()
    try:
        _write_jsonl(
            root / "trades.jsonl",
            [
                {"type": "SIGNAL", "ts": "2026-04-15T09:00:00+00:00", "ticker": "LEGACY_OLD"},
                {"type": "SIGNAL", "ts": "2026-04-16T09:00:00+00:00", "ticker": "LEGACY_OVERLAP"},
            ],
        )
        _write_jsonl(
            root / "live" / "trades.jsonl",
            [
                {"type": "SIGNAL", "ts": "2026-04-16T09:00:00+00:00", "ticker": "LIVE"},
            ],
        )

        rows = list(iter_trade_records(root))

        assert [row["ticker"] for row in rows] == ["LEGACY_OLD", "LIVE"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_iter_trade_records_handles_legacy_and_live_without_path_equality():
    root = _tmp_root()
    try:
        _write_jsonl(
            root / "trades.jsonl",
            [
                {"type": "SIGNAL", "ts": "2026-04-15T23:59:00+00:00", "ticker": "LEGACY_KEEP"},
                {"type": "SIGNAL", "ts": "2026-04-16T00:05:00+00:00", "ticker": "LEGACY_SKIP"},
            ],
        )
        _write_jsonl(
            root / "live" / "trades.jsonl",
            [
                {"type": "SIGNAL", "ts": "2026-04-16T00:05:00+00:00", "ticker": "LIVE"},
            ],
        )

        rows = list(iter_trade_records(root))

        assert [row["ticker"] for row in rows] == ["LEGACY_KEEP", "LIVE"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_iter_trade_records_skips_malformed_lines_and_filters_event_type():
    root = _tmp_root()
    try:
        _write_jsonl(
            root / "trades.jsonl",
            [
                {"type": "SIGNAL", "ts": "2026-04-16T12:00:00+00:00", "ticker": "KEEP"},
                '{"type":"SIGNAL"',
                ["not", "a", "dict"],
                {"type": "SKIPPED", "ts": "2026-04-16T12:01:00+00:00", "ticker": "DROP"},
                {"type": "SIGNAL", "ticker": "NO_TS"},
            ],
        )

        rows = list(iter_trade_records(root, event_types={"SIGNAL"}))

        assert [row.get("ticker") for row in rows] == ["KEEP", "NO_TS"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only copy+truncate fallback")
def test_trade_log_store_permission_error_fallback_preserves_records(monkeypatch):
    root = _tmp_root()
    try:
        store = TradeLogStore(
            root=root,
            live_path=root / "live" / "trades.jsonl",
            legacy_path=root / "trades.jsonl",
        )
        store.append({"type": "SIGNAL", "ts": "2026-04-15T23:59:00+00:00", "ticker": "DAY1"})

        def _raise_permission_error(src: Path, dst: Path) -> None:
            raise PermissionError("simulated windows replace failure")

        monkeypatch.setattr("utils.logger.os.replace", _raise_permission_error)
        store.append({"type": "SIGNAL", "ts": "2026-04-16T00:01:00+00:00", "ticker": "DAY2"})

        archive_path = root / "archive" / "2026" / "04" / "2026-04-15.jsonl"
        assert [row["ticker"] for row in _read_jsonl(archive_path)] == ["DAY1"]
        assert [row["ticker"] for row in _read_jsonl(root / "live" / "trades.jsonl")] == ["DAY2"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS/Linux: PermissionError must re-raise")
def test_trade_log_store_permission_error_raises_on_non_windows(monkeypatch):
    """MAC-LOG-001: on non-Windows, PermissionError during rotation must propagate.

    The Windows copy+truncate fallback masks real permission problems on macOS.
    On macOS/Linux the exception must re-raise so the caller is aware of the failure.
    """
    root = _tmp_root()
    try:
        store = TradeLogStore(
            root=root,
            live_path=root / "live" / "trades.jsonl",
            legacy_path=root / "trades.jsonl",
        )
        store.append({"type": "SIGNAL", "ts": "2026-04-15T23:59:00+00:00", "ticker": "DAY1"})

        def _raise_permission_error(src: Path, dst: Path) -> None:
            raise PermissionError("simulated permission denied")

        monkeypatch.setattr("utils.logger.os.replace", _raise_permission_error)

        with pytest.raises(PermissionError, match="simulated permission denied"):
            store.append({"type": "SIGNAL", "ts": "2026-04-16T00:01:00+00:00", "ticker": "DAY2"})
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_iter_trade_records_skips_archive_partitions_outside_requested_window(monkeypatch):
    root = _tmp_root()
    try:
        _write_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-14.jsonl",
            [{"type": "SIGNAL", "ts": "2026-04-14T09:00:00+00:00", "ticker": "DAY14"}],
        )
        _write_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-15.jsonl",
            [{"type": "SIGNAL", "ts": "2026-04-15T09:00:00+00:00", "ticker": "DAY15"}],
        )
        _write_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-16.jsonl",
            [{"type": "SIGNAL", "ts": "2026-04-16T09:00:00+00:00", "ticker": "DAY16"}],
        )
        _write_jsonl(
            root / "live" / "trades.jsonl",
            [{"type": "SIGNAL", "ts": "2026-04-17T09:00:00+00:00", "ticker": "LIVE"}],
        )

        opened_files: list[str] = []
        original_iter_records = trade_log_reader._iter_records_from_file

        def _record_open(path: Path, stats=None):
            opened_files.append(path.name)
            yield from original_iter_records(path, stats=stats)

        monkeypatch.setattr("utils.trade_log_reader._iter_records_from_file", _record_open)

        since = datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc)
        until = datetime(2026, 4, 15, 23, 59, 59, tzinfo=timezone.utc)
        rows = list(iter_trade_records(root, since=since, until=until))

        assert [row["ticker"] for row in rows] == ["DAY15"]
        assert "2026-04-14.jsonl" not in opened_files
        assert "2026-04-16.jsonl" not in opened_files
        assert "2026-04-15.jsonl" in opened_files
    finally:
        shutil.rmtree(root, ignore_errors=True)
