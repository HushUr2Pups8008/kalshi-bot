from __future__ import annotations

from utils.research_prewarm_targets import (
    DEFAULT_TARGET_REASONS,
    DEFAULT_TARGET_RESEARCH_SKIP_REASONS,
    keyword_count,
    record_targets_research_prewarm,
)


def test_default_research_skip_targets_include_retryable_research_gaps():
    assert "no_keywords" in DEFAULT_TARGET_REASONS
    assert "missing_resolution_source" in DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    assert "insufficient_corroboration" in DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    assert "probability_direction_conflict" in DEFAULT_TARGET_RESEARCH_SKIP_REASONS
    assert "no_trade_capital_protection" not in DEFAULT_TARGET_RESEARCH_SKIP_REASONS


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
