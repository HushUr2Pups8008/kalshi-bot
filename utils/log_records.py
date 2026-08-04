"""Typed log-record dataclasses for structured JSONL trade logging.

`SignalAnalysisDetail` replaces the 46-kwarg flat signature on
`utils.logger.TradeLogStore.log_signal_analysis_detail`. Field names match
the prior kwarg names verbatim so emitted JSON keys are unchanged.

Field order in this dataclass matches the prior emission order in the
logger so `dataclasses.fields()` iteration produces the same on-disk
key order as before.

See: docs/housekeeping/2026-05-09/phase-3-design/p1-03-logger-typed-params.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Literal


SignalAnalysisMethod = Literal["llm", "keyword", "keyword_gate"]


@dataclass(frozen=True)
class SignalAnalysisDetail:
    """One signal-analysis decision record. Immutable after construction.

    Required fields (no default): the minimum context every record carries
    regardless of LLM path, keyword path, or pre-LLM-gate skip.

    Optional fields (default None): populated only on the branches that
    produce them. The logger emits a key only when its value is not None,
    matching the prior `log_signal_analysis_detail` behavior exactly.
    """

    # Required — populated on every record
    ticker: str
    source: str
    headline: str
    method: SignalAnalysisMethod
    keywords: list[str]
    base_probability: float
    final_probability: float
    market_price: float

    # Market identity — optional for backwards compatibility with historical records
    venue: str | None = None

    # Publish/event timing — optional for backwards compatibility with historical records
    publish_ts: str | None = None
    age_at_analysis_seconds: float | None = None
    analysis_threshold_seconds: int | None = None

    # Optional — emitted only when truthy; preserves prior `if keyword_contributions:` guard
    keyword_contributions: list[dict[str, Any]] | None = None

    # LLM result (populated on LLM path)
    llm_direction: str | None = None
    llm_magnitude: str | None = None
    llm_confidence: float | None = None

    # LLM attempt metadata (populated whenever LLM was attempted, even on failure)
    llm_attempted: bool | None = None
    llm_result_used: bool | None = None
    llm_result_status: str | None = None
    llm_provider: str | None = None

    # LLM timing telemetry
    llm_latency_ms: int | None = None
    llm_total_stage_ms: int | None = None
    llm_queue_wait_ms: int | None = None
    llm_http_round_trip_ms: int | None = None
    llm_parse_ms: int | None = None
    llm_http_status: int | None = None
    llm_contention_observed: bool | None = None
    llm_in_flight_at_entry: int | None = None

    # LLM routing outcome
    llm_routing_passed: bool | None = None
    llm_routing_reason: str | None = None

    # Pre-LLM match-quality gate
    pre_llm_quality_pass: bool | None = None
    pre_llm_semantic_overlap_count: int | None = None
    pre_llm_semantic_overlap_ratio: float | None = None
    pre_llm_would_block: bool | None = None
    pre_llm_keyword_override: bool | None = None
    pre_llm_keyword_override_mode: str | None = None
    pre_llm_keyword_signal_strength: float | None = None
    pre_llm_gate_reason: str | None = None
    pre_llm_gate_enforced: bool | None = None

    # Pre-LLM token-level diagnostics
    pre_llm_headline_token_count: int | None = None
    pre_llm_market_token_count: int | None = None
    pre_llm_filtered_stopword_count: int | None = None
    pre_llm_filtered_generic_count: int | None = None
    pre_llm_semantic_token_types: dict[str, int] | None = None

    # Post-LLM movement / utility (LLM path only)
    llm_probability_movement: float | None = None
    llm_useful: bool | None = None
    pre_llm_would_block_and_useful: bool | None = None

    # Probe flags
    is_startup_probe: bool | None = None
    is_synthetic_probe: bool | None = None


@dataclass(frozen=True)
class ExecutedPriceSkipProvenance:
    """Fixed-schema diagnostics for an invalid executed-price handoff."""

    schema_version: int
    origin: str
    requested_side: str
    primary_fault: str
    fault_codes: list[str]
    executed_price_state: str
    observed_executed_price_cents: int | None
    source_quote_state: str
    observed_source_quote_cents: int | None
    market_price_available: bool | None
    source_kind: str
    source_method: str
    source_timestamp_state: str
    source_price_retrieved_at: str | None
    source_price_age_seconds: int | None
    source_price_age_bucket: str
    source_payload_sha256_prefix: str | None

    def as_log_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "origin": self.origin,
            "requested_side": self.requested_side,
            "primary_fault": self.primary_fault,
            "fault_codes": list(self.fault_codes),
            "executed_price_state": self.executed_price_state,
            "observed_executed_price_cents": self.observed_executed_price_cents,
            "source_quote_state": self.source_quote_state,
            "observed_source_quote_cents": self.observed_source_quote_cents,
            "market_price_available": self.market_price_available,
            "source_kind": self.source_kind,
            "source_method": self.source_method,
            "source_timestamp_state": self.source_timestamp_state,
            "source_price_retrieved_at": self.source_price_retrieved_at,
            "source_price_age_seconds": self.source_price_age_seconds,
            "source_price_age_bucket": self.source_price_age_bucket,
            "source_payload_sha256_prefix": self.source_payload_sha256_prefix,
        }


def build_executed_price_skip_provenance(
    *,
    executed_price_cents: object,
    requested_side: object,
    signal_type: object,
    selected_quote_cents: object,
    price_available: object,
    price_source: object,
    price_method: object,
    price_retrieved_at: object,
    raw_payload_hash: object,
    observed_at: object,
    stale_after_seconds: object,
) -> ExecutedPriceSkipProvenance:
    executed_state, executed_value = _price_value_state(executed_price_cents)
    source_state, source_value = _price_value_state(selected_quote_cents)
    side = requested_side if isinstance(requested_side, str) and requested_side in {"yes", "no"} else "unknown"
    available = price_available if isinstance(price_available, bool) else None
    source_kind = (
        price_source
        if isinstance(price_source, str)
        and price_source
        in {"rest_list", "rest_detail", "polymarket_public", "polymarket_us_rest", "unavailable", "other"}
        else "unknown"
    )
    source_method = (
        price_method
        if isinstance(price_method, str)
        and price_method in {"dollars_fixed_point", "legacy_cents", "none", "other"}
        else "unknown"
    )
    timestamp_state, age_seconds, age_bucket = _source_timestamp_state(
        price_retrieved_at,
        observed_at,
        stale_after_seconds,
    )
    secondary: list[str] = []
    if selected_quote_cents is None:
        source_state, source_value = "empty", None
    if source_state == "empty":
        secondary.append("source_quote_empty")
    elif source_state == "zero":
        secondary.append("source_quote_zero")
    elif source_state in {"boolean", "non_integer", "out_of_range_integer"}:
        secondary.append("source_quote_invalid")
    if timestamp_state == "missing":
        secondary.append("source_timestamp_missing")
    elif timestamp_state == "invalid":
        secondary.append("source_timestamp_invalid")
    elif timestamp_state == "future":
        secondary.append("source_timestamp_future")
    if age_bucket in {"stale", "stale_capped"}:
        secondary.append("source_quote_stale")

    if side == "unknown":
        primary = "side_unknown"
    elif executed_state == "missing":
        primary = "executed_price_missing"
    elif executed_state == "zero":
        primary = "executed_price_zero"
    elif executed_state != "valid":
        primary = "executed_price_invalid"
    elif source_state == "empty":
        primary = "source_quote_empty"
    elif source_state == "zero":
        primary = "source_quote_zero"
    elif source_state != "valid" and available is not False:
        primary = "source_quote_invalid"
    else:
        primary = "unknown"
    fault_codes = [primary, *[fault for fault in secondary if fault != primary]][:4]
    return ExecutedPriceSkipProvenance(
        schema_version=1,
        origin=signal_type
        if isinstance(signal_type, str)
        and signal_type in {"news", "research_decision_grade", "fade_tweet", "price_fade", "other"}
        else "unknown",
        requested_side=side,
        primary_fault=primary,
        fault_codes=fault_codes,
        executed_price_state=executed_state,
        observed_executed_price_cents=executed_value,
        source_quote_state=source_state,
        observed_source_quote_cents=source_value,
        market_price_available=available,
        source_kind=source_kind,
        source_method=source_method,
        source_timestamp_state=timestamp_state,
        source_price_retrieved_at=price_retrieved_at if isinstance(price_retrieved_at, str) else None,
        source_price_age_seconds=age_seconds,
        source_price_age_bucket=age_bucket,
        source_payload_sha256_prefix=(
            raw_payload_hash[:16]
            if isinstance(raw_payload_hash, str)
            and re.fullmatch(r"[0-9a-f]{64}", raw_payload_hash)
            else None
        ),
    )


def _price_value_state(value: object) -> tuple[str, int | None]:
    if value is None:
        return "missing", None
    if isinstance(value, bool):
        return "boolean", None
    if not isinstance(value, int):
        return "non_integer", None
    if value == 0:
        return "zero", 0
    if 1 <= value <= 99:
        return "valid", value
    return "out_of_range_integer", value if -100 <= value <= 200 else None


def _source_timestamp_state(
    retrieved_at: object,
    observed_at: object,
    stale_after_seconds: object,
) -> tuple[str, int | None, str]:
    if retrieved_at is None:
        return "missing", None, "unknown"
    if not isinstance(retrieved_at, str) or not isinstance(observed_at, str):
        return "unknown", None, "unknown"
    try:
        retrieved = datetime.fromisoformat(retrieved_at)
        observed = datetime.fromisoformat(observed_at)
    except ValueError:
        return "invalid", None, "unknown"
    if retrieved.tzinfo is None or observed.tzinfo is None:
        return "invalid", None, "unknown"
    retrieved = retrieved.astimezone(timezone.utc)
    observed = observed.astimezone(timezone.utc)
    age = (observed - retrieved).total_seconds()
    if age < 0:
        return "future", None, "unknown"
    try:
        stale_after = int(stale_after_seconds)
    except (TypeError, ValueError):
        stale_after = 60
    age_seconds = min(int(age), 86400)
    if age > 86400:
        return "present", age_seconds, "stale_capped"
    return (
        "present",
        age_seconds,
        "stale" if age > max(0, stale_after) else "fresh",
    )
