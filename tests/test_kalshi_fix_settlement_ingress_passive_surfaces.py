"""Passive-surface contracts for the default-off FIX settlement ingress."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
import subprocess
import sys
from types import ModuleType
from pathlib import Path

import pytest

from config import BotConfig
from scripts import botcheck
from tasks.kalshi_fix_settlement_ingress import capture_verified_ums
from trading.kalshi_fix_settlement_ingress import (
    KalshiFixSettlementIngressSnapshot,
    KalshiFixSettlementIngressStore,
    SessionProvenance,
    VerifiedKalshiFixUMSEnvelope,
)


_INVARIANT = {
    "transport_authentication": "upstream_attested_not_proven_by_ledger",
    "pagination_coverage": "unknown",
    "canonical_settlement_binding": "absent",
    "fee_net_pnl": "unscorable",
    "paper_trader_updated": False,
    "orders_changed": False,
    "promotion_eligible": False,
}


def _typed_envelope() -> VerifiedKalshiFixUMSEnvelope:
    account_party_id = "account-123"
    return VerifiedKalshiFixUMSEnvelope(
        raw_fix=b"8=FIXT.1.1\x0135=UMS\x0120105=settle-123\x01",
        message={
            "35": "UMS",
            "20105": "settle-123",
            "55": "HIGHNY-23DEC31",
            "715": "20231231",
            "20107": "yes",
            "730": "100.00",
            "20108": [
                {
                    "20109": account_party_id,
                    "20110": "24",
                    "704": "100",
                    "705": "0",
                    "136": [
                        {
                            "137": "0.006",
                            "138": "USD",
                            "139": "4",
                            "891": "0",
                        }
                    ],
                }
            ],
        },
        provenance=SessionProvenance(
            verifier_name="kalshi-fix-session-verifier",
            verifier_version="v1",
            session_fingerprint="session-fingerprint-sha256:example",
            account_party_id_sha256=sha256(account_party_id.encode("utf-8")).hexdigest(),
            received_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        ),
    )


def test_fix_settlement_ingress_flag_defaults_off_and_parses_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_KALSHI_FIX_SETTLEMENT_INGRESS", raising=False)

    assert BotConfig().enable_kalshi_fix_settlement_ingress is False

    monkeypatch.setenv("ENABLE_KALSHI_FIX_SETTLEMENT_INGRESS", "true")

    assert BotConfig().enable_kalshi_fix_settlement_ingress is True


def test_botcheck_fix_settlement_ingress_disabled_does_not_touch_sqlite(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_KALSHI_FIX_SETTLEMENT_INGRESS", raising=False)
    db_path = tmp_path / "data" / "kalshi_fix_settlement_ingress.db"

    botcheck.print_kalshi_fix_settlement_ingress_status(tmp_path)

    output = capsys.readouterr().out.strip()
    assert output.startswith(
        "kalshi_fix_settlement_ingress: configured-off (default) "
        "status=disabled_non_authoritative "
    )
    for key, value in _INVARIANT.items():
        assert f"{key}={str(value).lower()}" in output
    assert "raw_capture=absent" in output
    assert "session_provenance=absent" in output
    assert not db_path.exists()


def test_botcheck_fix_settlement_ingress_enabled_missing_does_not_import_store(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_KALSHI_FIX_SETTLEMENT_INGRESS", "true")
    db_path = tmp_path / "data" / "kalshi_fix_settlement_ingress.db"

    botcheck.print_kalshi_fix_settlement_ingress_status(tmp_path)

    output = capsys.readouterr().out.strip()
    assert output.startswith(
        "kalshi_fix_settlement_ingress: configured-on (process-env) "
        "status=absent_non_authoritative "
    )
    assert "db=missing" in output
    assert not db_path.exists()


def test_botcheck_fix_settlement_ingress_prefers_runtime_process_configuration(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_KALSHI_FIX_SETTLEMENT_INGRESS", "false")
    monkeypatch.setattr(
        botcheck,
        "_running_process_env_value",
        lambda _proc, _flag: ("true", True),
    )

    botcheck.print_kalshi_fix_settlement_ingress_status(
        tmp_path,
        current_proc=object(),  # type: ignore[arg-type]
    )

    assert capsys.readouterr().out.strip().startswith(
        "kalshi_fix_settlement_ingress: configured-on (runtime-process-env) "
        "status=absent_non_authoritative "
    )


def test_botcheck_fix_settlement_ingress_uses_read_only_snapshot(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_KALSHI_FIX_SETTLEMENT_INGRESS", "true")
    db_path = tmp_path / "data" / "kalshi_fix_settlement_ingress.db"
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    calls: list[Path] = []

    def fake_reader(path: Path) -> KalshiFixSettlementIngressSnapshot:
        calls.append(path)
        return KalshiFixSettlementIngressSnapshot(
            status="captured_non_authoritative",
            exists=True,
            schema_valid=True,
            integrity_check="ok",
            report_count=1,
            wire_observation_count=1,
            conflict_count=0,
            quarantine_count=0,
            latest_received_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
            raw_capture="present",
            session_provenance="present",
            **_INVARIANT,
        )

    fake_module = ModuleType("trading.kalshi_fix_settlement_ingress")
    fake_module.read_kalshi_fix_settlement_ingress_snapshot = fake_reader
    monkeypatch.setitem(sys.modules, fake_module.__name__, fake_module)

    botcheck.print_kalshi_fix_settlement_ingress_status(tmp_path)

    output = capsys.readouterr().out.strip()
    assert calls == [db_path]
    assert "status=captured_non_authoritative" in output
    assert "raw_capture=present" in output
    assert "session_provenance=present" in output


def test_inspector_missing_database_is_read_only_and_non_authoritative(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "inspect_kalshi_fix_settlement_ingress.py"
    )
    db_path = tmp_path / "missing" / "kalshi_fix_settlement_ingress.db"

    completed = subprocess.run(
        [
            sys.executable,
            "-E",
            str(script),
            "--db-path",
            str(db_path),
            "--status",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "absent_non_authoritative"
    assert payload["raw_capture"] == "absent"
    assert payload["session_provenance"] == "absent"
    assert {key: payload[key] for key in _INVARIANT} == _INVARIANT
    assert not db_path.exists()


def test_inspector_schema_verification_fails_closed_when_database_is_missing(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "inspect_kalshi_fix_settlement_ingress.py"
    )
    db_path = tmp_path / "missing" / "kalshi_fix_settlement_ingress.db"

    completed = subprocess.run(
        [
            sys.executable,
            "-E",
            str(script),
            "--db-path",
            str(db_path),
            "--verify-schema",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["status"] == "absent_non_authoritative"
    assert not db_path.exists()


def test_inspector_schema_verification_passes_for_existing_valid_empty_store(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "inspect_kalshi_fix_settlement_ingress.py"
    )
    db_path = tmp_path / "state" / "kalshi_fix_settlement_ingress.db"
    store = KalshiFixSettlementIngressStore(db_path)
    assert store.initialize() is True

    completed = subprocess.run(
        [
            sys.executable,
            "-E",
            str(script),
            "--db-path",
            str(db_path),
            "--verify-schema",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "absent_non_authoritative"
    assert payload["raw_capture"] == "absent"
    assert payload["session_provenance"] == "absent"


def test_inspector_populated_ledger_serializes_latest_received_at(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "inspect_kalshi_fix_settlement_ingress.py"
    )
    db_path = tmp_path / "state" / "kalshi_fix_settlement_ingress.db"
    store = KalshiFixSettlementIngressStore(db_path)
    assert store.initialize() is True
    assert store.append_verified_envelope(_typed_envelope()).status == "accepted"

    for command in ("--status", "--verify-schema"):
        completed = subprocess.run(
            [sys.executable, "-E", str(script), "--db-path", str(db_path), command],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert completed.returncode == 0
        payload = json.loads(completed.stdout)
        assert payload["status"] == "captured_non_authoritative"
        assert payload["latest_received_at"] == "2026-07-31T12:00:00+00:00"
        assert {key: payload[key] for key in _INVARIANT} == _INVARIANT


def test_inspector_help_runs_directly_outside_repository_root(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "inspect_kalshi_fix_settlement_ingress.py"
    )

    completed = subprocess.run(
        [sys.executable, "-E", str(script), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--db-path" in completed.stdout
    assert "--status" in completed.stdout
    assert "--verify-schema" in completed.stdout


def test_capture_verified_ums_rejects_file_shaped_inputs_before_ledger_call(
    tmp_path: Path,
) -> None:
    ledger = KalshiFixSettlementIngressStore(tmp_path / "ingress.db")
    assert ledger.initialize() is True

    for invalid_source in (
        {"raw": "fix"},
        "raw-fix",
        b"raw-fix",
        tmp_path / "wire.fix",
        BytesIO(b"raw-fix"),
        object(),
    ):
        with pytest.raises(TypeError):
            capture_verified_ums(
                invalid_source,  # type: ignore[arg-type]
                ledger,
                datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
            )

    assert ledger.snapshot().report_count == 0


def test_capture_verified_ums_requires_typed_envelope_return(
    tmp_path: Path,
) -> None:
    ledger = KalshiFixSettlementIngressStore(tmp_path / "ingress.db")
    assert ledger.initialize() is True

    class BadSource:
        def receive_verified_ums(self) -> object:
            return {"not": "typed"}

    with pytest.raises(TypeError, match="VerifiedKalshiFixUMSEnvelope"):
        capture_verified_ums(
            BadSource(),
            ledger,
            datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        )

    assert ledger.snapshot().report_count == 0


def test_capture_verified_ums_appends_only_a_typed_source_envelope(
    tmp_path: Path,
) -> None:
    ledger = KalshiFixSettlementIngressStore(tmp_path / "ingress.db")
    assert ledger.initialize() is True

    class TypedSource:
        def receive_verified_ums(self) -> VerifiedKalshiFixUMSEnvelope:
            return _typed_envelope()

    result = capture_verified_ums(
        TypedSource(),
        ledger,
        datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
    )

    assert result.status == "accepted"
    snapshot = ledger.snapshot()
    assert snapshot.status == "captured_non_authoritative"
    assert snapshot.raw_capture == "present"
    assert snapshot.session_provenance == "present"


def test_no_runtime_or_profit_consumers_reference_fix_settlement_ingress() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    main_text = (repo_root / "main.py").read_text(encoding="utf-8")
    paper_trader_text = (repo_root / "trading" / "paper_trader.py").read_text(
        encoding="utf-8"
    )
    task_text = (
        repo_root / "tasks" / "kalshi_fix_settlement_ingress.py"
    ).read_text(encoding="utf-8")

    assert "kalshi_fix_settlement_ingress" not in main_text
    assert "KalshiFixSettlementIngress" not in paper_trader_text
    assert "PaperTrader" not in task_text
    assert "settlement_economics" not in task_text
    assert "outbox" not in task_text
