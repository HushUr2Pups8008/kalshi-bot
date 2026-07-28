from collections import Counter
from datetime import datetime, timezone
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.decision_funnel_summary import (
    infer_signal_type,
    parse_date_end,
    parse_date_start,
    print_summary,
    stale_age_bucket,
    summarize,
)
from tests._helpers import write_jsonl


@pytest.fixture
def local_tmp_dir():
    root = Path(__file__).resolve().parent / "_tmp_decision_funnel_summary"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_summarize_empty_file(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    path.write_text("", encoding="utf-8")

    stats = summarize(path, since=None, until=None)

    assert stats["lines_total"] == 0
    assert stats["lines_malformed"] == 0
    assert stats["records_kept"] == 0
    assert not stats["event_counts"]
    assert not stats["skip_reasons"]
    assert not stats["skip_categories"]
    assert not stats["analysis_rejected_reasons"]
    assert not stats["analysis_rejected_categories"]
    assert not stats["stale_news_age_buckets"]
    assert not stats["stale_news_sources"]
    assert not stats["stale_news_tickers"]
    assert not stats["path_counts"]
    assert not stats["execution_paths"]


def test_summarize_skips_malformed_json_lines(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            '{"type": "SIGNAL", "ts": "2026-04-11T00:00:00+00:00"}',
            '{"type": "SIGNAL"',
            '["not", "a", "dict"]',
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["lines_total"] == 3
    assert stats["lines_malformed"] == 2
    assert stats["records_kept"] == 1
    assert stats["event_counts"]["SIGNAL"] == 1
    assert stats["path_counts"]["news"] == 1


def test_summarize_reads_partitioned_trade_root(local_tmp_dir):
    root = local_tmp_dir / "trades"
    write_jsonl(
        root / "archive" / "2026" / "04" / "2026-04-11.jsonl",
        [
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "Reuters",
                "ticker": "KXOLD",
                "age_seconds": 600,
                "ts": "2026-04-11T00:00:00+00:00",
            },
        ],
    )
    write_jsonl(
        root / "live" / "trades.jsonl",
        [
            {"type": "PAPER_TRADE", "signal_source": "Reuters", "ticker": "KXNEW", "ts": "2026-04-12T00:00:00+00:00"},
        ],
    )

    stats = summarize(root, since=None, until=None)

    assert stats["lines_total"] == 2
    assert stats["records_kept"] == 2
    assert stats["event_counts"]["ANALYSIS_REJECTED"] == 1
    assert stats["event_counts"]["PAPER_TRADE"] == 1


def test_summarize_handles_missing_optional_fields_gracefully(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SKIPPED", "ts": "2026-04-11T00:00:00+00:00"},
            {"ts": "2026-04-11T01:00:00+00:00"},
            {"type": "", "ts": "2026-04-11T02:00:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["records_kept"] == 3
    assert stats["event_counts"]["SKIPPED"] == 1
    assert stats["event_counts"]["UNKNOWN"] == 2
    assert stats["skip_reasons"]["unknown"] == 1
    assert stats["skip_categories"]["unknown"] == 1
    assert not stats["analysis_rejected_reasons"]
    assert not stats["analysis_rejected_categories"]
    assert stats["path_counts"]["news"] == 1
    assert not stats["execution_paths"]


def test_summarize_counts_funnel_stages(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "SIGNAL", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "OPPORTUNITY", "ts": "2026-04-11T00:01:00+00:00"},
            {"type": "SKIPPED", "reason": "cooldown", "ts": "2026-04-11T00:02:00+00:00"},
            {"type": "PAPER_TRADE", "ts": "2026-04-11T00:03:00+00:00"},
            {"type": "LIVE_ORDER", "ts": "2026-04-11T00:04:00+00:00"},
            {"type": "PAPER_RESOLUTION", "ts": "2026-04-11T00:05:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["event_counts"]["ANALYSIS_REJECTED"] == 1
    assert stats["event_counts"]["SIGNAL"] == 1
    assert stats["event_counts"]["OPPORTUNITY"] == 1
    assert stats["event_counts"]["SKIPPED"] == 1
    assert stats["event_counts"]["PAPER_TRADE"] == 1
    assert stats["event_counts"]["LIVE_ORDER"] == 1
    assert stats["event_counts"]["PAPER_RESOLUTION"] == 1
    assert stats["path_counts"]["news"] == 6


def test_summarize_aggregates_skip_reasons(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SKIPPED", "reason": "cooldown", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "SKIPPED", "reason": "cooldown", "skip_category": "cooldown", "ts": "2026-04-11T00:01:00+00:00"},
            {
                "type": "SKIPPED",
                "reason": "same-signal duplicate",
                "skip_category": "duplicate",
                "ts": "2026-04-11T00:02:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["skip_reasons"]["cooldown"] == 2
    assert stats["skip_reasons"]["same-signal duplicate"] == 1
    assert stats["skip_categories"]["unknown"] == 1
    assert stats["skip_categories"]["cooldown"] == 1
    assert stats["skip_categories"]["duplicate"] == 1


def test_summarize_aggregates_analysis_rejected_reasons(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "ts": "2026-04-11T00:01:00+00:00"},
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "ts": "2026-04-11T00:02:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["event_counts"]["ANALYSIS_REJECTED"] == 3
    assert stats["analysis_rejected_reasons"]["stale_news"] == 2
    assert stats["analysis_rejected_reasons"]["no_keywords"] == 1
    assert stats["analysis_rejected_categories"]["post_llm_neutral_empty_keywords"] == 1
    assert not stats["skip_reasons"]


def test_summarize_tracks_false_positive_neutral_match_reviews(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "MATCH_LLM_REVIEW",
                "verdict": "false_positive_neutral",
                "source": "NYT > U.S. News",
                "ticker": "PACCC-USSE-MIDTERMS-2026-11-03-REP",
                "market_prefix": "polymarket_us:paccc-usse-midterms",
                "keyword_count": 0,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "verdict": "true_positive",
                "source": "Reuters",
                "ticker": "KXREAL",
                "market_prefix": "KXREAL",
                "keyword_count": 2,
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "verdict": "false_positive_neutral",
                "source": "Politico",
                "ticker": "KXKEYWORDED",
                "market_prefix": "KXKEYWORDED",
                "keyword_count": 2,
                "ts": "2026-04-11T00:02:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["match_llm_reviews_total"] == 3
    assert stats["match_llm_review_verdicts"]["false_positive_neutral"] == 2
    assert stats["match_llm_review_verdicts"]["true_positive"] == 1
    assert stats["false_positive_neutral_empty_keyword_sources"] == Counter({"NYT > U.S. News": 1})
    assert stats["false_positive_neutral_empty_keyword_tickers"] == Counter({"PACCC-USSE-MIDTERMS-2026-11-03-REP": 1})
    assert stats["false_positive_neutral_empty_keyword_prefixes"] == Counter({"polymarket_us:paccc-usse-midterms": 1})
    assert stats["false_positive_neutral_empty_keyword_reviews"] == 1


def test_summarize_tracks_stale_news_breakdowns(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "Reuters",
                "ticker": "KX1",
                "age_seconds": 60,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "Reuters",
                "ticker": "KX2",
                "age_seconds": 600,
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "AP",
                "ticker": "KX1",
                "age_seconds": 2400,
                "ts": "2026-04-11T00:02:00+00:00",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "AP",
                "ticker": "KX3",
                "age_seconds": 5400,
                "ts": "2026-04-11T00:03:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["stale_news_age_buckets"]["0-5m"] == 1
    assert stats["stale_news_age_buckets"]["5-15m"] == 1
    assert stats["stale_news_age_buckets"]["30-60m"] == 1
    assert stats["stale_news_age_buckets"]["60m+"] == 1
    assert stats["stale_news_sources"]["Reuters"] == 2
    assert stats["stale_news_sources"]["AP"] == 2
    assert stats["stale_news_tickers"]["KX1"] == 2
    assert stats["stale_news_tickers"]["KX2"] == 1
    assert stats["stale_news_tickers"]["KX3"] == 1


@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [
        (0, "0-5m"),
        (300, "5-15m"),
        (900, "15-30m"),
        (1800, "30-60m"),
        (3600, "60m+"),
        (-1, None),
        (None, None),
    ],
)
def test_stale_age_bucket_boundaries(age_seconds, expected):
    assert stale_age_bucket(age_seconds) == expected


def test_summarize_infers_path_contribution(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SIGNAL", "source": "Reuters", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "OPPORTUNITY", "reasoning": "[FADE/GEO/@Kalshi] bullish: hype", "ts": "2026-04-11T00:01:00+00:00"},
            {"type": "SKIPPED", "source": "price_fade", "ts": "2026-04-11T00:02:00+00:00"},
            {"type": "PAPER_RESOLUTION", "reasoning": "[FADE/GEO/@Kalshi] old", "ts": "2026-04-11T00:03:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["path_counts"]["news"] == 1
    assert stats["path_counts"]["fade_tweet"] == 1
    assert stats["path_counts"]["price_fade"] == 1
    assert "fade_tweet" not in stats["event_counts"]


def test_summarize_tracks_execution_contribution(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "PAPER_TRADE",
                "signal_type": "fade_tweet",
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "LIVE_ORDER",
                "source": "price_fade",
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "PAPER_TRADE",
                "signal_source": "Reuters",
                "ts": "2026-04-11T00:02:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["execution_paths"]["fade_tweet"] == 1
    assert stats["execution_paths"]["price_fade"] == 1
    assert stats["execution_paths"]["news"] == 1


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"signal_type": "fade_tweet"}, "fade_tweet"),
        ({"source": "price_fade"}, "price_fade"),
        ({"url": "kalshi://price_fade/KXTEST"}, "price_fade"),
        ({"reasoning": "[PRICE_FADE] KXTEST high_cross"}, "price_fade"),
        ({"reasoning": "[FADE/GEO/@Kalshi] bullish: hype"}, "fade_tweet"),
        ({"source": "Reuters"}, "news"),
    ],
)
def test_infer_signal_type_classification(record, expected):
    assert infer_signal_type(record) == expected


def test_summarize_applies_date_filter(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SIGNAL", "ts": "2026-04-09T23:59:59+00:00"},
            {"type": "OPPORTUNITY", "ts": "2026-04-10T12:00:00+00:00"},
            {"type": "PAPER_TRADE", "ts": "2026-04-11T23:59:59+00:00"},
            {"type": "SKIPPED", "ts": "2026-04-12T00:00:00+00:00"},
            {"type": "LIVE_ORDER"},
        ],
    )

    stats = summarize(
        path,
        since=parse_date_start("2026-04-10"),
        until=parse_date_end("2026-04-11"),
    )

    assert stats["records_kept"] == 2
    assert stats["event_counts"]["OPPORTUNITY"] == 1
    assert stats["event_counts"]["PAPER_TRADE"] == 1
    assert "SIGNAL" not in stats["event_counts"]
    assert "SKIPPED" not in stats["event_counts"]
    assert "LIVE_ORDER" not in stats["event_counts"]


def test_print_summary_handles_empty_stats(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    path.write_text("", encoding="utf-8")
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "DECISION FUNNEL SUMMARY" in output
    assert "No matching log records found." in output


def test_print_summary_handles_no_observable_funnel_stages(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "NEW_MARKET", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "PAPER_RESOLUTION", "ts": "2026-04-11T00:01:00+00:00"},
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "DECISION FUNNEL SUMMARY" in output
    assert "Signals logged                : 0" in output
    assert "Opportunities logged          : 0" in output
    assert "of signals" not in output
    assert "Executor skips               : 0" in output
    assert "Paper-trade records          : 0" in output
    assert "Live order submissions       : 0" in output
    assert "Path Contribution (decision-stage records)" in output
    assert "  (none)" in output


def test_print_summary_includes_pre_signal_rejections_when_present(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "ts": "2026-04-11T00:00:00+00:00"},
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {"type": "SIGNAL", "ts": "2026-04-11T00:02:00+00:00"},
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "Pre-signal rejections         : 2" in output
    assert "No-signal exits               : partially observable via ANALYSIS_REJECTED" in output
    assert "Pre-Signal Rejection Reasons" in output
    assert "Pre-Signal Rejection Branches" in output
    assert "stale_news" in output
    assert "no_keywords" in output
    assert "post_llm_neutral_empty_keywords" in output


def test_print_summary_includes_stale_news_sections(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "Reuters",
                "ticker": "KX1",
                "age_seconds": 120,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "reason": "stale_news",
                "source": "AP",
                "ticker": "KX2",
                "age_seconds": 4200,
                "ts": "2026-04-11T00:01:00+00:00",
            },
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "Stale-News Age Buckets" in output
    assert "Stale-News Sources" in output
    assert "Stale-News Tickers" in output
    assert "0-5m" in output
    assert "60m+" in output
    assert "Reuters" in output
    assert "KX1" in output


def test_print_summary_handles_absent_analysis_rejected_events_gracefully(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "SIGNAL", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "OPPORTUNITY", "ts": "2026-04-11T00:01:00+00:00"},
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "Pre-signal rejections         : 0" in output
    assert "No-signal exits               : not directly observable in trades.jsonl" in output
    assert "Pre-Signal Rejection Reasons" in output


def test_summarize_attributes_match_diagnostic_loss(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "MATCH_DIAGNOSTIC",
                "ticker": "KXTRUMPUAP-26MAY-JUL01",
                "source": "Just In News",
                "match_score": 0.0671,
                "would_fail_pre_llm_gate": True,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "MATCH_DIAGNOSTIC",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "France 24",
                "match_score": 0.0973,
                "would_fail_pre_llm_gate": False,
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "France 24",
                "ts": "2026-04-11T00:02:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["match_diagnostics_total"] == 2
    assert stats["signal_analysis_detail_total"] == 1
    assert stats["match_to_signal_detail_gap"] == 1
    assert stats["match_diagnostic_pre_llm_gate"]["would_fail"] == 1
    assert stats["match_diagnostic_pre_llm_gate"]["would_pass"] == 1
    assert stats["match_diagnostic_sources"]["Just In News"] == 1
    assert stats["match_diagnostic_tickers"]["KXTRUMPUAP-26MAY-JUL01"] == 1


def test_summarize_attributes_match_suppression_and_token_weights(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "MATCH_SUPPRESSED",
                "ticker": "KXTRUMPUAP-26MAY-JUL01",
                "source": "Just In News",
                "reason": "low_token_overlap+near_threshold_score",
                "match_score": 0.0671,
                "raw_score": 0.0821,
                "adjusted_score": 0.0671,
                "threshold": 0.06,
                "token_weight_multiplier": 0.8173,
                "venue": "kalshi",
                "market_prefix": "KXTRUMPUAP",
                "matched_tokens": ["trump"],
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "MATCH_WEIGHT_APPLIED",
                "ticker": "KXZELENSKYYOUT-26JUL01",
                "source": "The Kyiv Independent",
                "market_prefix": "KXZELENSKYYOUT",
                "pre_weight_score": 0.0666,
                "post_weight_score": 0.0174,
                "final_multiplier": 0.2606,
                "token_weights": {"ukraine": {"weight": 0.2606, "status": "automatic"}},
                "tokens": ["ukraine"],
                "ts": "2026-04-11T00:01:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["match_suppressed_reasons"]["low_token_overlap"] == 1
    assert stats["match_suppressed_reasons"]["near_threshold_score"] == 1
    assert stats["match_suppressed_sources"]["Just In News"] == 1
    assert stats["match_suppressed_tokens"]["trump"] == 1
    assert stats["match_suppressed_column_coverage"]["raw_score"] == 1
    assert stats["match_suppressed_column_coverage"]["adjusted_score"] == 1
    assert stats["match_suppressed_column_coverage"]["threshold"] == 1
    assert stats["match_suppressed_column_coverage"]["token_weight_multiplier"] == 1
    assert stats["match_suppressed_column_coverage"]["venue"] == 1
    assert stats["match_suppressed_column_coverage"]["market_prefix"] == 1
    assert stats["match_suppressed_venues"]["kalshi"] == 1
    assert stats["match_suppressed_examples"][0]["raw_score"] == pytest.approx(0.0821)
    assert stats["match_suppressed_examples"][0]["adjusted_score"] == pytest.approx(0.0671)
    assert stats["match_suppressed_examples"][0]["threshold"] == pytest.approx(0.06)
    assert stats["match_suppressed_examples"][0]["token_weight_multiplier"] == pytest.approx(0.8173)
    assert stats["match_weight_applied_total"] == 1
    assert stats["match_weight_tokens"]["ukraine"] == 1
    assert stats["match_weight_prefixes"]["KXZELENSKYYOUT"] == 1
    assert stats["match_weight_score_delta_total"] == pytest.approx(-0.0492)


def test_summarize_attributes_opportunities_and_skips(local_tmp_dir, capsys):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "OPPORTUNITY",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "France 24",
                "source_class": "news",
                "retrieval_mode": "source_hint",
                "evidence_id": "ev-op-1",
                "settlement_source_match": True,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "SKIPPED",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "France 24",
                "reason": "price 1.0c is near limit",
                "signal_meta": {
                    "trigger_evidence_id": "ev-skip-1",
                    "trigger_evidence_source_class": "regional",
                    "source_lane": "rss",
                },
                "ts": "2026-04-11T00:01:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["opportunity_sources"]["France 24"] == 1
    assert stats["opportunity_source_classes"]["news"] == 1
    assert stats["opportunity_retrieval_modes"]["source_hint"] == 1
    assert stats["opportunity_evidence_ids"]["ev-op-1"] == 1
    assert stats["opportunity_settlement_source_matches"]["True"] == 1
    assert stats["skip_sources"]["France 24"] == 1
    assert stats["skip_source_classes"]["regional"] == 1
    assert stats["skip_retrieval_modes"]["rss"] == 1
    assert stats["skip_evidence_ids"]["ev-skip-1"] == 1
    assert stats["skip_settlement_source_matches"]["unknown"] == 1

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out
    assert "Opportunity Attribution: Sources" in output
    assert "Opportunity Attribution: Settlement-Source Match" in output
    assert "Skip Attribution: Evidence IDs" in output
    assert "ev-skip-1" in output


def test_print_summary_includes_match_attribution_sections(capsys, local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "MATCH_DIAGNOSTIC",
                "ticker": "KXTRUMPUAP-26MAY-JUL01",
                "source": "Just In News",
                "would_fail_pre_llm_gate": True,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "MATCH_SUPPRESSED",
                "ticker": "KXTRUMPUAP-26MAY-JUL01",
                "source": "Just In News",
                "reason": "low_token_overlap+near_threshold_score",
                "raw_score": 0.0821,
                "adjusted_score": 0.0671,
                "threshold": 0.06,
                "token_weight_multiplier": 0.8173,
                "venue": "kalshi",
                "market_prefix": "KXTRUMPUAP",
                "matched_tokens": ["trump"],
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "MATCH_WEIGHT_APPLIED",
                "ticker": "KXZELENSKYYOUT-26JUL01",
                "source": "The Kyiv Independent",
                "market_prefix": "KXZELENSKYYOUT",
                "pre_weight_score": 0.0666,
                "post_weight_score": 0.0174,
                "final_multiplier": 0.2606,
                "token_weights": {"ukraine": {"weight": 0.2606, "status": "automatic"}},
                "tokens": ["ukraine"],
                "ts": "2026-04-11T00:02:00+00:00",
            },
        ],
    )
    stats = summarize(path, since=None, until=None)

    print_summary(stats, top=5, since=None, until=None)
    output = capsys.readouterr().out

    assert "Match Attribution" in output
    assert "Match diagnostics            : 1" in output
    assert "Match -> analysis detail gap : 1" in output
    assert "Pre-LLM quality gate" in output
    assert "Match Suppression Reasons" in output
    assert "low_token_overlap" in output
    assert "Match Suppression Column Coverage" in output
    assert "raw_score               : 1/1" in output
    assert "Match Suppression Venues" in output
    assert "kalshi" in output
    assert "raw=0.0821 adjusted=0.0671 threshold=0.06 multiplier=0.8173" in output
    assert "Match Weight Tokens" in output
    assert "ukraine" in output


def test_summarize_attributes_same_window_lifecycle_terminals_without_execution_claim(
    local_tmp_dir,
):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-g7",
                "venue": "kalshi",
                "ticker": "KXG7",
                "side": "no",
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-g7",
                "reason": "G7_open_exposure_drawdown",
                "venue": "kalshi",
                "ticker": "KXG7",
                "side": "no",
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-g7",
                "reason": "G7_open_exposure_drawdown",
                "venue": "kalshi",
                "ticker": "KXG7",
                "side": "no",
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-cap",
                "venue": "kalshi",
                "ticker": "KXCAP",
                "side": "no",
                "ts": "2026-04-11T00:02:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-cap",
                "reason": "capped_dollars=0",
                "capped_dollars": 0,
                "venue": "kalshi",
                "ticker": "KXCAP",
                "side": "no",
                "ts": "2026-04-11T00:03:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-pending",
                "venue": "kalshi",
                "ticker": "KXPENDING",
                "side": "no",
                "ts": "2026-04-11T00:04:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-orphan",
                "reason": "cooldown",
                "venue": "kalshi",
                "ticker": "KXORPHAN",
                "side": "no",
                "ts": "2026-04-11T00:05:00+00:00",
            },
            {"type": "OPPORTUNITY", "lifecycle_id": "   ", "ts": "2026-04-11T00:06:00+00:00"},
            {"type": "PAPER_TRADE", "ts": "2026-04-11T00:07:00+00:00"},
        ],
    )

    stats = summarize(path, since=None, until=None)

    assert stats["same_window_lifecycle_attribution"] == {
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
        "live_submission_event_rows": 0,
        "live_submission_linked_event_rows": 0,
        "unknown_live_submission_event_rows": 0,
        "unknown_live_submission_linked_event_rows": 0,
        "live_submission_intent_event_rows": 0,
        "live_submission_intent_linked_event_rows": 0,
        "unattributed_event_counts": Counter({"OPPORTUNITY": 1, "PAPER_TRADE": 1}),
    }


def test_summarize_lifecycle_attribution_does_not_bridge_window_boundaries(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-before",
                "venue": "kalshi",
                "ticker": "KXBEFORE",
                "side": "no",
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-before",
                "reason": "cooldown",
                "venue": "kalshi",
                "ticker": "KXBEFORE",
                "side": "no",
                "ts": "2026-04-11T01:00:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-window",
                "venue": "kalshi",
                "ticker": "KXWINDOW",
                "side": "no",
                "ts": "2026-04-11T01:00:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-window",
                "reason": "cooldown",
                "venue": "kalshi",
                "ticker": "KXWINDOW",
                "side": "no",
                "ts": "2026-04-11T02:00:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-after",
                "venue": "kalshi",
                "ticker": "KXAFTER",
                "side": "no",
                "ts": "2026-04-11T02:01:00+00:00",
            },
        ],
    )

    stats = summarize(
        path,
        since=datetime(2026, 4, 11, 1, tzinfo=timezone.utc),
        until=datetime(2026, 4, 11, 2, tzinfo=timezone.utc),
    )

    attribution = stats["same_window_lifecycle_attribution"]
    assert attribution["opportunity_lifecycle_count"] == 1
    assert attribution["other_skip_lifecycle_count"] == 1
    assert attribution["orphan_skip_lifecycle_count"] == 1
    assert attribution["pending_opportunity_lifecycle_count"] == 0


def test_summarize_lifecycle_attribution_requires_execution_join_and_quarantines_conflicts(
    local_tmp_dir,
    capsys,
):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-executed",
                "venue": "kalshi",
                "ticker": "KXEXEC",
                "side": "no",
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "PAPER_TRADE",
                "trade_id": "paper-executed",
                "lifecycle_id": "lc-executed",
                "venue": "kalshi",
                "ticker": "KXEXEC",
                "side": "no",
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-pending",
                "venue": "kalshi",
                "ticker": "KXPENDING",
                "side": "no",
                "ts": "2026-04-11T00:02:00+00:00",
            },
            {
                "type": "PAPER_TRADE",
                "lifecycle_id": "lc-orphan-execution",
                "venue": "kalshi",
                "ticker": "KXORPHAN",
                "side": "no",
                "ts": "2026-04-11T00:03:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-terminal-conflict",
                "venue": "kalshi",
                "ticker": "KXCONFLICT",
                "side": "no",
                "ts": "2026-04-11T00:04:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-terminal-conflict",
                "reason": "G7_open_exposure_drawdown",
                "venue": "kalshi",
                "ticker": "KXCONFLICT",
                "side": "no",
                "ts": "2026-04-11T00:05:00+00:00",
            },
            {
                "type": "PAPER_TRADE",
                "trade_id": "paper-conflict",
                "lifecycle_id": "lc-terminal-conflict",
                "venue": "kalshi",
                "ticker": "KXCONFLICT",
                "side": "no",
                "ts": "2026-04-11T00:06:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-reused",
                "venue": "kalshi",
                "ticker": "KXONE",
                "side": "no",
                "ts": "2026-04-11T00:07:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-reused",
                "reason": "cooldown",
                "venue": "kalshi",
                "ticker": "KXTWO",
                "side": "no",
                "ts": "2026-04-11T00:08:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-live-submitted",
                "venue": "kalshi",
                "ticker": "KXLIVE",
                "side": "no",
                "ts": "2026-04-11T00:09:00+00:00",
            },
            {
                "type": "LIVE_ORDER",
                "order_id": "live-submitted",
                "submission_id": "submission-live-submitted",
                "lifecycle_id": "lc-live-submitted",
                "venue": "kalshi",
                "ticker": "KXLIVE",
                "side": "no",
                "status": "resting",
                "filled": 0,
                "ts": "2026-04-11T00:10:00+00:00",
            },
        ],
    )

    stats = summarize(path, since=None, until=None)
    attribution = stats["same_window_lifecycle_attribution"]

    assert attribution["opportunity_lifecycle_count"] == 4
    assert attribution["paper_trade_opportunity_lifecycle_count"] == 1
    assert attribution["live_submission_opportunity_lifecycle_count"] == 1
    assert attribution["pending_opportunity_lifecycle_count"] == 1
    assert attribution["outcome_conflict_lifecycle_count"] == 1
    assert attribution["g7_skip_lifecycle_count"] == 0
    assert attribution["orphan_paper_trade_lifecycle_count"] == 1
    assert attribution["orphan_live_submission_lifecycle_count"] == 0
    assert attribution["orphan_unknown_live_submission_lifecycle_count"] == 0
    assert attribution["orphan_live_submission_intent_lifecycle_count"] == 0
    assert attribution["conflicted_lifecycle_count"] == 1
    assert attribution["paper_trade_lifecycle_status"] == "unavailable"
    assert attribution["paper_trade_event_rows"] == 3
    assert attribution["paper_trade_linked_event_rows"] == 2
    assert attribution["live_submission_event_rows"] == 1
    assert attribution["live_submission_linked_event_rows"] == 1
    assert attribution["unknown_live_submission_event_rows"] == 0
    assert attribution["unknown_live_submission_linked_event_rows"] == 0
    assert attribution["live_submission_intent_event_rows"] == 0
    assert attribution["live_submission_intent_linked_event_rows"] == 0
    assert attribution["identity_incomplete_lifecycle_count"] == 0
    assert attribution["reused_opportunity_lifecycle_count"] == 0
    assert attribution["quarantined_lifecycle_count"] == 1

    print_summary(stats, top=1, since=None, until=None)
    output = capsys.readouterr().out
    assert "Opportunities logged          : 5" in output
    assert "of signals" not in output
    assert (
        "Linked outcomes               : G7=0 zero_cap=0 other=0 paper_trades=1 live_submissions=1 "
        "unknown_live_submissions=0 intents_without_matching_terminal_journal=0 conflicts=1 receipt_conflicts=0 pending=1"
        in output
    )
    assert "Live submission linkage       : 1/1 records linked; not fill or P&L evidence" in output
    assert "Live submission unknown       : 0/0 records linked; reconciliation required" in output
    assert (
        "Live submission intent lineage: 0/0 records linked; 0 without matching terminal journal; "
        "not fill or P&L evidence; reconciliation required" in output
    )
    assert "executed=" not in output
    assert "Same-window scope              : lifecycle links only; settlement and mark P&L excluded" in output


