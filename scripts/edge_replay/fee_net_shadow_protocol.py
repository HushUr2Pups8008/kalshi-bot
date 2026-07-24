"""Validate the prospective fee-net shadow cohort protocol without runtime side effects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Final, NoReturn


SCHEMA_VERSION: Final = 1
DEFAULT_PROTOCOL_PATH: Final = (
    Path(__file__).resolve().parents[2] / "docs" / "governance" / "fee-net-shadow-cohort-v1.json"
)
SHIPPED_PROTOCOL_SHA256: Final = "77ba2f8267b7a7dfced63b504b8d720cc4d3824df1d417d4c638f4aeb3048648"

_ROOT_KEYS: Final = frozenset(
    {
        "schema_version",
        "protocol_id",
        "status",
        "declared_at_utc",
        "approval_ref",
        "cohort_window",
        "scope",
        "provenance",
        "acceptance",
        "protocol_hash",
    }
)
_WINDOW_KEYS: Final = frozenset({"start_utc", "end_utc"})
_SCOPE_KEYS: Final = frozenset({"mode", "venue", "book_source", "llm_direction", "side", "gate_policy"})
_GATE_POLICY_KEYS: Final = frozenset({"required_passed", "sole_failure"})
_PROVENANCE_KEYS: Final = frozenset(
    {
        "shadow_schema_version",
        "legacy_versions_rejected",
        "record_mode",
        "required_fields",
        "require_void_refund_policy",
        "require_zero_settlement_refunds",
        "execution_price_minimum_dollars",
        "execution_price_maximum_dollars",
        "execution_price_increment_dollars",
        "require_execution_market_and_side_binding",
        "require_single_paper_account",
        "require_unique_paper_fill_ids_per_account",
        "require_chronological_replay",
        "protected_history_required",
    }
)
_ACCEPTANCE_KEYS: Final = frozenset(
    {
        "minimum_unique_market_ids",
        "minimum_weekly_blocks",
        "minimum_market_families",
        "minimum_scorable_fraction",
        "minimum_scorable_stake_fraction",
        "minimum_venue_scorable_fraction",
        "fee_net_ci_95_lower_bound_dollars",
        "require_fee_net_ci_lower_bound_strictly_positive",
        "max_stressed_chronological_drawdown_fraction",
        "require_non_negative_venue_expectancy",
        "max_market_abs_pnl_fraction",
        "bootstrap",
    }
)
_BOOTSTRAP_KEYS: Final = frozenset({"method", "iterations", "seed"})
_REQUIRED_GATES: Final = ("G1", "G2", "G3", "G4", "G5", "G6")
_REQUIRED_PROVENANCE_FIELDS: Final = (
    "evidence_schema_version",
    "shadow_schema_version",
    "record_type",
    "record_id",
    "protocol_id",
    "protocol_hash",
    "previous_record_hash",
    "recorded_at_utc",
    "candidate_id",
    "candidate_payload_sha256",
    "decision_at_utc",
    "venue",
    "venue_market_id",
    "market_family",
    "book_source",
    "selected_side",
    "mode",
    "fee_provenance_sha256",
    "llm_direction",
    "llm_model_id",
    "llm_prompt_sha256",
    "llm_input_sha256",
    "llm_output_sha256",
    "authoritative_observation_sha256",
    "authoritative_payload_sha256",
    "settlement_economics_contract_sha256",
    "settlement_fee_receipt_sha256",
    "account_party_id_sha256",
    "settlement_payload_sha256",
    "evaluation_payload_sha256",
    "gate_outcomes",
    "execution",
    "settlement",
    "economics",
    "record_hash",
)
_ID_RE: Final = re.compile(r"^[a-z][a-z0-9-]{2,127}$")
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DECIMAL_RE: Final = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
_EVIDENCE_SCHEMA_VERSION: Final = 3
_KALSHI_ENTRY_PRICE_MINIMUM: Final = Decimal("0.01")
_KALSHI_ENTRY_PRICE_MAXIMUM: Final = Decimal("0.99")
_KALSHI_ENTRY_PRICE_INCREMENT: Final = Decimal("0.01")
_EVIDENCE_KEYS: Final = frozenset(
    {
        "evidence_schema_version",
        "shadow_schema_version",
        "record_type",
        "record_id",
        "protocol_id",
        "protocol_hash",
        "previous_record_hash",
        "recorded_at_utc",
        "candidate_id",
        "candidate_payload_sha256",
        "decision_at_utc",
        "mode",
        "venue",
        "venue_market_id",
        "market_family",
        "book_source",
        "selected_side",
        "llm_direction",
        "llm_model_id",
        "llm_prompt_sha256",
        "llm_input_sha256",
        "llm_output_sha256",
        "gate_outcomes",
        "fee_provenance_sha256",
        "authoritative_observation_sha256",
        "authoritative_payload_sha256",
        "settlement_economics_contract_sha256",
        "settlement_fee_receipt_sha256",
        "account_party_id_sha256",
        "settlement_payload_sha256",
        "evaluation_payload_sha256",
        "execution",
        "settlement",
        "economics",
        "record_hash",
    }
)
_GATE_OUTCOME_KEYS: Final = frozenset((*_REQUIRED_GATES, "G7_open_exposure_drawdown"))
_SETTLEMENT_KEYS: Final = frozenset(
    {
        "kind",
        "settled_at_utc",
        "outcome",
        "payout_per_contract_dollars",
        "void_refund_policy_sha256",
        "void_refund_payload_sha256",
    }
)
_EXECUTION_KEYS: Final = frozenset(
    {
        "paper_account_id_sha256",
        "paper_order_id",
        "paper_fill_id",
        "execution_payload_sha256",
        "venue_market_id",
        "side",
        "executed_at_utc",
        "quantity",
        "entry_price_dollars",
    }
)
_ECONOMICS_KEYS: Final = frozenset(
    {
        "gross_entry_debit_dollars",
        "entry_fee_dollars",
        "net_entry_debit_dollars",
        "gross_payout_dollars",
        "settlement_fee_dollars",
        "settlement_refund_dollars",
        "net_payout_dollars",
        "gross_pnl_dollars",
        "fee_net_pnl_dollars",
    }
)
_RECORD_HASH_FIELDS: Final = (
    "candidate_payload_sha256",
    "llm_prompt_sha256",
    "llm_input_sha256",
    "llm_output_sha256",
    "fee_provenance_sha256",
    "authoritative_observation_sha256",
    "authoritative_payload_sha256",
    "settlement_economics_contract_sha256",
    "settlement_fee_receipt_sha256",
    "account_party_id_sha256",
    "settlement_payload_sha256",
    "evaluation_payload_sha256",
)


class FeeNetShadowProtocolError(ValueError):
    """A committed cohort protocol is malformed or insufficiently constrained."""


@dataclass(frozen=True)
class FeeNetShadowProtocol:
    protocol_id: str
    status: str
    declared_at_utc: str
    cohort_start_utc: str
    cohort_end_utc: str
    approval_ref: str
    book_source: str
    llm_direction: str
    side: str
    sole_failure: str
    shadow_schema_version: int
    protocol_hash: str


@dataclass(frozen=True)
class FeeNetShadowEvidenceRecord:
    record_id: str
    record_hash: str
    previous_record_hash: str | None
    candidate_id: str
    recorded_at_utc: str
    decision_at_utc: str
    venue_market_id: str
    paper_account_id_sha256: str
    paper_fill_id: str
    fee_net_pnl_dollars: str

    @property
    def structural_only(self) -> bool:
        return True

    @property
    def promotion_eligible(self) -> bool:
        return False


def _canonical_hash(document: Mapping[str, Any], *, self_hash_field: str) -> str:
    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")
    canonical_document = dict(document)
    canonical_document.pop(self_hash_field, None)
    try:
        encoded = json.dumps(
            canonical_document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FeeNetShadowProtocolError("document cannot be canonically serialized") from exc
    return hashlib.sha256(encoded).hexdigest()


def canonical_protocol_hash(protocol: Mapping[str, Any]) -> str:
    """Hash canonical protocol JSON while excluding its self-referential hash."""
    return _canonical_hash(protocol, self_hash_field="protocol_hash")


def canonical_evidence_record_hash(record: Mapping[str, Any]) -> str:
    """Hash canonical evidence JSON while excluding its self-referential hash."""
    return _canonical_hash(record, self_hash_field="record_hash")


def _exact_keys(raw: object, expected: frozenset[str], *, field_name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise FeeNetShadowProtocolError(f"{field_name} must be an object")
    actual = frozenset(raw)
    unknown = actual - expected
    missing = expected - actual
    if unknown:
        raise FeeNetShadowProtocolError(f"unknown {field_name} keys: {', '.join(sorted(unknown))}")
    if missing:
        raise FeeNetShadowProtocolError(f"missing {field_name} keys: {', '.join(sorted(missing))}")
    return raw


def _text(raw: object, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise FeeNetShadowProtocolError(f"{field_name} must be a non-empty string")
    return raw


def _canonical_utc(raw: object, *, field_name: str) -> datetime:
    value = _text(raw, field_name=field_name)
    if _UTC_RE.fullmatch(value) is None:
        raise FeeNetShadowProtocolError(f"{field_name} must be canonical UTC")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise FeeNetShadowProtocolError(f"{field_name} must be canonical UTC") from exc


def _exact_list(
    raw: object,
    expected: tuple[object, ...],
    *,
    field_name: str,
) -> None:
    if not isinstance(raw, list) or tuple(raw) != expected:
        raise FeeNetShadowProtocolError(f"{field_name} must equal {list(expected)!r}")


def _require_bool(raw: object, *, field_name: str) -> None:
    if type(raw) is not bool or raw is not True:
        raise FeeNetShadowProtocolError(f"{field_name} must be true")


def _integer_at_least(raw: object, *, field_name: str, minimum: int) -> int:
    if type(raw) is not int or raw < minimum:
        raise FeeNetShadowProtocolError(f"{field_name} must be integer >= {minimum}")
    return raw


def _fraction(
    raw: object,
    *,
    field_name: str,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    if type(raw) not in {int, float} or not math.isfinite(float(raw)):
        raise FeeNetShadowProtocolError(f"{field_name} must be finite")
    value = float(raw)
    if value < minimum or value > maximum:
        raise FeeNetShadowProtocolError(f"{field_name} must be between {minimum} and {maximum}")
    return value


def _validate_window(raw: object, *, declared_at: datetime) -> tuple[str, str]:
    window = _exact_keys(raw, _WINDOW_KEYS, field_name="cohort_window")
    start = _canonical_utc(window["start_utc"], field_name="cohort_window.start_utc")
    end = _canonical_utc(window["end_utc"], field_name="cohort_window.end_utc")
    if not declared_at < start < end:
        raise FeeNetShadowProtocolError("cohort_window must satisfy declared_at_utc < start_utc < end_utc")
    return str(window["start_utc"]), str(window["end_utc"])


def _validate_scope(raw: object) -> tuple[str, str, str, str]:
    scope = _exact_keys(raw, _SCOPE_KEYS, field_name="scope")
    if scope["mode"] != "paper_only":
        raise FeeNetShadowProtocolError("scope.mode must be paper_only")
    if scope["venue"] != "kalshi":
        raise FeeNetShadowProtocolError("scope.venue must be kalshi")
    if scope["book_source"] != "rest_detail":
        raise FeeNetShadowProtocolError("scope.book_source must be rest_detail")
    if scope["llm_direction"] != "no":
        raise FeeNetShadowProtocolError("scope.llm_direction must be no")
    if scope["side"] != "no":
        raise FeeNetShadowProtocolError("scope.side must be no")
    gate_policy = _exact_keys(scope["gate_policy"], _GATE_POLICY_KEYS, field_name="scope.gate_policy")
    _exact_list(
        gate_policy["required_passed"],
        _REQUIRED_GATES,
        field_name="scope.gate_policy.required_passed",
    )
    if gate_policy["sole_failure"] != "G7_open_exposure_drawdown":
        raise FeeNetShadowProtocolError("scope.gate_policy.sole_failure must be G7_open_exposure_drawdown")
    return (
        str(scope["book_source"]),
        str(scope["llm_direction"]),
        str(scope["side"]),
        str(gate_policy["sole_failure"]),
    )


def _validate_provenance(raw: object) -> int:
    provenance = _exact_keys(raw, _PROVENANCE_KEYS, field_name="provenance")
    if provenance["shadow_schema_version"] != 3:
        raise FeeNetShadowProtocolError("provenance.shadow_schema_version requires v3")
    _exact_list(
        provenance["legacy_versions_rejected"],
        (1, 2),
        field_name="provenance.legacy_versions_rejected",
    )
    if provenance["record_mode"] != "append_only_hash_linked":
        raise FeeNetShadowProtocolError("provenance.record_mode must be append_only_hash_linked")
    _exact_list(
        provenance["required_fields"],
        _REQUIRED_PROVENANCE_FIELDS,
        field_name="provenance.required_fields",
    )
    _require_bool(
        provenance["require_void_refund_policy"],
        field_name="provenance.require_void_refund_policy",
    )
    _require_bool(
        provenance["require_zero_settlement_refunds"],
        field_name="provenance.require_zero_settlement_refunds",
    )
    if provenance["execution_price_minimum_dollars"] != str(_KALSHI_ENTRY_PRICE_MINIMUM):
        raise FeeNetShadowProtocolError("provenance.execution_price_minimum_dollars must be 0.01")
    if provenance["execution_price_maximum_dollars"] != str(_KALSHI_ENTRY_PRICE_MAXIMUM):
        raise FeeNetShadowProtocolError("provenance.execution_price_maximum_dollars must be 0.99")
    if provenance["execution_price_increment_dollars"] != str(_KALSHI_ENTRY_PRICE_INCREMENT):
        raise FeeNetShadowProtocolError("provenance.execution_price_increment_dollars must be 0.01")
    _require_bool(
        provenance["require_execution_market_and_side_binding"],
        field_name="provenance.require_execution_market_and_side_binding",
    )
    _require_bool(
        provenance["require_single_paper_account"],
        field_name="provenance.require_single_paper_account",
    )
    _require_bool(
        provenance["require_unique_paper_fill_ids_per_account"],
        field_name="provenance.require_unique_paper_fill_ids_per_account",
    )
    _require_bool(
        provenance["require_chronological_replay"],
        field_name="provenance.require_chronological_replay",
    )
    _require_bool(
        provenance["protected_history_required"],
        field_name="provenance.protected_history_required",
    )
    return 3


def _validate_acceptance(raw: object) -> None:
    acceptance = _exact_keys(raw, _ACCEPTANCE_KEYS, field_name="acceptance")
    _integer_at_least(
        acceptance["minimum_unique_market_ids"],
        field_name="acceptance.minimum_unique_market_ids",
        minimum=30,
    )
    _integer_at_least(
        acceptance["minimum_weekly_blocks"],
        field_name="acceptance.minimum_weekly_blocks",
        minimum=4,
    )
    _integer_at_least(
        acceptance["minimum_market_families"],
        field_name="acceptance.minimum_market_families",
        minimum=2,
    )
    _fraction(
        acceptance["minimum_scorable_fraction"],
        field_name="acceptance.minimum_scorable_fraction",
        minimum=0.95,
    )
    _fraction(
        acceptance["minimum_scorable_stake_fraction"],
        field_name="acceptance.minimum_scorable_stake_fraction",
        minimum=0.95,
    )
    _fraction(
        acceptance["minimum_venue_scorable_fraction"],
        field_name="acceptance.minimum_venue_scorable_fraction",
        minimum=0.90,
    )
    if acceptance["fee_net_ci_95_lower_bound_dollars"] != 0:
        raise FeeNetShadowProtocolError("acceptance.fee_net_ci_95_lower_bound_dollars must be 0")
    _require_bool(
        acceptance["require_fee_net_ci_lower_bound_strictly_positive"],
        field_name="acceptance.require_fee_net_ci_lower_bound_strictly_positive",
    )
    _fraction(
        acceptance["max_stressed_chronological_drawdown_fraction"],
        field_name="acceptance.max_stressed_chronological_drawdown_fraction",
        maximum=0.20,
    )
    _require_bool(
        acceptance["require_non_negative_venue_expectancy"],
        field_name="acceptance.require_non_negative_venue_expectancy",
    )
    _fraction(
        acceptance["max_market_abs_pnl_fraction"],
        field_name="acceptance.max_market_abs_pnl_fraction",
        maximum=0.25,
    )
    bootstrap = _exact_keys(acceptance["bootstrap"], _BOOTSTRAP_KEYS, field_name="acceptance.bootstrap")
    if bootstrap["method"] != "clustered_by_market":
        raise FeeNetShadowProtocolError("acceptance.bootstrap.method must be clustered_by_market")
    _integer_at_least(
        bootstrap["iterations"],
        field_name="acceptance.bootstrap.iterations",
        minimum=10_000,
    )
    _integer_at_least(
        bootstrap["seed"],
        field_name="acceptance.bootstrap.seed",
        minimum=0,
    )


def load_fee_net_shadow_protocol(path: Path = DEFAULT_PROTOCOL_PATH) -> FeeNetShadowProtocol:
    """Load a locked, prospective, paper-only protocol; never mutates runtime state."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeeNetShadowProtocolError(f"cannot load fee-net shadow protocol: {path}") from exc
    root = _exact_keys(raw, _ROOT_KEYS, field_name="protocol")
    if type(root["schema_version"]) is not int or root["schema_version"] != SCHEMA_VERSION:
        raise FeeNetShadowProtocolError(f"schema_version must be integer {SCHEMA_VERSION}")
    protocol_id = _text(root["protocol_id"], field_name="protocol_id")
    if _ID_RE.fullmatch(protocol_id) is None:
        raise FeeNetShadowProtocolError("protocol_id is invalid")
    if root["status"] != "locked":
        raise FeeNetShadowProtocolError("status must be locked")
    protocol_hash = _text(root["protocol_hash"], field_name="protocol_hash")
    if _HASH_RE.fullmatch(protocol_hash) is None or protocol_hash != canonical_protocol_hash(root):
        raise FeeNetShadowProtocolError("protocol_hash mismatch")
    declared_at = _canonical_utc(root["declared_at_utc"], field_name="declared_at_utc")
    approval_ref = _text(root["approval_ref"], field_name="approval_ref")
    cohort_start_utc, cohort_end_utc = _validate_window(root["cohort_window"], declared_at=declared_at)
    book_source, llm_direction, side, sole_failure = _validate_scope(root["scope"])
    shadow_schema_version = _validate_provenance(root["provenance"])
    _validate_acceptance(root["acceptance"])
    if protocol_hash != SHIPPED_PROTOCOL_SHA256:
        raise FeeNetShadowProtocolError("protocol_hash does not match code-pinned shipped fee-net protocol")
    return FeeNetShadowProtocol(
        protocol_id=protocol_id,
        status="locked",
        declared_at_utc=str(root["declared_at_utc"]),
        cohort_start_utc=cohort_start_utc,
        cohort_end_utc=cohort_end_utc,
        approval_ref=approval_ref,
        book_source=book_source,
        llm_direction=llm_direction,
        side=side,
        sole_failure=sole_failure,
        shadow_schema_version=shadow_schema_version,
        protocol_hash=protocol_hash,
    )


