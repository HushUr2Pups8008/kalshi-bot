from __future__ import annotations

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


def _write_manifest(
    tmp_path: Path,
    *,
    study_kind: str = HORIZON_PAPER_STUDY_KIND,
    study_id: str = STUDY_ID,
    ledger_path: str = "study_ledger.db",
    live_order_forbidden: bool = True,
) -> tuple[Path, Path, dict[str, object]]:
    data_root = tmp_path / "data"
    study_root = data_root / "horizon_paper_studies" / study_id
    study_root.mkdir(parents=True)
    policy_path = study_root / "policy_snapshot.json"
    fee_path = study_root / "fee_schedule.json"
    policy_path.write_bytes(b'{"matcher":"pinned"}')
    fee_path.write_bytes(b'{"fee_schedule":"pinned"}')
    configuration = {
        "schema_version": 1,
        "study_id": study_id,
        "study_kind": study_kind,
        "venue": "polymarket_us",
        "created_at_utc": "2026-08-05T00:00:00.000000+00:00",
        "ledger_path": ledger_path,
        "state_db_path": "study_state.db",
        "starting_bankroll": "250.00",
        "horizon_lower_exclusive_days": 14.0,
        "horizon_upper_inclusive_days": 30.0,
        "policy_snapshot_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "fee_schedule_sha256": hashlib.sha256(fee_path.read_bytes()).hexdigest(),
        "paper_execution_mode": "isolated_paper_only",
        "live_order_forbidden": live_order_forbidden,
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


def _attestation_module():
    return importlib.import_module("trading.horizon_paper_study_attestation")


def test_attestation_writes_only_the_exact_study_scoped_runtime_attestation_path(
    tmp_path: Path,
):
    _study_root, manifest_path, _payload = _write_manifest(tmp_path)
    module = _attestation_module()
    path = module.write_runtime_attestation(
        manifest_path=manifest_path,
        service_label="com.jake.horizon-paper-study.pm-horizon-15-30-20260805",
        pid=43210,
        started_at_utc="2026-08-05T01:00:00.000000+00:00",
        live_trading_enabled=False,
    )
    expected = (
        tmp_path
        / "logs"
        / "state"
        / "horizon_paper_studies"
        / STUDY_ID
        / "runtime_attestation.json"
    )
    assert path == expected
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["study_id"] == STUDY_ID
    assert payload["study_kind"] == HORIZON_PAPER_STUDY_KIND
    assert payload["ledger_path_relative_to_study_root"] == "study_ledger.db"
    assert payload["live_trading_enabled"] is False


@pytest.mark.parametrize(
    ("service_label", "study_kind", "ledger_path", "live_trading_enabled"),
    (
        ("com.jake.kalshi-bot", HORIZON_PAPER_STUDY_KIND, "study_ledger.db", False),
        ("wrong-service", HORIZON_PAPER_STUDY_KIND, "study_ledger.db", False),
        (
            "com.jake.horizon-paper-study.pm-horizon-15-30-20260805",
            "legacy_pending",
            "study_ledger.db",
            False,
        ),
        (
            "com.jake.horizon-paper-study.pm-horizon-15-30-20260805",
            HORIZON_PAPER_STUDY_KIND,
            "/tmp/study_ledger.db",
            False,
        ),
        (
            "com.jake.horizon-paper-study.pm-horizon-15-30-20260805",
            HORIZON_PAPER_STUDY_KIND,
            "study_ledger.db",
            True,
        ),
    ),
    ids=("primary-label", "wrong-label", "wrong-kind", "nonrelative-ledger", "live-trading"),
)
def test_attestation_rejects_wrong_label_kind_nonrelative_ledger_and_live_trading(
    tmp_path: Path,
    service_label: str,
    study_kind: str,
    ledger_path: str,
    live_trading_enabled: bool,
):
    _study_root, manifest_path, _payload = _write_manifest(
        tmp_path,
        study_kind=study_kind,
        ledger_path=ledger_path,
    )
    module = _attestation_module()
    with pytest.raises(module.HorizonStudyAttestationError, match="attestation|service|study|ledger|live"):
        module.write_runtime_attestation(
            manifest_path=manifest_path,
            service_label=service_label,
            pid=43210,
            started_at_utc="2026-08-05T01:00:00.000000+00:00",
            live_trading_enabled=live_trading_enabled,
        )


def test_attestation_rejects_changed_manifest_digest_and_symlinked_target_directory(
    tmp_path: Path,
):
    study_root, manifest_path, payload = _write_manifest(tmp_path)
    module = _attestation_module()
    tampered = dict(payload)
    tampered["database_identity"] = "changed"
    manifest_path.write_text(_canonical_json(tampered), encoding="utf-8")
    with pytest.raises(module.HorizonStudyAttestationError, match="digest|manifest"):
        module.write_runtime_attestation(
            manifest_path=manifest_path,
            service_label="com.jake.horizon-paper-study.pm-horizon-15-30-20260805",
            pid=43210,
            started_at_utc="2026-08-05T01:00:00.000000+00:00",
            live_trading_enabled=False,
        )

    other_root, other_manifest_path, _other_payload = _write_manifest(tmp_path / "other")
    target_dir = tmp_path / "logs" / "state" / "horizon_paper_studies" / STUDY_ID
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    target_dir.symlink_to(other_root, target_is_directory=True)
    with pytest.raises(module.HorizonStudyAttestationError, match="path|symlink|attestation"):
        module.write_runtime_attestation(
            manifest_path=other_manifest_path,
            service_label="com.jake.horizon-paper-study.pm-horizon-15-30-20260805",
            pid=43210,
            started_at_utc="2026-08-05T01:00:00.000000+00:00",
            live_trading_enabled=False,
        )


def test_attestation_never_writes_primary_or_arbitrary_alternate_paths(tmp_path: Path):
    _study_root, manifest_path, _payload = _write_manifest(tmp_path)
    module = _attestation_module()
    primary = tmp_path / "logs" / "state" / "runtime_paper_cohort_attestation.json"
    alternate = tmp_path / "logs" / "state" / "horizon_paper_studies" / "alternate" / "runtime_attestation.json"
    primary.parent.mkdir(parents=True, exist_ok=True)
    alternate.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text("primary", encoding="utf-8")
    alternate.write_text("alternate", encoding="utf-8")
    path = module.write_runtime_attestation(
        manifest_path=manifest_path,
        service_label="com.jake.horizon-paper-study.pm-horizon-15-30-20260805",
        pid=43210,
        started_at_utc="2026-08-05T01:00:00.000000+00:00",
        live_trading_enabled=False,
    )
    assert path != primary
    assert path != alternate
    assert primary.read_text(encoding="utf-8") == "primary"
    assert alternate.read_text(encoding="utf-8") == "alternate"