def test_summarize_lifecycle_attribution_quarantines_mixed_terminal_outcomes(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    identity = {
        "lifecycle_id": "lc-mixed-terminal",
        "venue": "kalshi",
        "ticker": "KXMIXED",
        "side": "yes",
    }
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **identity},
            {"type": "PAPER_TRADE", "trade_id": "mixed-paper", **identity},
            {
                "type": "LIVE_ORDER",
                "order_id": "mixed-live",
                "submission_id": "mixed-submission",
                "status": "resting",
                **identity,
            },
        ],
    )

    stats = summarize(path, since=None, until=None)
    attribution = stats["same_window_lifecycle_attribution"]

    assert attribution["opportunity_lifecycle_count"] == 1
    assert attribution["paper_trade_opportunity_lifecycle_count"] == 0
    assert attribution["live_submission_opportunity_lifecycle_count"] == 0
    assert attribution["outcome_conflict_lifecycle_count"] == 1
    assert attribution["pending_opportunity_lifecycle_count"] == 0
    assert attribution["paper_trade_event_rows"] == 1
    assert attribution["paper_trade_linked_event_rows"] == 1
    assert attribution["live_submission_event_rows"] == 1
    assert attribution["live_submission_linked_event_rows"] == 1


def test_summarize_lifecycle_attribution_surfaces_unknown_live_submission(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    identity = {
        "lifecycle_id": "lc-unknown-submission",
        "venue": "kalshi",
        "ticker": "KXUNKNOWN",
        "side": "no",
    }
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **identity},
            {
                "type": "LIVE_SUBMISSION_UNKNOWN",
                "submission_id": "unknown-submission",
                "outcome": "error_result",
                **identity,
            },
        ],
    )

    stats = summarize(path, since=None, until=None)
    attribution = stats["same_window_lifecycle_attribution"]

    assert attribution["opportunity_lifecycle_count"] == 1
    assert attribution["unknown_live_submission_opportunity_lifecycle_count"] == 1
    assert attribution["pending_opportunity_lifecycle_count"] == 0
    assert attribution["outcome_conflict_lifecycle_count"] == 0
    assert attribution["unknown_live_submission_event_rows"] == 1
    assert attribution["unknown_live_submission_linked_event_rows"] == 1
    assert attribution["orphan_unknown_live_submission_lifecycle_count"] == 0
    assert stats["path_counts"]["news"] == 1