def _sha256(raw: object, *, field_name: str) -> str:
    value = _text(raw, field_name=field_name)
    if _HASH_RE.fullmatch(value) is None:
        raise FeeNetShadowProtocolError(f"{field_name} must be a lowercase SHA-256")
    return value


def _decimal(raw: object, *, field_name: str, nonnegative: bool = False) -> Decimal:
    if not isinstance(raw, str) or _DECIMAL_RE.fullmatch(raw) is None:
        raise FeeNetShadowProtocolError(f"{field_name} must be a canonical Decimal string")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise FeeNetShadowProtocolError(f"{field_name} must be a canonical Decimal string") from exc
    if not value.is_finite() or (value == 0 and raw.startswith("-")):
        raise FeeNetShadowProtocolError(f"{field_name} must be a canonical Decimal string")
    if nonnegative and value < 0:
        raise FeeNetShadowProtocolError(f"{field_name} must be non-negative")
    return value


def _validate_gate_outcomes(raw: object) -> None:
    outcomes = _exact_keys(raw, _GATE_OUTCOME_KEYS, field_name="gate_outcomes")
    for gate in _REQUIRED_GATES:
        if outcomes[gate] != "passed":
            raise FeeNetShadowProtocolError(f"gate_outcomes.{gate} must be passed")
    if outcomes["G7_open_exposure_drawdown"] != "failed":
        raise FeeNetShadowProtocolError("gate_outcomes.G7_open_exposure_drawdown must be failed")


