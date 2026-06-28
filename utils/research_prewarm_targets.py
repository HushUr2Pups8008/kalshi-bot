"""Pure trade-log targeting helpers for research prewarm repair."""

from __future__ import annotations

from typing import Any

DEFAULT_TARGET_REASONS = (
        "no_keywords",
        "research_incomplete",
        "research_operational_error",
)

DEFAULT_TARGET_RESEARCH_SKIP_REASONS = (
        "ambiguous_direction",
        "cached_dossier_insufficient",
        "cached_dossier_unvetted",
        "direction_reason_conflict",
        "insufficient_corroboration",
        "missing_estimated_probability",
        "missing_resolution_source",
        "new_market",
        "no_research_hits",
        "probability_direction_conflict",
        "research_timeout",
        "research_provider_error",
        "research_adjudicator_error",
)

RESEARCH_PREWARM_EVENT_TYPES = frozenset(
    {
        "ANALYSIS_REJECTED",
        "MATCH_LLM_REVIEW",
        "SIGNAL_ANALYSIS_DETAIL",
    }
)

MAX_REPAIR_KEYWORD_COUNT = 1


def keyword_count(record: dict[str, Any]) -> int | None:
    raw_count = record.get("keyword_count")
    if raw_count is not None:
        try:
            return int(raw_count)
        except (TypeError, ValueError):
            return None
    keywords = record.get("keywords")
    if isinstance(keywords, list):
        return len(keywords)
    return None


def sparse_keyword_record(record: dict[str, Any]) -> bool:
    count = keyword_count(record)
    return count is not None and count <= MAX_REPAIR_KEYWORD_COUNT


def record_targets_research_prewarm(
    record: dict[str, Any],
    *,
    reason_set: set[str] | frozenset[str] | tuple[str, ...] | None = (
        DEFAULT_TARGET_REASONS
    ),
    research_skip_reason_set: set[str] | frozenset[str] | tuple[str, ...] | None = (
        DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    ),
) -> bool:
    event_type = str(record.get("type") or "").strip()
    if event_type == "ANALYSIS_REJECTED":
        reason = str(record.get("reason") or "").strip()
        research_skip_reason = str(record.get("research_skip_reason") or "").strip()
        matches_reason = not reason_set or reason in reason_set
        matches_research_skip = (
            bool(research_skip_reason_set)
            and research_skip_reason in research_skip_reason_set
        )
        return matches_reason or matches_research_skip
    if event_type == "MATCH_LLM_REVIEW":
        return (
            str(record.get("verdict") or "").strip() == "false_positive_neutral"
            and sparse_keyword_record(record)
        )
    if event_type == "SIGNAL_ANALYSIS_DETAIL":
        return (
            str(record.get("pre_llm_gate_reason") or "").strip()
            == "insufficient_semantic_overlap"
        )
    return False