def test_summarize_lifecycle_attribution_pairs_intent_with_unknown_submission(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    identity = {"lifecycle_id": "lc-intent-unknown", "venue": "kalshi", "ticker": "KXINTENTUNKNOWN", "side": "no"}
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **identity},
            {"type": "LIVE_SUBMISSION_INTENT", "submission_id": "unknown-a", **identity},
            {
                "type": "LIVE_SUBMISSION_UNKNOWN",
                "submission_id": "unknown-a",
                "outcome": "exception",
                **identity,
            },
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["unknown_live_submission_opportunity_lifecycle_count"] == 1
    assert attribution["unresolved_live_submission_intent_opportunity_lifecycle_count"] == 0
    assert attribution["outcome_conflict_lifecycle_count"] == 0
    assert attribution["terminal_evidence_conflict_lifecycle_count"] == 0


def test_summarize_lifecycle_attribution_surfaces_unresolved_live_submission_intent(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    identity = {
        "lifecycle_id": "lc-unresolved-intent",
        "venue": "kalshi",
        "ticker": "KXINTENT",
        "side": "yes",
    }
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **identity},
            {
                "type": "LIVE_SUBMISSION_INTENT",
                "submission_id": "intent-1",
                **identity,
            },
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["opportunity_lifecycle_count"] == 1
    assert attribution["unresolved_live_submission_intent_opportunity_lifecycle_count"] == 1
    assert attribution["pending_opportunity_lifecycle_count"] == 0
    assert attribution["live_submission_intent_event_rows"] == 1
    assert attribution["live_submission_intent_linked_event_rows"] == 1
    assert attribution["orphan_live_submission_intent_lifecycle_count"] == 0


