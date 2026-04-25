"""Tests for governance.audit.AuditLogger.

The audit logger is append-only. Daily rotation matches the bot.log
pattern: `decisions.jsonl.YYYY-MM-DD` for archives, gzip after 7 days.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from governance.audit import AuditLogger


def _record(event_type: str = "GOVERNANCE_TEST") -> dict:
    return {
        "type": event_type,
        "ts": "2026-05-02T14:30:00+00:00",
        "payload": {"x": 1},
    }


class TestAuditLoggerAppend:
    def test_first_write_creates_file(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl")
        logger.append(_record())
        target = log_dir / "decisions.jsonl"
        assert target.exists()
        line = target.read_text(encoding="utf-8").strip()
        assert json.loads(line)["type"] == "GOVERNANCE_TEST"

    def test_multiple_appends_one_line_each(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl")
        logger.append(_record("FIRST"))
        logger.append(_record("SECOND"))
        logger.append(_record("THIRD"))
        target = log_dir / "decisions.jsonl"
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["type"] == "FIRST"
        assert json.loads(lines[2])["type"] == "THIRD"

    def test_log_dir_auto_created(self, tmp_path: Path):
        log_dir = tmp_path / "deeply" / "nested" / "governance"
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl")
        logger.append(_record())
        assert log_dir.exists()


class TestAuditLoggerRotation:
    def test_rotates_at_utc_midnight_change(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        # Inject a "now" function so we can simulate clock advancing.
        clock = datetime(2026, 5, 2, 23, 59, 0, tzinfo=timezone.utc)

        def fake_now():
            return clock

        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl", now=fake_now)
        logger.append(_record("BEFORE_MIDNIGHT"))

        # Advance past midnight UTC
        clock = datetime(2026, 5, 3, 0, 1, 0, tzinfo=timezone.utc)
        logger.append(_record("AFTER_MIDNIGHT"))

        archive = log_dir / "decisions.jsonl.2026-05-02"
        current = log_dir / "decisions.jsonl"
        assert archive.exists()
        assert current.exists()
        # Archive contains the BEFORE record
        before_line = archive.read_text(encoding="utf-8").strip()
        assert json.loads(before_line)["type"] == "BEFORE_MIDNIGHT"
        # Current contains only the AFTER record
        after_line = current.read_text(encoding="utf-8").strip()
        assert json.loads(after_line)["type"] == "AFTER_MIDNIGHT"

    def test_no_rotation_when_same_day(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        clock = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl", now=lambda: clock)
        logger.append(_record("FIRST"))
        clock = datetime(2026, 5, 2, 23, 59, 0, tzinfo=timezone.utc)
        logger.append(_record("SECOND"))
        target = log_dir / "decisions.jsonl"
        assert target.exists()
        archive = log_dir / "decisions.jsonl.2026-05-02"
        assert not archive.exists()
        assert len(target.read_text(encoding="utf-8").splitlines()) == 2


class TestAuditLoggerErrorHandling:
    def test_non_serializable_record_raises_typeerror(self, tmp_path: Path):
        """Caller is responsible for pre-serializing values (datetimes, etc).
        If a non-JSON-serializable value slips through, raise TypeError
        loudly rather than silently writing a corrupt line.
        """
        log_dir = tmp_path / "governance"
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl")
        bad = {"type": "T", "ts": datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc)}
        with pytest.raises(TypeError):
            logger.append(bad)
        # The current file may exist but should not contain a corrupt line.
        target = log_dir / "decisions.jsonl"
        if target.exists():
            text = target.read_text(encoding="utf-8")
            for line in text.splitlines():
                json.loads(line)  # would raise if any line is corrupt


class TestAuditLoggerCompression:
    def test_archive_older_than_7d_gzipped(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        log_dir.mkdir(parents=True)
        # Pre-populate an old archive that should be compressed
        old_archive = log_dir / "decisions.jsonl.2026-04-01"
        old_archive.write_text("{}\n", encoding="utf-8")

        clock = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)
        logger = AuditLogger(
            log_dir=log_dir,
            basename="decisions.jsonl",
            now=lambda: clock,
            compress_after_days=7,
        )
        logger.compress_old_archives()

        compressed = log_dir / "decisions.jsonl.2026-04-01.gz"
        assert compressed.exists()
        assert not old_archive.exists()
        with gzip.open(compressed, "rt") as f:
            assert f.read() == "{}\n"
