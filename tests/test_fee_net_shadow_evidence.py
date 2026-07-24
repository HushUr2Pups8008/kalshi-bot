from __future__ import annotations

import copy
import hashlib

import pytest

from scripts.edge_replay import fee_net_shadow_protocol as protocol


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _protocol() -> protocol.FeeNetShadowProtocol:
    return protocol.load_fee_net_shadow_protocol()


def _record(
    loaded: protocol.FeeNetShadowProtocol,
    *,
    record_id: str = "record-1",
    candidate_id: str = "candidate-1",
    decision_at_utc: str = "2026-08-01T00:00:00Z",
    recorded_at_utc: str = "2026-08-02T00:00:00Z",
    previous_record_hash: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "evidence_schema_version": 3,
        "shadow_schema_version": 3,
        "record_type": "fee_net_terminal_evaluation",
        "record_id": record_id,
        "protocol_id": loaded.protocol_id,
        "protocol_hash": loaded.protocol_hash,
        "previous_record_hash": previous_record_hash,
        "recorded_at_utc": recorded_at_utc,
        "candidate_id": candidate_id,
        "candidate_payload_sha256": _sha(f"{record_id}:candidate"),
        "decision_at_utc": decision_at_utc,
        "mode": "paper_only",
        "venue": "kalshi",
        "venue_market_id": f"KXTEST-{record_id}",
        "market_family": "KXTEST",
        "book_source": "rest_detail",
        "selected_side": "no",
        "llm_direction": "no",
        "llm_model_id": "test-model-v3",
        "llm_prompt_sha256": _sha(f"{record_id}:prompt"),
        "llm_input_sha256": _sha(f"{record_id}:input"),
        "llm_output_sha256": _sha(f"{record_id}:output"),
        "gate_outcomes": {
            "G1": "passed",
            "G2": "passed",
            "G3": "passed",
            "G4": "passed",
            "G5": "passed",
            "G6": "passed",
            "G7_open_exposure_drawdown": "failed",
        },
        "fee_provenance_sha256": _sha(f"{record_id}:fee"),
        "authoritative_observation_sha256": _sha(f"{record_id}:observation"),
        "authoritative_payload_sha256": _sha(f"{record_id}:payload"),
        "settlement_economics_contract_sha256": _sha(f"{record_id}:economics-contract"),
        "settlement_fee_receipt_sha256": _sha(f"{record_id}:fee-receipt"),
        "account_party_id_sha256": _sha(f"{record_id}:account"),
        "settlement_payload_sha256": _sha(f"{record_id}:settlement"),
        "evaluation_payload_sha256": _sha(f"{record_id}:evaluation"),
        "execution": {
            "paper_account_id_sha256": _sha(f"{record_id}:account"),
            "paper_order_id": f"order-{record_id}",
            "paper_fill_id": f"fill-{record_id}",
            "execution_payload_sha256": _sha(f"{record_id}:execution"),
            "venue_market_id": f"KXTEST-{record_id}",
            "side": "no",
            "executed_at_utc": decision_at_utc,
            "quantity": 20,
            "entry_price_dollars": "0.50",
        },
        "settlement": {
            "kind": "settled",
            "settled_at_utc": recorded_at_utc,
            "outcome": "no",
            "payout_per_contract_dollars": "1.00",
            "void_refund_policy_sha256": None,
            "void_refund_payload_sha256": None,
        },
        "economics": {
            "gross_entry_debit_dollars": "10.00",
            "entry_fee_dollars": "0.20",
            "net_entry_debit_dollars": "10.20",
            "gross_payout_dollars": "20.00",
            "settlement_fee_dollars": "0.10",
            "settlement_refund_dollars": "0.00",
            "net_payout_dollars": "19.90",
            "gross_pnl_dollars": "10.00",
            "fee_net_pnl_dollars": "9.70",
        },
    }
    record["record_hash"] = protocol.canonical_evidence_record_hash(record)
    return record


def _rehash(record: dict[str, object]) -> dict[str, object]:
    stored = copy.deepcopy(record)
    stored["record_hash"] = protocol.canonical_evidence_record_hash(stored)
    return stored