def test_summarize_lifecycle_attribution_pairs_matching_intent_and_idempotent_live_receipt(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    identity = {
        "lifecycle_id": "lc-matching-intent",
        "venue": "kalshi",
        "ticker": "KXMATCHING",
        "side": "yes",
    }
    live_order = {
        "type": "LIVE_ORDER",
        "order_id": "order-matching",
        "submission_id": "submission-matching",
        "status": "resting",
        **identity,
    }
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **identity},
            {"type": "LIVE_SUBMISSION_INTENT", "submission_id": "submission-matching", **identity},
            {**live_order, "ts": "2026-04-11T00:00:00+00:00"},
            {**live_order, "ts": "2026-04-11T00:00:01+00:00"},
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["live_submission_opportunity_lifecycle_count"] == 1
    assert attribution["unresolved_live_submission_intent_opportunity_lifecycle_count"] == 0
    assert attribution["outcome_conflict_lifecycle_count"] == 0
    assert attribution["terminal_evidence_conflict_lifecycle_count"] == 0
    assert attribution["live_submission_event_rows"] == 2
    assert attribution["live_submission_linked_event_rows"] == 2


def test_summarize_lifecycle_attribution_quarantines_mismatched_intent_submission_id(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    identity = {
        "lifecycle_id": "lc-mismatched-intent",
        "venue": "kalshi",
        "ticker": "KXMISMATCH",
        "side": "yes",
    }
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **identity},
            {"type": "LIVE_SUBMISSION_INTENT", "submission_id": "submission-a", **identity},
            {
                "type": "LIVE_ORDER",
                "order_id": "order-b",
                "submission_id": "submission-b",
                "status": "resting",
                **identity,
            },
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["live_submission_opportunity_lifecycle_count"] == 0
    assert attribution["unresolved_live_submission_intent_opportunity_lifecycle_count"] == 1
    assert attribution["outcome_conflict_lifecycle_count"] == 1
    assert attribution["terminal_evidence_conflict_lifecycle_count"] == 1


