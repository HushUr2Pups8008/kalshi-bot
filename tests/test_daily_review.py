import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.daily_review import (
    _build_tier_by_source,
    _format_fresh_pass_conversion_lines,
    _format_match_attribution_lines,
    _format_same_window_lifecycle_attribution_lines,
    _summarize_fresh_pass_assignment_shadow,
    _format_tier_change_lines,
    _load_previous_tier_state,
    _save_current_tier_state,
    build_daily_review,
    write_report,
)
from scripts.throughput_operator_metrics import ThroughputOperatorSummary


def test_format_fresh_pass_conversion_lines_labels_raw_stages_noncausal():
    lines = _format_fresh_pass_conversion_lines(
        fresh_passes=186,
        match_records=40,
        detail_rows=1,
        llm_attempted=0,
        opportunities=0,
        paper_trades=0,
    )

    assert lines == [
        "  Fresh-pass observability          : 186 fresh; 40 match diagnostics; 1 signal row; 0 LLM attempts; 0 opportunities; 0 raw paper-trade events",
        "    attribution                    : raw stage counts are not lifecycle conversion",
    ]


def test_format_same_window_lifecycle_attribution_lines_surfaces_missing_execution_lineage():
    lines = _format_same_window_lifecycle_attribution_lines(
        {
            "opportunity_lifecycle_count": 3,
            "g7_skip_lifecycle_count": 1,
            "zero_cap_skip_lifecycle_count": 1,
            "other_skip_lifecycle_count": 0,
            "pending_opportunity_lifecycle_count": 1,
            "orphan_skip_lifecycle_count": 1,
            "paper_trade_opportunity_lifecycle_count": 0,
            "live_submission_opportunity_lifecycle_count": 0,
            "unknown_live_submission_opportunity_lifecycle_count": 0,
            "unresolved_live_submission_intent_opportunity_lifecycle_count": 0,
            "outcome_conflict_lifecycle_count": 0,
            "terminal_evidence_conflict_lifecycle_count": 0,
            "orphan_paper_trade_lifecycle_count": 0,
            "orphan_live_submission_lifecycle_count": 0,
            "orphan_unknown_live_submission_lifecycle_count": 0,
            "orphan_live_submission_intent_lifecycle_count": 0,
            "conflicted_lifecycle_count": 0,
            "identity_incomplete_lifecycle_count": 0,
            "reused_opportunity_lifecycle_count": 0,
            "quarantined_lifecycle_count": 0,
            "paper_trade_lifecycle_status": "unavailable",
            "paper_trade_event_rows": 1,
            "paper_trade_linked_event_rows": 0,
            "live_submission_event_rows": 1,
            "live_submission_linked_event_rows": 0,
            "unknown_live_submission_event_rows": 0,
            "unknown_live_submission_linked_event_rows": 0,
            "live_submission_intent_event_rows": 0,
            "live_submission_intent_linked_event_rows": 0,
            "unattributed_event_counts": Counter({"PAPER_TRADE": 1}),
        },
        since=datetime(2026, 4, 11, tzinfo=timezone.utc),
        until=datetime(2026, 4, 12, tzinfo=timezone.utc),
    )

    assert lines == [
        "  Same-window linkable cohort      : 3 opportunities",
        "    Window                         : 2026-04-11T00:00:00+00:00 -> 2026-04-12T00:00:00+00:00",
        "    Terminal attribution           : G7 skips=1, zero-cap skips=1, other skips=0, paper trades=0, live submissions=0, unknown live submissions=0, intents without matching terminal journal=0, conflicts=0, receipt conflicts=0, pending=1, orphan skips=1",
        "    Paper-trade lineage            : unavailable (0/1 event rows linked)",
        "    Live submission lineage        : 0/1 event rows linked; not fill or P&L evidence",
        "    Live submission unknown       : 0/0 event rows linked; reconciliation required",
        "    Live submission intent lineage: 0/0 event rows linked; 0 without matching terminal journal; not fill or P&L evidence; reconciliation required",
        "    Linkage conflicts              : lifecycle IDs=0, incomplete IDs=0, reused opportunities=0, terminal evidence conflicts=0, orphan paper trades=0, orphan live submissions=0, orphan unknown submissions=0, orphan live intents=0",
        "    P&L basis                      : settlement and mark P&L excluded from lifecycle linkage",
        "    Unattributed lifecycle events  : PAPER_TRADE=1",
    ]


def test_write_report_overwrites_previous_snapshot_atomically(tmp_path):
    report_path = tmp_path / "daily_review.txt"
    report_path.write_text("PIPELINE REVIEW\nold snapshot\n", encoding="utf-8")

    write_report(report_path, ["PIPELINE REVIEW", "new snapshot"])

    assert report_path.read_text(encoding="utf-8") == (
        f"PIPELINE REVIEW\nnew snapshot\n\nDaily review report saved to: {report_path}\n"
    )


def test_format_match_attribution_lines_surfaces_suppression_drilldowns():
    lines = _format_match_attribution_lines(
        {
            "event_counts": {"MATCH_SUPPRESSED": 3},
            "match_diagnostics_total": 10,
            "signal_analysis_detail_total": 2,
            "match_to_signal_detail_gap": 8,
            "match_diagnostic_pre_llm_gate": Counter({"would_fail": 9, "would_pass": 1}),
            "match_suppressed_reasons": Counter({"minimal_overlap": 3}),
            "match_suppressed_tokens": Counter({"iran": 2, "israel": 1}),
            "match_weight_applied_total": 5,
            "match_weight_tokens": Counter({"iran": 4}),
            "match_weight_prefixes": Counter({"KXIRANCRUDE": 4}),
            "match_weight_score_delta_total": -0.125,
            "match_no_candidate_total": 3,
            "match_no_candidate_post_admission_rejection_complete_rows": 2,
            "match_no_candidate_post_admission_rejection_missing_breakdown_rows": 1,
            "match_no_candidate_post_admission_rejection_within_horizon_markets": 3,
            "match_no_candidate_post_admission_no_token_overlap": 2,
            "match_no_candidate_post_admission_below_min": 1,
            "match_no_candidate_post_admission_weight_demoted": 1,
        },
        top=2,
    )

    rendered = "\n".join(lines)

    assert "Match diagnostics                : 10" in rendered
    assert "Signal analysis detail rows      : 2" in rendered
    assert "Match -> analysis detail gap     : 8" in rendered
    assert "Match suppressions               : 3" in rendered
    assert "Match weight applications        : 5 (score_delta=-0.1250)" in rendered
    assert "Drilldown: pre-LLM quality gate" in rendered
    assert "  9  would_fail" in rendered
    assert "Drilldown: match suppression reasons" in rendered
    assert "  3  minimal_overlap" in rendered
    assert "Drilldown: match weight prefixes" in rendered
    assert "  4  KXIRANCRUDE" in rendered
    assert "Post-admission rejection attribution: complete=2 unavailable=1 market_rows=3" in rendered
    assert "no_token_overlap=2 (66.7%) below_min_score=1 (33.3%) weight_demoted=1" in rendered