def _validate_settlement(raw: object, *, decision_at: datetime) -> tuple[datetime, str, Decimal]:
    settlement = _exact_keys(raw, _SETTLEMENT_KEYS, field_name="settlement")
    if settlement["kind"] == "void":
        raise FeeNetShadowProtocolError("voids are not authorized by the locked protocol")
    if settlement["kind"] != "settled":
        raise FeeNetShadowProtocolError("settlement.kind must be settled")
    if settlement["outcome"] not in {"yes", "no"}:
        raise FeeNetShadowProtocolError("settlement.outcome must be yes or no")
    if settlement["void_refund_policy_sha256"] is not None or settlement["void_refund_payload_sha256"] is not None:
        raise FeeNetShadowProtocolError("settled evidence must not carry void refund artifacts")
    settled_at = _canonical_utc(settlement["settled_at_utc"], field_name="settlement.settled_at_utc")
    if settled_at < decision_at:
        raise FeeNetShadowProtocolError("settlement.settled_at_utc must not precede decision_at_utc")
    payout_per_contract = _decimal(
        settlement["payout_per_contract_dollars"],
        field_name="settlement.payout_per_contract_dollars",
        nonnegative=True,
    )
    if payout_per_contract != Decimal("1"):
        raise FeeNetShadowProtocolError("settlement.payout_per_contract_dollars must be 1")
    return settled_at, str(settlement["outcome"]), payout_per_contract