def test_verifies_a_v3_terminal_fee_net_record_and_linked_chain() -> None:
    loaded = _protocol()
    first = _record(loaded)
    verified = protocol.verify_fee_net_shadow_evidence_record(
        loaded,
        first,
        expected_previous_record_hash=None,
    )
    assert verified.record_id == "record-1"
    assert verified.fee_net_pnl_dollars == "9.70"
    assert verified.structural_only is True
    assert verified.promotion_eligible is False
    with pytest.raises(TypeError, match="promotion_eligible"):
        protocol.FeeNetShadowEvidenceRecord(
            record_id="manual",
            record_hash=_sha("manual"),
            previous_record_hash=None,
            candidate_id="manual-candidate",
            recorded_at_utc="2026-08-02T00:00:00Z",
            decision_at_utc="2026-08-01T00:00:00Z",
            venue_market_id="KXTEST-MANUAL",
            paper_account_id_sha256=_sha("manual-account"),
            paper_fill_id="fill-manual",
            fee_net_pnl_dollars="0.00",
            promotion_eligible=True,
        )

    second = _record(
        loaded,
        record_id="record-2",
        candidate_id="candidate-2",
        decision_at_utc="2026-08-03T00:00:00Z",
        recorded_at_utc="2026-08-04T00:00:00Z",
        previous_record_hash=verified.record_hash,
    )
    second_execution = second["execution"]
    assert isinstance(second_execution, dict)
    second_execution["paper_account_id_sha256"] = _sha("record-1:account")
    second["account_party_id_sha256"] = _sha("record-1:account")
    chain = protocol.verify_fee_net_shadow_chain(loaded, [first, _rehash(second)])
    assert tuple(record.record_id for record in chain) == ("record-1", "record-2")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda record: record.update({"evidence_schema_version": 2}),
            "evidence_schema_version",
        ),
        (lambda record: record.update({"mode": "live"}), "mode must be paper_only"),
        (
            lambda record: record["gate_outcomes"].update({"G1": "failed"}),  # type: ignore[index,union-attr]
            "gate_outcomes",
        ),
        (
            lambda record: record["economics"].update(  # type: ignore[index,union-attr]
                {"fee_net_pnl_dollars": "9.71"}
            ),
            "fee_net_pnl_dollars",
        ),
        (
            lambda record: record["economics"].update(  # type: ignore[index,union-attr]
                {"gross_payout_dollars": "19.00"}
            ),
            "gross_payout_dollars",
        ),
        (
            lambda record: record.update({"protocol_hash": _sha("other-protocol")}),
            "protocol_hash",
        ),
    ],
)
def test_evidence_rejects_legacy_wrong_selector_gate_or_gross_only_record(
    mutate: object,
    message: str,
) -> None:
    loaded = _protocol()
    record = _record(loaded)
    mutate(record)  # type: ignore[operator]

    with pytest.raises(protocol.FeeNetShadowProtocolError, match=message):
        protocol.verify_fee_net_shadow_evidence_record(
            loaded,
            _rehash(record),
            expected_previous_record_hash=None,
        )


def test_evidence_rejects_voids_bad_hashes_and_broken_chain_links() -> None:
    loaded = _protocol()
    record = _record(loaded)
    settlement = record["settlement"]
    assert isinstance(settlement, dict)
    settlement.update(
        {
            "kind": "void",
            "outcome": "void",
            "void_refund_policy_sha256": _sha("void-policy"),
            "void_refund_payload_sha256": _sha("void-refund"),
        }
    )
    with pytest.raises(protocol.FeeNetShadowProtocolError, match="voids are not authorized"):
        protocol.verify_fee_net_shadow_evidence_record(
            loaded,
            _rehash(record),
            expected_previous_record_hash=None,
        )

    first = _record(loaded)
    second = _record(
        loaded,
        record_id="record-2",
        candidate_id="candidate-2",
        decision_at_utc="2026-08-03T00:00:00Z",
        recorded_at_utc="2026-08-04T00:00:00Z",
        previous_record_hash=None,
    )
    with pytest.raises(protocol.FeeNetShadowProtocolError, match="previous_record_hash"):
        protocol.verify_fee_net_shadow_chain(loaded, [first, second])

    record = _record(loaded)
    record["record_hash"] = _sha("tampered")
    with pytest.raises(protocol.FeeNetShadowProtocolError, match="record_hash mismatch"):
        protocol.verify_fee_net_shadow_evidence_record(
            loaded,
            record,
            expected_previous_record_hash=None,
        )


