import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.daily_review import (
    _build_tier_by_source,
    _format_tier_change_lines,
    _load_previous_tier_state,
    _save_current_tier_state,
    build_daily_review,
)


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
            "analysis_rejected_reasons": {"stale_news": 2, "no_keywords": 1},
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
            },
            "skip_breakdown": {
                "zero_edge": 1,
                "below_threshold": 1,
                "duplicate": 1,
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
                "llm_rows": 1,
                "near_neutral_outputs": 0,
                "non_zero_edge_outputs": 1,
                "meaningful_signals": 1,
                "trade_candidates": 1,
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
        "scripts.daily_review.paper_performance_drilldown.summarize",
        lambda *args, **kwargs: {
            "total_trades": 2,
            "resolved_trades": 1,
            "open_trades": 1,
            "win_rate": 1.0,
            "total_pnl": 5.0,
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
    assert "1. INGESTION" in rendered
    assert "2. MATCHING" in rendered
    assert "3. ANALYSIS" in rendered
    assert "4. EDGE FORMATION" in rendered
    assert "5. EXECUTION" in rendered
    assert "6. LLM VALUE-ADD ANALYSIS" in rendered
    assert "7. LLM VALUE-ADD SEGMENTATION" in rendered
    assert "Appendix" in rendered
    assert "Low-quality flagged              : 2 (25.0%)" in rendered
    assert "Pre-LLM gate would-block         : 3 (37.5%)" in rendered
    assert "Drilldown: pre-LLM would-block by source (top)" in rendered
    assert "Drilldown: pre-LLM would-block by market (top)" in rendered
    assert "Drilldown: per-source freshness waterfall" in rendered
    assert "LLM rows                         : 1" in rendered
    assert "LLM attempted (post-filter)       : 1" in rendered
    assert "LLM skipped (routing)             : 2" in rendered
    assert "Routing skip reasons              : price_band_excluded=2" in rendered
    assert "Meaningful signals                : 1 (100.0%)" in rendered
    assert "Trade candidates                  : 1 (100.0%)" in rendered
    assert "Top sources by meaningful signal rate" in rendered
    assert "Market price bands by meaningful signal rate" in rendered
    assert "Rare non-neutral cases" in rendered
    assert "Ollama runtime                   : configured=qwen2.5:7b health=ok available=['qwen2.5:7b']" in rendered
    assert "Paper trades                     : 2" in rendered


# ---------------------------------------------------------------------------
# Section 1 tier-filter + status-change diff
# ---------------------------------------------------------------------------


def test_build_tier_by_source_flattens_grouped_dict():
    scorecard = {
        "grouped": {
            "top performers": [{"source": "Reuters"}],
            "keep": [{"source": "AP"}, {"source": "BBC"}],
            "watch / investigate": [],
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
    target.write_text(json.dumps({"current": {"date": "2026-04-24", "tiers": {"X": "keep"}}, "previous": None}), encoding="utf-8")
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
        json.dumps({
            "current": {"date": "2026-04-25", "tiers": {"Reuters": "keep"}, "saved_at_utc": "2026-04-25T08:00:00+00:00"},
            "previous": {"date": "2026-04-24", "tiers": {"Reuters": "top performers"}},
        }),
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
        json.dumps({
            "current": {"date": "2026-04-24", "tiers": {"Reuters": "keep"}},
            "previous": {"date": "2026-04-23", "tiers": {"Reuters": "top performers"}},
        }),
        encoding="utf-8",
    )
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    _save_current_tier_state(target, {"Reuters": "watch / investigate"}, now)

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["current"]["date"] == "2026-04-25"
    assert saved["previous"] == {"date": "2026-04-24", "tiers": {"Reuters": "keep"}}


def test_format_tier_change_lines_no_prior_baseline_returns_first_run_notice():
    lines = _format_tier_change_lines(None, {"X": "keep"}, prior_date=None)
    assert lines == [
        "  Status changes since previous report : (no prior baseline; first run or state reset)"
    ]


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
        "Reuters": "top performers",       # -> watch (regression, jump 2)
        "AP": "keep",                       # unchanged
        "BBC": "keep",                      # silent
        "NoiseFeed": "prune",               # already low-tier; ignored
        "Disabled": "disabled by source",   # ignored
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
                "attempted": 0, "skipped_routing": 0,
                "skipped_routing_reasons": Counter(),
                "result_used": 0, "fallback": 0,
                "status_counts": Counter(), "latency_ms_samples": [],
                "total_stage_ms_samples": [], "queue_wait_ms_samples": [],
                "http_round_trip_ms_samples": [], "parse_ms_samples": [],
                "contention_observed": 0, "max_in_flight_at_entry": 0,
            },
            "skip_breakdown": {},
            "audit_rows": [],
            "llm_value_add": {
                "llm_rows": 0, "near_neutral_outputs": 0, "non_zero_edge_outputs": 0,
                "meaningful_signals": 0, "trade_candidates": 0, "llm_created_edge": 0,
                "probability_movement_buckets": Counter(),
                "edge_magnitude_buckets": Counter(),
                "meaningful_sources": Counter(), "meaningful_tickers": Counter(),
                "strong_examples": [], "neutral_examples": [], "rare_non_neutral_examples": [],
                "segmentation": {
                    "by_source": [], "by_ticker": [], "by_price_band": [],
                    "timing": {"available": False, "reason": "n/a"},
                    "headline_category": {"available": False, "reason": "n/a"},
                },
            },
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.paper_performance_drilldown.summarize",
        lambda *args, **kwargs: {
            "total_trades": 0, "resolved_trades": 0, "open_trades": 0,
            "win_rate": None, "total_pnl": 0.0,
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.match_suppression_audit.summarize",
        lambda *args, **kwargs: {
            "total_candidates": 0, "safe_count": 0, "risky_count": 0, "by_source": {},
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.keyword_feedback.summarize",
        lambda *args, **kwargs: {
            "no_keyword_misses": 0, "corroborating_keyword_gate_records": 0,
            "unique_candidate_phrases": 0, "grouped_phrases": {},
        },
    )
    monkeypatch.setattr(
        "scripts.daily_review.source_scorecard.summarize",
        lambda *args, **kwargs: {
            "rows": [], "log_meta": {"records_kept": 0}, "db_exists": False,
            "grouped": {
                "top performers": [],
                "keep": [{"source": "Reuters"}],
                "watch / investigate": [],
                "prune": [{"source": "NoiseFeed"}],
                "remove immediately": [{"source": "DeadFeed"}],
                "disabled by source": [],
                "disabled by family": [],
            },
        },
    )

    state_path = tmp_path / "tier_state.json"
    lines = build_daily_review(
        trades_path=Path("logs/trades/trades.jsonl"),
        paper_db_path=Path("data/paper_trades.db"),
        since=None, until=None, top=5, exclude_test=True,
        tier_state_path=state_path,
    )
    rendered = "\n".join(lines)

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
        json.dumps({
            "current": {"date": "2026-04-25", "tiers": {"Reuters": "keep", "BBC": "keep"}},
            "previous": {"date": "2026-04-24", "tiers": {"Reuters": "top performers", "BBC": "keep"}},
        }),
        encoding="utf-8",
    )

    # Bare-minimum mocks; reuse pattern from prior test.
    monkeypatch.setattr("scripts.daily_review._ollama_runtime_summary", lambda: "stub")
    monkeypatch.setattr(
        "scripts.daily_review.freshness_diagnostics.summarize",
        lambda *a, **kw: {"sources": {"Reuters": {
            "source": "Reuters", "observed_records": 10, "fresh_passes": 5,
            "early_stale_drops": 2, "within_300s": 3, "stale_rate": 0.2,
            "median_age_seconds": 100.0, "freshest_age_seconds": 30.0,
            "p90_age_seconds": 200.0, "age_samples_count": 5,
            "interpretation": "near-threshold",
        }}},
    )
    for stub_target, stub_value in [
        ("match_quality_diagnostics", {
            "match_records": 0, "low_quality_matches": 0, "heuristic_flags": Counter(),
            "pre_llm_would_block": 0, "pre_llm_would_block_by_source": Counter(),
            "pre_llm_would_block_by_ticker": Counter(), "examples_bad": [],
        }),
        ("decision_funnel_summary", {
            "records_kept": 0, "event_counts": {}, "analysis_rejected_reasons": {},
        }),
        ("signal_edge_diagnostics", {
            "counts": {}, "llm_observability": {
                "attempted": 0, "skipped_routing": 0, "skipped_routing_reasons": Counter(),
                "result_used": 0, "fallback": 0, "status_counts": Counter(),
                "latency_ms_samples": [], "total_stage_ms_samples": [],
                "queue_wait_ms_samples": [], "http_round_trip_ms_samples": [],
                "parse_ms_samples": [], "contention_observed": 0, "max_in_flight_at_entry": 0,
            },
            "skip_breakdown": {}, "audit_rows": [],
            "llm_value_add": {
                "llm_rows": 0, "near_neutral_outputs": 0, "non_zero_edge_outputs": 0,
                "meaningful_signals": 0, "trade_candidates": 0, "llm_created_edge": 0,
                "probability_movement_buckets": Counter(), "edge_magnitude_buckets": Counter(),
                "meaningful_sources": Counter(), "meaningful_tickers": Counter(),
                "strong_examples": [], "neutral_examples": [], "rare_non_neutral_examples": [],
                "segmentation": {"by_source": [], "by_ticker": [], "by_price_band": [],
                                 "timing": {"available": False, "reason": "n/a"},
                                 "headline_category": {"available": False, "reason": "n/a"}},
            },
        }),
        ("paper_performance_drilldown", {
            "total_trades": 0, "resolved_trades": 0, "open_trades": 0,
            "win_rate": None, "total_pnl": 0.0,
        }),
        ("match_suppression_audit", {
            "total_candidates": 0, "safe_count": 0, "risky_count": 0, "by_source": {},
        }),
        ("keyword_feedback", {
            "no_keyword_misses": 0, "corroborating_keyword_gate_records": 0,
            "unique_candidate_phrases": 0, "grouped_phrases": {},
        }),
        ("source_scorecard", {
            "rows": [], "log_meta": {"records_kept": 0}, "db_exists": False,
            "grouped": {
                "top performers": [], "keep": [{"source": "Reuters"}],
                "watch / investigate": [], "prune": [], "remove immediately": [],
                "disabled by source": [], "disabled by family": [],
            },
        }),
    ]:
        monkeypatch.setattr(f"scripts.daily_review.{stub_target}.summarize",
                            lambda *a, _v=stub_value, **kw: _v)

    lines = build_daily_review(
        trades_path=Path("logs/trades/trades.jsonl"),
        paper_db_path=Path("data/paper_trades.db"),
        since=None, until=None, top=5, exclude_test=True,
        tier_state_path=state_path,
    )
    rendered = "\n".join(lines)

    assert "Status changes since previous report (vs 2026-04-24):" in rendered
    assert "Reuters: top performers -> keep" in rendered
    assert "BBC: SILENT (was keep" in rendered