def _validate_execution(
    raw: object,
    *,
    decision_at: datetime,
    settled_at: datetime,
    venue_market_id: str,
    selected_side: str,
) -> tuple[str, str, int, Decimal]:
    execution = _exact_keys(raw, _EXECUTION_KEYS, field_name="execution")
    paper_account_id_sha256 = _sha256(
        execution["paper_account_id_sha256"], field_name="execution.paper_account_id_sha256"
    )
    _text(execution["paper_order_id"], field_name="execution.paper_order_id")
    paper_fill_id = _text(execution["paper_fill_id"], field_name="execution.paper_fill_id")
    _sha256(execution["execution_payload_sha256"], field_name="execution.execution_payload_sha256")
    if execution["venue_market_id"] != venue_market_id:
        raise FeeNetShadowProtocolError("execution.venue_market_id must match evidence venue_market_id")
    if execution["side"] != selected_side:
        raise FeeNetShadowProtocolError("execution.side must match evidence selected_side")
    executed_at = _canonical_utc(execution["executed_at_utc"], field_name="execution.executed_at_utc")
    if executed_at < decision_at or executed_at > settled_at:
        raise FeeNetShadowProtocolError("execution.executed_at_utc must be between decision_at_utc and settlement")
    quantity = _integer_at_least(execution["quantity"], field_name="execution.quantity", minimum=1)
    entry_price = _decimal(
        execution["entry_price_dollars"],
        field_name="execution.entry_price_dollars",
        nonnegative=True,
    )
    if (
        entry_price < _KALSHI_ENTRY_PRICE_MINIMUM
        or entry_price > _KALSHI_ENTRY_PRICE_MAXIMUM
        or entry_price % _KALSHI_ENTRY_PRICE_INCREMENT != 0
    ):
        raise FeeNetShadowProtocolError(
            "execution.entry_price_dollars must be a whole-cent Kalshi price in [0.01, 0.99]"
        )
    return paper_account_id_sha256, paper_fill_id, quantity, entry_price


