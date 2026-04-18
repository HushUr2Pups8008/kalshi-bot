from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.pipeline_impact_audit import collect_window_metrics, render_report, resolve_windows


def test_resolve_windows_with_explicit_dates():
    current, previous = resolve_windows(
        since=datetime(2026, 4, 10, tzinfo=timezone.utc),
        until=datetime(2026, 4, 11, 23, 59, 59, tzinfo=timezone.utc),
        hours=24,
    )

    assert current[0] == datetime(2026, 4, 10, tzinfo=timezone.utc)
    assert current[1] == datetime(2026, 4, 11, 23, 59, 59, tzinfo=timezone.utc)
    assert previous[1] < current[0]


def test_collect_window_metrics_uses_summary_modules(monkeypatch):
    monkeypatch.setattr(
        "scripts.pipeline_impact_audit.match_quality_diagnostics.summarize",
        lambda *args, **kwargs: {
            "match_records": 10,
            "low_quality_matches": 2,
        },
    )
    monkeypatch.setattr(
        "scripts.pipeline_impact_audit.decision_funnel_summary.summarize",
        lambda *args, **kwargs: {
            "event_counts": {
                "MATCH_SUPPRESSION_CANDIDATE": 3,
                "MATCH_SUPPRESSED": 1,
            },
        },
    )
    monkeypatch.setattr(
        "scripts.pipeline_impact_audit.signal_edge_diagnostics.summarize",
        lambda *args, **kwargs: {
            "counts": {
                "SIGNAL_ANALYSIS_DETAIL": 6,
                "OPPORTUNITY": 4,
                "SKIPPED": 3,
                "EXECUTED": 1,
            },
            "skip_breakdown": {
                "zero_edge": 1,
                "below_threshold": 1,
                "duplicate": 1,
                "other": 0,
            },
            "audit_rows": [
                {"method": "llm", "llm_attempted": True, "llm_result_used": True, "llm_result_status": "ollama_success"},
                {"method": "llm", "llm_attempted": True, "llm_result_used": True, "llm_result_status": "anthropic_success"},
                {"method": "keyword", "llm_attempted": True, "llm_result_used": False, "llm_result_status": "ollama_timeout", "llm_total_stage_ms": 41000, "llm_queue_wait_ms": 5000, "llm_http_round_trip_ms": 35000},
                {"method": "keyword_gate", "llm_attempted": False, "llm_result_used": False, "llm_result_status": "no_provider_available"},
                {"method": "keyword", "llm_attempted": False, "llm_result_used": False, "llm_result_status": "llm_skipped_routing_price_band_excluded"},
                {
                    "method": "keyword_gate",
                    "ticker": "KXIRAN",
                    "source": "Reuters",
                    "headline": "Weak match blocked",
                    "pre_llm_quality_pass": False,
                    "pre_llm_would_block": True,
                    "pre_llm_keyword_override": False,
                    "pre_llm_gate_reason": "weak_semantic_overlap",
                    "pre_llm_gate_enforced": True,
                    "pre_llm_would_block_and_useful": False,
                    "llm_result_status": "llm_skipped_match_quality_gate",
                    "ts": datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc),
                },
                {
                    "method": "llm",
                    "ticker": "KXPROBE",
                    "source": "startup_probe",
                    "headline": "Probe",
                    "pre_llm_quality_pass": False,
                    "pre_llm_would_block": False,
                    "pre_llm_keyword_override": True,
                    "pre_llm_gate_reason": "weak_semantic_overlap",
                    "is_startup_probe": True,
                },
            ],
            "llm_observability": {
                "total_stage_ms_samples": [41000],
                "queue_wait_ms_samples": [5000],
                "http_round_trip_ms_samples": [35000],
                "contention_observed": 1,
                "skipped_routing": 1,
                "skipped_routing_reasons": Counter({"price_band_excluded": 1}),
            },
            "llm_value_add": {
                "llm_rows": 2,
                "near_neutral_outputs": 1,
                "non_zero_edge_outputs": 1,
                "meaningful_signals": 1,
                "trade_candidates": 0,
                "llm_created_edge": 1,
                "probability_movement_buckets": Counter({"near_neutral": 1, "weak": 1}),
                "edge_magnitude_buckets": Counter({"zero_neutral": 1, "weak": 1}),
                "segmentation": {
                    "by_source": [
                        {
                            "source": "Reuters",
                            "llm_rows": 2,
                            "meaningful_signal_rate": 0.5,
                            "neutral_confirmation_rate": 0.5,
                        }
                    ],
                    "by_price_band": [
                        {
                            "price_band": "0.40-0.60",
                            "llm_rows": 2,
                            "meaningful_signal_rate": 0.5,
                        }
                    ],
                    "timing": {"available": False},
                },
            },
        },
    )

    stats = collect_window_metrics(
        path=Path("logs/trades/trades.jsonl"),
        since=datetime(2026, 4, 11, tzinfo=timezone.utc),
        until=datetime(2026, 4, 12, tzinfo=timezone.utc),
        exclude_test=True,
    )

    assert stats["matching"]["matched_candidates"] == 10
    assert stats["matching"]["low_quality_pct"] == 0.2
    assert stats["analysis"]["method_llm"] == 3
    assert stats["analysis"]["method_keyword"] == 2
    assert stats["analysis"]["method_keyword_gate"] == 2
    assert stats["analysis"]["keyword_gate_exit_pct"] == 2 / 6
    assert stats["analysis"]["llm_attempted"] == 3
    assert stats["analysis"]["llm_result_used"] == 2
    assert stats["analysis"]["llm_fallback"] == 1
    assert stats["analysis"]["llm_status_counts"]["ollama_timeout"] == 1
    assert stats["analysis"]["llm_total_stage_ms_samples"] == [41000]
    assert stats["analysis"]["llm_queue_wait_ms_samples"] == [5000]
    assert stats["analysis"]["llm_http_round_trip_ms_samples"] == [35000]
    assert stats["analysis"]["llm_contention_observed"] == 1
    assert stats["analysis"]["llm_skipped_routing"] == 1
    assert stats["analysis"]["llm_skipped_routing_reasons"]["price_band_excluded"] == 1
    assert stats["analysis"]["llm_value_add"]["llm_rows"] == 2
    assert stats["analysis"]["llm_value_add"]["meaningful_signals"] == 1
    assert stats["analysis"]["pre_llm_gate"]["signal_analysis_detail_total"] == 6
    assert stats["analysis"]["pre_llm_gate"]["startup_probe_excluded"] == 1
    assert stats["analysis"]["pre_llm_gate"]["instrumented_non_probe_records"] == 1
    assert stats["analysis"]["pre_llm_gate"]["would_block"] == 1
    assert stats["analysis"]["pre_llm_gate"]["gate_enforced"] == 1
    assert stats["analysis"]["pre_llm_gate"]["llm_skipped_match_quality_gate"] == 1
    assert stats["analysis"]["pre_llm_gate"]["gate_status_consistent"] is True
    assert stats["analysis"]["pre_llm_gate"]["gate_status_delta"] == 0
    assert stats["analysis"]["pre_llm_gate"]["would_block_and_useful"] == 0
    assert stats["analysis"]["pre_llm_gate"]["suppression_false_positive_candidate_count"] == 0
    assert stats["analysis"]["pre_llm_gate"]["suppression_false_positive_candidate_rate"] == 0
    assert stats["analysis"]["pre_llm_gate"]["keyword_override_count"] == 0
    assert stats["analysis"]["pre_llm_gate"]["interpretation"] == "Blocked calls appear safe"
    assert stats["analysis"]["pre_llm_gate"]["top_gate_reasons"]["weak_semantic_overlap"] == 1
    assert stats["analysis"]["pre_llm_gate"]["top_enforced_tickers"]["KXIRAN"] == 1
    assert stats["analysis"]["pre_llm_gate"]["top_enforced_sources"]["Reuters"] == 1
    assert stats["analysis"]["pre_llm_gate"]["phase3_plan"]["recommendation"] == "leave gate as-is"
    assert stats["edge"]["derived_non_zero_above_threshold"] == 2
    assert stats["execution"]["skipped_duplicate"] == 1


