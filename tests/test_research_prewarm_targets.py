from __future__ import annotations

from utils.research_prewarm_targets import (
    DEFAULT_TARGET_REASONS,
    DEFAULT_TARGET_RESEARCH_SKIP_REASONS,
    DEFAULT_TARGET_RESEARCH_STATUSES,
    keyword_count,
    record_targets_kalshi_research_prewarm,
    record_targets_research_prewarm,
)


def test_default_research_skip_targets_include_retryable_research_gaps():
    assert "no_keywords" in DEFAULT_TARGET_REASONS
    assert "missing_resolution_source" in DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    assert "official_data_pending" in DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    assert "insufficient_corroboration" in DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    assert "probability_direction_conflict" in DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    assert "no_trade_capital_protection" not in DEFAULT_TARGET_RESEARCH_SKIP_REASONS


def test_default_research_status_targets_keep_nonterminal_results_researching():
    assert "needs_research" in DEFAULT_TARGET_RESEARCH_STATUSES
    assert "needs_counter_evidence" in DEFAULT_TARGET_RESEARCH_STATUSES
    assert "continue_researching" in DEFAULT_TARGET_RESEARCH_STATUSES
    assert "trade_candidate" not in DEFAULT_TARGET_RESEARCH_STATUSES
    assert "decision_grade_candidate" not in DEFAULT_TARGET_RESEARCH_STATUSES


def test_analysis_rejected_targets_default_information_gaps():
    assert record_targets_research_prewarm(
        {"type": "ANALYSIS_REJECTED", "reason": "no_keywords"},
    )
    assert record_targets_research_prewarm(
        {
            "type": "ANALYSIS_REJECTED",
            "reason": "researched_no_edge",
            "research_skip_reason": "missing_resolution_source",
        },
    )
    assert record_targets_research_prewarm(
        {
            "type": "ANALYSIS_REJECTED",
            "reason": "researched_no_edge",
            "research_skip_reason": "official_data_pending",
        },
    )
    assert not record_targets_research_prewarm(
        {
            "type": "ANALYSIS_REJECTED",
            "reason": "researched_no_edge",
            "research_skip_reason": "no_trade_capital_protection",
        },
    )


def test_false_neutral_targets_only_sparse_keyword_rows():
    assert record_targets_research_prewarm(
        {
            "type": "MATCH_LLM_REVIEW",
            "verdict": "false_positive_neutral",
            "keyword_count": 1,
        },
    )
    assert not record_targets_research_prewarm(
        {
            "type": "MATCH_LLM_REVIEW",
            "verdict": "false_positive_neutral",
            "keyword_count": 2,
        },
    )


def test_semantic_overlap_targets_regardless_of_keyword_count():
    assert record_targets_research_prewarm(
        {
            "type": "SIGNAL_ANALYSIS_DETAIL",
            "pre_llm_gate_reason": "insufficient_semantic_overlap",
            "keywords": ["midterms", "senate"],
        },
    )


def test_prewarm_result_targets_nonterminal_research_statuses_only():
    assert record_targets_research_prewarm(
        {
            "type": "RESEARCH_PREWARM_RESULT",
            "research_status": "needs_counter_evidence",
        },
    )
    assert not record_targets_research_prewarm(
        {
            "type": "RESEARCH_PREWARM_RESULT",
            "research_status": "trade_candidate",
        },
    )
    assert not record_targets_research_prewarm(
        {
            "type": "RESEARCH_PREWARM_RESULT",
            "research_status": "decision_grade_candidate",
        },
    )


def test_kalshi_prewarm_targets_exclude_probe_and_non_kalshi_records():
    assert not record_targets_kalshi_research_prewarm(
        {
            "type": "SIGNAL_ANALYSIS_DETAIL",
            "ticker": "KXSTARTUP-PROBE",
            "venue": "kalshi",
            "is_synthetic_probe": True,
            "is_startup_probe": True,
            "pre_llm_gate_reason": "insufficient_semantic_overlap",
        },
    )
    assert not record_targets_kalshi_research_prewarm(
        {
            "type": "MATCH_LLM_REVIEW",
            "ticker": "ewc-usse-me-2026-11-03-dem",
            "venue": "polymarket_us",
            "verdict": "false_positive_neutral",
            "keyword_count": 0,
        },
    )
    assert record_targets_kalshi_research_prewarm(
        {
            "type": "MATCH_LLM_REVIEW",
            "ticker": "KX-MISS",
            "venue": "kalshi",
            "verdict": "false_positive_neutral",
            "keyword_count": 0,
        },
    )


def test_custom_empty_reason_set_preserves_cli_match_any_behavior():
    assert record_targets_research_prewarm(
        {"type": "ANALYSIS_REJECTED", "reason": "stale_news"},
        reason_set=set(),
        research_skip_reason_set=set(),
    )


def test_keyword_count_prefers_explicit_count_then_keyword_list():
    assert keyword_count({"keyword_count": "1", "keywords": ["a", "b"]}) == 1
    assert keyword_count({"keywords": ["a", "b"]}) == 2
    assert keyword_count({"keyword_count": "bad"}) is None