def _validate_economics(
    raw: object,
    *,
    quantity: int,
    entry_price: Decimal,
    selected_side: str,
    settlement_outcome: str,
    payout_per_contract: Decimal,
) -> str:
    economics = _exact_keys(raw, _ECONOMICS_KEYS, field_name="economics")
    gross_entry = _decimal(
        economics["gross_entry_debit_dollars"],
        field_name="economics.gross_entry_debit_dollars",
        nonnegative=True,
    )
    entry_fee = _decimal(
        economics["entry_fee_dollars"],
        field_name="economics.entry_fee_dollars",
        nonnegative=True,
    )
    net_entry = _decimal(
        economics["net_entry_debit_dollars"],
        field_name="economics.net_entry_debit_dollars",
        nonnegative=True,
    )
    gross_payout = _decimal(
        economics["gross_payout_dollars"],
        field_name="economics.gross_payout_dollars",
        nonnegative=True,
    )
    settlement_fee = _decimal(
        economics["settlement_fee_dollars"],
        field_name="economics.settlement_fee_dollars",
        nonnegative=True,
    )
    settlement_refund = _decimal(
        economics["settlement_refund_dollars"],
        field_name="economics.settlement_refund_dollars",
        nonnegative=True,
    )
    net_payout = _decimal(
        economics["net_payout_dollars"],
        field_name="economics.net_payout_dollars",
        nonnegative=True,
    )
    gross_pnl = _decimal(economics["gross_pnl_dollars"], field_name="economics.gross_pnl_dollars")
    fee_net_pnl = _decimal(economics["fee_net_pnl_dollars"], field_name="economics.fee_net_pnl_dollars")
    if settlement_refund != Decimal(0):
        raise FeeNetShadowProtocolError("economics.settlement_refund_dollars must be zero for a settled-only cohort")
    if gross_entry <= 0:
        raise FeeNetShadowProtocolError("economics.gross_entry_debit_dollars must be positive")
    if gross_entry != Decimal(quantity) * entry_price:
        raise FeeNetShadowProtocolError(
            "economics.gross_entry_debit_dollars does not match execution quantity and price"
        )
    expected_gross_payout = (
        Decimal(quantity) * payout_per_contract if settlement_outcome == selected_side else Decimal(0)
    )
    if gross_payout != expected_gross_payout:
        raise FeeNetShadowProtocolError(
            "economics.gross_payout_dollars does not match selected side and settlement outcome"
        )
    if net_entry != gross_entry + entry_fee:
        raise FeeNetShadowProtocolError("economics.net_entry_debit_dollars does not reconcile")
    if net_payout != gross_payout - settlement_fee + settlement_refund:
        raise FeeNetShadowProtocolError("economics.net_payout_dollars does not reconcile")
    if gross_pnl != gross_payout - gross_entry:
        raise FeeNetShadowProtocolError("economics.gross_pnl_dollars does not reconcile")
    if fee_net_pnl != net_payout - net_entry:
        raise FeeNetShadowProtocolError("economics.fee_net_pnl_dollars does not reconcile")
    return str(economics["fee_net_pnl_dollars"])