def test_collect_window_metrics_flags_gate_status_divergence(monkeypatch):
    monkeypatch.setattr(
        "scripts.pipeline_impact_audit.match_quality_diagnostics.summarize",
        lambda *args, **kwargs: {
            "match_records": 0,
            "low_quality_matches": 0,
        },
    )
    monkeypatch.setattr(
        "scripts.pipeline_impact_audit.decision_funnel_summary.summarize",
        lambda *args, **kwargs: {
            "event_counts": {},
        },
    )
    monkeypatch.setattr(
        "scripts.pipeline_impact_audit.signal_edge_diagnostics.summarize",
        lambda *args, **kwargs: {
            "counts": {
                "SIGNAL_ANALYSIS_DETAIL": 1,
                "OPPORTUNITY": 0,
                "SKIPPED": 0,
                "EXECUTED": 0,
            },
            "skip_breakdown": {
                "zero_edge": 0,
                "below_threshold": 0,
                "duplicate": 0,
                "other": 0,
            },
            "audit_rows": [
                {
                    "method": "keyword_gate",
                    "ticker": "KXIRAN",
                    "source": "Reuters",
                    "headline": "Enforced without matching status",
                    "pre_llm_quality_pass": False,
                    "pre_llm_would_block": True,
                    "pre_llm_keyword_override": False,
                    "pre_llm_gate_reason": "weak_semantic_overlap",
                    "pre_llm_gate_enforced": True,
                    "pre_llm_would_block_and_useful": False,
                    "pre_llm_filtered_stopword_count": 1,
                    "pre_llm_filtered_generic_count": 0,
                }
            ],
            "llm_observability": {
                "total_stage_ms_samples": [],
                "queue_wait_ms_samples": [],
                "http_round_trip_ms_samples": [],
                "contention_observed": 0,
                "skipped_routing": 0,
                "skipped_routing_reasons": Counter(),
            },
            "llm_value_add": {},
        },
    )

    stats = collect_window_metrics(
        path=Path("logs/trades/trades.jsonl"),
        since=datetime(2026, 4, 11, tzinfo=timezone.utc),
        until=datetime(2026, 4, 12, tzinfo=timezone.utc),
        exclude_test=True,
    )

    assert stats["analysis"]["pre_llm_gate"]["gate_enforced"] == 1
    assert stats["analysis"]["pre_llm_gate"]["llm_skipped_match_quality_gate"] == 0
    assert stats["analysis"]["pre_llm_gate"]["gate_status_consistent"] is False
    assert stats["analysis"]["pre_llm_gate"]["gate_status_delta"] == 1
    assert stats["analysis"]["pre_llm_gate"]["top_gate_reasons"]["weak_semantic_overlap"] == 1
    assert "review stopword/generic token filter candidates" in stats["analysis"]["pre_llm_gate"]["phase3_plan"]["heuristic_tuning_candidates"]


