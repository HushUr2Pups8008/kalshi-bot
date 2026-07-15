from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from config import BotConfig
from scripts import botcheck
from trading.capital_guard_shadow import CapitalGuardShadowStore


def test_capital_guard_shadow_flags_default_off_and_parse_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE", raising=False)
    monkeypatch.delenv(
        "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION",
        raising=False,
    )

    disabled = BotConfig()

    assert disabled.enable_capital_guard_shadow_capture is False
    assert disabled.enable_capital_guard_shadow_settlement_collection is False

    monkeypatch.setenv("ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE", "true")
    monkeypatch.setenv(
        "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION",
        "true",
    )

    enabled = BotConfig()

    assert enabled.enable_capital_guard_shadow_capture is True
    assert enabled.enable_capital_guard_shadow_settlement_collection is True


def test_capital_guard_shadow_status_disabled_performs_zero_sqlite_io(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE", raising=False)
    monkeypatch.delenv(
        "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION",
        raising=False,
    )
    db_path = tmp_path / "data" / "capital_guard_shadow.db"

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        pytest.fail(f"disabled capital guard status opened SQLite: {args!r} {kwargs!r}")

    monkeypatch.setattr(botcheck.sqlite3, "connect", unexpected_connect)

    botcheck.print_capital_guard_shadow_status(tmp_path)

    assert capsys.readouterr().out.strip() == (
        "capital_guard_shadow: capture=off (default) collection=off (default)"
    )
    assert not db_path.exists()


def test_capital_guard_shadow_status_collector_only_missing_does_not_create_db(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE", "false")
    monkeypatch.setenv(
        "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION",
        "true",
    )
    db_path = tmp_path / "data" / "capital_guard_shadow.db"

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        pytest.fail(f"missing capital guard DB was opened: {args!r} {kwargs!r}")

    monkeypatch.setattr(botcheck.sqlite3, "connect", unexpected_connect)

    botcheck.print_capital_guard_shadow_status(tmp_path)

    assert capsys.readouterr().out.strip() == (
        "capital_guard_shadow: capture=off (process-env) "
        "collection=on (process-env) db=missing"
    )
    assert not db_path.exists()


def test_capital_guard_shadow_status_reads_existing_db_read_only_and_bounded(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE", "true")
    monkeypatch.setenv(
        "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION",
        "false",
    )
    db_path = tmp_path / "data" / "capital_guard_shadow.db"
    CapitalGuardShadowStore(db_path).initialize(
        applied_at=datetime(2026, 7, 15, tzinfo=timezone.utc)
    )

    real_connect = sqlite3.connect
    connect_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    statements: list[str] = []

    def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connect_calls.append((args, kwargs))
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(botcheck.sqlite3, "connect", traced_connect)

    botcheck.print_capital_guard_shadow_status(tmp_path)

    assert connect_calls == [
        ((f"{db_path.resolve().as_uri()}?mode=ro",), {"uri": True})
    ]
    sql = "\n".join(statements)
    assert "PRAGMA integrity_check" in sql
    assert "sqlite_schema" in sql
    count_queries = [
        statement
        for statement in statements
        if "COUNT(*)" in statement and "sqlite_schema" not in statement
    ]
    assert len(count_queries) == len(botcheck.CAPITAL_GUARD_SHADOW_TABLES)
    assert all("LIMIT" in statement for statement in count_queries)
    assert capsys.readouterr().out.strip() == (
        "capital_guard_shadow: capture=on (process-env) "
        "collection=off (process-env) integrity=ok tables=8/8 schema=ok "
        "missing=none unexpected=none "
        "rows=meta:1,attempts:0,candidates:0,conflicts:0,observations:0,links:0,"
        "settlements:0,evaluations:0"
    )


def test_capital_guard_shadow_status_reports_exact_schema_drift(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE", "true")
    monkeypatch.setenv(
        "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION",
        "false",
    )
    db_path = tmp_path / "data" / "capital_guard_shadow.db"
    CapitalGuardShadowStore(db_path).initialize(
        applied_at=datetime(2026, 7, 15, tzinfo=timezone.utc)
    )
    with sqlite3.connect(db_path) as conn:
        trigger_name = conn.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'trigger' AND name GLOB 'immutable_*' LIMIT 1"
        ).fetchone()[0]
        conn.execute(f'DROP TRIGGER "{trigger_name}"')
        conn.execute(
            "CREATE VIEW capital_guard_shadow_rogue_view AS SELECT 1 AS value"
        )

    botcheck.print_capital_guard_shadow_status(tmp_path)

    out = capsys.readouterr().out.strip()
    assert "integrity=ok tables=8/8 schema=mismatch" in out


def test_capital_guard_shadow_status_running_env_is_authoritative(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE", "true")
    monkeypatch.setenv(
        "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION",
        "true",
    )
    proc = botcheck.ProcessInfo(
        pid=2468,
        ppid=1,
        cpu="0.0",
        mem="0.1",
        etimes=3600,
        command="/python main.py",
    )
    monkeypatch.setattr(
        botcheck,
        "run_command",
        lambda args: (
            "/python main.py ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE=false "
            "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION=false"
        ),
    )

    botcheck.print_capital_guard_shadow_status(tmp_path, current_proc=proc)

    assert capsys.readouterr().out.strip() == (
        "capital_guard_shadow: capture=off (runtime-process-env) "
        "collection=off (runtime-process-env)"
    )
