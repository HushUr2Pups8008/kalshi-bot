from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
import json

import pytest

from trading.settlement import MarketOutcome, VoidRefundContract
from trading.settlement_economics import (
    KALSHI_FIX_MISC_FEE_RECEIPT_V1,
    SettlementCashflows,
    SettlementEconomicsBinding,
    SettlementEconomicsContract,
    SettlementEconomicsUnscorableError,
    VoidSettlementRefundPolicy,
    canonical_json,
    derive_settlement_cashflows,
    derive_settlement_fee_receipt,
    deserialize_settlement_economics_contract,
    deserialize_settlement_economics_evidence,
    serialize_settlement_economics_contract,
    serialize_settlement_economics_evidence,
    settlement_economics_contract_sha256,
)
from trading.venue import Venue


_ACCOUNT_PARTY_ID = "test-customer-account"


def _source_payload(
    *,
    fee: str = "0.0137",
    market_id: str = "KXTEST-26JUL15-T50",
    party_id: str = _ACCOUNT_PARTY_ID,
    party_role: str = "24",
    message_fields: dict[str, str] | None = None,
) -> str:
    message = {
        "MarketSettlementReportID": "test-settlement-report-1",
        "NoMarketSettlementPartyIDs": [
            {
                "LongQty": "5",
                "MarketSettlementPartyID": party_id,
                "MarketSettlementPartyRole": party_role,
                "MiscFees": [
                    {
                        "MiscFeeAmt": fee,
                        "MiscFeeBasis": "0",
                        "MiscFeeCurr": "USD",
                        "MiscFeeType": "4",
                    }
                ],
                "NoMiscFees": "1",
                "ShortQty": "0",
            }
        ],
        "Symbol": market_id,
        **(message_fields or {}),
    }
    message_json = canonical_json(message)
    return canonical_json(
        {
            "market_id": market_id,
            "result": "yes",
            "settlement_fee_receipt": {
                "message": message,
                "message_sha256": hashlib.sha256(message_json.encode("utf-8")).hexdigest(),
            },
        }
    )


def _contract(
    *,
    void_refund_policy: VoidSettlementRefundPolicy | None = None,
) -> SettlementEconomicsContract:
    return SettlementEconomicsContract(
        settlement_fee_receipt_profile=KALSHI_FIX_MISC_FEE_RECEIPT_V1,
        void_refund_policy=void_refund_policy,
    )


def _binding(
    source_payload_json: str,
    *,
    venue_market_id: str = "KXTEST-26JUL15-T50",
    account_party_id: str = _ACCOUNT_PARTY_ID,
) -> SettlementEconomicsBinding:
    return SettlementEconomicsBinding(
        venue=Venue.KALSHI,
        venue_market_id=venue_market_id,
        account_party_id_sha256=hashlib.sha256(account_party_id.encode("utf-8")).hexdigest(),
        contract_fingerprint="contract-v1",
        rules_fingerprint="rules-v1",
        settlement_fingerprint="settlement-v1",
        authoritative_observation_sha256="a" * 64,
        authoritative_payload_sha256=hashlib.sha256(source_payload_json.encode("utf-8")).hexdigest(),
        source_id="kalshi-fix-market-settlement-v1",
    )


def _receipt(
    *,
    fee: str = "0.0137",
) -> tuple[SettlementEconomicsBinding, object]:
    source_payload_json = _source_payload(fee=fee)
    binding = _binding(source_payload_json)
    return binding, derive_settlement_fee_receipt(
        contract=_contract(),
        binding=binding,
        source_payload_json=source_payload_json,
    )


def test_fee_receipt_derives_exact_fee_from_hash_bound_authoritative_message() -> None:
    """A document example never substitutes for the actual settlement fee field."""

    source_payload_json = _source_payload(fee="0.0137")
    binding = _binding(source_payload_json)

    receipt = derive_settlement_fee_receipt(
        contract=_contract(),
        binding=binding,
        source_payload_json=source_payload_json,
    )

    assert receipt.settlement_fee == Decimal("0.0137")
    with pytest.raises(SettlementEconomicsUnscorableError, match="fee receipt"):
        derive_settlement_fee_receipt(
            contract=_contract(),
            binding=_binding('{"market_id":"KXTEST-26JUL15-T50","result":"yes"}'),
            source_payload_json='{"market_id":"KXTEST-26JUL15-T50","result":"yes"}',
        )
    malformed_payload = json.loads(source_payload_json)
    malformed_payload["settlement_fee_receipt"]["message_sha256"] = "0" * 64
    malformed_payload_json = canonical_json(malformed_payload)
    with pytest.raises(SettlementEconomicsUnscorableError, match="message hash"):
        derive_settlement_fee_receipt(
            contract=_contract(),
            binding=_binding(malformed_payload_json),
            source_payload_json=malformed_payload_json,
        )


