from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from trading.horizon_paper_study_manifest import (
    HORIZON_PAPER_STUDY_KIND,
    derive_horizon_paper_study_database_identity,
)


STUDY_ID = "pm-horizon-15-30-20260805"
_BOOTSTRAP_TABLE = "horizon_study_bootstrap"
_LEDGER_APPLICATION_ID = 0x48504C47
_STATE_APPLICATION_ID = 0x48505354


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _seal_manifest(payload: dict[str, object]) -> dict[str, object]:
    sealed = dict(payload)
    sealed.pop("manifest_sha256", None)
    sealed["manifest_sha256"] = hashlib.sha256(
        _canonical_json(sealed).encode("utf-8")
    ).hexdigest()
    return sealed


def _write_initialized_database(
    path: Path,
    *,
    role: str,
    payload: dict[str, object],
) -> None:
    application_id = {
        "ledger": _LEDGER_APPLICATION_ID,
        "state": _STATE_APPLICATION_ID,
    }[role]
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA application_id = {application_id}")
        conn.execute(
            f"""
            CREATE TABLE {_BOOTSTRAP_TABLE} (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                bootstrap_schema_version INTEGER NOT NULL,
                database_role TEXT NOT NULL,
                study_id TEXT NOT NULL,
                study_kind TEXT NOT NULL,
                database_identity TEXT NOT NULL,
                manifest_preimage_sha256 TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {_BOOTSTRAP_TABLE} (
                singleton,
                bootstrap_schema_version,
                database_role,
                study_id,
                study_kind,
                database_identity,
                manifest_preimage_sha256
            ) VALUES (1, 1, ?, ?, ?, ?, ?)
            """,
            (
                role,
                payload["study_id"],
                payload["study_kind"],
                payload["database_identity"],
                payload["manifest_sha256"],
            ),
        )


