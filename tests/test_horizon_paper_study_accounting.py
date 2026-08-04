from __future__ import annotations

import ast
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from trading.horizon_paper_study_manifest import (
    HORIZON_PAPER_STUDY_KIND,
    derive_horizon_paper_study_database_identity,
)


STUDY_ID = "pm-horizon-15-30-20260805"


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


def _write_study_root(
    tmp_path: Path,
    *,
    fee_schedule: dict[str, object] | None,
) -> tuple[Path, Path, dict[str, object]]:
    data_root = tmp_path / "data"
    study_root = data_root / "horizon_paper_studies" / STUDY_ID
    study_root.mkdir(parents=True)
    policy_path = study_root / "policy_snapshot.json"
    fee_path = study_root / "fee_schedule.json"
    policy_path.write_bytes(b'{"matcher":"pinned"}')
    if fee_schedule is None:
        fee_path.write_text("", encoding="utf-8")
    else:
        fee_path.write_text(_canonical_json(fee_schedule), encoding="utf-8")
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
    manifest_path = study_root / "manifest.json"
    manifest_path.write_text(_canonical_json(payload), encoding="utf-8")
    return study_root, manifest_path, payload


def _accounting_module():
    return importlib.import_module("trading.horizon_paper_study_accounting")


def _module_path() -> Path:
    return Path(__file__).resolve().parents[1] / "trading" / "horizon_paper_study_accounting.py"


def _trade_row() -> dict[str, object]:
    return {
        "study_trade_id": "study-trade-1",
        "executed_at_utc": "2026-08-05T01:05:00.000000+00:00",
        "observed_at_utc": "2026-08-25T00:01:00.000000+00:00",
        "entry_price_snapshot": "0.37",
        "size": "5",
        "gross_pnl_cents": 315,
    }


def test_accounting_module_avoids_primary_imports_and_exposes_pure_surface():
    path = _module_path()
    assert path.exists(), f"missing module: {path}"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    forbidden = {
        "trading.paper_trader",
        "trading.paper_accounting",
        "trading.settlement_economics",
        "config",
    }
    assert forbidden.isdisjoint(imported)
    module = _accounting_module()
    assert hasattr(module, "HorizonStudyAccounting")
    assert hasattr(module, "HorizonStudyAccountingError")


def test_accounting_returns_unscorable_when_fee_schedule_is_absent(tmp_path: Path):
    study_root, manifest_path, payload = _write_study_root(tmp_path, fee_schedule=None)
    (study_root / "fee_schedule.json").unlink()
    module = _accounting_module()
    accounting = module.HorizonStudyAccounting(manifest_path)
    result = accounting.evaluate_settlement(_trade_row())
    assert result["accounting_state"] == "unscorable"
    assert result["modeled_fee_net_pnl_cents"] is None
    assert result["fee_schedule_sha256"] == payload["fee_schedule_sha256"]


@pytest.mark.parametrize(
    "fee_schedule",
    (
        {
            "schema_version": 1,
            "source_document_url": "https://example.com/fees",
            "retrieved_at_utc": "2026-08-01T00:00:00.000000+00:00",
            "effective_from_utc": "2026-07-01T00:00:00.000000+00:00",
            "effective_to_utc": "2026-08-04T23:59:59.000000+00:00",
            "currency": "USD",
            "entry_fee_function": {"kind": "fixed_cents", "amount": 3},
            "settlement_fee_function": {"kind": "fixed_cents", "amount": 2},
        },
        {
            "schema_version": 1,
            "source_document_url": "https://example.com/fees",
            "retrieved_at_utc": "2026-08-01T00:00:00.000000+00:00",
            "effective_from_utc": "2026-07-01T00:00:00.000000+00:00",
            "effective_to_utc": "2026-08-31T23:59:59.000000+00:00",
            "currency": "USD",
            "entry_fee_function": {"kind": "fixed_cents", "amount": 3},
        },
    ),
    ids=("expired", "partial"),
)
def test_accounting_returns_unscorable_for_expired_or_partial_schedule(
    tmp_path: Path,
    fee_schedule: dict[str, object],
):
    _study_root, manifest_path, _payload = _write_study_root(tmp_path, fee_schedule=fee_schedule)
    module = _accounting_module()
    accounting = module.HorizonStudyAccounting(manifest_path)
    result = accounting.evaluate_settlement(_trade_row())
    assert result["accounting_state"] == "unscorable"
    assert result["modeled_fee_net_pnl_cents"] is None
    assert result["entry_fee_provenance"] == "unscorable"
    assert result["settlement_fee_provenance"] == "unscorable"


def test_accounting_returns_modeled_pinned_schedule_when_both_fee_functions_cover(
    tmp_path: Path,
):
    fee_schedule = {
        "schema_version": 1,
        "source_document_url": "https://example.com/fees",
        "retrieved_at_utc": "2026-08-01T00:00:00.000000+00:00",
        "effective_from_utc": "2026-07-01T00:00:00.000000+00:00",
        "effective_to_utc": "2026-08-31T23:59:59.000000+00:00",
        "currency": "USD",
        "entry_fee_function": {"kind": "fixed_cents", "amount": 3},
        "settlement_fee_function": {"kind": "fixed_cents", "amount": 2},
    }
    _study_root, manifest_path, payload = _write_study_root(tmp_path, fee_schedule=fee_schedule)
    module = _accounting_module()
    accounting = module.HorizonStudyAccounting(manifest_path)
    result = accounting.evaluate_settlement(_trade_row())
    assert result["accounting_state"] == "modeled_pinned_schedule"
    assert result["entry_fee_provenance"] == "modeled_pinned_schedule"
    assert result["settlement_fee_provenance"] == "modeled_pinned_schedule"
    assert result["fee_schedule_sha256"] == payload["fee_schedule_sha256"]
    assert isinstance(result["modeled_fee_net_pnl_cents"], int)