def test_fee_receipt_preserves_full_hash_bound_fix_message() -> None:
    source_payload_json = _source_payload(
        fee="0.0137",
        message_fields={"SecurityID": "KXTEST-26JUL15-T50"},
    )
    binding = _binding(source_payload_json)

    receipt = derive_settlement_fee_receipt(
        contract=_contract(),
        binding=binding,
        source_payload_json=source_payload_json,
    )

    assert receipt.settlement_fee == Decimal("0.0137")


def test_fee_receipt_rejects_non_customer_account_party() -> None:
    source_payload_json = _source_payload(party_role="1")

    with pytest.raises(SettlementEconomicsUnscorableError, match="party"):
        derive_settlement_fee_receipt(
            contract=_contract(),
            binding=_binding(source_payload_json),
            source_payload_json=source_payload_json,
        )


def test_fee_receipt_rejects_a_hash_bound_source_payload_for_another_market() -> None:
    source_payload = json.loads(_source_payload())
    source_payload["market_id"] = "KXOTHER-26JUL15-T50"
    source_payload_json = canonical_json(source_payload)

    with pytest.raises(SettlementEconomicsUnscorableError, match="market identity"):
        derive_settlement_fee_receipt(
            contract=_contract(),
            binding=_binding(source_payload_json),
            source_payload_json=source_payload_json,
        )


def test_fee_receipt_rejects_a_hash_bound_fix_symbol_for_another_market() -> None:
    source_payload_json = _source_payload(
        message_fields={"Symbol": "KXOTHER-26JUL15-T50"},
    )

    with pytest.raises(SettlementEconomicsUnscorableError, match="market identity"):
        derive_settlement_fee_receipt(
            contract=_contract(),
            binding=_binding(source_payload_json),
            source_payload_json=source_payload_json,
        )


def test_contract_round_trip_is_canonical_and_hash_bound() -> None:
    contract = _contract(
        void_refund_policy=VoidSettlementRefundPolicy(
            kind="fixed_per_contract",
            refund_cents_per_contract=Decimal("42.5"),
            refunds_entry_fee=True,
        )
    )

    payload = serialize_settlement_economics_contract(contract)

    assert deserialize_settlement_economics_contract(payload) == contract
    assert settlement_economics_contract_sha256(contract) == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert payload == serialize_settlement_economics_contract(deserialize_settlement_economics_contract(payload))
    assert '"settlement_fee_receipt_profile"' in payload


@pytest.mark.parametrize(
    "payload",
    (
        '{"schema_version":2}',
        '{"payout_model":"binary_par","schema_version":1,"settlement_fee_receipt_profile":{},"void_refund_policy":null}',
        '{"payout_model":"binary_par","schema_version":2,"settlement_fee_receipt_profile":{},"void_refund_policy":null,"unexpected":true}',
        '{"payout_model":"binary_par","schema_version":2,"settlement_fee_receipt_profile":{"artifact_sha256":"f"},"void_refund_policy":null}',
        serialize_settlement_economics_contract(_contract()).replace('"schema_version":3', '"schema_version":1'),
    ),
)
def test_contract_deserialization_fails_closed_on_noncanonical_or_unknown_payload(
    payload: str,
) -> None:
    with pytest.raises(SettlementEconomicsUnscorableError):
        deserialize_settlement_economics_contract(payload)


@pytest.mark.parametrize(
    ("outcome", "held_side", "expected_payout"),
    (
        (MarketOutcome.YES, "yes", Decimal("5")),
        (MarketOutcome.YES, "no", Decimal("0")),
        (MarketOutcome.NO, "yes", Decimal("0")),
        (MarketOutcome.NO, "no", Decimal("5")),
    ),
)
def test_binary_par_directional_cashflows_are_derived_from_immutable_inputs(
    outcome: MarketOutcome,
    held_side: str,
    expected_payout: Decimal,
) -> None:
    binding, receipt = _receipt()

    cashflows = derive_settlement_cashflows(
        contract=_contract(),
        binding=binding,
        outcome=outcome,
        held_side=held_side,
        quantity=Decimal("5"),
        entry_price=Decimal("0.42"),
        entry_fee=Decimal("0.0524"),
        void_refund=None,
        fee_receipt=receipt,
    )

    assert cashflows == SettlementCashflows(
        outcome=outcome.value,
        gross_payout=expected_payout,
        settlement_fee=Decimal("0.0137"),
        settlement_refund=Decimal("0"),
        net_payout=expected_payout - Decimal("0.0137"),
    )