def test_summarize_fresh_pass_assignment_shadow_counts_assignment_outcomes(tmp_path):
    trades_path = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    shadow_path = tmp_path / "logs" / "trades" / "shadow" / "fresh_pass_assignment_shadow.jsonl"
    shadow_path.parent.mkdir(parents=True)
    trades_path.parent.mkdir(parents=True)
    trades_path.write_text("", encoding="utf-8")
    shadow_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "FRESH_PASS_ASSIGNMENT_SHADOW",
                        "ts": "2026-04-11T12:00:00+00:00",
                        "source": "Reuters",
                        "headline": "Assigned",
                        "assigned": True,
                        "candidate_count": 2,
                        "top_ticker": "KXASSIGNED",
                        "top_score": 0.12,
                    }
                ),
                json.dumps(
                    {
                        "type": "FRESH_PASS_ASSIGNMENT_SHADOW",
                        "ts": "2026-04-11T13:00:00+00:00",
                        "source": "AP",
                        "headline": "Unassigned",
                        "assigned": False,
                        "candidate_count": 0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stats = _summarize_fresh_pass_assignment_shadow(
        trades_path,
        since=datetime(2026, 4, 11, tzinfo=timezone.utc),
        until=datetime(2026, 4, 11, 23, 59, tzinfo=timezone.utc),
    )

    assert stats["rows"] == 2
    assert stats["assigned"] == 1
    assert stats["unassigned"] == 1
    assert stats["top_unassigned_sources"] == Counter({"AP": 1})


def test_build_daily_review_formats_pipeline_stages(monkeypatch):
    monkeypatch.setattr(
        "scripts.daily_review._ollama_runtime_summary",
        lambda: "configured=qwen2.5:7b health=ok available=['qwen2.5:7b']",
    )
    monkeypatch.setattr(
        "scripts.daily_review.freshness_diagnostics.summarize",
        lambda *args, **kwargs: {
            "sources": {
                "Reuters": {
                    "source": "Reuters",
                    "observed_records": 12,
                    "fresh_passes": 9,
                    "early_stale_drops": 3,
                    "within_300s": 5,
                    "stale_rate": 0.25,
                    "median_age_seconds": 120.0,
                    "freshest_age_seconds": 45.0,
                    "p90_age_seconds": 200.0,
                    "age_samples_count": 9,
                    "interpretation": "near-threshold",
                }
            }
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.match_quality_diagnostics.summarize",
        lambda *args, **kwargs: {
            "match_records": 8,
            "low_quality_matches": 2,
            "heuristic_flags": Counter({"single_named_entity_only": 2}),
            "pre_llm_would_block": 3,
            "pre_llm_would_block_by_source": Counter({"Reuters": 2, "AP": 1}),
            "pre_llm_would_block_by_ticker": Counter({"KXTRUMP-1": 3}),
            "examples_bad": [
                {
                    "ticker": "KXTRUMP-1",
                    "match_score": 0.061,
                    "heuristic_flags": ["single_named_entity_only"],
                    "headline": "Trump comments on talks",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.decision_funnel_summary.summarize",
        lambda *args, **kwargs: {
            "records_kept": 25,
            "event_counts": {
                "MATCH_SUPPRESSION_CANDIDATE": 1,
                "MATCH_SUPPRESSED": 1,
                "PAPER_TRADE": 2,
                "LIVE_ORDER": 0,
            },
            "same_window_lifecycle_attribution": {
                "opportunity_lifecycle_count": 3,
                "g7_skip_lifecycle_count": 1,
                "zero_cap_skip_lifecycle_count": 1,
                "other_skip_lifecycle_count": 0,
                "pending_opportunity_lifecycle_count": 1,
                "orphan_skip_lifecycle_count": 0,
                "paper_trade_opportunity_lifecycle_count": 0,
                "live_submission_opportunity_lifecycle_count": 0,
                "outcome_conflict_lifecycle_count": 0,
                "orphan_paper_trade_lifecycle_count": 0,
                "orphan_live_submission_lifecycle_count": 0,
                "conflicted_lifecycle_count": 0,
                "identity_incomplete_lifecycle_count": 0,
                "reused_opportunity_lifecycle_count": 0,
                "quarantined_lifecycle_count": 0,
                "paper_trade_lifecycle_status": "unavailable",
                "paper_trade_event_rows": 2,
                "paper_trade_linked_event_rows": 0,
                "live_submission_event_rows": 0,
                "live_submission_linked_event_rows": 0,
                "unattributed_event_counts": Counter({"PAPER_TRADE": 2}),
            },
            "analysis_rejected_reasons": {"stale_news": 2, "no_keywords": 1},
            "analysis_rejected_categories": {"post_llm_neutral_empty_keywords": 1},
            "match_diagnostics_total": 8,
            "signal_analysis_detail_total": 6,
            "match_to_signal_detail_gap": 2,
            "fresh_pass_route_log_linkage": {
                "fresh_pass_rows": 9,
                "fresh_pass_distinct_keys": 8,
                "fresh_pass_unique_keys": 8,
                "fresh_pass_keys_with_candidate_diagnostic": 6,
                "fresh_pass_keys_with_explicit_no_match": 2,
                "fresh_pass_keys_with_market_availability_exit": 1,
                "fresh_pass_keys_with_unknown_route_exit": 1,
                "fresh_pass_keys_with_multiple_route_signals": 1,
                "fresh_pass_keys_without_tracked_route_signal": 1,
                "fresh_pass_ambiguous_duplicate_keys": 0,
                "fresh_pass_missing_identity_rows": 1,
                "match_diagnostic_missing_identity_rows": 2,
                "match_no_candidate_missing_identity_rows": 0,
                "candidate_diagnostic_keys_without_fresh_pass": 0,
                "explicit_no_match_keys_without_fresh_pass": 0,
                "market_availability_keys_without_fresh_pass": 0,
                "unknown_route_exit_keys_without_fresh_pass": 0,
            },
            "fresh_pass_without_tracked_route_signal_sources": Counter({"Reuters": 1}),
            "match_no_candidate_total": 2,
            "match_no_candidate_reasons": Counter({"no_match": 2}),
            "match_no_candidate_candidate_pool_stages": Counter(
                {"post_admission_no_match": 2}
            ),
            "match_no_candidate_missing_candidate_pool_stage": 0,
            "match_no_candidate_venues": Counter({"polymarket_us": 2}),
            "match_diagnostic_pre_llm_gate": Counter({"would_fail": 7, "would_pass": 1}),
            "match_diagnostic_sources": Counter({"Reuters": 5, "AP": 3}),
            "match_diagnostic_tickers": Counter({"KXIRAN": 5, "KXTRUMP": 3}),
            "match_suppressed_reasons": Counter({"minimal_overlap": 1}),
            "match_suppressed_tokens": Counter({"iran": 1}),
            "match_suppressed_column_coverage": Counter(
                {
                    "raw_score": 1,
                    "adjusted_score": 1,
                    "threshold": 1,
                    "token_weight_multiplier": 1,
                    "venue": 1,
                    "market_prefix": 1,
                }
            ),
            "match_suppressed_venues": Counter({"kalshi": 1}),
            "match_weight_applied_total": 4,
            "match_weight_tokens": Counter({"iran": 3, "trump": 1}),
            "match_weight_prefixes": Counter({"KXIRANCRUDE": 3, "KXTRUMP": 1}),
            "match_weight_score_delta_total": -0.25,
            "opportunity_sources": Counter({"France 24": 2, "Reuters": 1}),
            "opportunity_source_classes": Counter({"news": 2, "regional": 1}),
            "opportunity_retrieval_modes": Counter({"source_hint": 2, "unknown": 1}),
            "opportunity_settlement_source_matches": Counter({"True": 2, "unknown": 1}),
            "skip_sources": Counter({"France 24": 1}),
            "skip_source_classes": Counter({"regional": 1}),
            "skip_retrieval_modes": Counter({"rss": 1}),
            "skip_evidence_ids": Counter({"ev-skip-1": 1}),
            "skip_settlement_source_matches": Counter({"unknown": 1}),
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review._summarize_fresh_pass_assignment_shadow",
        lambda *args, **kwargs: {
            "rows": 9,
            "assigned": 4,
            "unassigned": 5,
            "malformed": 0,
            "top_unassigned_sources": Counter({"Reuters": 3, "AP": 2}),
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.signal_edge_diagnostics.summarize",
        lambda *args, **kwargs: {
            "counts": {
                "SIGNAL_ANALYSIS_DETAIL": 6,
                "OPPORTUNITY": 3,
                "EXECUTED": 2,
            },
            "llm_observability": {
                "attempted": 1,
                "skipped_routing": 2,
                "skipped_routing_reasons": Counter({"price_band_excluded": 2}),
                "pre_llm_would_block_and_useful": 1,
            },
            "skip_breakdown": {
                "zero_edge": 1,
                "below_threshold": 1,
                "duplicate": 1,
                "liquidity": 1,
                "other": 0,
            },
            "audit_rows": [
                {
                    "ts": datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc),
                    "ticker": "KX1",
                    "estimated_probability": 0.62,
                    "market_price": 0.55,
                    "edge": 0.07,
                    "outcome": "executed",
                    "method": "llm",
                },
                {
                    "ts": datetime(2026, 4, 11, 11, 0, tzinfo=timezone.utc),
                    "ticker": "KX2",
                    "estimated_probability": 0.50,
                    "market_price": 0.50,
                    "edge": 0.0,
                    "outcome": "opportunity skipped: zero edge",
                    "method": "keyword_gate",
                },
            ],
            "llm_value_add": {
                "llm_rows": 3,
                "near_neutral_outputs": 0,
                "non_zero_edge_outputs": 1,
                "meaningful_signals": 1,
                "trade_candidates": 3,
                "admitted_trade_candidates": 1,
                "blocked_trade_candidates": 1,
                "pending_trade_candidates": 1,
                "llm_created_edge": 0,
                "probability_movement_buckets": Counter({"moderate": 1}),
                "edge_magnitude_buckets": Counter({"moderate": 1}),
                "meaningful_sources": Counter({"Reuters": 1}),
                "meaningful_tickers": Counter({"KX1": 1}),
                "strong_examples": [
                    {
                        "ts": datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc),
                        "ticker": "KX1",
                        "source": "Reuters",
                        "estimated_probability": 0.62,
                        "market_price": 0.55,
                        "edge": 0.07,
                        "llm_decision_impact": "trade_candidate",
                    }
                ],
                "neutral_examples": [],
                "rare_non_neutral_examples": [
                    {
                        "ts": datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc),
                        "ticker": "KX1",
                        "source": "Reuters",
                        "estimated_probability": 0.62,
                        "market_price": 0.55,
                        "edge": 0.07,
                        "llm_decision_impact": "trade_candidate",
                    }
                ],
                "segmentation": {
                    "by_source": [
                        {
                            "source": "Reuters",
                            "llm_rows": 1,
                            "near_neutral_outputs": 0,
                            "non_zero_edge_outputs": 1,
                            "meaningful_signals": 1,
                            "trade_candidates": 1,
                            "neutral_confirmations": 0,
                            "meaningful_signal_rate": 1.0,
                            "neutral_confirmation_rate": 0.0,
                        }
                    ],
                    "by_ticker": [
                        {
                            "ticker": "KX1",
                            "llm_rows": 1,
                            "near_neutral_outputs": 0,
                            "non_zero_edge_outputs": 1,
                            "meaningful_signals": 1,
                            "trade_candidates": 1,
                            "neutral_confirmations": 0,
                            "meaningful_signal_rate": 1.0,
                            "neutral_confirmation_rate": 0.0,
                        }
                    ],
                    "by_price_band": [
                        {
                            "price_band": "0.40-0.60",
                            "llm_rows": 1,
                            "near_neutral_outputs": 0,
                            "non_zero_edge_outputs": 1,
                            "meaningful_signals": 1,
                            "trade_candidates": 1,
                            "neutral_confirmations": 0,
                            "meaningful_signal_rate": 1.0,
                            "neutral_confirmation_rate": 0.0,
                        }
                    ],
                    "timing": {"available": False, "reason": "not present"},
                    "headline_category": {"available": False, "reason": "not present"},
                },
            },
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.summarize_operator_throughput_from_trade_log",
        lambda *args, **kwargs: ThroughputOperatorSummary(
            opportunities=3,
            skipped=1,
            paper_trades=2,
            window_days=2.0,
            opportunities_per_day=1.5,
            skipped_per_opportunity=0.3333333333,
            top_ticker_trades_per_day=[("KX1", 1.0), ("KX2", 0.5)],
            opportunity_age_p50_seconds=120.0,
            opportunity_age_p90_seconds=300.0,
        ),
    )
    monkeypatch.setattr(
        "scripts.daily_review.paper_performance_drilldown.summarize",
        lambda *args, **kwargs: {
            "total_trades": 2,
            "resolved_trades": 1,
            "open_trades": 1,
            "win_rate": 1.0,
            "total_pnl": 5.0,
            "high_confidence_full_losses": [
                {
                    "ticker": "PM-BAD",
                    "venue": "polymarket_us",
                    "pnl_dollars": -4.15,
                    "cost_dollars": 4.15,
                    "estimated_prob": 0.898,
                    "entry_price_cents": 83.0,
                    "llm_confidence": 0.85,
                    "signal_source": "qns.com",
                }
            ],
            "open_resolution_buckets": [{"bucket": "0-3d", "venue": "polymarket", "trades": 1, "exposure": 12.5}],
            "open_mark_summary": {
                "open_cost_dollars": 12.5,
                "marked_kalshi_cost_dollars": 0.5,
                "marked_kalshi_bid_value_dollars": 0.25,
                "marked_kalshi_unrealized_pnl_dollars": -0.25,
                "unknown_mark_cost_dollars": 12.0,
            },
            "executable_liquidation": {
                "as_of": "2026-07-14T12:00:00+00:00",
                "gross_bid_value": 10.50,
                "estimated_exit_fees": 0.20,
                "report_net_liquidation_value": 10.30,
                "unrealized_fee_net_pnl": -2.20,
                "unscorable_cost": 0.0,
                "unscorable_reasons": {},
                "fee_schedule_hashes": {
                    "polymarket_us": {
                        "name": "polymarket-us-2026-07-01",
                        "artifact_sha256": "83580a99558f43d3",
                    }
                },
                "by_venue": [
                    {
                        "venue": "polymarket_us",
                        "gross_bid_value": 10.50,
                        "estimated_exit_fees": 0.20,
                        "report_net_liquidation_value": 10.30,
                        "unscorable_cost": 0.0,
                    }
                ],
            },
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review._matcher_weight_runtime_status",
        lambda: {
            "status": "fail_closed",
            "reason": "matcher weights staged: data/matcher_token_weights.json",
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.keyword_feedback.summarize",
        lambda *args, **kwargs: {
            "no_keyword_misses": 3,
            "corroborating_keyword_gate_records": 2,
            "no_keyword_rejection_categories": Counter({"post_llm_neutral_empty_keywords": 3}),
            "empty_keyword_llm_directional_rows": 1,
            "empty_keyword_llm_neutral_rows": 2,
            "unique_candidate_phrases": 4,
            "grouped_phrases": {},
            "top_no_keyword_sources": [("Reuters", 2), ("AP", 1)],
            "top_no_keyword_tickers": [("KXIRAN", 2), ("KXTRUMP", 1)],
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.counterfactual_llm_eval.build_eval_report",
        lambda *args, **kwargs: {
            "model_eval_status": "skipped_no_context_ready_cases",
            "target_counts": {
                "neutral_none_no_keywords": 3,
                "context_ready": 1,
                "missing_contract_context": 2,
                "evaluated_context_ready": 1,
                "skipped_missing_contract_context": 2,
            },
            "model_summary": {
                "qwen2.5:7b": {
                    "evaluated": 1,
                    "paper_candidate_positive": 0,
                    "errors": 0,
                },
                "qwen3:14b": {
                    "evaluated": 1,
                    "paper_candidate_positive": 1,
                    "errors": 0,
                },
            },
            "cases": [
                {
                    "ts": "2026-04-11T12:00:00+00:00",
                    "ticker": "KXIRAN",
                    "eval_status": "context_ready",
                    "model_results": {
                        "qwen3:14b": {
                            "direction": "yes",
                            "magnitude": "moderate",
                            "confidence": 0.8,
                            "paper_candidate_positive": True,
                        }
                    },
                }
            ],
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review._latest_restart_timestamp_from_health_reports",
        lambda: "2026-04-11T10:00:00+00:00",
    )
    monkeypatch.setattr(
        "scripts.daily_review.since_restart_money_path.build_money_path_report",
        lambda *args, **kwargs: {
            "window": {"since": "2026-04-11T10:00:00+00:00", "until": None},
            "boundaries": {
                "process_start_utc": "2026-04-11T10:00:00+00:00",
                "log_boot_utc": "2026-04-11T12:00:00+00:00",
            },
            "summary": {
                "candidates": 1,
                "terminal_counts": {"SKIPPED": 1},
                "measurement_gaps": 0,
            },
            "legacy_resolutions_between_process_start_and_log_boot": {
                "count": 2,
                "pnl_total": -5.45,
                "tickers": ["PM-BAD", "KXLOSS"],
            },
            "no_keywords": {"count": 3},
            "polymarket_settlement_feedback": {
                "status": "insufficient_sample",
                "resolved_count": 1,
                "min_resolved_required": 10,
                "proof_rows": [
                    {
                        "ticker": "PM-IRAN-2026-06-20",
                        "trade_id": "trade-pm-1",
                        "pnl_dollars": 1.7,
                        "feedback_ts": "2026-06-20T02:05:00+00:00",
                        "market_prefix": "polymarket_us:iran",
                    }
                ],
            },
            "candidates": [
                {
                    "ticker": "KXTEST",
                    "terminal_type": "SKIPPED",
                    "terminal_venue": "kalshi",
                    "terminal_reason": "price 1.0c is near limit (too illiquid)",
                }
            ],
        },
    )

    lines = build_daily_review(
        trades_path=Path("logs/trades/trades.jsonl"),
        paper_db_path=Path("data/paper_trades.db"),
        since=datetime(2026, 4, 10, tzinfo=timezone.utc),
        until=datetime(2026, 4, 11, 23, 59, tzinfo=timezone.utc),
        top=2,
        exclude_test=True,
    )

    rendered = "\n".join(lines)

    assert "PIPELINE REVIEW" in rendered
    assert "Software version                 : v" in rendered
    assert "SINCE-RESTART MONEY PATH" in rendered
    assert "Boundary source                  : health bot_runtime.started_utc (process start)" in rendered
    assert "Process-start boundary           : 2026-04-11T10:00:00+00:00" in rendered
    assert "Latest log-boot boundary         : 2026-04-11T12:00:00+00:00" in rendered
    assert "Legacy resolutions before boot   : 2 pnl=$-5.45" in rendered
    assert "Candidates                       : 1" in rendered
    assert "Terminal outcomes                : SKIPPED=1" in rendered
    assert "No-keyword exits                 : 3" in rendered
    assert "Polymarket settlement feedback   : insufficient_sample (1/10 resolved)" in rendered
    assert "PM-IRAN-2026-06-20 trade_id=trade-pm-1 pnl=1.7" in rendered
    assert "1. INGESTION" in rendered
    assert "2. MATCHING" in rendered
    assert "3. ANALYSIS" in rendered
    assert "OPERATOR THROUGHPUT LEADING INDICATORS" in rendered
    assert "Opportunities/day              : 1.50" in rendered
    assert "Skipped/opportunity ratio      : 0.333" in rendered
    assert "Opportunity age p50/p90        : 2.0m / 5.0m" in rendered
    assert "KX1: 1.00/day" in rendered
    assert "4. EDGE FORMATION" in rendered
    assert "5. PAPER TRADES AND LIVE SUBMISSIONS" in rendered
    assert "Live order submissions" in rendered
    assert "Scope: live order submissions are not fill or P&L evidence." in rendered
    assert "Matcher weights runtime status   : FAIL_CLOSED" in rendered
    assert "matcher weights staged: data/matcher_token_weights.json" in rendered
    assert "High-confidence full-loss rows   : 1" in rendered
    assert (
        "PM-BAD venue=polymarket_us pnl=$-4.15 cost=$4.15 p=89.8% entry=83.0c llm_conf=85.0% source=qns.com" in rendered
    )
    assert "6. LLM VALUE-ADD ANALYSIS" in rendered
    assert "7. LLM VALUE-ADD SEGMENTATION" in rendered
    assert "Appendix" in rendered
    assert "Low-quality flagged              : 2 (25.0%)" in rendered
    assert "Pre-LLM gate would-block         : 3 (37.5%)" in rendered
    assert "Drilldown: pre-LLM would-block by source (top)" in rendered
    assert "Drilldown: pre-LLM would-block by market (top)" in rendered
    assert "Drilldown: per-source freshness waterfall" in rendered
    assert "Fresh-pass observability" in rendered
    assert (
        "9 fresh; 8 match diagnostics; 6 signal rows; 1 LLM attempt; 3 opportunities; 2 raw paper-trade events"
        in rendered
    )
    assert "Fresh-pass route linkage        : venue-agnostic, window-local log signals; not attempt, conversion, or per-venue coverage; signal rates overlap" in rendered
    assert (
        "candidate_diagnostic=6/8 (75.0%) logged_no_match=2/8 (25.0%) "
        "market_availability=1/8 (12.5%) unknown_or_other_exit=1/8 (12.5%)"
    ) in rendered
    assert "multiple=1 no_signal=1" in rendered
    assert "ambiguous=0 missing_identity=fresh=1 diagnostic=2 no_candidate=0" in rendered
    assert (
        "signal_without_fresh=candidate_diagnostic=0 logged_no_match=0 "
        "market_availability=0 unknown_or_other_exit=0"
    ) in rendered
    assert "Explicit no-candidate rows      : 2" in rendered
    assert "No-candidate records missing pool-stage: 0" in rendered
    assert "Drilldown: no-candidate pool stages" in rendered
    assert "post_admission_no_match" in rendered
    assert "Same-window linkable cohort      : 3 opportunities" in rendered
    assert "Paper-trade lineage            : unavailable (0/2 event rows linked)" in rendered
    assert "Live submission lineage        : 0/0 event rows linked; not fill or P&L evidence" in rendered
    assert "P&L basis                      : settlement and mark P&L excluded from lifecycle linkage" in rendered
    assert "Match diagnostics                : 8" in rendered
    assert "Signal analysis detail rows      : 6" in rendered
    assert "Match -> analysis detail gap     : 2" in rendered
    assert "Match suppressions               : 1" in rendered
    assert "Match weight applications        : 4 (score_delta=-0.2500)" in rendered
    assert "Match suppression metadata       : raw_score=1/1" in rendered
    assert "venue=1/1" in rendered
    assert "Drilldown: pre-LLM quality gate" in rendered
    assert "  7  would_fail" in rendered
    assert "Drilldown: match suppression reasons" in rendered
    assert "  1  minimal_overlap" in rendered
    assert "Drilldown: match suppression venues" in rendered
    assert "  1  kalshi" in rendered
    assert "Drilldown: match weight prefixes" in rendered
    assert "  3  KXIRANCRUDE" in rendered
    assert "Drilldown: opportunity sources" in rendered
    assert "  2  France 24" in rendered
    assert "Drilldown: opportunity settlement-source match" in rendered
    assert "  2  True" in rendered
    assert "Drilldown: skip evidence IDs" in rendered
    assert "  1  ev-skip-1" in rendered
    assert "Fresh assignment shadow         : 4 assigned, 5 unassigned, 0 malformed" in rendered
    assert "Drilldown: unassigned fresh-pass sources" in rendered
    assert "LLM rows                         : 1" in rendered
    assert "LLM attempted (post-filter)       : 1" in rendered
    assert "LLM skipped (routing)             : 2" in rendered
    assert "Routing skip reasons              : price_band_excluded=2" in rendered
    assert "Meaningful signals                : 1 (33.3%)" in rendered
    assert "Model gross-edge candidates (fee unscored): 3 (100.0%)" in rendered
    assert "Candidates admitted               : 1" in rendered
    assert "Candidates blocked by gates       : 1" in rendered
    assert "Candidates pending                : 1" in rendered
    assert "Top sources by meaningful signal rate" in rendered
    assert "Market price bands by meaningful signal rate" in rendered
    assert "Rare non-neutral cases" in rendered
    assert "Ollama runtime                   : configured=qwen2.5:7b health=ok available=['qwen2.5:7b']" in rendered
    assert "No-keyword analysis exits       : 3" in rendered
    assert "post_llm_neutral_empty_keywords: 3" in rendered
    assert "Empty-keyword LLM detail rows   : directional=1 neutral=2" in rendered
    assert "Counterfactual LLM eval          : skipped_no_context_ready_cases" in rendered
    assert "neutral_none_no_keywords=3 context_ready=1 missing_contract_context=2" in rendered
    assert "qwen3:14b: evaluated=1 paper_candidate_positive=1 errors=0" in rendered
    assert "Pre-LLM would-block useful rows : 1" in rendered
    assert "Drilldown: top no-keyword analysis-exit sources" in rendered
    assert "Reuters: 2" in rendered
    assert "Drilldown: top no-keyword analysis-exit tickers" in rendered
    assert "KXIRAN: 2" in rendered
    assert "Paper-trade records              : 2" in rendered
    assert "Skipped liquidity/near-limit     : 1" in rendered
    assert "Open cost                        : +$12.50" in rendered
    assert "Gross executable bid value       : +$10.50" in rendered
    assert "Estimated exit fees              : +$0.20" in rendered
    assert "Fee-net liquidation value        : +$10.30" in rendered
    assert "Unrealized fee-net P&L           : $-2.20" in rendered
    assert "Unscorable liquidation cost      : +$0.00" in rendered
    assert "polymarket_us gross=+$10.50 fees=+$0.20 net=+$10.30" in rendered
    assert "Unknown mark cost" not in rendered
    assert "Drilldown: open exposure by resolution horizon" in rendered
    assert "0-3d venue=polymarket trades=1 exposure=$12.50" in rendered


def test_counterfactual_eval_report_hydrates_when_env_enabled(monkeypatch):
    from scripts.daily_review import _build_counterfactual_llm_eval_report

    captured: dict[str, object] = {}

    def fake_build_eval_report(*args, **kwargs):
        captured["market_detail_provider"] = kwargs.get("market_detail_provider")
        provider = kwargs.get("market_detail_provider")
        hydrated = provider("KXTEST") if provider else None
        return {
            "model_eval_status": "not_run_offline_eval_set_only",
            "target_counts": {
                "neutral_none_no_keywords": 1,
                "context_ready": 1 if hydrated else 0,
                "missing_contract_context": 0 if hydrated else 1,
                "hydrated_contract_context": 1 if hydrated else 0,
            },
            "cases": [],
        }

    class FakeClient:
        def get_market(self, ticker):
            return {"rules_primary": f"{ticker} resolves from official details"}

    monkeypatch.setenv("DAILY_REVIEW_COUNTERFACTUAL_HYDRATE_KALSHI_MARKETS", "true")
    monkeypatch.setattr(
        "scripts.daily_review.counterfactual_llm_eval.build_eval_report",
        fake_build_eval_report,
    )
    monkeypatch.setattr("scripts.daily_review.KalshiRestClient", FakeClient)

    report = _build_counterfactual_llm_eval_report(
        Path("logs/trades/live/trades.jsonl"),
        datetime(2026, 6, 21, tzinfo=timezone.utc),
        None,
        exclude_test=True,
    )

    assert captured["market_detail_provider"] is not None
    assert report["target_counts"]["hydrated_contract_context"] == 1


# ---------------------------------------------------------------------------
# Section 1 tier-filter + status-change diff
# ---------------------------------------------------------------------------


def test_build_tier_by_source_flattens_grouped_dict():
    scorecard = {
        "grouped": {
            "top performers": [{"source": "Reuters"}],
            "keep": [{"source": "AP"}, {"source": "BBC"}],
            "watch / investigate": [],
            "incubating": [{"source": "NewDesk"}],
            "prune": [{"source": "NoiseFeed"}],
            "remove immediately": [{"source": "DeadFeed"}],
            "disabled by source": [{"source": "BlockedFeed"}],
            "disabled by family": [],
        },
    }
    result = _build_tier_by_source(scorecard)
    assert result == {
        "Reuters": "top performers",
        "AP": "keep",
        "BBC": "keep",
        "NewDesk": "incubating",
        "NoiseFeed": "prune",
        "DeadFeed": "remove immediately",
        "BlockedFeed": "disabled by source",
    }


def test_build_tier_by_source_handles_missing_grouped():
    assert _build_tier_by_source({}) == {}
    assert _build_tier_by_source({"grouped": None}) == {}
    assert _build_tier_by_source({"grouped": {}}) == {}


def test_build_tier_by_source_skips_rows_without_source():
    scorecard = {"grouped": {"keep": [{"source": ""}, {"source": None}, {"observed_records": 5}, {"source": "Real"}]}}
    assert _build_tier_by_source(scorecard) == {"Real": "keep"}


def test_load_previous_tier_state_returns_none_for_missing_path(tmp_path):
    assert _load_previous_tier_state(tmp_path / "absent.json") is None


def test_load_previous_tier_state_returns_none_for_corrupt_json(tmp_path):
    target = tmp_path / "corrupt.json"
    target.write_text("not valid json {{", encoding="utf-8")
    assert _load_previous_tier_state(target) is None


def test_load_previous_tier_state_returns_none_for_non_dict_payload(tmp_path):
    target = tmp_path / "list.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    assert _load_previous_tier_state(target) is None


def test_load_previous_tier_state_returns_dict_for_valid_payload(tmp_path):
    target = tmp_path / "ok.json"
    target.write_text(
        json.dumps({"current": {"date": "2026-04-24", "tiers": {"X": "keep"}}, "previous": None}), encoding="utf-8"
    )
    state = _load_previous_tier_state(target)
    assert state == {"current": {"date": "2026-04-24", "tiers": {"X": "keep"}}, "previous": None}


def test_save_current_tier_state_first_run_creates_file_with_no_previous(tmp_path):
    target = tmp_path / "state.json"
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    _save_current_tier_state(target, {"Reuters": "keep"}, now)

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["current"]["date"] == "2026-04-25"
    assert saved["current"]["tiers"] == {"Reuters": "keep"}
    assert saved["previous"] is None


def test_save_current_tier_state_same_day_rerun_preserves_previous(tmp_path):
    target = tmp_path / "state.json"
    target.write_text(
        json.dumps(
            {
                "current": {
                    "date": "2026-04-25",
                    "tiers": {"Reuters": "keep"},
                    "saved_at_utc": "2026-04-25T08:00:00+00:00",
                },
                "previous": {"date": "2026-04-24", "tiers": {"Reuters": "top performers"}},
            }
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc)
    _save_current_tier_state(target, {"Reuters": "watch / investigate"}, now)

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["current"]["date"] == "2026-04-25"
    assert saved["current"]["tiers"] == {"Reuters": "watch / investigate"}
    # Yesterday's reference must NOT be overwritten by a same-day rerun.
    assert saved["previous"] == {"date": "2026-04-24", "tiers": {"Reuters": "top performers"}}


def test_save_current_tier_state_new_day_shifts_current_to_previous(tmp_path):
    target = tmp_path / "state.json"
    target.write_text(
        json.dumps(
            {
                "current": {"date": "2026-04-24", "tiers": {"Reuters": "keep"}},
                "previous": {"date": "2026-04-23", "tiers": {"Reuters": "top performers"}},
            }
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    _save_current_tier_state(target, {"Reuters": "watch / investigate"}, now)

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["current"]["date"] == "2026-04-25"
    assert saved["previous"] == {"date": "2026-04-24", "tiers": {"Reuters": "keep"}}


def test_format_tier_change_lines_no_prior_baseline_returns_first_run_notice():
    lines = _format_tier_change_lines(None, {"X": "keep"}, prior_date=None)
    assert lines == ["  Status changes since previous report : (no prior baseline; first run or state reset)"]


def test_format_tier_change_lines_no_changes_returns_empty_notice():
    prior = {"Reuters": "keep", "AP": "top performers"}
    current = {"Reuters": "keep", "AP": "top performers"}
    lines = _format_tier_change_lines(prior, current, prior_date="2026-04-24")
    assert lines == [
        "  Status changes since previous report (vs 2026-04-24):",
        "    (no tier regressions or new silences since previous report)",
    ]


def test_format_tier_change_lines_reports_degradation_and_silence():
    prior = {
        "Reuters": "top performers",  # -> watch (regression, jump 2)
        "AP": "keep",  # unchanged
        "BBC": "keep",  # silent
        "NoiseFeed": "prune",  # already low-tier; ignored
        "Disabled": "disabled by source",  # ignored
    }
    current = {
        "Reuters": "watch / investigate",
        "AP": "keep",
        "NoiseFeed": "prune",
    }
    lines = _format_tier_change_lines(prior, current, prior_date="2026-04-24")
    assert lines[0] == "  Status changes since previous report (vs 2026-04-24):"
    body = lines[1:]
    # Reuters jumped 2 tier levels; sorted ahead of single-step regressions.
    assert any("Reuters: top performers -> watch / investigate" in line for line in body)
    assert any("BBC: SILENT (was keep" in line for line in body)
    # Sources that were already in non-visible tiers shouldn't be reported.
    assert not any("NoiseFeed" in line for line in body)
    assert not any("Disabled:" in line for line in body)


def test_format_tier_change_lines_does_not_report_improvements():
    """Section is for regressions only; tier improvements are not the focus."""
    prior = {"Reuters": "watch / investigate"}
    current = {"Reuters": "top performers"}
    lines = _format_tier_change_lines(prior, current, prior_date="2026-04-24")
    assert lines == [
        "  Status changes since previous report (vs 2026-04-24):",
        "    (no tier regressions or new silences since previous report)",
    ]


def test_build_daily_review_filters_waterfall_by_tier_and_appends_summary(monkeypatch, tmp_path):
    """End-to-end: when scorecard tiers a source as 'prune', section 1 hides
    that source from the waterfall and reports the count in the summary line."""
    monkeypatch.setattr(
        "scripts.daily_review._ollama_runtime_summary",
        lambda: "configured=stub health=ok available=['stub']",
    )
    monkeypatch.setattr(
        "scripts.daily_review.freshness_diagnostics.summarize",
        lambda *args, **kwargs: {
            "sources": {
                "Reuters": {
                    "source": "Reuters",
                    "observed_records": 50,
                    "fresh_passes": 40,
                    "early_stale_drops": 5,
                    "within_300s": 20,
                    "stale_rate": 0.10,
                    "median_age_seconds": 100.0,
                    "freshest_age_seconds": 30.0,
                    "p90_age_seconds": 180.0,
                    "age_samples_count": 40,
                    "interpretation": "fast operational",
                },
                "NoiseFeed": {
                    "source": "NoiseFeed",
                    "observed_records": 200,
                    "fresh_passes": 1,
                    "early_stale_drops": 199,
                    "within_300s": 0,
                    "stale_rate": 0.99,
                    "median_age_seconds": 100000.0,
                    "freshest_age_seconds": 100000.0,
                    "p90_age_seconds": 200000.0,
                    "age_samples_count": 200,
                    "interpretation": "chronically late",
                },
                "DeadFeed": {
                    "source": "DeadFeed",
                    "observed_records": 1,
                    "fresh_passes": 0,
                    "early_stale_drops": 1,
                    "within_300s": 0,
                    "stale_rate": 1.0,
                    "median_age_seconds": 999999.0,
                    "freshest_age_seconds": 999999.0,
                    "p90_age_seconds": 999999.0,
                    "age_samples_count": 1,
                    "interpretation": "insufficient data",
                },
            }
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.match_quality_diagnostics.summarize",
        lambda *args, **kwargs: {
            "match_records": 0,
            "low_quality_matches": 0,
            "heuristic_flags": Counter(),
            "pre_llm_would_block": 0,
            "pre_llm_would_block_by_source": Counter(),
            "pre_llm_would_block_by_ticker": Counter(),
            "examples_bad": [],
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.decision_funnel_summary.summarize",
        lambda *args, **kwargs: {
            "records_kept": 0,
            "event_counts": {},
            "analysis_rejected_reasons": {},
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.signal_edge_diagnostics.summarize",
        lambda *args, **kwargs: {
            "counts": {},
            "llm_observability": {
                "attempted": 0,
                "skipped_routing": 0,
                "skipped_routing_reasons": Counter(),
                "result_used": 0,
                "fallback": 0,
                "status_counts": Counter(),
                "latency_ms_samples": [],
                "total_stage_ms_samples": [],
                "queue_wait_ms_samples": [],
                "http_round_trip_ms_samples": [],
                "parse_ms_samples": [],
                "contention_observed": 0,
                "max_in_flight_at_entry": 0,
            },
            "skip_breakdown": {},
            "audit_rows": [],
            "llm_value_add": {
                "llm_rows": 0,
                "near_neutral_outputs": 0,
                "non_zero_edge_outputs": 0,
                "meaningful_signals": 0,
                "trade_candidates": 0,
                "llm_created_edge": 0,
                "probability_movement_buckets": Counter(),
                "edge_magnitude_buckets": Counter(),
                "meaningful_sources": Counter(),
                "meaningful_tickers": Counter(),
                "strong_examples": [],
                "neutral_examples": [],
                "rare_non_neutral_examples": [],
                "segmentation": {
                    "by_source": [],
                    "by_ticker": [],
                    "by_price_band": [],
                    "timing": {"available": False, "reason": "n/a"},
                    "headline_category": {"available": False, "reason": "n/a"},
                },
            },
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.paper_performance_drilldown.summarize",
        lambda *args, **kwargs: {
            "total_trades": 0,
            "resolved_trades": 0,
            "open_trades": 0,
            "win_rate": None,
            "total_pnl": 0.0,
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.match_suppression_audit.summarize",
        lambda *args, **kwargs: {
            "total_candidates": 0,
            "safe_count": 0,
            "risky_count": 0,
            "by_source": {},
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.keyword_feedback.summarize",
        lambda *args, **kwargs: {
            "no_keyword_misses": 0,
            "corroborating_keyword_gate_records": 0,
            "unique_candidate_phrases": 0,
            "grouped_phrases": {},
        },
    )
    captured_scorecard_kwargs = {}

    def _capture_scorecard(*args, **kwargs):
        captured_scorecard_kwargs.update(kwargs)
        return {
            "rows": [],
            "log_meta": {"records_kept": 0},
            "db_exists": False,
            "grouped": {
                "top performers": [],
                "keep": [{"source": "Reuters"}],
                "watch / investigate": [],
                "incubating": [],
                "prune": [{"source": "NoiseFeed"}],
                # DeadFeed carries a lifetime funnel so Section 8 must render it
                # beside the "remove immediately" verdict (B). An operator who
                # sees a bare verdict without the funnel could delete a producer.
                "remove immediately": [
                    {
                        "source": "DeadFeed",
                        "lifetime_posts": 300,
                        "lifetime_signals": 5,
                        "lifetime_opportunities": 2,
                        "lifetime_trades": 1,
                    }
                ],
                "disabled by source": [],
                "disabled by family": [],
            },
        }

    monkeypatch.setattr("scripts.daily_review.source_scorecard.summarize", _capture_scorecard)

    state_path = tmp_path / "tier_state.json"
    lines = build_daily_review(
        trades_path=Path("logs/trades/trades.jsonl"),
        paper_db_path=Path("data/paper_trades.db"),
        since=None,
        until=None,
        top=5,
        exclude_test=True,
        tier_state_path=state_path,
    )
    rendered = "\n".join(lines)

    # Regression guard (PROFIT-ROT-002): build_daily_review MUST pass the wide
    # recommendation window into the scorecard. If a refactor drops this kwarg the
    # operator report silently reverts to the broken 24h judgment that flagged the
    # bot's top producers for deletion -- and nothing else in the suite catches it.
    from scripts import source_scorecard

    assert (
        captured_scorecard_kwargs.get("recommendation_window_days")
        == source_scorecard.DEFAULT_RECOMMENDATION_WINDOW_DAYS
    )
    # Section 8 renders the lifetime funnel beside every tier verdict, so
    # "remove immediately" can never print without the source's real yield.
    section8 = rendered.split("8. SOURCE SCORECARD")[1]
    assert "DeadFeed" in section8
    assert "| life: posts=300 sig=5 opp=2 trade=1" in section8

    assert "operationally-relevant tiers only" in rendered
    # Reuters (keep) is shown.
    assert "Reuters" in rendered
    # NoiseFeed (prune) and DeadFeed (remove immediately) are hidden from
    # the per-source waterfall, but may still appear in the unrelated
    # "top stale sources" drilldown above it. Scope assertion to the
    # waterfall sub-section by splitting on its header.
    waterfall_section = rendered.split("operationally-relevant tiers only")[1].split("2. MATCHING")[0]
    assert "NoiseFeed" not in waterfall_section
    assert "DeadFeed" not in waterfall_section
    assert "Reuters" in waterfall_section
    # Hidden-count summary line breaks down by category. NoiseFeed counts
    # under its tier (`prune`); DeadFeed (interpretation=insufficient data)
    # is dropped at the freshness-bucket layer before the tier filter runs.
    assert "2 lower-relevance sources hidden" in rendered
    assert "prune=1" in rendered
    assert "insufficient data=1" in rendered
    # First-run notice when no prior baseline exists.
    assert "no prior baseline" in rendered
    # State file was written.
    assert state_path.exists()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["current"]["tiers"] == {
        "Reuters": "keep",
        "NoiseFeed": "prune",
        "DeadFeed": "remove immediately",
    }
    assert saved["previous"] is None


def test_build_daily_review_uses_persisted_state_for_change_diff(monkeypatch, tmp_path):
    """When a prior baseline exists, regressions appear in the changes section."""
    state_path = tmp_path / "tier_state.json"
    state_path.write_text(
        json.dumps(
            {
                "current": {"date": "2026-04-25", "tiers": {"Reuters": "keep", "BBC": "keep"}},
                "previous": {"date": "2026-04-24", "tiers": {"Reuters": "top performers", "BBC": "keep"}},
            }
        ),
        encoding="utf-8",
    )

    # Bare-minimum mocks; reuse pattern from prior test.
    monkeypatch.setattr("scripts.daily_review._ollama_runtime_summary", lambda: "stub")
    monkeypatch.setattr(
        "scripts.daily_review.freshness_diagnostics.summarize",
        lambda *a, **kw: {
            "sources": {
                "Reuters": {
                    "source": "Reuters",
                    "observed_records": 10,
                    "fresh_passes": 5,
                    "early_stale_drops": 2,
                    "within_300s": 3,
                    "stale_rate": 0.2,
                    "median_age_seconds": 100.0,
                    "freshest_age_seconds": 30.0,
                    "p90_age_seconds": 200.0,
                    "age_samples_count": 5,
                    "interpretation": "near-threshold",
                }
            }
        },
    )
    for stub_target, stub_value in [
        (
            "match_quality_diagnostics",
            {
                "match_records": 0,
                "low_quality_matches": 0,
                "heuristic_flags": Counter(),
                "pre_llm_would_block": 0,
                "pre_llm_would_block_by_source": Counter(),
                "pre_llm_would_block_by_ticker": Counter(),
                "examples_bad": [],
            },
        ),
        (
            "decision_funnel_summary",
            {
                "records_kept": 0,
                "event_counts": {},
                "analysis_rejected_reasons": {},
            },
        ),
        (
            "signal_edge_diagnostics",
            {
                "counts": {},
                "llm_observability": {
                    "attempted": 0,
                    "skipped_routing": 0,
                    "skipped_routing_reasons": Counter(),
                    "result_used": 0,
                    "fallback": 0,
                    "status_counts": Counter(),
                    "latency_ms_samples": [],
                    "total_stage_ms_samples": [],
                    "queue_wait_ms_samples": [],
                    "http_round_trip_ms_samples": [],
                    "parse_ms_samples": [],
                    "contention_observed": 0,
                    "max_in_flight_at_entry": 0,
                },
                "skip_breakdown": {},
                "audit_rows": [],
                "llm_value_add": {
                    "llm_rows": 0,
                    "near_neutral_outputs": 0,
                    "non_zero_edge_outputs": 0,
                    "meaningful_signals": 0,
                    "trade_candidates": 0,
                    "llm_created_edge": 0,
                    "probability_movement_buckets": Counter(),
                    "edge_magnitude_buckets": Counter(),
                    "meaningful_sources": Counter(),
                    "meaningful_tickers": Counter(),
                    "strong_examples": [],
                    "neutral_examples": [],
                    "rare_non_neutral_examples": [],
                    "segmentation": {
                        "by_source": [],
                        "by_ticker": [],
                        "by_price_band": [],
                        "timing": {"available": False, "reason": "n/a"},
                        "headline_category": {"available": False, "reason": "n/a"},
                    },
                },
            },
        ),
        (
            "paper_performance_drilldown",
            {
                "total_trades": 0,
                "resolved_trades": 0,
                "open_trades": 0,
                "win_rate": None,
                "total_pnl": 0.0,
            },
        ),
        (
            "match_suppression_audit",
            {
                "total_candidates": 0,
                "safe_count": 0,
                "risky_count": 0,
                "by_source": {},
            },
        ),
        (
            "keyword_feedback",
            {
                "no_keyword_misses": 0,
                "corroborating_keyword_gate_records": 0,
                "unique_candidate_phrases": 0,
                "grouped_phrases": {},
            },
        ),
        (
            "source_scorecard",
            {
                "rows": [],
                "log_meta": {"records_kept": 0},
                "db_exists": False,
                "grouped": {
                    "top performers": [],
                    "keep": [{"source": "Reuters"}],
                    "watch / investigate": [],
                    "incubating": [],
                    "prune": [],
                    "remove immediately": [],
                    "disabled by source": [],
                    "disabled by family": [],
                },
            },
        ),
    ]:
        monkeypatch.setattr(f"scripts.daily_review.{stub_target}.summarize", lambda *a, _v=stub_value, **kw: _v)

    lines = build_daily_review(
        trades_path=Path("logs/trades/trades.jsonl"),
        paper_db_path=Path("data/paper_trades.db"),
        since=None,
        until=None,
        top=5,
        exclude_test=True,
        tier_state_path=state_path,
    )
    rendered = "\n".join(lines)

    assert "Status changes since previous report (vs 2026-04-24):" in rendered
    assert "Reuters: top performers -> keep" in rendered
    assert "BBC: SILENT (was keep" in rendered