def test_summarize_lifecycle_attribution_quarantines_receipt_reuse_across_lifecycles(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    first = {"lifecycle_id": "lc-reuse-one", "venue": "kalshi", "ticker": "KXREUSE1", "side": "yes"}
    second = {"lifecycle_id": "lc-reuse-two", "venue": "kalshi", "ticker": "KXREUSE2", "side": "yes"}
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **first},
            {"type": "PAPER_TRADE", "trade_id": "reused-trade", **first},
            {"type": "OPPORTUNITY", **second},
            {"type": "PAPER_TRADE", "trade_id": "reused-trade", **second},
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["paper_trade_opportunity_lifecycle_count"] == 0
    assert attribution["outcome_conflict_lifecycle_count"] == 2
    assert attribution["terminal_evidence_conflict_lifecycle_count"] == 2


def test_summarize_lifecycle_attribution_quarantines_unknown_venue_receipt_reuse(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    unknown = {"lifecycle_id": "lc-unknown-receipt", "venue": "kalshi", "ticker": "KXUNKNOWN", "side": "yes"}
    live = {"lifecycle_id": "lc-live-receipt", "venue": "kalshi", "ticker": "KXLIVE", "side": "yes"}
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **unknown},
            {
                "type": "LIVE_SUBMISSION_UNKNOWN",
                "submission_id": "unknown-submission",
                "venue_order_id": "shared-venue-order",
                **unknown,
            },
            {"type": "OPPORTUNITY", **live},
            {
                "type": "LIVE_ORDER",
                "order_id": "shared-venue-order",
                "submission_id": "live-submission",
                **live,
            },
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["unknown_live_submission_opportunity_lifecycle_count"] == 0
    assert attribution["live_submission_opportunity_lifecycle_count"] == 0
    assert attribution["outcome_conflict_lifecycle_count"] == 2
    assert attribution["terminal_evidence_conflict_lifecycle_count"] == 2


