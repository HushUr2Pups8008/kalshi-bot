from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.edge_replay import oos_registry


def _registration_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "profit-oos-2026-08-a",
        "declared_at_utc": "2026-07-15T05:00:00Z",
        "window": {
            "start_utc": "2026-08-01T00:00:00Z",
            "end_utc": "2026-08-08T00:00:00Z",
        },
        "regime_label": "post_profit_accounting",
        "universe_policy": {
            "type": "market_families",
            "market_families": ["KXGDP", "KXWEATHER"],
        },
        "exclude_contamination": True,
    }
    payload.update(overrides)
    return payload


def _hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_registry(path: Path, registration: dict[str, object]) -> None:
    stored = dict(registration)
    stored["registration_hash"] = _hash(registration)
    path.write_text(
        json.dumps({"schema_version": 1, "registrations": [stored]}),
        encoding="utf-8",
    )


def test_shipped_registry_has_schema_v1_and_no_registrations() -> None:
    registry = oos_registry.load_oos_registry(oos_registry.DEFAULT_REGISTRY_PATH)

    assert registry == {}


def test_load_valid_registration_and_verify_canonical_hash(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload()
    _write_registry(path, payload)

    registrations = oos_registry.load_oos_registry(path)
    registration = registrations["profit-oos-2026-08-a"]

    assert registration.registration_hash == _hash(payload)
    assert oos_registry.canonical_registration_hash(payload) == _hash(payload)
    assert registration.window_start_utc == "2026-08-01T00:00:00Z"
    assert registration.market_families == ("KXGDP", "KXWEATHER")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"unexpected": "field"}, "unknown registration keys"),
        ({"declared_at_utc": "2026-08-01T00:00:00Z"}, "declared_at_utc must be before"),
        ({"declared_at_utc": "2026-07-15T05:00:00+00:00"}, "canonical UTC"),
        (
            {"window": {"start_utc": "2026-08-08T00:00:00Z", "end_utc": "2026-08-08T00:00:00Z"}},
            "start_utc must be before",
        ),
        ({"exclude_contamination": False}, "exclude_contamination must be true"),
    ],
)
def test_registry_rejects_invalid_registration_shape(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "registry.json"
    _write_registry(path, _registration_payload(**overrides))

    with pytest.raises(oos_registry.OOSRegistryError, match=message):
        oos_registry.load_oos_registry(path)


def test_registry_rejects_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload()
    stored = dict(payload, registration_hash="0" * 64)
    path.write_text(
        json.dumps({"schema_version": 1, "registrations": [stored]}),
        encoding="utf-8",
    )

    with pytest.raises(oos_registry.OOSRegistryError, match="registration_hash mismatch"):
        oos_registry.load_oos_registry(path)


def test_materialization_rejected_before_closed_window_end(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload()
    _write_registry(path, payload)
    registration = oos_registry.get_oos_registration("profit-oos-2026-08-a", path)

    with pytest.raises(oos_registry.OOSMaterializationTooEarlyError):
        oos_registry.assert_materializable(
            registration,
            as_of_utc=datetime(2026, 8, 7, 23, 59, 59, tzinfo=timezone.utc),
        )

    oos_registry.assert_materializable(
        registration,
        as_of_utc=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload()
    stored = dict(payload, registration_hash=_hash(payload))
    path.write_text(
        json.dumps({"schema_version": 1, "registrations": [stored, stored]}),
        encoding="utf-8",
    )

    with pytest.raises(oos_registry.OOSRegistryError, match="duplicate registration id"):
        oos_registry.load_oos_registry(path)