def verify_fee_net_shadow_evidence_record(
    protocol: FeeNetShadowProtocol,
    record: Mapping[str, Any],
    *,
    expected_previous_record_hash: str | None,
) -> FeeNetShadowEvidenceRecord:
    """Validate one untrusted v3 terminal record against the locked protocol.

    Structural validation does not replace trusted-history attestation or source
    receipt verification. Promotion remains blocked until those later boundaries
    exist.
    """
    if not isinstance(protocol, FeeNetShadowProtocol):
        raise TypeError("protocol must be FeeNetShadowProtocol")
    if protocol.protocol_hash != SHIPPED_PROTOCOL_SHA256:
        raise FeeNetShadowProtocolError("protocol must use the code-pinned shipped digest")
    evidence = _exact_keys(record, _EVIDENCE_KEYS, field_name="evidence record")
    if (
        type(evidence["evidence_schema_version"]) is not int
        or evidence["evidence_schema_version"] != _EVIDENCE_SCHEMA_VERSION
    ):
        raise FeeNetShadowProtocolError("evidence_schema_version must be integer 3")
    if type(evidence["shadow_schema_version"]) is not int or evidence["shadow_schema_version"] != 3:
        raise FeeNetShadowProtocolError("shadow_schema_version must be integer 3")
    if evidence["record_type"] != "fee_net_terminal_evaluation":
        raise FeeNetShadowProtocolError("record_type must be fee_net_terminal_evaluation")
    record_hash = _sha256(evidence["record_hash"], field_name="record_hash")
    if record_hash != canonical_evidence_record_hash(evidence):
        raise FeeNetShadowProtocolError("record_hash mismatch")
    if evidence["protocol_id"] != protocol.protocol_id:
        raise FeeNetShadowProtocolError("protocol_id does not match the locked protocol")
    if evidence["protocol_hash"] != protocol.protocol_hash:
        raise FeeNetShadowProtocolError("protocol_hash does not match the locked protocol")
    previous_record_hash = evidence["previous_record_hash"]
    if expected_previous_record_hash is None:
        if previous_record_hash is not None:
            raise FeeNetShadowProtocolError("previous_record_hash must be null for genesis evidence")
    elif previous_record_hash != expected_previous_record_hash:
        raise FeeNetShadowProtocolError("previous_record_hash does not link to the expected record")
    elif _HASH_RE.fullmatch(expected_previous_record_hash) is None:
        raise FeeNetShadowProtocolError("expected_previous_record_hash must be a lowercase SHA-256")
    record_id = _text(evidence["record_id"], field_name="record_id")
    candidate_id = _text(evidence["candidate_id"], field_name="candidate_id")
    recorded_at = _canonical_utc(evidence["recorded_at_utc"], field_name="recorded_at_utc")
    decision_at = _canonical_utc(evidence["decision_at_utc"], field_name="decision_at_utc")
    cohort_start = _canonical_utc(protocol.cohort_start_utc, field_name="cohort_start_utc")
    cohort_end = _canonical_utc(protocol.cohort_end_utc, field_name="cohort_end_utc")
    if not cohort_start <= decision_at < cohort_end:
        raise FeeNetShadowProtocolError("decision_at_utc is outside the locked cohort window")
    if recorded_at < decision_at:
        raise FeeNetShadowProtocolError("recorded_at_utc must not precede decision_at_utc")
    if evidence["mode"] != "paper_only":
        raise FeeNetShadowProtocolError("mode must be paper_only")
    if evidence["venue"] != "kalshi":
        raise FeeNetShadowProtocolError("venue must be kalshi")
    if evidence["book_source"] != protocol.book_source:
        raise FeeNetShadowProtocolError("book_source does not match the locked protocol")
    if evidence["selected_side"] != protocol.side:
        raise FeeNetShadowProtocolError("selected_side does not match the locked protocol")
    if evidence["llm_direction"] != protocol.llm_direction:
        raise FeeNetShadowProtocolError("llm_direction does not match the locked protocol")
    for field_name in ("venue_market_id", "market_family", "llm_model_id"):
        _text(evidence[field_name], field_name=field_name)
    for field_name in _RECORD_HASH_FIELDS:
        _sha256(evidence[field_name], field_name=field_name)
    _validate_gate_outcomes(evidence["gate_outcomes"])
    settled_at, settlement_outcome, payout_per_contract = _validate_settlement(
        evidence["settlement"], decision_at=decision_at
    )
    if recorded_at < settled_at:
        raise FeeNetShadowProtocolError("recorded_at_utc must not precede settlement.settled_at_utc")
    paper_account_id_sha256, paper_fill_id, quantity, entry_price = _validate_execution(
        evidence["execution"],
        decision_at=decision_at,
        settled_at=settled_at,
        venue_market_id=str(evidence["venue_market_id"]),
        selected_side=str(evidence["selected_side"]),
    )
    if paper_account_id_sha256 != evidence["account_party_id_sha256"]:
        raise FeeNetShadowProtocolError("execution.paper_account_id_sha256 must match account_party_id_sha256")
    fee_net_pnl_dollars = _validate_economics(
        evidence["economics"],
        quantity=quantity,
        entry_price=entry_price,
        selected_side=str(evidence["selected_side"]),
        settlement_outcome=settlement_outcome,
        payout_per_contract=payout_per_contract,
    )
    return FeeNetShadowEvidenceRecord(
        record_id=record_id,
        record_hash=record_hash,
        previous_record_hash=previous_record_hash if isinstance(previous_record_hash, str) else None,
        candidate_id=candidate_id,
        recorded_at_utc=str(evidence["recorded_at_utc"]),
        decision_at_utc=str(evidence["decision_at_utc"]),
        venue_market_id=str(evidence["venue_market_id"]),
        paper_account_id_sha256=paper_account_id_sha256,
        paper_fill_id=paper_fill_id,
        fee_net_pnl_dollars=fee_net_pnl_dollars,
    )


