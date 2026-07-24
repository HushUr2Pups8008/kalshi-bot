"""Pinned, fail-closed cashflows for binary shadow settlements."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
from typing import Literal

from trading.settlement import MarketOutcome, VoidRefundContract
from trading.venue import Venue


SETTLEMENT_ECONOMICS_SCHEMA_VERSION = 3
_SHA256_TEXT = re.compile(r"[0-9a-f]{64}")
_ZERO = Decimal("0")
_ONE = Decimal("1")


class SettlementEconomicsUnscorableError(ValueError):
    """Settlement cashflows lack complete pinned economics evidence."""


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise SettlementEconomicsUnscorableError("settlement economics value is not canonical JSON") from exc


def _decimal_text(value: Decimal) -> str:
    _require_decimal("decimal", value)
    normalized = value.normalize()
    if normalized == _ZERO:
        return "0"
    return format(normalized, "f")


def _require_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise SettlementEconomicsUnscorableError(f"{name} must be a finite Decimal")
    return value


def _decimal_from_text(name: str, value: object) -> Decimal:
    if not isinstance(value, str):
        raise SettlementEconomicsUnscorableError(f"{name} must be canonical Decimal text")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise SettlementEconomicsUnscorableError(f"{name} must be canonical Decimal text") from exc
    _require_decimal(name, parsed)
    if _decimal_text(parsed) != value:
        raise SettlementEconomicsUnscorableError(f"{name} must be canonical Decimal text")
    return parsed


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SettlementEconomicsUnscorableError(f"{name} must be nonempty text")
    return value


def _require_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256_TEXT.fullmatch(value) is None:
        raise SettlementEconomicsUnscorableError(f"{name} must be a lowercase SHA-256")
    return value


def _require_aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SettlementEconomicsUnscorableError(f"{name} must be timezone-aware")
    return value


def _parse_canonical_object(name: str, value: str | Mapping[str, object]) -> dict[str, object]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SettlementEconomicsUnscorableError(f"{name} must be canonical JSON") from exc
        if not isinstance(parsed, dict) or canonical_json(parsed) != value:
            raise SettlementEconomicsUnscorableError(f"{name} must be canonical JSON")
        return parsed
    if isinstance(value, Mapping):
        return dict(value)
    raise SettlementEconomicsUnscorableError(f"{name} must be an object")


def _require_exact_keys(name: str, value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise SettlementEconomicsUnscorableError(f"{name} has an unsupported schema")


@dataclass(frozen=True)
class SettlementFeeReceiptProfile:
    """Pinned parser for one diagnostic account-level settlement-report page.

    The profile documents how to read a fee field. It never supplies a fee
    amount or a venue-wide no-fee assumption. One report page is deliberately
    insufficient to attribute fee-net P&L to a shadow candidate; a future
    typed path must validate a complete paginated receipt set and an immutable
    candidate-to-fill mapping.
    """

    name: str
    venue: Venue
    source_id: str
    source_url: str
    artifact_sha256: str
    receipt_field: Literal["settlement_fee_receipt"] = "settlement_fee_receipt"
    message_field: Literal["message"] = "message"
    message_sha256_field: Literal["message_sha256"] = "message_sha256"
    market_id_field: Literal["market_id"] = "market_id"
    message_market_id_field: Literal["Symbol"] = "Symbol"
    fee_field: Literal["MiscFeeAmt"] = "MiscFeeAmt"

    def __post_init__(self) -> None:
        _require_text("settlement fee receipt profile name", self.name)
        if not isinstance(self.venue, Venue):
            raise SettlementEconomicsUnscorableError("settlement fee receipt profile venue must be Venue")
        _require_text("settlement fee receipt profile source_id", self.source_id)
        _require_text("settlement fee receipt profile source_url", self.source_url)
        _require_sha256("settlement fee receipt profile artifact_sha256", self.artifact_sha256)
        if (
            self.receipt_field != "settlement_fee_receipt"
            or self.message_field != "message"
            or self.message_sha256_field != "message_sha256"
            or self.market_id_field != "market_id"
            or self.message_market_id_field != "Symbol"
            or self.fee_field != "MiscFeeAmt"
        ):
            raise SettlementEconomicsUnscorableError("unsupported settlement fee receipt profile fields")


# The official FIX reference defines MiscFeeAmt as the fee amount in dollars.
# It does not establish that every settlement has a zero fee.
KALSHI_FIX_MISC_FEE_RECEIPT_V1 = SettlementFeeReceiptProfile(
    name="kalshi-fix-misc-fee-receipt-v1",
    venue=Venue.KALSHI,
    source_id="kalshi-fix-market-settlement-v1",
    source_url="https://docs.kalshi.com/fix/market-settlement.md",
    artifact_sha256="ad87e8ea51113c7abf8c86e2e4916c1a5f0dcd61661b84532c06df1df68b1788",
)

_SUPPORTED_SETTLEMENT_FEE_RECEIPT_PROFILES = frozenset((KALSHI_FIX_MISC_FEE_RECEIPT_V1,))


def settlement_fee_receipt_profile_record(
    profile: SettlementFeeReceiptProfile,
) -> dict[str, object]:
    if profile not in _SUPPORTED_SETTLEMENT_FEE_RECEIPT_PROFILES:
        raise SettlementEconomicsUnscorableError("unknown or unpinned settlement fee receipt profile")
    return {
        "artifact_sha256": profile.artifact_sha256,
        "fee_field": profile.fee_field,
        "market_id_field": profile.market_id_field,
        "message_field": profile.message_field,
        "message_market_id_field": profile.message_market_id_field,
        "message_sha256_field": profile.message_sha256_field,
        "name": profile.name,
        "receipt_field": profile.receipt_field,
        "source_id": profile.source_id,
        "source_url": profile.source_url,
        "venue": profile.venue.value,
    }


def _deserialize_settlement_fee_receipt_profile(
    value: object,
) -> SettlementFeeReceiptProfile:
    if not isinstance(value, Mapping):
        raise SettlementEconomicsUnscorableError("settlement fee receipt profile must be an object")
    record = dict(value)
    for profile in _SUPPORTED_SETTLEMENT_FEE_RECEIPT_PROFILES:
        if record == settlement_fee_receipt_profile_record(profile):
            return profile
    raise SettlementEconomicsUnscorableError("unknown or unpinned settlement fee receipt profile")


@dataclass(frozen=True)
class VoidSettlementRefundPolicy:
    kind: Literal["entry_debit", "fixed_per_contract"]
    refund_cents_per_contract: Decimal | None
    refunds_entry_fee: bool

    def __post_init__(self) -> None:
        if self.kind not in ("entry_debit", "fixed_per_contract"):
            raise SettlementEconomicsUnscorableError("unsupported void refund policy")
        if not isinstance(self.refunds_entry_fee, bool):
            raise SettlementEconomicsUnscorableError("void refunds_entry_fee must be boolean")
        if self.kind == "entry_debit":
            if self.refund_cents_per_contract is not None:
                raise SettlementEconomicsUnscorableError("entry_debit void policy must not set a fixed refund")
            return
        cents = _require_decimal("void refund_cents_per_contract", self.refund_cents_per_contract)
        if cents < _ZERO or cents > Decimal("100"):
            raise SettlementEconomicsUnscorableError("void refund_cents_per_contract must be between 0 and 100")


def _void_refund_policy_record(
    policy: VoidSettlementRefundPolicy | None,
) -> dict[str, object] | None:
    if policy is None:
        return None
    return {
        "kind": policy.kind,
        "refund_cents_per_contract": (
            None if policy.refund_cents_per_contract is None else _decimal_text(policy.refund_cents_per_contract)
        ),
        "refunds_entry_fee": policy.refunds_entry_fee,
    }


def _deserialize_void_refund_policy(value: object) -> VoidSettlementRefundPolicy | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SettlementEconomicsUnscorableError("void refund policy must be an object")
    record = dict(value)
    _require_exact_keys(
        "void refund policy",
        record,
        {"kind", "refund_cents_per_contract", "refunds_entry_fee"},
    )
    kind = record["kind"]
    if kind not in ("entry_debit", "fixed_per_contract"):
        raise SettlementEconomicsUnscorableError("unsupported void refund policy")
    refunds_entry_fee = record["refunds_entry_fee"]
    if not isinstance(refunds_entry_fee, bool):
        raise SettlementEconomicsUnscorableError("void refunds_entry_fee must be boolean")
    raw_cents = record["refund_cents_per_contract"]
    if kind == "entry_debit":
        if raw_cents is not None:
            raise SettlementEconomicsUnscorableError("entry_debit void policy must not set a fixed refund")
        return VoidSettlementRefundPolicy(
            kind=kind,
            refund_cents_per_contract=None,
            refunds_entry_fee=refunds_entry_fee,
        )
    return VoidSettlementRefundPolicy(
        kind=kind,
        refund_cents_per_contract=_decimal_from_text("void refund_cents_per_contract", raw_cents),
        refunds_entry_fee=refunds_entry_fee,
    )


@dataclass(frozen=True)
class SettlementEconomicsContract:
    settlement_fee_receipt_profile: SettlementFeeReceiptProfile
    void_refund_policy: VoidSettlementRefundPolicy | None
    payout_model: Literal["binary_par"] = "binary_par"

    def __post_init__(self) -> None:
        if self.payout_model != "binary_par":
            raise SettlementEconomicsUnscorableError("unsupported payout model")
        if self.settlement_fee_receipt_profile not in _SUPPORTED_SETTLEMENT_FEE_RECEIPT_PROFILES:
            raise SettlementEconomicsUnscorableError("unknown or unpinned settlement fee receipt profile")
        if self.void_refund_policy is not None and not isinstance(self.void_refund_policy, VoidSettlementRefundPolicy):
            raise SettlementEconomicsUnscorableError("void_refund_policy must be typed")


def settlement_economics_contract_record(
    contract: SettlementEconomicsContract,
) -> dict[str, object]:
    if not isinstance(contract, SettlementEconomicsContract):
        raise SettlementEconomicsUnscorableError("settlement contract must be typed")
    return {
        "payout_model": contract.payout_model,
        "schema_version": SETTLEMENT_ECONOMICS_SCHEMA_VERSION,
        "settlement_fee_receipt_profile": settlement_fee_receipt_profile_record(
            contract.settlement_fee_receipt_profile
        ),
        "void_refund_policy": _void_refund_policy_record(contract.void_refund_policy),
    }


def serialize_settlement_economics_contract(contract: SettlementEconomicsContract) -> str:
    return canonical_json(settlement_economics_contract_record(contract))


def deserialize_settlement_economics_contract(
    value: str | Mapping[str, object],
) -> SettlementEconomicsContract:
    record = _parse_canonical_object("settlement contract", value)
    _require_exact_keys(
        "settlement contract",
        record,
        {
            "payout_model",
            "schema_version",
            "settlement_fee_receipt_profile",
            "void_refund_policy",
        },
    )
    if record["schema_version"] != SETTLEMENT_ECONOMICS_SCHEMA_VERSION:
        raise SettlementEconomicsUnscorableError("unsupported settlement contract version")
    payout_model = record["payout_model"]
    if payout_model != "binary_par":
        raise SettlementEconomicsUnscorableError("unsupported payout model")
    return SettlementEconomicsContract(
        payout_model=payout_model,
        settlement_fee_receipt_profile=_deserialize_settlement_fee_receipt_profile(
            record["settlement_fee_receipt_profile"]
        ),
        void_refund_policy=_deserialize_void_refund_policy(record["void_refund_policy"]),
    )


def settlement_economics_contract_sha256(contract: SettlementEconomicsContract) -> str:
    return hashlib.sha256(serialize_settlement_economics_contract(contract).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SettlementEconomicsBinding:
    venue: Venue
    venue_market_id: str
    account_party_id_sha256: str
    contract_fingerprint: str
    rules_fingerprint: str
    settlement_fingerprint: str
    authoritative_observation_sha256: str
    authoritative_payload_sha256: str
    source_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.venue, Venue):
            raise SettlementEconomicsUnscorableError("binding venue must be Venue")
        _require_text("binding venue_market_id", self.venue_market_id)
        _require_sha256("binding account_party_id_sha256", self.account_party_id_sha256)
        _require_text("binding contract_fingerprint", self.contract_fingerprint)
        _require_text("binding rules_fingerprint", self.rules_fingerprint)
        _require_text("binding settlement_fingerprint", self.settlement_fingerprint)
        _require_sha256(
            "binding authoritative_observation_sha256",
            self.authoritative_observation_sha256,
        )
        _require_sha256(
            "binding authoritative_payload_sha256",
            self.authoritative_payload_sha256,
        )
        _require_text("binding source_id", self.source_id)


def settlement_economics_binding_record(
    binding: SettlementEconomicsBinding,
) -> dict[str, object]:
    if not isinstance(binding, SettlementEconomicsBinding):
        raise SettlementEconomicsUnscorableError("settlement binding must be typed")
    return {
        "account_party_id_sha256": binding.account_party_id_sha256,
        "authoritative_observation_sha256": binding.authoritative_observation_sha256,
        "authoritative_payload_sha256": binding.authoritative_payload_sha256,
        "contract_fingerprint": binding.contract_fingerprint,
        "rules_fingerprint": binding.rules_fingerprint,
        "settlement_fingerprint": binding.settlement_fingerprint,
        "source_id": binding.source_id,
        "venue": binding.venue.value,
        "venue_market_id": binding.venue_market_id,
    }


def _deserialize_settlement_economics_binding(value: object) -> SettlementEconomicsBinding:
    if not isinstance(value, Mapping):
        raise SettlementEconomicsUnscorableError("settlement binding must be an object")
    record = dict(value)
    _require_exact_keys(
        "settlement binding",
        record,
        {
            "account_party_id_sha256",
            "authoritative_observation_sha256",
            "authoritative_payload_sha256",
            "contract_fingerprint",
            "rules_fingerprint",
            "settlement_fingerprint",
            "source_id",
            "venue",
            "venue_market_id",
        },
    )
    try:
        venue = Venue(str(record["venue"]))
    except ValueError as exc:
        raise SettlementEconomicsUnscorableError("unsupported binding venue") from exc
    return SettlementEconomicsBinding(
        venue=venue,
        venue_market_id=_require_text("binding venue_market_id", record["venue_market_id"]),
        account_party_id_sha256=_require_sha256(
            "binding account_party_id_sha256",
            record["account_party_id_sha256"],
        ),
        contract_fingerprint=_require_text("binding contract_fingerprint", record["contract_fingerprint"]),
        rules_fingerprint=_require_text("binding rules_fingerprint", record["rules_fingerprint"]),
        settlement_fingerprint=_require_text("binding settlement_fingerprint", record["settlement_fingerprint"]),
        authoritative_observation_sha256=_require_sha256(
            "binding authoritative_observation_sha256",
            record["authoritative_observation_sha256"],
        ),
        authoritative_payload_sha256=_require_sha256(
            "binding authoritative_payload_sha256",
            record["authoritative_payload_sha256"],
        ),
        source_id=_require_text("binding source_id", record["source_id"]),
    )


@dataclass(frozen=True)
class SettlementFeeReceipt:
    """Exact fee fact parsed from one hash-bound account settlement message."""

    profile: SettlementFeeReceiptProfile
    source_payload_sha256: str
    fee_message_sha256: str
    settlement_fee: Decimal

    def __post_init__(self) -> None:
        if self.profile not in _SUPPORTED_SETTLEMENT_FEE_RECEIPT_PROFILES:
            raise SettlementEconomicsUnscorableError("unknown or unpinned settlement fee receipt profile")
        _require_sha256("fee receipt source_payload_sha256", self.source_payload_sha256)
        _require_sha256("fee receipt message_sha256", self.fee_message_sha256)
        fee = _require_decimal("fee receipt settlement_fee", self.settlement_fee)
        if fee < _ZERO:
            raise SettlementEconomicsUnscorableError("fee receipt settlement_fee must be nonnegative")


def settlement_fee_receipt_record(receipt: SettlementFeeReceipt) -> dict[str, object]:
    if not isinstance(receipt, SettlementFeeReceipt):
        raise SettlementEconomicsUnscorableError("settlement fee receipt must be typed")
    return {
        "fee_message_sha256": receipt.fee_message_sha256,
        "profile": settlement_fee_receipt_profile_record(receipt.profile),
        "settlement_fee_dollars": _decimal_text(receipt.settlement_fee),
        "source_payload_sha256": receipt.source_payload_sha256,
    }


def _deserialize_settlement_fee_receipt(value: object) -> SettlementFeeReceipt:
    if not isinstance(value, Mapping):
        raise SettlementEconomicsUnscorableError("settlement fee receipt must be an object")
    record = dict(value)
    _require_exact_keys(
        "settlement fee receipt",
        record,
        {
            "fee_message_sha256",
            "profile",
            "settlement_fee_dollars",
            "source_payload_sha256",
        },
    )
    return SettlementFeeReceipt(
        profile=_deserialize_settlement_fee_receipt_profile(record["profile"]),
        source_payload_sha256=_require_sha256("fee receipt source_payload_sha256", record["source_payload_sha256"]),
        fee_message_sha256=_require_sha256("fee receipt message_sha256", record["fee_message_sha256"]),
        settlement_fee=_decimal_from_text("fee receipt settlement_fee", record["settlement_fee_dollars"]),
    )


def derive_settlement_fee_receipt(
    *,
    contract: SettlementEconomicsContract,
    binding: SettlementEconomicsBinding,
    source_payload_json: str,
) -> SettlementFeeReceipt:
    """Parse a per-settlement fee from the immutable authoritative payload."""

    validate_settlement_economics_contract(contract, venue=binding.venue)
    profile = contract.settlement_fee_receipt_profile
    if binding.source_id != profile.source_id:
        raise SettlementEconomicsUnscorableError("settlement fee receipt source does not match contract")
    payload = _parse_canonical_object("settlement fee receipt source payload", source_payload_json)
    payload_sha256 = hashlib.sha256(source_payload_json.encode("utf-8")).hexdigest()
    if payload_sha256 != binding.authoritative_payload_sha256:
        raise SettlementEconomicsUnscorableError("settlement fee receipt source payload does not match observation")
    source_market_id = _require_text(
        "settlement fee receipt source payload market_id",
        payload.get(profile.market_id_field),
    )
    if source_market_id != binding.venue_market_id:
        raise SettlementEconomicsUnscorableError("settlement fee receipt market identity does not match binding")
    receipt_value = payload.get(profile.receipt_field)
    if not isinstance(receipt_value, Mapping):
        raise SettlementEconomicsUnscorableError("settlement fee receipt source payload has no fee receipt")
    receipt = dict(receipt_value)
    _require_exact_keys(
        "settlement fee receipt source payload",
        receipt,
        {profile.message_field, profile.message_sha256_field},
    )
    message = receipt[profile.message_field]
    if not isinstance(message, Mapping):
        raise SettlementEconomicsUnscorableError("settlement fee receipt message must be an object")
    message_json = canonical_json(dict(message))
    message_sha256 = _require_sha256("settlement fee receipt message_sha256", receipt[profile.message_sha256_field])
    if hashlib.sha256(message_json.encode("utf-8")).hexdigest() != message_sha256:
        raise SettlementEconomicsUnscorableError("settlement fee receipt message hash does not match")
    message_market_id = _require_text(
        "settlement fee receipt message Symbol",
        message.get(profile.message_market_id_field),
    )
    if message_market_id != binding.venue_market_id:
        raise SettlementEconomicsUnscorableError("settlement fee receipt market identity does not match binding")
    report_id = _require_text(
        "settlement fee receipt MarketSettlementReportID",
        message.get("MarketSettlementReportID"),
    )
    _require_text("settlement fee receipt MarketSettlementReportID", report_id)
    party_entries = message.get("NoMarketSettlementPartyIDs")
    if not isinstance(party_entries, list) or not party_entries:
        raise SettlementEconomicsUnscorableError(
            "settlement fee receipt requires a nonempty customer-account party group"
        )
    matching_parties: list[dict[str, object]] = []
    for raw_party in party_entries:
        if not isinstance(raw_party, Mapping):
            raise SettlementEconomicsUnscorableError("settlement fee receipt party must be an object")
        party = dict(raw_party)
        party_id = _require_text(
            "settlement fee receipt MarketSettlementPartyID",
            party.get("MarketSettlementPartyID"),
        )
        party_id_sha256 = hashlib.sha256(party_id.encode("utf-8")).hexdigest()
        if party_id_sha256 == binding.account_party_id_sha256:
            matching_parties.append(party)
    if len(matching_parties) != 1:
        raise SettlementEconomicsUnscorableError(
            "settlement fee receipt must contain exactly one bound customer-account party"
        )
    party = matching_parties[0]
    if party.get("MarketSettlementPartyRole") != "24":
        raise SettlementEconomicsUnscorableError("settlement fee receipt bound party is not a Customer Account")
    for quantity_name in ("LongQty", "ShortQty"):
        quantity = _decimal_from_text(
            f"settlement fee receipt {quantity_name}",
            party.get(quantity_name),
        )
        if quantity < _ZERO:
            raise SettlementEconomicsUnscorableError(f"settlement fee receipt {quantity_name} must be nonnegative")
    if party.get("NoMiscFees") not in ("1", 1):
        raise SettlementEconomicsUnscorableError("settlement fee receipt must contain one fee entry")
    fee_entries = party.get("MiscFees")
    if not isinstance(fee_entries, list) or len(fee_entries) != 1 or not isinstance(fee_entries[0], Mapping):
        raise SettlementEconomicsUnscorableError("settlement fee receipt must contain one fee object")
    fee_entry = dict(fee_entries[0])
    _require_exact_keys(
        "settlement fee receipt fee object",
        fee_entry,
        {"MiscFeeAmt", "MiscFeeBasis", "MiscFeeCurr", "MiscFeeType"},
    )
    if fee_entry["MiscFeeCurr"] != "USD" or fee_entry["MiscFeeType"] != "4" or fee_entry["MiscFeeBasis"] != "0":
        raise SettlementEconomicsUnscorableError("settlement fee receipt fee object is not a USD exchange fee")
    settlement_fee = _decimal_from_text("settlement fee receipt MiscFeeAmt", fee_entry[profile.fee_field])
    if settlement_fee < _ZERO:
        raise SettlementEconomicsUnscorableError("settlement fee receipt MiscFeeAmt must be nonnegative")
    return SettlementFeeReceipt(
        profile=profile,
        source_payload_sha256=payload_sha256,
        fee_message_sha256=message_sha256,
        settlement_fee=settlement_fee,
    )


@dataclass(frozen=True)
class SettlementCashflows:
    outcome: Literal["yes", "no", "void"]
    gross_payout: Decimal
    settlement_fee: Decimal
    settlement_refund: Decimal
    net_payout: Decimal

    def __post_init__(self) -> None:
        if self.outcome not in ("yes", "no", "void"):
            raise SettlementEconomicsUnscorableError("unsupported settlement outcome")
        for name in (
            "gross_payout",
            "settlement_fee",
            "settlement_refund",
        ):
            value = _require_decimal(name, getattr(self, name))
            if value < _ZERO:
                raise SettlementEconomicsUnscorableError(f"{name} must be nonnegative")
        _require_decimal("net_payout", self.net_payout)
        if self.net_payout != self.gross_payout - self.settlement_fee + self.settlement_refund:
            raise SettlementEconomicsUnscorableError("settlement cashflows do not reconcile")


def settlement_cashflows_record(cashflows: SettlementCashflows) -> dict[str, object]:
    if not isinstance(cashflows, SettlementCashflows):
        raise SettlementEconomicsUnscorableError("settlement cashflows must be typed")
    return {
        "gross_payout_dollars": _decimal_text(cashflows.gross_payout),
        "net_payout_dollars": _decimal_text(cashflows.net_payout),
        "outcome": cashflows.outcome,
        "settlement_fee_dollars": _decimal_text(cashflows.settlement_fee),
        "settlement_refund_dollars": _decimal_text(cashflows.settlement_refund),
    }


def _deserialize_settlement_cashflows(value: object) -> SettlementCashflows:
    if not isinstance(value, Mapping):
        raise SettlementEconomicsUnscorableError("settlement cashflows must be an object")
    record = dict(value)
    _require_exact_keys(
        "settlement cashflows",
        record,
        {
            "gross_payout_dollars",
            "net_payout_dollars",
            "outcome",
            "settlement_fee_dollars",
            "settlement_refund_dollars",
        },
    )
    outcome = record["outcome"]
    if outcome not in ("yes", "no", "void"):
        raise SettlementEconomicsUnscorableError("unsupported settlement outcome")
    return SettlementCashflows(
        outcome=outcome,
        gross_payout=_decimal_from_text("cashflows gross_payout_dollars", record["gross_payout_dollars"]),
        settlement_fee=_decimal_from_text("cashflows settlement_fee_dollars", record["settlement_fee_dollars"]),
        settlement_refund=_decimal_from_text(
            "cashflows settlement_refund_dollars",
            record["settlement_refund_dollars"],
        ),
        net_payout=_decimal_from_text("cashflows net_payout_dollars", record["net_payout_dollars"]),
    )


def validate_settlement_economics_contract(
    contract: SettlementEconomicsContract,
    *,
    venue: Venue,
) -> None:
    if not isinstance(contract, SettlementEconomicsContract):
        raise SettlementEconomicsUnscorableError("settlement contract must be typed")
    if not isinstance(venue, Venue):
        raise SettlementEconomicsUnscorableError("venue must be a supported Venue")
    profile = contract.settlement_fee_receipt_profile
    if profile.venue is not venue:
        raise SettlementEconomicsUnscorableError("settlement fee receipt profile venue does not match settlement")


def derive_settlement_cashflows(
    *,
    contract: SettlementEconomicsContract,
    binding: SettlementEconomicsBinding,
    outcome: MarketOutcome,
    held_side: str,
    quantity: Decimal,
    entry_price: Decimal,
    entry_fee: Decimal,
    void_refund: VoidRefundContract | None,
    fee_receipt: SettlementFeeReceipt,
) -> SettlementCashflows:
    if not isinstance(binding, SettlementEconomicsBinding):
        raise SettlementEconomicsUnscorableError("settlement binding must be typed")
    validate_settlement_economics_contract(contract, venue=binding.venue)
    if not isinstance(fee_receipt, SettlementFeeReceipt):
        raise SettlementEconomicsUnscorableError("settlement fee receipt must be typed")
    if fee_receipt.profile != contract.settlement_fee_receipt_profile:
        raise SettlementEconomicsUnscorableError("settlement fee receipt profile does not match contract")
    if fee_receipt.source_payload_sha256 != binding.authoritative_payload_sha256:
        raise SettlementEconomicsUnscorableError("settlement fee receipt source payload does not match binding")
    if not isinstance(outcome, MarketOutcome):
        raise SettlementEconomicsUnscorableError("outcome must be a MarketOutcome")
    if held_side not in ("yes", "no"):
        raise SettlementEconomicsUnscorableError("held_side must be yes or no")
    quantity = _require_decimal("quantity", quantity)
    entry_price = _require_decimal("entry_price", entry_price)
    entry_fee = _require_decimal("entry_fee", entry_fee)
    if quantity <= _ZERO:
        raise SettlementEconomicsUnscorableError("quantity must be positive")
    if entry_price < _ZERO or entry_price > _ONE:
        raise SettlementEconomicsUnscorableError("entry_price must be in [0, 1]")
    if entry_fee < _ZERO:
        raise SettlementEconomicsUnscorableError("entry_fee must be nonnegative")
    settlement_fee = fee_receipt.settlement_fee

    if outcome in (MarketOutcome.YES, MarketOutcome.NO):
        if void_refund is not None:
            raise SettlementEconomicsUnscorableError("directional settlement cannot include a void refund")
        gross_payout = quantity if held_side == outcome.value else _ZERO
        return SettlementCashflows(
            outcome=outcome.value,
            gross_payout=gross_payout,
            settlement_fee=settlement_fee,
            settlement_refund=_ZERO,
            net_payout=gross_payout - settlement_fee,
        )

    if void_refund is None:
        raise SettlementEconomicsUnscorableError("void settlement requires a refund contract")
    policy = contract.void_refund_policy
    if policy is None:
        raise SettlementEconomicsUnscorableError("void settlement has no pinned refund policy")
    if policy.refunds_entry_fee != void_refund.refunds_entry_fee:
        raise SettlementEconomicsUnscorableError("void refund entry-fee treatment does not match contract")
    if policy.kind == "entry_debit":
        expected_cents = entry_price * Decimal("100")
        if void_refund.refund_cents_per_contract != expected_cents:
            raise SettlementEconomicsUnscorableError("void refund does not match entry-debit policy")
        refund = quantity * entry_price
    else:
        assert policy.refund_cents_per_contract is not None
        if void_refund.refund_cents_per_contract != policy.refund_cents_per_contract:
            raise SettlementEconomicsUnscorableError("void refund does not match fixed refund policy")
        refund = quantity * policy.refund_cents_per_contract / Decimal("100")
    if policy.refunds_entry_fee:
        refund += entry_fee
    if refund > quantity * entry_price + entry_fee:
        raise SettlementEconomicsUnscorableError("void refund exceeds immutable entry debit")
    return SettlementCashflows(
        outcome=MarketOutcome.VOID.value,
        gross_payout=_ZERO,
        settlement_fee=settlement_fee,
        settlement_refund=refund,
        net_payout=refund - settlement_fee,
    )


@dataclass(frozen=True)
class SettlementEconomicsEvidence:
    contract: SettlementEconomicsContract
    binding: SettlementEconomicsBinding
    fee_receipt: SettlementFeeReceipt
    cashflows: SettlementCashflows

    def __post_init__(self) -> None:
        if not isinstance(self.contract, SettlementEconomicsContract):
            raise SettlementEconomicsUnscorableError("settlement contract must be typed")
        if not isinstance(self.binding, SettlementEconomicsBinding):
            raise SettlementEconomicsUnscorableError("settlement binding must be typed")
        if not isinstance(self.fee_receipt, SettlementFeeReceipt):
            raise SettlementEconomicsUnscorableError("settlement fee receipt must be typed")
        if self.fee_receipt.profile != self.contract.settlement_fee_receipt_profile:
            raise SettlementEconomicsUnscorableError("settlement fee receipt profile does not match contract")
        if self.fee_receipt.source_payload_sha256 != self.binding.authoritative_payload_sha256:
            raise SettlementEconomicsUnscorableError("settlement fee receipt source payload does not match binding")
        if not isinstance(self.cashflows, SettlementCashflows):
            raise SettlementEconomicsUnscorableError("settlement cashflows must be typed")


def settlement_economics_evidence_record(
    *,
    contract: SettlementEconomicsContract,
    binding: SettlementEconomicsBinding,
    fee_receipt: SettlementFeeReceipt,
    cashflows: SettlementCashflows,
) -> dict[str, object]:
    evidence = SettlementEconomicsEvidence(
        contract=contract,
        binding=binding,
        fee_receipt=fee_receipt,
        cashflows=cashflows,
    )
    return {
        "binding": settlement_economics_binding_record(evidence.binding),
        "cashflows": settlement_cashflows_record(evidence.cashflows),
        "contract": settlement_economics_contract_record(evidence.contract),
        "contract_sha256": settlement_economics_contract_sha256(evidence.contract),
        "fee_receipt": settlement_fee_receipt_record(evidence.fee_receipt),
        "schema_version": SETTLEMENT_ECONOMICS_SCHEMA_VERSION,
    }


def serialize_settlement_economics_evidence(
    *,
    contract: SettlementEconomicsContract,
    binding: SettlementEconomicsBinding,
    fee_receipt: SettlementFeeReceipt,
    cashflows: SettlementCashflows,
) -> str:
    return canonical_json(
        settlement_economics_evidence_record(
            contract=contract,
            binding=binding,
            fee_receipt=fee_receipt,
            cashflows=cashflows,
        )
    )


def deserialize_settlement_economics_evidence(
    value: str | Mapping[str, object],
) -> SettlementEconomicsEvidence:
    record = _parse_canonical_object("settlement economics evidence", value)
    _require_exact_keys(
        "settlement economics evidence",
        record,
        {
            "binding",
            "cashflows",
            "contract",
            "contract_sha256",
            "fee_receipt",
            "schema_version",
        },
    )
    if record["schema_version"] != SETTLEMENT_ECONOMICS_SCHEMA_VERSION:
        raise SettlementEconomicsUnscorableError("unsupported settlement economics evidence version")
    contract = deserialize_settlement_economics_contract(record["contract"])
    if _require_sha256("contract_sha256", record["contract_sha256"]) != (
        settlement_economics_contract_sha256(contract)
    ):
        raise SettlementEconomicsUnscorableError("settlement economics contract hash does not match")
    return SettlementEconomicsEvidence(
        contract=contract,
        binding=_deserialize_settlement_economics_binding(record["binding"]),
        fee_receipt=_deserialize_settlement_fee_receipt(record["fee_receipt"]),
        cashflows=_deserialize_settlement_cashflows(record["cashflows"]),
    )
