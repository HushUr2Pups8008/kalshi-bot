from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from scripts.edge_replay import fee_net_shadow_protocol as protocol


def _payload() -> dict[str, object]:
    return json.loads(protocol.DEFAULT_PROTOCOL_PATH.read_text(encoding="utf-8"))


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    stored = copy.deepcopy(payload)
    stored["protocol_hash"] = protocol.canonical_protocol_hash(stored)
    return stored


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(_rehash(payload)), encoding="utf-8")


def test_shipped_protocol_is_locked_prospective_and_fee_net_only() -> None:
    loaded = protocol.load_fee_net_shadow_protocol(protocol.DEFAULT_PROTOCOL_PATH)

    assert loaded.protocol_id == "fee-net-shadow-kalshi-rest-detail-no-v1"
    assert loaded.status == "locked"
    assert loaded.shadow_schema_version == 3
    assert loaded.book_source == "rest_detail"
    assert loaded.llm_direction == "no"
    assert loaded.side == "no"
    assert loaded.sole_failure == "G7_open_exposure_drawdown"
    assert loaded.protocol_hash == protocol.canonical_protocol_hash(_payload())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload.update({"unexpected": True}),
            "unknown protocol keys",
        ),
        (
            lambda payload: payload["provenance"].update(  # type: ignore[index,union-attr]
                {"shadow_schema_version": 2}
            ),
            "requires v3",
        ),
        (
            lambda payload: payload["scope"].update(  # type: ignore[index,union-attr]
                {
                    "gate_policy": {
                        "required_passed": ["G1", "G2", "G3", "G4", "G5"],
                        "sole_failure": "G7_open_exposure_drawdown",
                    }
                }
            ),
            "required_passed",
        ),
        (
            lambda payload: payload["provenance"].update(  # type: ignore[index,union-attr]
                {"required_fields": ["candidate_id"]}
            ),
            "required_fields",
        ),
    ],
)
def test_protocol_rejects_nonprospective_or_mutable_evidence_policy(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    payload = _payload()
    mutate(payload)  # type: ignore[operator]
    path = tmp_path / "protocol.json"
    _write(path, payload)

    with pytest.raises(protocol.FeeNetShadowProtocolError, match=message):
        protocol.load_fee_net_shadow_protocol(path)


def test_protocol_rejects_tampered_payload_without_a_matching_hash(tmp_path: Path) -> None:
    payload = _payload()
    selector = payload["scope"]
    assert isinstance(selector, dict)
    selector["side"] = "yes"
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(protocol.FeeNetShadowProtocolError, match="protocol_hash mismatch"):
        protocol.load_fee_net_shadow_protocol(path)


def test_protocol_rejects_a_semantically_valid_rehash_outside_the_shipped_pin(
    tmp_path: Path,
) -> None:
    payload = _payload()
    acceptance = payload["acceptance"]
    assert isinstance(acceptance, dict)
    acceptance["minimum_unique_market_ids"] = 31
    path = tmp_path / "protocol.json"
    _write(path, payload)

    assert protocol.SHIPPED_PROTOCOL_SHA256 == protocol.canonical_protocol_hash(_payload())
    with pytest.raises(
        protocol.FeeNetShadowProtocolError,
        match="code-pinned shipped fee-net protocol",
    ):
        protocol.load_fee_net_shadow_protocol(path)


def test_protocol_rejects_nonlocked_or_nonpaper_scope(tmp_path: Path) -> None:
    payload = _payload()
    payload["status"] = "draft"
    path = tmp_path / "protocol.json"
    _write(path, payload)

    with pytest.raises(protocol.FeeNetShadowProtocolError, match="status must be locked"):
        protocol.load_fee_net_shadow_protocol(path)

    payload = _payload()
    scope = payload["scope"]
    assert isinstance(scope, dict)
    scope["mode"] = "live"
    _write(path, payload)

    with pytest.raises(protocol.FeeNetShadowProtocolError, match="mode must be paper_only"):
        protocol.load_fee_net_shadow_protocol(path)


def test_protocol_loader_is_read_only_and_stdlib_only(tmp_path: Path) -> None:
    path = tmp_path / "protocol.json"
    _write(path, _payload())
    before = {entry.name for entry in tmp_path.iterdir()}

    protocol.load_fee_net_shadow_protocol(path)

    assert {entry.name for entry in tmp_path.iterdir()} == before
    source_path = Path(protocol.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    imported_modules.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert imported_modules <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "hashlib",
        "json",
        "math",
        "pathlib",
        "re",
        "typing",
    }
