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
    assert stats["analysis"]["method_llm"] == 2
    assert stats["analysis"]["method_keyword"] == 2
    assert stats["analysis"]["method_keyword_gate"] == 1
    assert stats["analysis"]["keyword_gate_exit_pct"] == 1 / 6
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
    assert stats["edge"]["derived_non_zero_above_threshold"] == 2
    assert stats["execution"]["skipped_duplicate"] == 1


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
    assert "3. LLM Value-Add Analysis" in rendered
    assert "4. LLM Value-Add Segmentation" in rendered
    assert "5. Edge Formation" in rendered
    assert "6. Execution Quality" in rendered
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
