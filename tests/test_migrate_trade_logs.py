import json
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.migrate_trade_logs import HOLDING_FILE_NAME, MANIFEST_NAME, MigrationError, migrate_legacy_trade_log


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_migrate_trade_logs"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _cleanup_tmp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_migrate_partitions_records_by_day():
    tmp = _make_tmp_dir()
    try:
        src = tmp / "trades.jsonl"
        dest = tmp / "trades"
        _write_lines(
            src,
            [
                json.dumps({"type": "SIGNAL", "ts": "2026-04-11T12:00:00+00:00", "ticker": "KX1"}) + "\n",
                json.dumps({"type": "SKIPPED", "ts": "2026-04-12T01:00:00+00:00", "ticker": "KX2"}) + "\n",
            ],
        )

        summary = migrate_legacy_trade_log(src, dest)

        assert summary["migrated_records_written"] == 2
        assert summary["per_day_counts"] == {"2026-04-11": 1, "2026-04-12": 1}
        assert _read_jsonl(dest / "archive" / "2026" / "04" / "2026-04-11.jsonl")[0]["ticker"] == "KX1"
        assert _read_jsonl(dest / "archive" / "2026" / "04" / "2026-04-12.jsonl")[0]["ticker"] == "KX2"
    finally:
        _cleanup_tmp_dir(tmp)


def test_migrate_sends_malformed_and_missing_ts_to_holding_file():
    tmp = _make_tmp_dir()
    try:
        src = tmp / "trades.jsonl"
        dest = tmp / "trades"
        _write_lines(
            src,
            [
                '{"type":"SIGNAL","ts":"2026-04-11T12:00:00+00:00"}\n',
                '{"type":"SIGNAL"\n',
                json.dumps({"type": "SKIPPED"}) + "\n",
            ],
        )

        summary = migrate_legacy_trade_log(src, dest)

        holding = dest / "legacy" / HOLDING_FILE_NAME
        assert summary["migrated_records_written"] == 1
        assert summary["undated_or_malformed_count"] == 2
        assert holding.exists()
        assert len(holding.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        _cleanup_tmp_dir(tmp)


def test_migrate_is_idempotent_with_overwrite_generated():
    tmp = _make_tmp_dir()
    try:
        src = tmp / "trades.jsonl"
        dest = tmp / "trades"
        _write_lines(
            src,
            [
                json.dumps({"type": "SIGNAL", "ts": "2026-04-11T12:00:00+00:00", "ticker": "KX1"}) + "\n",
            ],
        )

        first = migrate_legacy_trade_log(src, dest)
        with pytest.raises(MigrationError):
            migrate_legacy_trade_log(src, dest)
        second = migrate_legacy_trade_log(src, dest, overwrite_generated=True)

        partition = dest / "archive" / "2026" / "04" / "2026-04-11.jsonl"
        assert first["migrated_records_written"] == 1
        assert second["migrated_records_written"] == 1
        assert len(partition.read_text(encoding="utf-8").splitlines()) == 1
    finally:
        _cleanup_tmp_dir(tmp)


def test_migrate_dry_run_writes_nothing():
    tmp = _make_tmp_dir()
    try:
        src = tmp / "trades.jsonl"
        dest = tmp / "trades"
        _write_lines(
            src,
            [
                json.dumps({"type": "SIGNAL", "ts": "2026-04-11T12:00:00+00:00"}) + "\n",
            ],
        )

        summary = migrate_legacy_trade_log(src, dest, dry_run=True)

        assert summary["dry_run"] is True
        assert summary["migrated_records_written"] == 1
        assert not dest.exists()
    finally:
        _cleanup_tmp_dir(tmp)


def test_migrate_summary_reconciles_and_writes_manifest():
    tmp = _make_tmp_dir()
    try:
        src = tmp / "trades.jsonl"
        dest = tmp / "trades"
        _write_lines(
            src,
            [
                json.dumps({"type": "SIGNAL", "ts": "2026-04-11T12:00:00+00:00"}) + "\n",
                json.dumps({"type": "SKIPPED", "ts": "2026-04-11T13:00:00+00:00"}) + "\n",
                json.dumps({"type": "BAD"}) + "\n",
            ],
        )

        summary = migrate_legacy_trade_log(src, dest)
        manifest = json.loads((dest / "legacy" / MANIFEST_NAME).read_text(encoding="utf-8"))

        assert summary["reconciled"] is True
        assert summary["event_type_counts"] == {"SIGNAL": 1, "SKIPPED": 1}
        assert manifest["input_lines_read"] == 3
        assert manifest["migrated_records_written"] == 2
        assert manifest["undated_or_malformed_count"] == 1
    finally:
        _cleanup_tmp_dir(tmp)