def test_render_report_includes_comparison_sections():
    current = {
        "window": (
            datetime(2026, 4, 11, tzinfo=timezone.utc),
            datetime(2026, 4, 12, tzinfo=timezone.utc),
        ),
        "matching": {
            "matched_candidates": 10,
            "low_quality_flagged": 2,
            "low_quality_pct": 0.2,
            "suppression_candidates": 3,
            "suppressed": 1,
        },
        "analysis": {
            "signal_analysis_detail": 6,
            "method_llm": 3,
            "method_keyword": 2,
            "method_keyword_gate": 1,
            "keyword_gate_exit_pct": 1 / 6,
            "llm_attempted": 5,
            "llm_result_used": 3,
            "llm_fallback": 2,
            "llm_status_counts": Counter({"ollama_success": 2, "ollama_timeout": 2, "anthropic_success": 1}),
            "llm_total_stage_ms_samples": [12000, 14000, 45000],
            "llm_queue_wait_ms_samples": [0, 1000, 8000],
            "llm_http_round_trip_ms_samples": [11000, 13000, 36000],
            "llm_contention_observed": 2,
            "llm_skipped_routing": 2,
            "llm_skipped_routing_reasons": Counter({"price_band_excluded": 2}),
            "llm_value_add": {
                "llm_rows": 3,
                "near_neutral_outputs": 1,
                "non_zero_edge_outputs": 2,
                "meaningful_signals": 2,
                "trade_candidates": 1,
                "llm_created_edge": 1,
                "probability_movement_buckets": Counter({"near_neutral": 1, "weak": 1, "moderate": 1}),
                "edge_magnitude_buckets": Counter({"zero_neutral": 1, "weak": 1, "moderate": 1}),
                "segmentation": {
                    "by_source": [
                        {
                            "source": "Reuters",
                            "llm_rows": 2,
                            "meaningful_signal_rate": 0.5,
                            "neutral_confirmation_rate": 0.5,
                        },
                        {
                            "source": "AP",
                            "llm_rows": 1,
                            "meaningful_signal_rate": 1.0,
                            "neutral_confirmation_rate": 0.0,
                        },
                    ],
                    "by_price_band": [
                        {
                            "price_band": "0.40-0.60",
                            "llm_rows": 2,
                            "meaningful_signal_rate": 0.5,
                        },
                        {
                            "price_band": "0.60-0.80",
                            "llm_rows": 1,
                            "meaningful_signal_rate": 1.0,
                        },
                    ],
                    "timing": {"available": False},
                },
            },
            "pre_llm_gate": {
                "signal_analysis_detail_total": 8,
                "startup_probe_excluded": 1,
                "instrumented_non_probe_records": 4,
                "would_block": 3,
                "gate_enforced": 2,
                "llm_skipped_match_quality_gate": 2,
                "gate_status_consistent": True,
                "gate_status_delta": 0,
                "would_block_and_useful": 0,
                "suppression_false_positive_candidate_count": 0,
                "suppression_false_positive_candidate_rate": 0.0,
                "block_candidate_rate": 0.75,
                "enforced_suppression_rate": 0.5,
                "dangerous_suppression_rate": 0.0,
                "keyword_override_count": 1,
                "top_gate_reasons": Counter({"weak_semantic_overlap": 2, "generic_only_overlap": 1}),
                "interpretation": "Blocked calls appear safe",
                "top_block_tickers": Counter({"KXIRAN": 2, "KXTRUMP": 1}),
                "top_enforced_tickers": Counter({"KXIRAN": 2}),
                "top_block_sources": Counter({"Reuters": 2, "AP": 1}),
                "top_enforced_sources": Counter({"Reuters": 2}),
                "recent_enforced": [
                    {
                        "ts": datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc),
                        "source": "Reuters",
                        "ticker": "KXIRAN",
                        "headline": "Weak match blocked one",
                        "pre_llm_gate_reason": "weak_semantic_overlap",
                        "pre_llm_keyword_override": False,
                        "llm_useful": None,
                    }
                ],
                "recent_dangerous": [],
                "phase3_plan": {
                    "gate_health": {
                        "enforced_suppression_rate": 0.5,
                        "dangerous_suppression_rate": 0.0,
                        "top_suppressed_tickers": Counter({"KXIRAN": 2}),
                        "top_suppressed_sources": Counter({"Reuters": 2}),
                    },
                    "keyword_override_review": {
                        "prevented_suppressions": 1,
                        "assessment": "Keyword override appears acceptable under current evidence",
                    },
                    "heuristic_tuning_candidates": [
                        "review stopword/generic token filter candidates",
                        "review overlap/ratio refinement opportunities",
                        "review keyword override precision",
                    ],
                    "recommendation": "leave gate as-is",
                },
            },
        },
        "edge": {
            "opportunities": 4,
            "below_min_edge": 1,
            "zero_edge": 1,
            "derived_non_zero_above_threshold": 2,
        },
        "execution": {
            "executed": 2,
            "skipped_total": 2,
            "skipped_duplicate": 1,
            "skipped_below_threshold": 1,
            "skipped_zero_edge": 0,
            "skipped_other": 0,
        },
    }
    previous = {
        "window": (
            datetime(2026, 4, 10, tzinfo=timezone.utc),
            datetime(2026, 4, 11, tzinfo=timezone.utc),
        ),
        "matching": {
            "matched_candidates": 12,
            "low_quality_flagged": 6,
            "low_quality_pct": 0.5,
            "suppression_candidates": 5,
            "suppressed": 0,
        },
        "analysis": {
            "signal_analysis_detail": 6,
            "method_llm": 1,
            "method_keyword": 2,
            "method_keyword_gate": 3,
            "keyword_gate_exit_pct": 0.5,
            "llm_attempted": 4,
            "llm_result_used": 1,
            "llm_fallback": 3,
            "llm_status_counts": Counter({"no_provider_available": 2, "ollama_timeout": 2}),
            "llm_total_stage_ms_samples": [20000, 48000],
            "llm_queue_wait_ms_samples": [0, 9000],
            "llm_http_round_trip_ms_samples": [18000, 37000],
            "llm_contention_observed": 1,
            "llm_skipped_routing": 0,
            "llm_skipped_routing_reasons": Counter(),
            "llm_value_add": {
                "llm_rows": 1,
                "near_neutral_outputs": 1,
                "non_zero_edge_outputs": 0,
                "meaningful_signals": 0,
                "trade_candidates": 0,
                "llm_created_edge": 0,
                "probability_movement_buckets": Counter({"near_neutral": 1}),
                "edge_magnitude_buckets": Counter({"zero_neutral": 1}),
                "segmentation": {
                    "by_source": [
                        {
                            "source": "Reuters",
                            "llm_rows": 1,
                            "meaningful_signal_rate": 0.0,
                            "neutral_confirmation_rate": 1.0,
                        }
                    ],
                    "by_price_band": [
                        {
                            "price_band": "0.40-0.60",
                            "llm_rows": 1,
                            "meaningful_signal_rate": 0.0,
                        }
                    ],
                    "timing": {"available": False},
                },
            },
            "pre_llm_gate": {
                "signal_analysis_detail_total": 7,
                "startup_probe_excluded": 0,
                "instrumented_non_probe_records": 2,
                "would_block": 1,
                "gate_enforced": 0,
                "llm_skipped_match_quality_gate": 1,
                "gate_status_consistent": False,
                "gate_status_delta": -1,
                "would_block_and_useful": 1,
                "suppression_false_positive_candidate_count": 1,
                "suppression_false_positive_candidate_rate": 0.5,
                "block_candidate_rate": 0.5,
                "enforced_suppression_rate": 0.0,
                "dangerous_suppression_rate": 0.5,
                "keyword_override_count": 0,
                "top_gate_reasons": Counter({"weak_semantic_overlap": 1}),
                "interpretation": "Review dangerous suppressions before trusting gate",
                "top_block_tickers": Counter({"KXTRUMP": 1}),
                "top_enforced_tickers": Counter(),
                "top_block_sources": Counter({"AP": 1}),
                "top_enforced_sources": Counter(),
                "recent_enforced": [],
                "recent_dangerous": [
                    {
                        "ts": datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc),
                        "source": "AP",
                        "ticker": "KXTRUMP",
                        "headline": "Potentially dangerous block",
                        "pre_llm_gate_reason": "weak_semantic_overlap",
                        "pre_llm_keyword_override": False,
                        "llm_useful": True,
                    }
                ],
                "phase3_plan": {
                    "gate_health": {
                        "enforced_suppression_rate": 0.0,
                        "dangerous_suppression_rate": 0.5,
                        "top_suppressed_tickers": Counter(),
                        "top_suppressed_sources": Counter(),
                    },
                    "keyword_override_review": {
                        "prevented_suppressions": 0,
                        "assessment": "No keyword overrides observed",
                    },
                    "heuristic_tuning_candidates": [
                        "review overlap/ratio refinement opportunities",
                    ],
                    "recommendation": "investigate dangerous suppressions before tightening",
                },
            },
        },
        "edge": {
            "opportunities": 3,
            "below_min_edge": 1,
            "zero_edge": 1,
            "derived_non_zero_above_threshold": 1,
        },
        "execution": {
            "executed": 1,
            "skipped_total": 2,
            "skipped_duplicate": 1,
            "skipped_below_threshold": 1,
            "skipped_zero_edge": 0,
            "skipped_other": 0,
        },
    }

    rendered = "\n".join(render_report(current, previous))

    assert "PIPELINE IMPACT AUDIT" in rendered
    assert "1. Matching Quality" in rendered
    assert "2. Analysis Quality" in rendered
    assert "3. Pre-LLM Match Gate" in rendered
    assert "4. LLM Value-Add Analysis" in rendered
    assert "5. LLM Value-Add Segmentation" in rendered
    assert "6. Edge Formation" in rendered
    assert "7. Execution Quality" in rendered
    assert "Startup probes excluded" in rendered
    assert "Would block and useful" in rendered
    assert "Dangerous suppression rate" in rendered
    assert "False-positive candidate rate" in rendered
    assert "Gate/status consistency" in rendered
    assert "Gate/status consistency      : OK" in rendered
    assert "Previous gate consistency    : WARNING (delta=-1)" in rendered
    assert "Current top gate reasons" in rendered
    assert "Current interpretation" in rendered
    assert "Recent enforced suppressions" in rendered
    assert "Recent dangerous suppressions" in rendered
    assert "Low-quality percentage" in rendered
    assert "Keyword-gate exit fraction" in rendered
    assert "LLM attempted" in rendered
    assert "LLM skipped (routing)" in rendered
    assert "Near-neutral outputs" in rendered
    assert "Trade candidates" in rendered
    assert "Current top sources (meaningful)" in rendered
    assert "Current top price bands" in rendered
    assert "Current routing skips" in rendered
    assert "Current LLM statuses" in rendered
    assert "Current LLM total latency" in rendered
    assert "Current LLM queue wait" in rendered
    assert "8. Phase 3 Planning Summary" in rendered
    assert "Keyword override review" in rendered
    assert "Recommendation               : leave gate as-is" in rendered


