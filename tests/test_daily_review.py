from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.daily_review import build_daily_review


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
