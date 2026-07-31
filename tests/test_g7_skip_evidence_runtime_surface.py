from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from config import BotConfig
from main import _build_g7_skip_evidence_capture_sink
from scripts import botcheck
import tasks.g7_skip_evidence_capture as capture_module
import trading.g7_skip_evidence as evidence_module


def test_g7_skip_evidence_capture_flag_defaults_off_and_parses_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_G7_SKIP_EVIDENCE_CAPTURE", raising=False)

    assert BotConfig().enable_g7_skip_evidence_capture is False

    monkeypatch.setenv("ENABLE_G7_SKIP_EVIDENCE_CAPTURE", "true")

    assert BotConfig().enable_g7_skip_evidence_capture is True


def test_disabled_g7_skip_evidence_builder_performs_zero_store_or_file_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "missing" / "g7_skip_evidence.db"

    def unexpected_store(*args: object, **kwargs: object) -> None:
        pytest.fail(f"disabled G7 evidence capture constructed store: {args!r} {kwargs!r}")

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        pytest.fail(f"disabled G7 evidence capture opened SQLite: {args!r} {kwargs!r}")

    monkeypatch.setattr(evidence_module, "G7SkipEvidenceStore", unexpected_store)
    monkeypatch.setattr(sqlite3, "connect", unexpected_connect)

    assert _build_g7_skip_evidence_capture_sink(False, db_path=db_path) is None
    assert not db_path.parent.exists()


def test_enabled_g7_skip_evidence_builder_initializes_isolated_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    class SpyStore:
        def __init__(self, db_path: Path) -> None:
            events.append(("store", db_path))

        def initialize(self, *, applied_at: datetime) -> None:
            events.append(("initialize", applied_at))

    class SpySink:
        def __init__(self, store: SpyStore) -> None:
            events.append(("sink", store))

    monkeypatch.setattr(evidence_module, "G7SkipEvidenceStore", SpyStore)
    monkeypatch.setattr(capture_module, "G7SkipEvidenceCaptureSink", SpySink)
    db_path = tmp_path / "isolated" / "g7_skip_evidence.db"

    sink = _build_g7_skip_evidence_capture_sink(True, db_path=db_path)

    assert isinstance(sink, SpySink)
    assert [event for event, _value in events] == ["store", "initialize", "sink"]
    assert events[0][1] == db_path
    assert isinstance(events[1][1], datetime)


def test_g7_skip_evidence_status_disabled_performs_zero_sqlite_io(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_G7_SKIP_EVIDENCE_CAPTURE", raising=False)
    db_path = tmp_path / "data" / "g7_skip_evidence.db"

    def unexpected_open_read_only(*args: object, **kwargs: object) -> None:
        pytest.fail(f"disabled G7 evidence status opened SQLite: {args!r} {kwargs!r}")

    monkeypatch.setattr(evidence_module, "_open_read_only", unexpected_open_read_only)

    botcheck.print_g7_skip_evidence_status(tmp_path)

    assert capsys.readouterr().out.strip() == "g7_skip_evidence: off (default)"
    assert not db_path.exists()


def test_g7_skip_evidence_status_enabled_missing_does_not_create_store(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_G7_SKIP_EVIDENCE_CAPTURE", "true")
    db_path = tmp_path / "data" / "g7_skip_evidence.db"

    def unexpected_open_read_only(*args: object, **kwargs: object) -> None:
        pytest.fail(f"missing G7 evidence status opened SQLite: {args!r} {kwargs!r}")

    monkeypatch.setattr(evidence_module, "_open_read_only", unexpected_open_read_only)

    botcheck.print_g7_skip_evidence_status(tmp_path)

    assert capsys.readouterr().out.strip() == (
        "g7_skip_evidence: on (process-env) db=missing"
    )
    assert not db_path.exists()


def test_g7_skip_evidence_status_reads_existing_store_without_write_path(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_G7_SKIP_EVIDENCE_CAPTURE", "true")
    db_path = tmp_path / "data" / "g7_skip_evidence.db"
    store = evidence_module.G7SkipEvidenceStore(db_path)
    store.initialize()

    def unexpected_open_writable(*args: object, **kwargs: object) -> None:
        pytest.fail(f"status used SQLite write path: {args!r} {kwargs!r}")

    monkeypatch.setattr(evidence_module, "_open_writable", unexpected_open_writable)

    botcheck.print_g7_skip_evidence_status(tmp_path)

    assert capsys.readouterr().out.strip() == (
        "g7_skip_evidence: on (process-env) integrity=ok schema=ok rows=0 "
        "statuses=none last_capture=n/a diagnostic_only=true"
    )