def _write_study_root(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    data_root = tmp_path / "data"
    study_root = data_root / "horizon_paper_studies" / STUDY_ID
    study_root.mkdir(parents=True)
    policy_path = study_root / "policy_snapshot.json"
    fee_path = study_root / "fee_schedule.json"
    policy_path.write_bytes(b'{"matcher":"pinned"}')
    fee_path.write_bytes(b'{"fee_schedule":"pinned"}')
    configuration = {
        "schema_version": 1,
        "study_id": STUDY_ID,
        "study_kind": HORIZON_PAPER_STUDY_KIND,
        "venue": "polymarket_us",
        "created_at_utc": "2026-08-05T00:00:00.000000+00:00",
        "ledger_path": "study_ledger.db",
        "state_db_path": "study_state.db",
        "starting_bankroll": "250.00",
        "horizon_lower_exclusive_days": 14.0,
        "horizon_upper_inclusive_days": 30.0,
        "policy_snapshot_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "fee_schedule_sha256": hashlib.sha256(fee_path.read_bytes()).hexdigest(),
        "paper_execution_mode": "isolated_paper_only",
        "live_order_forbidden": True,
        "profit_receipt_attested": False,
    }
    payload = _seal_manifest(
        {
            **configuration,
            "database_identity": derive_horizon_paper_study_database_identity(
                configuration
            ),
        }
    )
    _write_initialized_database(study_root / "study_ledger.db", role="ledger", payload=payload)
    _write_initialized_database(study_root / "study_state.db", role="state", payload=payload)
    manifest_path = study_root / "manifest.json"
    manifest_path.write_text(_canonical_json(payload), encoding="utf-8")
    manifest_path.chmod(0o600)
    return study_root, manifest_path, payload


def _ledger_module():
    return importlib.import_module("trading.horizon_paper_study_ledger")


def _seed_study_trade(
    ledger_path: Path,
    *,
    study_id: str = STUDY_ID,
    admission_id: str = "a" * 64,
    study_trade_id: str = "study-trade-1",
    enforce_unique_link: bool = True,
) -> None:
    unique_clause = ",\n                UNIQUE(study_id, admission_id)" if enforce_unique_link else ""
    with sqlite3.connect(ledger_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS study_trades (
                study_trade_id TEXT PRIMARY KEY,
                study_id TEXT NOT NULL,
                admission_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price_snapshot TEXT NOT NULL,
                size TEXT NOT NULL,
                executed_at_utc TEXT NOT NULL{unique_clause}
            )
            """
        )
        conn.execute(
            """
            INSERT INTO study_trades (
                study_trade_id,
                study_id,
                admission_id,
                market_id,
                side,
                entry_price_snapshot,
                size,
                executed_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                study_trade_id,
                study_id,
                admission_id,
                "market-123",
                "yes",
                "0.37",
                "5",
                "2026-08-05T01:05:00.000000+00:00",
            ),
        )


def _seed_ambiguous_study_trades(
    ledger_path: Path,
    seed: tuple[tuple[str, str, str], ...],
) -> None:
    with sqlite3.connect(ledger_path) as conn:
        conn.execute("DROP TABLE IF EXISTS study_trades")
        conn.execute(
            """
            CREATE TABLE study_trades (
                study_trade_id TEXT PRIMARY KEY,
                study_id TEXT NOT NULL,
                admission_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price_snapshot TEXT NOT NULL,
                size TEXT NOT NULL,
                executed_at_utc TEXT NOT NULL
            )
            """
        )
        for trade_id, study_id, admission_id in seed:
            conn.execute(
                """
                INSERT INTO study_trades (
                    study_trade_id,
                    study_id,
                    admission_id,
                    market_id,
                    side,
                    entry_price_snapshot,
                    size,
                    executed_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    study_id,
                    admission_id,
                    "market-123",
                    "yes",
                    "0.37",
                    "5",
                    "2026-08-05T01:05:00.000000+00:00",
                ),
            )


def test_ledger_module_exposes_isolated_surface(tmp_path: Path):
    _write_study_root(tmp_path)
    module = _ledger_module()
    assert hasattr(module, "HorizonStudyLedger")
    assert hasattr(module, "HorizonStudyLedgerError")
    assert hasattr(module, "ManifestStudyLedgerExecutionLookup")
    assert hasattr(module, "StudyLedgerExecutionLink")


def test_ledger_creates_only_study_local_tables_and_recovers_one_link_idempotently(
    tmp_path: Path,
):
    _study_root, manifest_path, _payload = _write_study_root(tmp_path)
    module = _ledger_module()
    with module.HorizonStudyLedger(manifest_path) as ledger:
        first = ledger.record_execution(
            study_id=STUDY_ID,
            admission_id="a" * 64,
            market_id="market-123",
            side="yes",
            entry_price_snapshot="0.37",
            size="5",
            executed_at_utc="2026-08-05T01:05:00.000000+00:00",
        )
        second = ledger.record_execution(
            study_id=STUDY_ID,
            admission_id="a" * 64,
            market_id="market-123",
            side="yes",
            entry_price_snapshot="0.37",
            size="5",
            executed_at_utc="2026-08-05T01:05:00.000000+00:00",
        )
        assert first.study_trade_id == second.study_trade_id
        with sqlite3.connect(ledger.ledger_db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            }
            assert "study_trades" in tables
            assert "paper_trades" not in tables
            count = conn.execute(
                "SELECT COUNT(*) FROM study_trades WHERE study_id = ? AND admission_id = ?",
                (STUDY_ID, "a" * 64),
            ).fetchone()[0]
        assert count == 1


def test_lookup_reads_only_the_manifest_derived_study_ledger_db(tmp_path: Path, monkeypatch):
    study_root, manifest_path, _payload = _write_study_root(tmp_path)
    module = _ledger_module()
    _seed_study_trade(study_root / "study_ledger.db")
    opened_paths: list[Path] = []
    real_connect = module.sqlite3.connect

    def _recording_connect(path: str | Path, *args, **kwargs):
        opened_paths.append(Path(path))
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", _recording_connect)
    lookup = module.ManifestStudyLedgerExecutionLookup(manifest_path)
    links = lookup.lookup_study_trade_links(study_id=STUDY_ID, admission_id="a" * 64)
    assert [link.study_trade_id for link in links] == ["study-trade-1"]
    assert opened_paths == [study_root / "study_ledger.db"]
    assert all(path.name != "paper_trades.db" for path in opened_paths)


@pytest.mark.parametrize(
    ("case", "seed"),
    (
        ("missing", None),
        ("zero", ()),
        (
            "multiple",
            (
                ("study-trade-1", STUDY_ID, "a" * 64),
                ("study-trade-2", STUDY_ID, "a" * 64),
            ),
        ),
        ("mismatched-study", (("study-trade-1", "wrong-study", "a" * 64),)),
        ("mismatched-admission", (("study-trade-1", STUDY_ID, "b" * 64),)),
    ),
)
def test_lookup_fail_closes_missing_zero_multiple_and_mismatched_links(
    tmp_path: Path,
    case: str,
    seed: tuple[tuple[str, str, str], ...] | None,
):
    study_root, manifest_path, _payload = _write_study_root(tmp_path)
    module = _ledger_module()
    ledger_path = study_root / "study_ledger.db"
    if case != "missing":
        if case == "multiple":
            _seed_ambiguous_study_trades(ledger_path, seed or ())
        else:
            for trade_id, seeded_study_id, seeded_admission_id in seed or ():
                _seed_study_trade(
                    ledger_path,
                    study_id=seeded_study_id,
                    admission_id=seeded_admission_id,
                    study_trade_id=trade_id,
                )
    else:
        ledger_path.unlink()

    lookup = module.ManifestStudyLedgerExecutionLookup(manifest_path)
    with pytest.raises(module.HorizonStudyLedgerError, match="missing|zero|multiple|mismatch|ambiguous|lookup"):
        lookup.lookup_study_trade_links(study_id=STUDY_ID, admission_id="a" * 64)