@pytest.mark.parametrize(
    ("policy", "void_refund", "expected_refund"),
    (
        (
            VoidSettlementRefundPolicy(
                kind="entry_debit",
                refund_cents_per_contract=None,
                refunds_entry_fee=False,
            ),
            VoidRefundContract(Decimal("42"), False),
            Decimal("2.1"),
        ),
        (
            VoidSettlementRefundPolicy(
                kind="entry_debit",
                refund_cents_per_contract=None,
                refunds_entry_fee=True,
            ),
            VoidRefundContract(Decimal("42"), True),
            Decimal("2.1524"),
        ),
        (
            VoidSettlementRefundPolicy(
                kind="fixed_per_contract",
                refund_cents_per_contract=Decimal("37.25"),
                refunds_entry_fee=False,
            ),
            VoidRefundContract(Decimal("37.25"), False),
            Decimal("1.8625"),
        ),
        (
            VoidSettlementRefundPolicy(
                kind="fixed_per_contract",
                refund_cents_per_contract=Decimal("37.25"),
                refunds_entry_fee=True,
            ),
            VoidRefundContract(Decimal("37.25"), True),
            Decimal("1.9149"),
        ),
    ),
)
def test_void_cashflows_require_exact_contractual_refund_and_requote_entry_fee(
    policy: VoidSettlementRefundPolicy,
    void_refund: VoidRefundContract,
    expected_refund: Decimal,
) -> None:
    binding, receipt = _receipt(fee="0.05")

    cashflows = derive_settlement_cashflows(
        contract=_contract(void_refund_policy=policy),
        binding=binding,
        outcome=MarketOutcome.VOID,
        held_side="yes",
        quantity=Decimal("5"),
        entry_price=Decimal("0.42"),
        entry_fee=Decimal("0.0524"),
        void_refund=void_refund,
        fee_receipt=receipt,
    )

    assert cashflows.settlement_refund == expected_refund
    assert cashflows.net_payout == expected_refund - Decimal("0.05")
    assert cashflows.gross_payout == Decimal("0")
    assert cashflows.settlement_fee == Decimal("0.05")


def test_void_cashflows_reject_a_mismatched_observation_or_unknown_fee_profile() -> None:
    policy = VoidSettlementRefundPolicy(
        kind="fixed_per_contract",
        refund_cents_per_contract=Decimal("42"),
        refunds_entry_fee=False,
    )
    binding, receipt = _receipt()
    with pytest.raises(SettlementEconomicsUnscorableError, match="void refund"):
        derive_settlement_cashflows(
            contract=_contract(void_refund_policy=policy),
            binding=binding,
            outcome=MarketOutcome.VOID,
            held_side="yes",
            quantity=Decimal("5"),
            entry_price=Decimal("0.42"),
            entry_fee=Decimal("0.0524"),
            void_refund=VoidRefundContract(Decimal("37.25"), False),
            fee_receipt=receipt,
        )

    with pytest.raises(SettlementEconomicsUnscorableError, match="unpinned"):
        SettlementEconomicsContract(
            settlement_fee_receipt_profile=replace(
                KALSHI_FIX_MISC_FEE_RECEIPT_V1,
                source_id="unknown-source",
            ),
            void_refund_policy=None,
        )


def test_evidence_round_trip_binds_contract_receipt_and_market_identity() -> None:
    contract = _contract()
    source_payload_json = _source_payload()
    binding = _binding(source_payload_json)
    receipt = derive_settlement_fee_receipt(
        contract=contract,
        binding=binding,
        source_payload_json=source_payload_json,
    )
    cashflows = derive_settlement_cashflows(
        contract=contract,
        binding=binding,
        outcome=MarketOutcome.YES,
        held_side="yes",
        quantity=Decimal("5"),
        entry_price=Decimal("0.42"),
        entry_fee=Decimal("0.0524"),
        void_refund=None,
        fee_receipt=receipt,
    )
    payload = serialize_settlement_economics_evidence(
        contract=contract,
        binding=binding,
        fee_receipt=receipt,
        cashflows=cashflows,
    )

    evidence = deserialize_settlement_economics_evidence(payload)

    assert evidence.contract == contract
    assert evidence.binding == binding
    assert evidence.fee_receipt == receipt
    assert evidence.cashflows == cashflows
    with pytest.raises(SettlementEconomicsUnscorableError, match="binding"):
        deserialize_settlement_economics_evidence(payload.replace("a" * 64, "b" * 63))