def test_summarize_lifecycle_attribution_requires_verified_live_order_receipt(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    identity = {"lifecycle_id": "lc-missing-order-receipt", "venue": "kalshi", "ticker": "KXORDER", "side": "yes"}
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **identity},
            {"type": "LIVE_SUBMISSION_INTENT", "submission_id": "submission-a", **identity},
            {"type": "LIVE_ORDER", "submission_id": "submission-a", **identity},
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["live_submission_opportunity_lifecycle_count"] == 0
    assert attribution["outcome_conflict_lifecycle_count"] == 1
    assert attribution["terminal_evidence_conflict_lifecycle_count"] == 1


def test_summarize_lifecycle_attribution_quarantines_changed_live_receipt_payload(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    identity = {"lifecycle_id": "lc-changed-live-receipt", "venue": "kalshi", "ticker": "KXCHANGED", "side": "yes"}
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **identity},
            {"type": "LIVE_SUBMISSION_INTENT", "submission_id": "submission-a", **identity},
            {
                "type": "LIVE_ORDER",
                "order_id": "order-a",
                "submission_id": "submission-a",
                "contracts": 1,
                "status": "resting",
                **identity,
            },
            {
                "type": "LIVE_ORDER",
                "order_id": "order-a",
                "submission_id": "submission-a",
                "contracts": 2,
                "status": "filled",
                **identity,
            },
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["live_submission_opportunity_lifecycle_count"] == 0
    assert attribution["outcome_conflict_lifecycle_count"] == 1
    assert attribution["terminal_evidence_conflict_lifecycle_count"] == 1