def test_evidence_remains_promotion_blocked_and_must_be_recorded_after_settlement() -> None:
    loaded = _protocol()
    record = _record(loaded)
    settlement = record["settlement"]
    assert isinstance(settlement, dict)
    settlement["settled_at_utc"] = "2026-08-03T00:00:00Z"

    with pytest.raises(protocol.FeeNetShadowProtocolError, match="recorded_at_utc must not precede"):
        protocol.verify_fee_net_shadow_evidence_record(
            loaded,
            _rehash(record),
            expected_previous_record_hash=None,
        )

    record = _record(loaded)
    with pytest.raises(protocol.FeeNetShadowProtocolError, match="promotion remains blocked"):
        protocol.assert_fee_net_shadow_promotion_eligible(loaded, [record])


def test_evidence_rejects_settled_refunds() -> None:
    loaded = _protocol()

    record = _record(loaded)
    settlement = record["settlement"]
    economics = record["economics"]
    assert isinstance(settlement, dict)
    assert isinstance(economics, dict)
    settlement["outcome"] = "yes"
    economics.update(
        {
            "gross_payout_dollars": "0.00",
            "settlement_refund_dollars": "100.00",
            "net_payout_dollars": "99.90",
            "gross_pnl_dollars": "-10.00",
            "fee_net_pnl_dollars": "89.70",
        }
    )
    with pytest.raises(protocol.FeeNetShadowProtocolError, match="settlement_refund_dollars must be zero"):
        protocol.verify_fee_net_shadow_evidence_record(
            loaded,
            _rehash(record),
            expected_previous_record_hash=None,
        )


@pytest.mark.parametrize("entry_price_dollars", ["0.00", "1.00", "0.123"])
def test_evidence_rejects_non_kalshi_execution_prices(entry_price_dollars: str) -> None:
    loaded = _protocol()
    record = _record(loaded)
    execution = record["execution"]
    assert isinstance(execution, dict)
    execution["entry_price_dollars"] = entry_price_dollars

    with pytest.raises(protocol.FeeNetShadowProtocolError, match="whole-cent Kalshi price"):
        protocol.verify_fee_net_shadow_evidence_record(
            loaded,
            _rehash(record),
            expected_previous_record_hash=None,
        )


def test_evidence_chain_rejects_reused_paper_fills() -> None:
    loaded = _protocol()
    first = _record(loaded)
    second = _record(
        loaded,
        record_id="record-2",
        candidate_id="candidate-2",
        decision_at_utc="2026-08-03T00:00:00Z",
        recorded_at_utc="2026-08-04T00:00:00Z",
        previous_record_hash=str(first["record_hash"]),
    )
    first_execution = first["execution"]
    second_execution = second["execution"]
    assert isinstance(first_execution, dict)
    assert isinstance(second_execution, dict)
    second_execution["paper_account_id_sha256"] = first_execution["paper_account_id_sha256"]
    second["account_party_id_sha256"] = first["account_party_id_sha256"]
    second_execution["paper_fill_id"] = first_execution["paper_fill_id"]

    with pytest.raises(protocol.FeeNetShadowProtocolError, match="duplicate paper fill"):
        protocol.verify_fee_net_shadow_chain(loaded, [first, _rehash(second)])


def test_evidence_chain_rejects_mixed_paper_accounts() -> None:
    loaded = _protocol()
    first = _record(loaded)
    second = _record(
        loaded,
        record_id="record-2",
        candidate_id="candidate-2",
        decision_at_utc="2026-08-03T00:00:00Z",
        recorded_at_utc="2026-08-04T00:00:00Z",
        previous_record_hash=str(first["record_hash"]),
    )

    with pytest.raises(protocol.FeeNetShadowProtocolError, match="more than one paper account"):
        protocol.verify_fee_net_shadow_chain(loaded, [first, second])


@pytest.mark.parametrize(
    ("field", "value"),
    [("venue_market_id", "KXUNRELATED-1"), ("side", "yes")],
)
def test_evidence_rejects_execution_that_does_not_match_its_record(field: str, value: str) -> None:
    loaded = _protocol()
    record = _record(loaded)
    execution = record["execution"]
    assert isinstance(execution, dict)
    execution[field] = value

    with pytest.raises(protocol.FeeNetShadowProtocolError, match=f"execution.{field} must match evidence"):
        protocol.verify_fee_net_shadow_evidence_record(
            loaded,
            _rehash(record),
            expected_previous_record_hash=None,
        )