def test_render_report_phase3_summary_handles_no_data():
    current = {
        "window": (
            datetime(2026, 4, 11, tzinfo=timezone.utc),
            datetime(2026, 4, 12, tzinfo=timezone.utc),
        ),
        "matching": {
            "matched_candidates": 0,
            "low_quality_flagged": 0,
            "low_quality_pct": None,
            "suppression_candidates": 0,
            "suppressed": 0,
        },
        "analysis": {
            "signal_analysis_detail": 0,
            "method_llm": 0,
            "method_keyword": 0,
            "method_keyword_gate": 0,
            "keyword_gate_exit_pct": None,
            "llm_attempted": 0,
            "llm_result_used": 0,
            "llm_fallback": 0,
            "llm_status_counts": Counter(),
            "llm_total_stage_ms_samples": [],
            "llm_queue_wait_ms_samples": [],
            "llm_http_round_trip_ms_samples": [],
            "llm_contention_observed": 0,
            "llm_skipped_routing": 0,
            "llm_skipped_routing_reasons": Counter(),
            "llm_value_add": {
                "llm_rows": 0,
                "near_neutral_outputs": 0,
                "non_zero_edge_outputs": 0,
                "meaningful_signals": 0,
                "trade_candidates": 0,
                "llm_created_edge": 0,
                "probability_movement_buckets": Counter(),
                "edge_magnitude_buckets": Counter(),
                "segmentation": {"by_source": [], "by_price_band": [], "timing": {"available": False}},
            },
            "pre_llm_gate": {
                "signal_analysis_detail_total": 0,
                "startup_probe_excluded": 0,
                "instrumented_non_probe_records": 0,
                "would_block": 0,
                "gate_enforced": 0,
                "llm_skipped_match_quality_gate": 0,
                "gate_status_consistent": True,
                "gate_status_delta": 0,
                "would_block_and_useful": 0,
                "suppression_false_positive_candidate_count": 0,
                "suppression_false_positive_candidate_rate": None,
                "block_candidate_rate": None,
                "enforced_suppression_rate": None,
                "dangerous_suppression_rate": None,
                "keyword_override_count": 0,
                "top_gate_reasons": Counter(),
                "interpretation": "Blocked calls appear safe",
                "top_block_tickers": Counter(),
                "top_enforced_tickers": Counter(),
                "top_block_sources": Counter(),
                "top_enforced_sources": Counter(),
                "recent_enforced": [],
                "recent_dangerous": [],
                "phase3_plan": {
                    "gate_health": {
                        "enforced_suppression_rate": None,
                        "dangerous_suppression_rate": None,
                        "top_suppressed_tickers": Counter(),
                        "top_suppressed_sources": Counter(),
                    },
                    "keyword_override_review": {
                        "prevented_suppressions": 0,
                        "assessment": "No keyword overrides observed",
                    },
                    "heuristic_tuning_candidates": ["No clear tuning candidate from current window"],
                    "recommendation": "collect more data before tuning",
                },
            },
        },
        "edge": {
            "opportunities": 0,
            "below_min_edge": 0,
            "zero_edge": 0,
            "derived_non_zero_above_threshold": 0,
        },
        "execution": {
            "executed": 0,
            "skipped_total": 0,
            "skipped_duplicate": 0,
            "skipped_below_threshold": 0,
            "skipped_zero_edge": 0,
            "skipped_other": 0,
        },
    }

    rendered = "\n".join(render_report(current, current))

    assert "Heuristic tuning candidates  : No clear tuning candidate from current window" in rendered
    assert "Recommendation               : collect more data before tuning" in rendered
