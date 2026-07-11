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
        "official_data_pending",
        "probability_direction_conflict",
        "research_timeout",
        "research_provider_error",
        "research_adjudicator_error",
)

DEFAULT_TARGET_RESEARCH_STATUSES = (
        "continue_researching",
        "needs_counter_evidence",
        "needs_research",
)

RESEARCH_PREWARM_EVENT_TYPES = frozenset(
    {
        "ANALYSIS_REJECTED",
        "MATCH_LLM_REVIEW",
        "RESEARCH_PREWARM_RESULT",
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
    research_status_set: set[str] | frozenset[str] | tuple[str, ...] | None = (
        DEFAULT_TARGET_RESEARCH_STATUSES
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
    if event_type == "RESEARCH_PREWARM_RESULT":
        status = str(record.get("research_status") or "").strip()
        return bool(research_status_set) and status in research_status_set
    return False


def record_targets_kalshi_research_prewarm(
    record: dict[str, Any],
    *,
    reason_set: set[str] | frozenset[str] | tuple[str, ...] | None = (
        DEFAULT_TARGET_REASONS
    ),
    research_skip_reason_set: set[str] | frozenset[str] | tuple[str, ...] | None = (
        DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    ),
    research_status_set: set[str] | frozenset[str] | tuple[str, ...] | None = (
        DEFAULT_TARGET_RESEARCH_STATUSES
    ),
) -> bool:
    """Return whether a trade-log row is actionable for Kalshi prewarm repair."""

    if not record_targets_research_prewarm(
        record,
        reason_set=reason_set,
        research_skip_reason_set=research_skip_reason_set,
        research_status_set=research_status_set,
    ):
        return False
    if record.get("is_synthetic_probe") is True or record.get("is_startup_probe") is True:
        return False
    venue = str(record.get("venue") or "kalshi").strip().lower()
    return not venue or venue == "kalshi"
