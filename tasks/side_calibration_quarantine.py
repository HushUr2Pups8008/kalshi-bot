"""Default-off, decision-time paper-side calibration quarantine capture."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from kalshi import KalshiMarket
from tasks.prequeue_book_provenance import (
    PrequeueBookProvenanceResult,
    fetch_prequeue_book_provenance,
)
from trading.fees import fee_schedule_at, serialize_fee_schedule
from trading.runtime_paper_cohort_attestation import RuntimePaperCohortAttestation
from trading.side_calibration_quarantine import (
    SideCalibrationCapture,
    SideCalibrationCaptureResult,
    SideCalibrationFeeContext,
    SideCalibrationMarketContract,
    SideCalibrationPaperCohort,
    SideCalibrationPolicy,
    SideCalibrationProvenance,
    SideCalibrationQuarantineStore,
    SideCalibrationSizingProvenance,
)
from trading.venue import Venue


_POLICY_ID = "paper-side-calibration-quarantine"
_POLICY_VERSION = "1"
_POLICY_SCHEMA_VERSION = 1
_SHA256_LENGTH = 64


@dataclass(frozen=True)
class SideCalibrationStartupConfig:
    """Nonsecret startup configuration frozen into each capture."""

    feature_enabled: bool
    is_paper_trading: bool
    live_trading_enabled: bool
    paper_cohort_id: str | None
    paper_cohort_kind: str | None


@dataclass(frozen=True)
class SideCalibrationRuntimeProvenance:
    """Typed immutable startup facts used by every quarantined decision."""

    paper_cohort: SideCalibrationPaperCohort
    quarantine_policy: SideCalibrationPolicy
    software_provenance: SideCalibrationProvenance
    config_provenance: SideCalibrationProvenance


@dataclass(frozen=True)
class SideCalibrationPrequeueBookDecision:
    """Frozen result of the read-only prequeue book request."""

    status: str
    venue: str | None
    requested_market_id: str | None
    native_market_id: str | None
    book_market_id: str | None
    book_observed_at: datetime | str | None
    book_payload_hash: str | None
    reason: str | None

    def as_metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "venue": self.venue,
            "requested_market_id": self.requested_market_id,
            "native_market_id": self.native_market_id,
            "book_market_id": self.book_market_id,
            "book_observed_at": (
                self.book_observed_at.isoformat()
                if isinstance(self.book_observed_at, datetime)
                else self.book_observed_at
            ),
            "book_payload_hash": self.book_payload_hash,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SideCalibrationCandidateDecision:
    """Final-candidate facts copied before the runtime yields to persistence."""

    capture_id: str
    lifecycle_id: str | None
    venue: str | None
    ticker: str | None
    native_market_id: str | None
    settlement_alias: str | None
    side: str | None
    model_yes_probability: Decimal | None
    selected_side_probability: Decimal | None
    executed_price: Decimal | None
    derived_gross_edge: Decimal | None
    reported_gross_edge: Decimal | None
    sizing: SideCalibrationSizingProvenance
    fee_context: SideCalibrationFeeContext
    market_contract: SideCalibrationMarketContract


@dataclass(frozen=True)
class SideCalibrationQuarantineDecisionContext:
    """Complete immutable decision-time input for one append-only capture."""

    decision_at: datetime
    candidate: SideCalibrationCandidateDecision
    evidence_ids: tuple[str, ...]
    research_provenance: SideCalibrationProvenance
    dossier_provenance: SideCalibrationProvenance
    run_provenance: SideCalibrationProvenance
    contract_provenance: SideCalibrationProvenance
    prequeue_book_provenance: SideCalibrationPrequeueBookDecision


PrequeueBookProvenanceProvider = Callable[
    [Any],
    Awaitable[PrequeueBookProvenanceResult],
]


@dataclass(frozen=True)
class SideCalibrationQuarantineRuntime:
    """The only runtime objects created after explicit paper-only opt-in."""

    sink: "SideCalibrationQuarantineSink"
    prequeue_book_provenance_provider: PrequeueBookProvenanceProvider


class SideCalibrationQuarantineSink:
    """Persist one frozen decision without reading mutable runtime state."""

    def __init__(
        self,
        store: SideCalibrationQuarantineStore,
        runtime_provenance: SideCalibrationRuntimeProvenance,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._runtime_provenance = runtime_provenance
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def capture(
        self,
        context: SideCalibrationQuarantineDecisionContext,
    ) -> SideCalibrationCaptureResult:
        if not isinstance(context, SideCalibrationQuarantineDecisionContext):
            raise TypeError("context must be SideCalibrationQuarantineDecisionContext")
        return await asyncio.to_thread(self._capture_sync, context)

    def _capture_sync(
        self,
        context: SideCalibrationQuarantineDecisionContext,
    ) -> SideCalibrationCaptureResult:
        captured_at = _utc_datetime(self._now())
        if captured_at is None:
            raise ValueError("capture clock must return a timezone-aware datetime")
        decision_at = _utc_datetime(context.decision_at)
        if decision_at is None:
            raise ValueError("decision_at must be timezone-aware")
        if captured_at < decision_at:
            captured_at = decision_at
        candidate = context.candidate
        prequeue = context.prequeue_book_provenance
        book_observed_at: datetime | str | None = None
        book_payload_sha256: str | None = None
        if prequeue.status == "available":
            book_observed_at = prequeue.book_observed_at
            book_payload_sha256 = prequeue.book_payload_hash
        capture = SideCalibrationCapture(
            capture_id=candidate.capture_id,
            lifecycle_id=candidate.lifecycle_id,
            decision_at=decision_at,
            captured_at=captured_at,
            venue=candidate.venue,
            ticker=candidate.ticker,
            native_market_id=candidate.native_market_id,
            settlement_alias=candidate.settlement_alias,
            side=candidate.side,
            model_yes_probability=candidate.model_yes_probability,
            selected_side_probability=candidate.selected_side_probability,
            executed_price=candidate.executed_price,
            derived_gross_edge=candidate.derived_gross_edge,
            reported_gross_edge=candidate.reported_gross_edge,
            sizing=candidate.sizing,
            book_observed_at=book_observed_at,
            book_payload_sha256=book_payload_sha256,
            evidence_ids=context.evidence_ids,
            research_provenance=context.research_provenance,
            dossier_provenance=context.dossier_provenance,
            run_provenance=context.run_provenance,
            contract_provenance=context.contract_provenance,
            fee_context=candidate.fee_context,
            market_contract=candidate.market_contract,
            paper_cohort=self._runtime_provenance.paper_cohort,
            quarantine_policy=self._runtime_provenance.quarantine_policy,
            software_provenance=self._runtime_provenance.software_provenance,
            config_provenance=self._runtime_provenance.config_provenance,
        )
        return self._store.append_capture(capture)


def build_side_calibration_quarantine_runtime(
    *,
    db_path: str | Path,
    startup_config: SideCalibrationStartupConfig,
    software_version: str | None,
    cohort_attestation: RuntimePaperCohortAttestation | None,
    kalshi_reader: Any,
    polymarket_reader: Any,
) -> SideCalibrationQuarantineRuntime:
    """Initialize the isolated store and read-only provider after opt-in only."""

    runtime_provenance = _runtime_provenance(
        startup_config=startup_config,
        software_version=software_version,
        cohort_attestation=cohort_attestation,
    )
    store = SideCalibrationQuarantineStore(db_path)

    async def provider(analysis: Any) -> PrequeueBookProvenanceResult:
        return await fetch_prequeue_book_provenance(
            analysis,
            kalshi_reader=kalshi_reader,
            polymarket_reader=polymarket_reader,
        )

    return SideCalibrationQuarantineRuntime(
        sink=SideCalibrationQuarantineSink(store, runtime_provenance),
        prequeue_book_provenance_provider=provider,
    )


def build_side_calibration_decision_context(
    *,
    decision_at: datetime,
    candidate: Any,
    dossier: Any | None,
    evidence_ids: tuple[str, ...],
) -> SideCalibrationQuarantineDecisionContext:
    """Copy all final-candidate facts before append-only capture starts."""

    market = getattr(candidate, "market", None)
    analysis = getattr(candidate, "fast_lane_analysis", None)
    signal_meta = _mapping_copy(getattr(candidate, "signal_meta", None))
    venue = _optional_text(_market_venue(market))
    ticker = _optional_text(getattr(market, "ticker", None))
    side = _optional_side(getattr(candidate, "side", None))
    model_yes_probability = _decimal_value(getattr(candidate, "blended_probability", None))
    selected_side_probability = _selected_side_probability(side, model_yes_probability)
    executed_price = _executed_price(getattr(candidate, "executed_price_cents", None))
    derived_gross_edge = _derived_gross_edge(selected_side_probability, executed_price)
    candidate_decision = SideCalibrationCandidateDecision(
        capture_id=_capture_id(
            lifecycle_id=_optional_text(signal_meta.get("lifecycle_id")),
            decision_at=decision_at,
            venue=venue,
            ticker=ticker,
            side=side,
            model_yes_probability=model_yes_probability,
            executed_price=executed_price,
        ),
        lifecycle_id=_optional_text(signal_meta.get("lifecycle_id")),
        venue=venue,
        ticker=ticker,
        native_market_id=_native_market_id(venue, market, ticker),
        settlement_alias=_settlement_alias(venue, market, ticker),
        side=side,
        model_yes_probability=model_yes_probability,
        selected_side_probability=selected_side_probability,
        executed_price=executed_price,
        derived_gross_edge=derived_gross_edge,
        # This is the final candidate's direct calculation, never the upstream
        # fast-lane ``analysis.edge`` value.
        reported_gross_edge=derived_gross_edge,
        sizing=_sizing_provenance(analysis),
        fee_context=_fee_context(
            decision_at=decision_at,
            venue=venue,
            market=market,
        ),
        market_contract=_market_contract(market),
    )
    research_status = _optional_text(signal_meta.get("research_admission_status"))
    run_id = _optional_text(signal_meta.get("research_run_id"))
    contract_fingerprint = _optional_text(signal_meta.get("research_contract_fingerprint"))
    is_research = research_status is not None or run_id is not None or contract_fingerprint is not None
    return SideCalibrationQuarantineDecisionContext(
        decision_at=decision_at,
        candidate=candidate_decision,
        evidence_ids=tuple(evidence_ids),
        research_provenance=_research_provenance(research_status, run_id),
        dossier_provenance=_dossier_provenance(dossier, is_research=is_research),
        run_provenance=_required_provenance("research_run", run_id),
        contract_provenance=_required_provenance("research_contract", contract_fingerprint),
        prequeue_book_provenance=_prequeue_book_provenance(signal_meta),
    )


def _runtime_provenance(
    *,
    startup_config: SideCalibrationStartupConfig,
    software_version: str | None,
    cohort_attestation: RuntimePaperCohortAttestation | None,
) -> SideCalibrationRuntimeProvenance:
    policy_payload = {
        "policy_id": _POLICY_ID,
        "policy_version": _POLICY_VERSION,
        "schema_version": _POLICY_SCHEMA_VERSION,
    }
    policy = SideCalibrationPolicy(
        policy_id=_POLICY_ID,
        policy_version=_POLICY_VERSION,
        schema_version=_POLICY_SCHEMA_VERSION,
        payload_sha256=_sha256_json(policy_payload),
    )
    config_payload = asdict(startup_config)
    config_provenance = _available_provenance(
        "paper-side-calibration-startup-config-v1",
        config_payload,
    )
    version = _optional_text(software_version)
    software_provenance = (
        _available_provenance(f"version:{version}", {"version": version})
        if version is not None
        else _unavailable_provenance("software_version_unavailable")
    )
    return SideCalibrationRuntimeProvenance(
        paper_cohort=_paper_cohort(cohort_attestation),
        quarantine_policy=policy,
        software_provenance=software_provenance,
        config_provenance=config_provenance,
    )


def _paper_cohort(
    attestation: RuntimePaperCohortAttestation | None,
) -> SideCalibrationPaperCohort:
    if (
        isinstance(attestation, RuntimePaperCohortAttestation)
        and attestation.manifest_bound
        and attestation.cohort_kind != "legacy"
    ):
        return SideCalibrationPaperCohort(
            cohort_id=attestation.cohort_id,
            cohort_kind=attestation.cohort_kind,
            cohort_identity=attestation.cohort_identity,
            manifest_sha256=attestation.manifest_sha256,
        )
    return SideCalibrationPaperCohort(
        cohort_id=(
            attestation.cohort_id
            if isinstance(attestation, RuntimePaperCohortAttestation)
            else None
        ),
        cohort_kind=(
            attestation.cohort_kind
            if isinstance(attestation, RuntimePaperCohortAttestation)
            else None
        ),
        cohort_identity=None,
        manifest_sha256=None,
    )


def _research_provenance(
    status: str | None,
    run_id: str | None,
) -> SideCalibrationProvenance:
    if status is None and run_id is None:
        return SideCalibrationProvenance(
            state="not_applicable",
            detail="non_research_candidate",
        )
    if status != "decision_grade_candidate" or run_id is None:
        return _unavailable_provenance("research_admission_unvalidated")
    return _available_provenance(
        run_id,
        {"research_admission_status": status, "research_run_id": run_id},
    )


def _dossier_provenance(
    dossier: Any | None,
    *,
    is_research: bool,
) -> SideCalibrationProvenance:
    if not is_research:
        return SideCalibrationProvenance(
            state="not_applicable",
            detail="non_research_candidate",
        )
    if dossier is None:
        return _unavailable_provenance("decision_time_dossier_unavailable")
    payload = {
        "market_ticker": _optional_text(getattr(dossier, "market_ticker", None)),
        "dossier_version": getattr(dossier, "dossier_version", None),
        "current_estimate": _decimal_text(getattr(dossier, "current_estimate", None)),
        "confidence": _decimal_text(getattr(dossier, "confidence", None)),
        "prior_estimate": _decimal_text(getattr(dossier, "prior_estimate", None)),
        "created_ts": _optional_text(getattr(dossier, "created_ts", None)),
        "updated_ts": _optional_text(getattr(dossier, "updated_ts", None)),
    }
    identifier = (
        f"{payload['market_ticker']}:v{payload['dossier_version']}"
        if payload["market_ticker"] is not None and isinstance(payload["dossier_version"], int)
        else None
    )
    if identifier is None:
        return _unavailable_provenance("decision_time_dossier_identity_unavailable")
    return _available_provenance(identifier, payload)


def _required_provenance(name: str, identifier: str | None) -> SideCalibrationProvenance:
    if identifier is None:
        return _unavailable_provenance(f"{name}_unavailable")
    return _available_provenance(identifier, {"identifier": identifier, "kind": name})


def _prequeue_book_provenance(
    signal_meta: Mapping[str, object],
) -> SideCalibrationPrequeueBookDecision:
    raw = signal_meta.get("prequeue_book_provenance")
    if not isinstance(raw, Mapping):
        return SideCalibrationPrequeueBookDecision(
            status="unavailable",
            venue=None,
            requested_market_id=None,
            native_market_id=None,
            book_market_id=None,
            book_observed_at=None,
            book_payload_hash=None,
            reason="missing_prequeue_book_provenance",
        )
    return SideCalibrationPrequeueBookDecision(
        status=_optional_text(raw.get("status")) or "unavailable",
        venue=_optional_text(raw.get("venue")),
        requested_market_id=_optional_text(raw.get("requested_market_id")),
        native_market_id=_optional_text(raw.get("native_market_id")),
        book_market_id=_optional_text(raw.get("book_market_id")),
        book_observed_at=_optional_datetime(raw.get("book_observed_at")),
        book_payload_hash=_optional_text(raw.get("book_payload_hash")),
        reason=_optional_text(raw.get("reason")),
    )


def _sizing_provenance(analysis: Any | None) -> SideCalibrationSizingProvenance:
    if analysis is None:
        return SideCalibrationSizingProvenance(
            state="unavailable",
            detail="final_candidate_analysis_unavailable",
        )
    requested_stake = _decimal_value(getattr(analysis, "capped_dollars", None))
    financial = getattr(analysis, "decision_financial_provenance", None)
    if requested_stake is None or requested_stake <= 0 or financial is None:
        return SideCalibrationSizingProvenance(
            state="unavailable",
            detail="decision_time_sizing_unavailable",
        )
    payload = {
        "kelly_fraction": _decimal_text(getattr(analysis, "kelly_fraction", None)),
        "kelly_dollars": _decimal_text(getattr(analysis, "kelly_dollars", None)),
        "requested_stake_dollars": _decimal_text(requested_stake),
        "sizing_bankroll_dollars": _decimal_text(
            getattr(financial, "sizing_bankroll_dollars", None)
        ),
        "max_position_dollars": _decimal_text(
            getattr(financial, "max_position_dollars", None)
        ),
        "max_ticker_exposure_dollars": _decimal_text(
            getattr(financial, "max_ticker_exposure_dollars", None)
        ),
        "fee_account_precision_dollars": _decimal_text(
            getattr(financial, "fee_account_precision_dollars", None)
        ),
        "fee_accumulator_dollars": _decimal_text(
            getattr(financial, "fee_accumulator_dollars", None)
        ),
    }
    if any(value is None for key, value in payload.items() if key != "fee_account_precision_dollars"):
        return SideCalibrationSizingProvenance(
            state="unavailable",
            detail="decision_time_sizing_provenance_incomplete",
        )
    return SideCalibrationSizingProvenance(
        state="available",
        method="final_candidate_capped_dollars",
        requested_stake_dollars=requested_stake,
        payload_sha256=_sha256_json(payload),
    )


def _fee_context(
    *,
    decision_at: datetime,
    venue: str | None,
    market: Any | None,
) -> SideCalibrationFeeContext:
    fee_role = _optional_text(getattr(market, "fill_role", None))
    if venue not in {Venue.KALSHI.value, Venue.POLYMARKET_US.value} or fee_role not in {
        "maker",
        "taker",
    }:
        return SideCalibrationFeeContext(
            state="unavailable",
            detail="decision_time_fee_role_or_venue_unavailable",
        )
    try:
        schedule = fee_schedule_at(venue=Venue(venue), timestamp=decision_at)
        schedule_payload = serialize_fee_schedule(schedule)
    except (TypeError, ValueError):
        return SideCalibrationFeeContext(
            state="unavailable",
            detail="decision_time_fee_schedule_unavailable",
        )
    fee_schedule_sha256 = _sha256_json(schedule_payload)
    return SideCalibrationFeeContext(
        state="available",
        fee_role=fee_role,
        fee_schedule_sha256=fee_schedule_sha256,
        provenance_sha256=_sha256_json(
            {
                "decision_at": _timestamp(decision_at),
                "fee_role": fee_role,
                "fee_schedule_sha256": fee_schedule_sha256,
                "venue": venue,
            }
        ),
    )


def _market_contract(market: Any | None) -> SideCalibrationMarketContract:
    canonical_contract = _optional_text(getattr(market, "question", None)) or _optional_text(
        getattr(market, "title", None)
    )
    question = _optional_text(getattr(market, "question", None)) or canonical_contract
    snapshot_hash = _first_sha256(
        getattr(market, "raw_payload_hash", None),
        getattr(market, "source_payload_hash", None),
    )
    close_at = _optional_datetime(getattr(market, "close_time", None))
    settlement_at = _first_datetime(
        getattr(market, "expected_expiration_time", None),
        getattr(market, "expiration_time", None),
    )
    if (
        canonical_contract is None
        or question is None
        or snapshot_hash is None
        or close_at is None
        or settlement_at is None
        or settlement_at < close_at
    ):
        return SideCalibrationMarketContract(
            state="unavailable",
            detail="decision_time_market_contract_metadata_unavailable",
        )
    return SideCalibrationMarketContract(
        state="available",
        canonical_contract=canonical_contract,
        question=question,
        market_snapshot_sha256=snapshot_hash,
        scheduled_close_at=close_at,
        scheduled_settlement_at=settlement_at,
    )


def _capture_id(
    *,
    lifecycle_id: str | None,
    decision_at: datetime,
    venue: str | None,
    ticker: str | None,
    side: str | None,
    model_yes_probability: Decimal | None,
    executed_price: Decimal | None,
) -> str:
    if lifecycle_id is not None:
        return f"side-calibration:{lifecycle_id}"
    return "side-calibration:" + _sha256_json(
        {
            "decision_at": _timestamp(decision_at),
            "executed_price": _decimal_text(executed_price),
            "model_yes_probability": _decimal_text(model_yes_probability),
            "side": side,
            "ticker": ticker,
            "venue": venue,
        }
    )


def _native_market_id(venue: str | None, market: Any | None, ticker: str | None) -> str | None:
    if venue == Venue.KALSHI.value:
        return ticker
    if venue == Venue.POLYMARKET_US.value:
        value = _optional_text(getattr(market, "venue_market_id", None))
        return value if value is not None and value.isdecimal() else None
    return _optional_text(getattr(market, "venue_market_id", None))


def _settlement_alias(venue: str | None, market: Any | None, ticker: str | None) -> str | None:
    if venue == Venue.KALSHI.value:
        return ticker
    if venue == Venue.POLYMARKET_US.value:
        return ticker or _optional_text(getattr(market, "market_id", None))
    return ticker


def _market_venue(market: Any | None) -> object | None:
    if market is None:
        return None
    explicit_venue = getattr(market, "venue", None) or getattr(market, "report_venue", None)
    if explicit_venue is not None:
        return explicit_venue
    if isinstance(market, KalshiMarket):
        return Venue.KALSHI.value
    return None


def _selected_side_probability(side: str | None, model_yes_probability: Decimal | None) -> Decimal | None:
    if side == "yes":
        return model_yes_probability
    if side == "no" and model_yes_probability is not None:
        return Decimal("1") - model_yes_probability
    return None


def _executed_price(value: object) -> Decimal | None:
    cents = _decimal_value(value)
    if cents is None or cents <= 0 or cents >= 100 or cents != cents.to_integral_value():
        return None
    return cents / Decimal("100")


def _derived_gross_edge(
    selected_side_probability: Decimal | None,
    executed_price: Decimal | None,
) -> Decimal | None:
    if selected_side_probability is None or executed_price is None:
        return None
    return selected_side_probability - executed_price


def _available_provenance(identifier: str, payload: Mapping[str, object]) -> SideCalibrationProvenance:
    return SideCalibrationProvenance(
        state="available",
        identifier=identifier,
        payload_sha256=_sha256_json(payload),
    )


def _unavailable_provenance(detail: str) -> SideCalibrationProvenance:
    return SideCalibrationProvenance(state="unavailable", detail=detail)


def _mapping_copy(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _optional_text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        value = enum_value
    text = str(value).strip()
    return text or None


def _optional_side(value: object) -> str | None:
    side = _optional_text(value)
    return side.lower() if side is not None and side.lower() in {"yes", "no"} else None


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc_datetime(value)
    if not isinstance(value, str):
        return None
    try:
        return _utc_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _first_datetime(*values: object) -> datetime | None:
    for value in values:
        parsed = _optional_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _decimal_value(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return decimal if decimal.is_finite() else None


def _decimal_text(value: object) -> str | None:
    decimal = _decimal_value(value)
    return format(decimal, "f") if decimal is not None else None


def _first_sha256(*values: object) -> str | None:
    for value in values:
        text = _optional_text(value)
        if text is not None and _is_sha256(text):
            return text
    return None


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_LENGTH and all(char in "0123456789abcdef" for char in value)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("decimal must be finite")
        return format(value, "f")
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("float must be finite")
        return format(Decimal(str(value)), "f")
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")
