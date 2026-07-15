"""Default-off, decision-time capture for capital-guard shadow evidence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import json
import re
from typing import Any, Mapping

from analysis import SignalAnalysis
from analysis.decision_blender import BlendResult
from config import cfg
from tasks.trade_readiness_gate import (
    G1_CONFIDENCE_THRESHOLD,
    G1_FAILSAFE_CONFIDENCE_THRESHOLD,
    G2_MIN_SOURCE_CLASSES,
    G3_DISAGREEMENT_THRESHOLD,
    G3_FAILSAFE_DISAGREEMENT_THRESHOLD,
    G3_OVERRIDE_BAND_START,
    G3_OVERRIDE_MULTIPLIER,
    G4_REGIME_CONFIDENCE_THRESHOLD,
    G6_RECENCY_THRESHOLD,
    G7_MAX_OPEN_EXPOSURE_DRAWDOWN_PCT,
    G7_MIN_MARKET_LIQUIDITY_DOLLARS,
    ReadinessDecision,
)
from trading.capital_guard_shadow import (
    CapitalGuardCandidate,
    CapitalGuardCaptureAttempt,
    CapitalGuardShadowStore,
    canonical_json,
)
from trading.fees import (
    FeeRole,
    fee_coefficient_for,
    fee_schedule_at,
    fee_type_for_schedule,
    serialize_fee_schedule,
)
from trading.venue import Venue, normalize_venue


_SHA256 = re.compile(r"[0-9a-f]{64}")
_FILL_POLICY_SOURCE_SHA256 = hashlib.sha256(
    b"capital-guard-shadow-full-depth-immediate-or-cancel-v1"
).hexdigest()


class CapitalGuardShadowCaptureError(RuntimeError):
    """Capture persistence failed or produced a conflicting retry."""


@dataclass(frozen=True)
class CapitalGuardShadowCaptureEnvelope:
    analysis: SignalAnalysis
    blend_result: BlendResult
    readiness_decision: ReadinessDecision
    readiness_input: Mapping[str, Any]
    regime_weights: Mapping[str, float]
    regime_confidence: float
    trade_blocked_reason: str | None
    venue: str
    market_family: str
    lifecycle_id: str | None
    decision_at: datetime
    default_min_edge: float


@dataclass(frozen=True)
class CapitalGuardShadowCaptureResult:
    attempt_status: str
    candidate_status: str | None


class CapitalGuardShadowCaptureSink:
    """Persist shadow attempts without blocking the event loop on SQLite."""

    def __init__(self, store: CapitalGuardShadowStore) -> None:
        self._store = store

    async def capture(
        self, envelope: CapitalGuardShadowCaptureEnvelope
    ) -> CapitalGuardShadowCaptureResult:
        return await asyncio.to_thread(self._capture_sync, envelope)

    def _capture_sync(
        self, envelope: CapitalGuardShadowCaptureEnvelope
    ) -> CapitalGuardShadowCaptureResult:
        attempt, candidate = _records(envelope)
        attempt_result = self._store.append_capture_attempt(attempt)
        if attempt_result.status == "conflict":
            raise CapitalGuardShadowCaptureError("capture attempt conflict")
        if candidate is None:
            return CapitalGuardShadowCaptureResult(attempt_result.status, None)
        candidate_result = self._store.append_candidate(candidate)
        if candidate_result.status == "conflict":
            raise CapitalGuardShadowCaptureError("candidate conflict")
        return CapitalGuardShadowCaptureResult(
            attempt_result.status,
            candidate_result.status,
        )


def _records(
    envelope: CapitalGuardShadowCaptureEnvelope,
) -> tuple[CapitalGuardCaptureAttempt, CapitalGuardCandidate | None]:
    if "G7_open_exposure_drawdown" not in envelope.readiness_decision.failure_reasons:
        raise ValueError("capture requires a G7 open-exposure drawdown failure")
    if envelope.decision_at.tzinfo is None or envelope.decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    decision_at = envelope.decision_at.astimezone(UTC)
    market = envelope.analysis.market
    venue = normalize_venue(envelope.venue)
    venue_market_id = str(
        getattr(market, "report_venue_market_id", None) or market.ticker
    ).strip()
    side = str(envelope.analysis.side).strip().lower()
    if side not in {"yes", "no"}:
        raise ValueError("capture side must be yes or no")

    reasons: list[str] = []
    lifecycle_id = _clean_text(envelope.lifecycle_id)
    if lifecycle_id is None:
        lifecycle_id = _sha256_json(
            {
                "decision_at": decision_at.isoformat(),
                "side": side,
                "venue": venue.value,
                "venue_market_id": venue_market_id,
            }
        )
        reasons.append("missing_lifecycle_id")
    decision_key = _sha256_json(
        {
            "blended_probability": _decimal_text(envelope.blend_result.blended_p),
            "decision_at": decision_at.isoformat(),
            "failures": list(envelope.readiness_decision.failure_reasons),
            "lifecycle_id": lifecycle_id,
            "side": side,
            "venue": venue.value,
            "venue_market_id": venue_market_id,
        }
    )
    failures = tuple(envelope.readiness_decision.failure_reasons)
    blocker = envelope.trade_blocked_reason
    non_gate_blocker = (
        blocker if blocker is not None and blocker not in failures else None
    )

    gate_inputs_json = _gate_inputs_json(envelope, side, reasons)
    gate_results_json = _gate_results_json(envelope.readiness_decision)
    identity_json = _identity_json(
        envelope,
        venue,
        venue_market_id,
        lifecycle_id,
        decision_key,
        reasons,
    )
    book = _book_artifact(envelope, side, reasons)
    sizing = _sizing_artifact(envelope, book, reasons)
    fee = _fee_artifact(envelope, venue, reasons)

    artifacts = {
        "executable_book": _artifact_metadata(book[0] if book is not None else None),
        "fee_provenance": _artifact_metadata(fee[0] if fee is not None else None),
        "gate_inputs": _artifact_metadata(gate_inputs_json),
        "gate_results": _artifact_metadata(gate_results_json),
        "identity": _artifact_metadata(identity_json),
        "sizing": _artifact_metadata(sizing[0] if sizing is not None else None),
    }
    partial_artifacts_json = canonical_json(
        {"artifacts": artifacts, "schema_version": 1}
    )

    candidate: CapitalGuardCandidate | None = None
    requested_stake = sizing[2] if sizing is not None else None
    if not reasons and book is not None and sizing is not None and fee is not None:
        executable_book_json, executable_price, _executable_depth = book
        sizing_json, requested_quantity, capital_at_risk = sizing
        fee_provenance_json, fee_provenance_sha256, schedule_json, fee_role, fee_multiplier, fee_coefficient, fee_precision, fee_accumulator = fee
        try:
            candidate = CapitalGuardCandidate(
                decision_key=decision_key,
                lifecycle_id=lifecycle_id,
                decision_at=decision_at,
                captured_at=decision_at,
                venue=venue,
                venue_market_id=venue_market_id,
                market_family=envelope.market_family,
                side=side,
                ordered_failures=failures,
                non_gate_blocker=non_gate_blocker,
                gate_inputs_json=gate_inputs_json,
                gate_results_json=gate_results_json,
                identity_json=identity_json,
                executable_book_json=executable_book_json,
                book_observed_at=market.book_as_of.astimezone(UTC),
                book_source=str(market.price_source),
                book_method=str(market.price_method),
                book_payload_sha256=str(market.book_payload_hash),
                expected_probability=_selected_probability(
                    envelope.blend_result.blended_p, side
                ),
                executable_price=executable_price,
                executable_quantity=requested_quantity,
                gross_edge=_selected_probability(
                    envelope.blend_result.blended_p, side
                ) - executable_price,
                sizing_json=sizing_json,
                fill_policy_json=_fill_policy_json(
                    lifecycle_id,
                    str(market.book_payload_hash),
                    executable_price,
                    requested_quantity,
                ),
                fee_schedule_json=schedule_json,
                fee_provenance_json=fee_provenance_json,
                fee_provenance_sha256=fee_provenance_sha256,
                fee_formula_type=str(market.fee_type),
                fee_role=fee_role,
                fee_multiplier=fee_multiplier,
                fee_coefficient=fee_coefficient,
                fee_account_precision=fee_precision,
                fee_accumulator=fee_accumulator,
            )
        except (TypeError, ValueError):
            reasons.append("candidate_contract_unscorable")
            candidate = None

    attempt = CapitalGuardCaptureAttempt(
        decision_key=decision_key,
        lifecycle_id=lifecycle_id,
        decision_at=decision_at,
        captured_at=decision_at,
        venue=venue,
        venue_market_id=venue_market_id,
        market_family=envelope.market_family,
        side=side,
        ordered_failures=failures,
        non_gate_blocker=non_gate_blocker,
        target_gate="G7",
        target_failure="G7_open_exposure_drawdown",
        scorable=candidate is not None,
        ordered_unscorable_reasons=tuple(reasons),
        requested_stake=requested_stake,
        partial_artifacts_json=partial_artifacts_json,
    )
    return attempt, candidate


def _gate_inputs_json(
    envelope: CapitalGuardShadowCaptureEnvelope,
    side: str,
    reasons: list[str],
) -> str:
    value = envelope.readiness_input
    required = (
        "source_lane",
        "blended_confidence",
        "disagreement_score",
        "evidence_source_classes",
        "drift_suspect",
        "in_recovery",
        "recency_score",
        "time_to_close_seconds",
        "settlement_source_relevant",
        "open_exposure_drawdown_pct",
    )
    for key in required:
        if value.get(key) is None:
            reasons.append(f"missing_gate_input_{key}")
    blended = _decimal_or_zero(value.get("blended_confidence"))
    regime = _decimal_or_zero(envelope.regime_confidence)
    fail_safe = envelope.readiness_decision.fail_safe_active
    source_lane = str(value.get("source_lane") or "")
    source_classes = value.get("evidence_source_classes")
    if not isinstance(source_classes, list):
        source_classes = []
    settlement_relevant = value.get("settlement_source_relevant")
    if not isinstance(settlement_relevant, bool):
        settlement_relevant = False
    gates = {
        "G1": {
            "blended_confidence": _decimal_text(blended),
            "regime_confidence": _decimal_text(regime),
            "scaled_confidence": _decimal_text(blended * regime),
            "threshold": _decimal_text(
                G1_FAILSAFE_CONFIDENCE_THRESHOLD
                if fail_safe
                else G1_CONFIDENCE_THRESHOLD
            ),
        },
        "G2": {
            "evidence_source_classes": list(source_classes),
            "minimum_source_classes": G2_MIN_SOURCE_CLASSES,
            "source_lane": source_lane,
        },
        "G3": {
            "default_min_edge": _decimal_text(envelope.default_min_edge),
            "disagreement_score": _decimal_text(
                _decimal_or_zero(value.get("disagreement_score"))
            ),
            "override_band_start": _decimal_text(G3_OVERRIDE_BAND_START),
            "override_multiplier": _decimal_text(G3_OVERRIDE_MULTIPLIER),
            "threshold": _decimal_text(
                G3_FAILSAFE_DISAGREEMENT_THRESHOLD
                if fail_safe
                else G3_DISAGREEMENT_THRESHOLD
            ),
        },
        "G4": {
            "regime_confidence": _decimal_text(regime),
            "threshold": _decimal_text(G4_REGIME_CONFIDENCE_THRESHOLD),
        },
        "G5": {
            "drift_suspect": bool(value.get("drift_suspect")),
            "in_recovery": bool(value.get("in_recovery")),
            "source_lane": source_lane,
        },
        "G6": {
            "recency_score": _decimal_text(_decimal_or_zero(value.get("recency_score"))),
            "recency_threshold": _decimal_text(
                envelope.readiness_decision.recency_threshold
                if envelope.readiness_decision.recency_threshold is not None
                else G6_RECENCY_THRESHOLD
            ),
            "settlement_source_relevant": settlement_relevant,
            "source_lane": source_lane,
            "time_to_close_seconds": _decimal_text(
                _decimal_or_zero(value.get("time_to_close_seconds"))
            ),
        },
        "G7": {
            "intended_side": side,
            "market_liquidity_dollars": _optional_decimal_text(
                value.get("market_liquidity_dollars")
            ),
            "market_price_momentum_cents": _optional_decimal_text(
                value.get("market_price_momentum_cents")
            ),
            "max_open_exposure_drawdown_pct": _decimal_text(
                G7_MAX_OPEN_EXPOSURE_DRAWDOWN_PCT
            ),
            "minimum_market_liquidity_dollars": _decimal_text(
                G7_MIN_MARKET_LIQUIDITY_DOLLARS
            ),
            "open_exposure_drawdown_pct": _decimal_text(
                _decimal_or_zero(value.get("open_exposure_drawdown_pct"))
            ),
        },
    }
    return canonical_json({"gates": gates, "schema_version": 1})


def _gate_results_json(readiness: ReadinessDecision) -> str:
    failures = readiness.failure_reasons
    gates = {}
    for number in range(1, 8):
        gate = f"G{number}"
        gate_failures = [
            failure
            for failure in failures
            if failure == gate or failure.startswith(f"{gate}_")
        ]
        applied = gate in readiness.applied_conditions
        gates[gate] = {
            "applied": applied,
            "failure_reasons": gate_failures,
            "passed": not gate_failures,
        }
    return canonical_json({"gates": gates, "schema_version": 1})


def _identity_json(
    envelope: CapitalGuardShadowCaptureEnvelope,
    venue: Venue,
    venue_market_id: str,
    lifecycle_id: str,
    decision_key: str,
    reasons: list[str],
) -> str:
    market = envelope.analysis.market
    if not _clean_text(getattr(market, "title", None)):
        reasons.append("missing_contract_title")
    if not _clean_text(getattr(market, "rules_primary", None)):
        reasons.append("missing_market_rules")
    sources = getattr(market, "settlement_sources", ())
    if not sources:
        reasons.append("missing_settlement_sources")
    source_records = [
        {"domain": source.domain, "label": source.label, "url": source.url}
        for source in sources
    ]
    return canonical_json(
        {
            "alias": market.ticker,
            "contract_fingerprint": _sha256_json(
                {
                    "event_ticker": market.event_ticker,
                    "series_ticker": market.series_ticker,
                    "subtitle": market.subtitle,
                    "title": market.title,
                }
            ),
            "decision_key": decision_key,
            "lifecycle_id": lifecycle_id,
            "rules_fingerprint": _sha256_json(
                {
                    "contract_terms_url": market.contract_terms_url,
                    "early_close_condition": market.early_close_condition,
                    "rules_primary": market.rules_primary,
                    "rules_secondary": market.rules_secondary,
                }
            ),
            "schema_version": 1,
            "settlement_fingerprint": _sha256_json(source_records),
            "venue": venue.value,
            "venue_market_id": venue_market_id,
        }
    )


def _book_artifact(
    envelope: CapitalGuardShadowCaptureEnvelope,
    side: str,
    reasons: list[str],
) -> tuple[str, Decimal, Decimal] | None:
    market = envelope.analysis.market
    reason_count = len(reasons)
    required = {
        "book_as_of": market.book_as_of,
        "book_payload_hash": market.book_payload_hash,
        "book_source": _clean_text(market.price_source),
        "book_method": _clean_text(market.price_method),
    }
    for name, value in required.items():
        if value is None or (name == "book_payload_hash" and not _is_sha256(value)):
            reasons.append(f"missing_{name}")
    book_as_of = market.book_as_of
    if book_as_of is not None and (
        not isinstance(book_as_of, datetime)
        or book_as_of.tzinfo is None
        or book_as_of.utcoffset() is None
        or book_as_of > envelope.decision_at
    ):
        reasons.append("invalid_book_as_of")
    if market.price_source == "unavailable":
        reasons.append("missing_book_source")
    if market.price_method == "none":
        reasons.append("missing_book_method")
    selected = market.yes_bid_levels if side == "yes" else market.no_bid_levels
    opposite = market.no_bid_levels if side == "yes" else market.yes_bid_levels
    if not selected:
        reasons.append("missing_selected_side_bid_depth")
    if not opposite:
        reasons.append("missing_opposite_side_bid_depth")
    if not _valid_book_levels(selected) or not _valid_book_levels(opposite):
        reasons.append("invalid_book_depth")
    cents = market.yes_ask_cents if side == "yes" else market.no_ask_cents
    valid_cents = (
        isinstance(cents, int)
        and not isinstance(cents, bool)
        and 0 < cents < 100
    )
    if not valid_cents:
        reasons.append("missing_executable_price")
    if len(reasons) != reason_count or not valid_cents:
        return None
    bids = sorted(selected, key=lambda item: item[0], reverse=True)
    asks = sorted(
        ((Decimal("1") - price, quantity) for price, quantity in opposite),
        key=lambda item: item[0],
    )
    price = Decimal(cents) / Decimal("100")
    if not asks or asks[0][0] != price:
        reasons.append("executable_price_book_mismatch")
        return None
    book_json = canonical_json(
        {
            "asks": [
                {"price_dollars": _decimal_text(level), "quantity": _decimal_text(qty)}
                for level, qty in asks
            ],
            "bids": [
                {"price_dollars": _decimal_text(level), "quantity": _decimal_text(qty)}
                for level, qty in bids
            ],
            "schema_version": 1,
            "side": side,
        }
    )
    executable_depth = sum((qty for level, qty in asks if level <= price), Decimal("0"))
    return book_json, price, executable_depth


def _sizing_artifact(
    envelope: CapitalGuardShadowCaptureEnvelope,
    book: tuple[str, Decimal, Decimal] | None,
    reasons: list[str],
) -> tuple[str, Decimal, Decimal] | None:
    meta = envelope.analysis.signal_meta or {}
    bankroll = _decimal_metadata(meta, "sizing_bankroll_dollars", reasons)
    max_position = _optional_sizing_cap(
        meta, "shadow_max_position_dollars", reasons
    )
    max_ticker = _optional_sizing_cap(
        meta, "shadow_max_ticker_exposure_dollars", reasons
    )
    if bankroll is not None:
        if max_position is None:
            percent_cap = Decimal(str(cfg.max_bet_pct_bankroll)) * bankroll
            max_position = max(
                Decimal(str(cfg.min_bet_dollars)),
                min(Decimal(str(cfg.max_bet_hard_cap)), percent_cap),
            )
        if max_ticker is None:
            max_ticker = Decimal(str(cfg.max_ticker_exposure_pct)) * bankroll
    step = getattr(envelope.analysis.market, "quantity_step", None)
    if not isinstance(step, Decimal) or not step.is_finite() or step <= 0:
        reasons.append("missing_quantity_step")
    if book is None or bankroll is None or max_position is None or max_ticker is None:
        return None
    price = book[1]
    kelly_fraction = _decimal_or_none(envelope.analysis.kelly_fraction)
    kelly_dollars = _decimal_or_none(envelope.analysis.kelly_dollars)
    capped_dollars = _decimal_or_none(envelope.analysis.capped_dollars)
    if kelly_fraction is None or kelly_dollars is None or capped_dollars is None:
        reasons.append("missing_kelly_sizing")
        return None
    requested_quantity = (
        (capped_dollars / price / step).to_integral_value(rounding=ROUND_FLOOR) * step
    )
    requested_quantity = min(requested_quantity, book[2])
    if requested_quantity <= 0:
        reasons.append("nonpositive_hypothetical_quantity")
        return None
    capital_at_risk = requested_quantity * price
    return (
        canonical_json(
            {
                "bankroll_dollars": _decimal_text(bankroll),
                "capital_at_risk_dollars": _decimal_text(capital_at_risk),
                "capped_dollars": _decimal_text(capped_dollars),
                "kelly_dollars": _decimal_text(kelly_dollars),
                "kelly_fraction": _decimal_text(kelly_fraction),
                "max_position_dollars": _decimal_text(max_position),
                "max_ticker_exposure_dollars": _decimal_text(max_ticker),
                "quantity_method": "floor_to_step",
                "quantity_step": _decimal_text(step),
                "requested_quantity": _decimal_text(requested_quantity),
                "schema_version": 1,
            }
        ),
        requested_quantity,
        capital_at_risk,
    )


def _fee_artifact(
    envelope: CapitalGuardShadowCaptureEnvelope,
    venue: Venue,
    reasons: list[str],
) -> tuple[str, str, str, FeeRole, Decimal, Decimal, Decimal | None, Decimal] | None:
    market = envelope.analysis.market
    meta = envelope.analysis.signal_meta or {}
    reason_count = len(reasons)
    try:
        schedule = fee_schedule_at(venue=venue, timestamp=envelope.decision_at)
    except ValueError:
        reasons.append("missing_pinned_fee_schedule")
        return None
    schedule_json = serialize_fee_schedule(schedule)
    expected_type = fee_type_for_schedule(schedule)
    if market.fee_type != expected_type:
        reasons.append("fee_type_provenance_mismatch")
    if market.fee_effective_at != schedule.effective_from:
        reasons.append("fee_effective_at_provenance_mismatch")
    fee_multiplier = market.fee_multiplier
    if not isinstance(fee_multiplier, Decimal) or not fee_multiplier.is_finite():
        reasons.append("missing_fee_multiplier")
    try:
        fee_role = FeeRole(str(market.fill_role))
    except ValueError:
        reasons.append("missing_fee_role")
        fee_role = FeeRole.TAKER
    if not _is_sha256(market.fee_provenance_hash):
        reasons.append("missing_fee_source_payload_sha256")
    fee_accumulator = _decimal_metadata(meta, "fee_accumulator_dollars", reasons)
    if venue is Venue.KALSHI:
        fee_precision = _decimal_metadata(
            meta, "fee_account_precision_dollars", reasons
        )
    else:
        fee_precision = None
    if (
        len(reasons) != reason_count
        or fee_accumulator is None
        or not isinstance(fee_multiplier, Decimal)
    ):
        return None
    fee_coefficient = fee_coefficient_for(schedule, fee_role)
    provenance_json = canonical_json(
        {
            "account_precision_dollars": (
                _decimal_text(fee_precision) if fee_precision is not None else None
            ),
            "accumulator_dollars": _decimal_text(fee_accumulator),
            "coefficient": _decimal_text(fee_coefficient),
            "effective_at": schedule.effective_from.isoformat(),
            "fee_multiplier": _decimal_text(fee_multiplier),
            "fee_role": fee_role.value,
            "fee_schedule": json.loads(schedule_json),
            "fee_type": expected_type,
            "schema_version": 1,
            "source_payload_sha256": str(market.fee_provenance_hash),
            "venue": venue.value,
        }
    )
    return (
        provenance_json,
        hashlib.sha256(provenance_json.encode("utf-8")).hexdigest(),
        schedule_json,
        fee_role,
        fee_multiplier,
        fee_coefficient,
        fee_precision,
        fee_accumulator,
    )


def _fill_policy_json(
    lifecycle_id: str,
    book_payload_sha256: str,
    price: Decimal,
    quantity: Decimal,
) -> str:
    return canonical_json(
        {
            "allow_partial": True,
            "book_payload_sha256": book_payload_sha256,
            "entry_request_id": f"capital-guard-shadow-{lifecycle_id}",
            "order_id": f"capital-guard-shadow-{lifecycle_id}",
            "order_type": "limit",
            "policy_id": "full-depth-v1",
            "price_limit_dollars": _decimal_text(price),
            "quantity": _decimal_text(quantity),
            "schema_version": 1,
            "source_code_sha256": _FILL_POLICY_SOURCE_SHA256,
            "time_in_force": "immediate_or_cancel",
        }
    )


def _artifact_metadata(value: str | None) -> dict[str, object]:
    return {
        "available": value is not None,
        "payload_sha256": (
            hashlib.sha256(value.encode("utf-8")).hexdigest()
            if value is not None
            else None
        ),
    }


def _valid_book_levels(value: object) -> bool:
    if not isinstance(value, tuple) or not value:
        return False
    return all(
        isinstance(level, tuple)
        and len(level) == 2
        and all(isinstance(item, Decimal) and item.is_finite() for item in level)
        and Decimal("0") < level[0] < Decimal("1")
        and level[1] > 0
        for level in value
    )


def _selected_probability(probability: float, side: str) -> Decimal:
    value = _decimal_or_zero(probability)
    return value if side == "yes" else Decimal("1") - value


def _decimal_metadata(
    meta: Mapping[str, Any], key: str, reasons: list[str]
) -> Decimal | None:
    value = _decimal_or_none(meta.get(key))
    if value is None:
        reasons.append(f"missing_{key}")
    return value


def _optional_sizing_cap(
    meta: Mapping[str, Any], key: str, reasons: list[str]
) -> Decimal | None:
    value = _decimal_or_none(meta.get(key))
    if key in meta and (value is None or value < 0):
        reasons.append(f"invalid_{key}")
    return value if value is not None and value >= 0 else None


def _decimal_or_none(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_or_zero(value: object) -> Decimal:
    return _decimal_or_none(value) or Decimal("0")


def _decimal_text(value: object) -> str:
    parsed = _decimal_or_none(value)
    if parsed is None:
        raise ValueError("decimal value is not finite")
    if parsed == 0:
        return "0"
    text = format(parsed, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _optional_decimal_text(value: object) -> str | None:
    return None if value is None else _decimal_text(value)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _clean_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