def test_summarize_lifecycle_attribution_quarantines_duplicate_terminal_evidence(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    paper_identity = {"lifecycle_id": "lc-paper-duplicate", "venue": "kalshi", "ticker": "KXPAPER", "side": "yes"}
    live_identity = {"lifecycle_id": "lc-live-duplicate", "venue": "kalshi", "ticker": "KXLIVE", "side": "yes"}
    unknown_identity = {
        "lifecycle_id": "lc-unknown-duplicate",
        "venue": "kalshi",
        "ticker": "KXUNKNOWN",
        "side": "yes",
    }
    skip_identity = {"lifecycle_id": "lc-skip-mixed", "venue": "kalshi", "ticker": "KXSKIP", "side": "yes"}
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", **paper_identity},
            {"type": "PAPER_TRADE", "trade_id": "paper-1", **paper_identity},
            {"type": "PAPER_TRADE", "trade_id": "paper-2", **paper_identity},
            {"type": "OPPORTUNITY", **live_identity},
            {"type": "LIVE_ORDER", "order_id": "live-1", **live_identity},
            {"type": "LIVE_ORDER", "order_id": "live-2", **live_identity},
            {"type": "OPPORTUNITY", **unknown_identity},
            {
                "type": "LIVE_SUBMISSION_UNKNOWN",
                "submission_id": "unknown-1",
                "outcome": "error_result",
                **unknown_identity,
            },
            {
                "type": "LIVE_SUBMISSION_UNKNOWN",
                "submission_id": "unknown-2",
                "outcome": "error_result",
                **unknown_identity,
            },
            {"type": "OPPORTUNITY", **skip_identity},
            {"type": "SKIPPED", "reason": "G7_open_exposure_drawdown", **skip_identity},
            {"type": "SKIPPED", "reason": "capped_dollars=0", "capped_dollars": 0, **skip_identity},
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["opportunity_lifecycle_count"] == 4
    assert attribution["outcome_conflict_lifecycle_count"] == 4
    assert attribution["terminal_evidence_conflict_lifecycle_count"] == 4
    assert attribution["paper_trade_opportunity_lifecycle_count"] == 0
    assert attribution["live_submission_opportunity_lifecycle_count"] == 0
    assert attribution["unknown_live_submission_opportunity_lifecycle_count"] == 0
    assert attribution["g7_skip_lifecycle_count"] == 0
    assert attribution["zero_cap_skip_lifecycle_count"] == 0
    assert attribution["pending_opportunity_lifecycle_count"] == 0


def test_print_summary_defaults_unknown_submission_fields_for_legacy_attribution(local_tmp_dir, capsys):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-legacy-summary",
                "venue": "kalshi",
                "ticker": "KXLEGACY",
                "side": "yes",
            }
        ],
    )
    stats = summarize(path, since=None, until=None)
    attribution = stats["same_window_lifecycle_attribution"]
    for key in (
        "unknown_live_submission_opportunity_lifecycle_count",
        "unknown_live_submission_event_rows",
        "unknown_live_submission_linked_event_rows",
        "orphan_unknown_live_submission_lifecycle_count",
        "unresolved_live_submission_intent_opportunity_lifecycle_count",
        "live_submission_intent_event_rows",
        "live_submission_intent_linked_event_rows",
        "orphan_live_submission_intent_lifecycle_count",
        "terminal_evidence_conflict_lifecycle_count",
    ):
        attribution.pop(key)

    print_summary(stats, top=1, since=None, until=None)

    output = capsys.readouterr().out
    assert "unknown_live_submissions=0" in output
    assert "Live submission unknown       : 0/0 records linked; reconciliation required" in output
    assert "orphan_unknown_live_submissions=0" in output
    assert "intents_without_matching_terminal_journal=0" in output
    assert (
        "Live submission intent lineage: 0/0 records linked; 0 without matching terminal journal; "
        "not fill or P&L evidence; reconciliation required" in output
    )
    assert "receipt_conflicts=0" in output


