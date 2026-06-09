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
