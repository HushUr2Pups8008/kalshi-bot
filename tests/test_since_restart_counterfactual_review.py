from __future__ import annotations

from tests._helpers import write_jsonl


def test_counterfactual_review_preserves_no_keyword_llm_fields_and_later_chain(tmp_path):
    from scripts.since_restart_counterfactual_review import build_counterfactual_report

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-20T01:00:00Z",
                "ticker": "KXIRAN",
                "source": "Reuters",
                "headline": "Iran deal stalls",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "signal_branch": "empty_keywords_neutral_llm",
                "method": "llm",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
                "llm_confidence": 0.82,
                "match_score": 0.11,
                "keywords": [],
            },
            {"type": "OPPORTUNITY", "ts": "2026-06-20T01:03:00Z", "ticker": "KXIRAN"},
            {"type": "GATE_SUMMARY", "ts": "2026-06-20T01:03:01Z", "ticker": "KXIRAN", "gate_chain": ["G1: PASS"]},
            {"type": "BLEND_DECISION", "ts": "2026-06-20T01:03:02Z", "ticker": "KXIRAN", "venue": "kalshi"},
            {"type": "PAPER_TRADE", "ts": "2026-06-20T01:03:03Z", "ticker": "KXIRAN"},
        ],
    )

    report = build_counterfactual_report(path, since="2026-06-20T00:00:00Z")

    assert report["policy_guardrail"] == "no_live_policy_change_without_counterfactual_evidence"
    assert report["target_counts"] == {"post_llm_neutral_empty_keywords": 1}
    row = report["targets"][0]
    assert row["llm_direction"] == "neutral"
    assert row["llm_magnitude"] == "none"
    assert row["keywords_empty"] is True
    assert row["empty_llm_corpus"] == "neutral_empty_llm"
    assert row["terminal_type"] == "PAPER_TRADE"
    assert row["counterfactual_bucket"] == "later_chain_executed"


def test_counterfactual_review_preserves_illiquidity_skip_upstream_chain(tmp_path):
    from scripts.since_restart_counterfactual_review import build_counterfactual_report

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {"type": "OPPORTUNITY", "ts": "2026-06-20T02:00:00Z", "ticker": "KXLOW", "source": "AP"},
            {"type": "GATE_SUMMARY", "ts": "2026-06-20T02:00:01Z", "ticker": "KXLOW", "gate_chain": ["G1: PASS"]},
            {"type": "BLEND_DECISION", "ts": "2026-06-20T02:00:02Z", "ticker": "KXLOW", "venue": "kalshi"},
            {
                "type": "SKIPPED",
                "ts": "2026-06-20T02:00:03Z",
                "ticker": "KXLOW",
                "source": "AP",
                "reason": "price 1.0c is near limit (too illiquid)",
                "market_price": 1.0,
            },
        ],
    )

    report = build_counterfactual_report(path, since="2026-06-20T00:00:00Z")

    assert report["target_counts"] == {"near_limit_illiquidity_skip": 1}
    row = report["targets"][0]
    assert row["terminal_reason"] == "price 1.0c is near limit (too illiquid)"
    assert row["executed_price_cents"] == 1.0
    assert row["opportunity_ts"] == "2026-06-20T02:00:00+00:00"
    assert row["blend_venue"] == "kalshi"
    assert row["counterfactual_bucket"] == "retain_liquidity_gate"
    assert row["paper_micro_lane_candidate"] is False
    assert row["paper_micro_lane_contracts"] == 0


def test_counterfactual_review_marks_kxvisitiran_1c_micro_lane_with_source_confidence(tmp_path):
    from scripts.since_restart_counterfactual_review import build_counterfactual_report, format_text_report

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-20T02:00:00Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "AP",
                "source_class": "wire",
                "retrieval_mode": "source_hint",
                "settlement_source_match": True,
            },
            {
                "type": "GATE_SUMMARY",
                "ts": "2026-06-20T02:00:01Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "gate_chain": ["G1: PASS"],
            },
            {
                "type": "BLEND_DECISION",
                "ts": "2026-06-20T02:00:02Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "venue": "kalshi",
            },
            {
                "type": "SKIPPED",
                "ts": "2026-06-20T02:00:03Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "AP",
                "reason": "price 1.0c is near limit (too illiquid)",
                "market_price": 1.0,
            },
        ],
    )

    report = build_counterfactual_report(path, since="2026-06-20T00:00:00Z")

    assert report["evidence_buckets"] == {"paper_micro_1c_candidate": 1}
    row = report["targets"][0]
    assert row["counterfactual_bucket"] == "paper_micro_1c_candidate"
    assert row["paper_micro_lane_candidate"] is True
    assert row["paper_micro_lane_contracts"] == 1
    assert row["paper_micro_lane_max_loss_dollars"] == 0.01
    assert row["paper_micro_lane_reason"] == "kxvisitiran_1c_high_rules_source_confidence"
    text = format_text_report(report)
    assert "paper_micro_lane_candidate=True contracts=1" in text


def test_counterfactual_review_filters_window_test_rows_and_limits(tmp_path):
    from scripts.since_restart_counterfactual_review import build_counterfactual_report

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-19T23:59:00Z",
                "ticker": "KXOLD",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-20T01:00:00Z",
                "ticker": "KXTEST",
                "source": "r/test",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-20T01:01:00Z",
                "ticker": "KXKEEP",
                "source": "Reuters",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "yes",
                "llm_magnitude": "small",
                "keywords": [],
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-20T01:02:00Z",
                "ticker": "KXLIMIT",
                "source": "Reuters",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
            },
        ],
    )

    report = build_counterfactual_report(
        path,
        since="2026-06-20T00:00:00Z",
        exclude_test=True,
        limit=1,
    )

    assert report["target_counts"] == {"post_llm_neutral_empty_keywords": 1}
    assert report["targets"][0]["ticker"] == "KXKEEP"
    assert report["targets"][0]["empty_llm_corpus"] == "directional_empty_llm"