def verify_fee_net_shadow_chain(
    protocol: FeeNetShadowProtocol,
    records: Iterable[Mapping[str, Any]],
) -> tuple[FeeNetShadowEvidenceRecord, ...]:
    """Validate an append-only, chronological terminal-evaluation chain."""
    expected_previous_record_hash: str | None = None
    previous_recorded_at: datetime | None = None
    seen_record_ids: set[str] = set()
    seen_record_hashes: set[str] = set()
    seen_candidate_ids: set[str] = set()
    seen_markets: set[str] = set()
    seen_paper_fills: set[tuple[str, str]] = set()
    cohort_paper_account_id_sha256: str | None = None
    verified: list[FeeNetShadowEvidenceRecord] = []
    for raw in records:
        record = verify_fee_net_shadow_evidence_record(
            protocol,
            raw,
            expected_previous_record_hash=expected_previous_record_hash,
        )
        recorded_at = _canonical_utc(record.recorded_at_utc, field_name="recorded_at_utc")
        if previous_recorded_at is not None and recorded_at <= previous_recorded_at:
            raise FeeNetShadowProtocolError("evidence chain recorded_at_utc must be strictly monotonic")
        if record.record_id in seen_record_ids or record.record_hash in seen_record_hashes:
            raise FeeNetShadowProtocolError("evidence chain contains a duplicate record")
        if record.candidate_id in seen_candidate_ids:
            raise FeeNetShadowProtocolError("evidence chain contains a duplicate candidate")
        if record.venue_market_id in seen_markets:
            raise FeeNetShadowProtocolError("evidence chain contains more than one candidate per market")
        if (
            cohort_paper_account_id_sha256 is not None
            and record.paper_account_id_sha256 != cohort_paper_account_id_sha256
        ):
            raise FeeNetShadowProtocolError("evidence chain contains more than one paper account")
        paper_fill_key = (record.paper_account_id_sha256, record.paper_fill_id)
        if paper_fill_key in seen_paper_fills:
            raise FeeNetShadowProtocolError("evidence chain contains a duplicate paper fill")
        seen_record_ids.add(record.record_id)
        seen_record_hashes.add(record.record_hash)
        seen_candidate_ids.add(record.candidate_id)
        seen_markets.add(record.venue_market_id)
        seen_paper_fills.add(paper_fill_key)
        cohort_paper_account_id_sha256 = record.paper_account_id_sha256
        verified.append(record)
        expected_previous_record_hash = record.record_hash
        previous_recorded_at = recorded_at
    return tuple(verified)


def assert_fee_net_shadow_promotion_eligible(
    protocol: FeeNetShadowProtocol,
    records: Iterable[Mapping[str, Any]],
) -> NoReturn:
    """Fail closed until protected-history and authoritative-receipt attesters exist."""
    verify_fee_net_shadow_chain(protocol, records)
    raise FeeNetShadowProtocolError(
        "promotion remains blocked pending trusted protected-history and authoritative receipt attestation"
    )