def test_summarize_lifecycle_attribution_quarantines_incomplete_and_reused_opportunities(
    local_tmp_dir,
):
    path = local_tmp_dir / "trades.jsonl"
    duplicate = {
        "type": "OPPORTUNITY",
        "lifecycle_id": "lc-duplicate",
        "venue": "kalshi",
        "ticker": "KXDUPLICATE",
        "side": "no",
        "edge": 0.05,
        "ts": "2026-04-11T00:00:00+00:00",
    }
    write_jsonl(
        path,
        [
            duplicate,
            duplicate,
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-incomplete",
                "venue": "kalshi",
                "ticker": "KXINCOMPLETE",
                "side": "no",
                "ts": "2026-04-11T00:01:00+00:00",
            },
            {
                "type": "SKIPPED",
                "lifecycle_id": "lc-incomplete",
                "reason": "cooldown",
                "venue": "kalshi",
                "ticker": "KXINCOMPLETE",
                "ts": "2026-04-11T00:02:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-reused-opportunity",
                "venue": "kalshi",
                "ticker": "KXREUSED",
                "side": "no",
                "edge": 0.05,
                "ts": "2026-04-11T00:03:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-reused-opportunity",
                "venue": "kalshi",
                "ticker": "KXREUSED",
                "side": "no",
                "edge": 0.06,
                "ts": "2026-04-11T00:04:00+00:00",
            },
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["opportunity_lifecycle_count"] == 1
    assert attribution["identity_incomplete_lifecycle_count"] == 1
    assert attribution["reused_opportunity_lifecycle_count"] == 1
    assert attribution["quarantined_lifecycle_count"] == 2
    assert attribution["other_skip_lifecycle_count"] == 0


def test_summarize_lifecycle_attribution_quarantines_timestamp_distinct_opportunities(local_tmp_dir):
    path = local_tmp_dir / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-timestamp-retry",
                "venue": "kalshi",
                "ticker": "KXTIMESTAMP",
                "side": "no",
                "edge": 0.05,
                "ts": "2026-04-11T00:00:00+00:00",
            },
            {
                "type": "OPPORTUNITY",
                "lifecycle_id": "lc-timestamp-retry",
                "venue": "kalshi",
                "ticker": "KXTIMESTAMP",
                "side": "no",
                "edge": 0.05,
                "ts": "2026-04-11T00:00:01+00:00",
            },
        ],
    )

    attribution = summarize(path, since=None, until=None)["same_window_lifecycle_attribution"]

    assert attribution["opportunity_lifecycle_count"] == 0
    assert attribution["reused_opportunity_lifecycle_count"] == 1
    assert attribution["quarantined_lifecycle_count"] == 1
