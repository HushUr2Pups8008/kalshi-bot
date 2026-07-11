"""Tests for utils.log_records.SignalAnalysisDetail (P1-03).

Covers:
- Struct construction with required fields only.
- Struct construction with full optional payload.
- `dataclasses.asdict` serialization shape.
- JSONL snapshot: emitted top-level key set matches the locked snapshot.
  Catches future field additions that aren't reflected in the struct
  declaration, and any drift in the logger's None-omission semantics.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from tests._helpers import make_tmp_dir
from utils.log_records import SignalAnalysisDetail
from utils.logger import TradeLogger


# Locked snapshot of every key the logger may emit for SIGNAL_ANALYSIS_DETAIL
# when EVERY optional field is populated. Includes the implicit "type" field.
# Drift here is intentional: any addition to SignalAnalysisDetail must be
# accompanied by an update to this snapshot, ensuring downstream consumers
# (governance audit, edge replay, dashboards) see the change.
_FULL_SNAPSHOT_KEYS = frozenset({
    "type", "ts",
    "ticker", "source", "headline", "method", "keywords", "venue",
    "base_probability", "final_probability", "market_price",
    "publish_ts", "age_at_analysis_seconds", "analysis_threshold_seconds",
    "keyword_contributions",
    "llm_direction", "llm_magnitude", "llm_confidence",
    "llm_attempted", "llm_result_used", "llm_result_status", "llm_provider",
    "llm_latency_ms", "llm_total_stage_ms", "llm_queue_wait_ms",
    "llm_http_round_trip_ms", "llm_parse_ms", "llm_http_status",
    "llm_contention_observed", "llm_in_flight_at_entry",
    "llm_routing_passed", "llm_routing_reason",
    "pre_llm_quality_pass",
    "pre_llm_semantic_overlap_count", "pre_llm_semantic_overlap_ratio",
    "pre_llm_would_block",
    "pre_llm_keyword_override", "pre_llm_keyword_override_mode",
    "pre_llm_keyword_signal_strength",
    "pre_llm_gate_reason", "pre_llm_gate_enforced",
    "pre_llm_headline_token_count", "pre_llm_market_token_count",
    "pre_llm_filtered_stopword_count", "pre_llm_filtered_generic_count",
    "pre_llm_semantic_token_types",
    "llm_probability_movement", "llm_useful", "pre_llm_would_block_and_useful",
    "is_startup_probe", "is_synthetic_probe",
})


def _required_only_detail() -> SignalAnalysisDetail:
    return SignalAnalysisDetail(
        ticker="KXTEST-25DEC31",
        source="AP",
        headline="A test headline",
        method="keyword",
        keywords=["war"],
        base_probability=0.5,
        final_probability=0.55,
        market_price=0.5,
    )


def _full_payload_detail() -> SignalAnalysisDetail:
    """Every field populated except llm_routing_reason and pre_llm_gate_reason
    which stay None to verify the None-omission contract holds independently
    of other field population.
    """
    return SignalAnalysisDetail(
        ticker="KXTEST-25DEC31",
        source="AP",
        headline="Full payload headline",
        method="llm",
        keywords=["war", "ceasefire"],
        keyword_contributions=[{"keyword": "war", "contribution": 0.1}],
        base_probability=0.5,
        final_probability=0.65,
        market_price=0.5,
        venue="kalshi",
        publish_ts="2026-06-20T01:00:00+00:00",
        age_at_analysis_seconds=123.4567,
        analysis_threshold_seconds=1800,
        llm_direction="yes",
        llm_magnitude="moderate",
        llm_confidence=0.85,
        llm_attempted=True,
        llm_result_used=True,
        llm_result_status="ollama_success",
        llm_provider="ollama",
        llm_latency_ms=2500,
        llm_total_stage_ms=3200,
        llm_queue_wait_ms=600,
        llm_http_round_trip_ms=2400,
        llm_parse_ms=80,
        llm_http_status=200,
        llm_contention_observed=False,
        llm_in_flight_at_entry=1,
        llm_routing_passed=True,
        llm_routing_reason=None,
        pre_llm_quality_pass=True,
        pre_llm_semantic_overlap_count=2,
        pre_llm_semantic_overlap_ratio=0.4,
        pre_llm_would_block=False,
        pre_llm_keyword_override=False,
        pre_llm_keyword_override_mode="any_hit",
        pre_llm_keyword_signal_strength=0.12,
        pre_llm_gate_reason=None,
        pre_llm_gate_enforced=False,
        pre_llm_headline_token_count=8,
        pre_llm_market_token_count=4,
        pre_llm_filtered_stopword_count=3,
        pre_llm_filtered_generic_count=1,
        pre_llm_semantic_token_types={"core": 2},
        llm_probability_movement=0.15,
        llm_useful=True,
        pre_llm_would_block_and_useful=False,
        is_startup_probe=False,
        is_synthetic_probe=False,
    )


def test_required_fields_construct_with_optional_defaults_to_none():
    detail = _required_only_detail()
    assert detail.ticker == "KXTEST-25DEC31"
    assert detail.method == "keyword"
    assert detail.keywords == ["war"]
    # Optional fields default to None
    assert detail.llm_direction is None
    assert detail.llm_confidence is None
    assert detail.pre_llm_quality_pass is None
    assert detail.is_startup_probe is None
    # keyword_contributions defaults to None too
    assert detail.keyword_contributions is None


def test_struct_is_frozen():
    detail = _required_only_detail()
    with pytest.raises(dataclasses.FrozenInstanceError):
        detail.method = "llm"  # type: ignore[misc]


def test_asdict_round_trip_preserves_field_names():
    detail = _full_payload_detail()
    raw = dataclasses.asdict(detail)
    # Every dataclass field appears in the dict
    field_names = {f.name for f in dataclasses.fields(detail)}
    assert set(raw.keys()) == field_names
    # Sample value preservation
    assert raw["llm_direction"] == "yes"
    assert raw["pre_llm_semantic_token_types"] == {"core": 2}


def _cleanup(tmp: Path) -> None:
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()


def test_logger_emits_required_only_record():
    """Required-only detail emits required fields plus implicit 'type' marker.
    Optional None fields are omitted (preserves prior emission contract).
    """
    tmp = make_tmp_dir("log_records_required_only")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_signal_analysis_detail(_required_only_detail())
        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "SIGNAL_ANALYSIS_DETAIL"
        assert record["ticker"] == "KXTEST-25DEC31"
        assert record["method"] == "keyword"
        assert record["keywords"] == ["war"]
        # Required floats rounded to 4 decimals (round() of 0.5 yields 0.5)
        assert record["base_probability"] == 0.5
        # Optional None fields not emitted
        assert "age_at_analysis_seconds" not in record
        assert "llm_direction" not in record
        assert "is_startup_probe" not in record
        # keyword_contributions=None falsy → not emitted
        assert "keyword_contributions" not in record
    finally:
        _cleanup(tmp)


def test_logger_full_payload_snapshot():
    """Snapshot test: full payload emits exactly the locked key set.

    Drift catches: a new field added to SignalAnalysisDetail without an
    update to _FULL_SNAPSHOT_KEYS, OR a field removed without snapshot
    update, both fail this test.
    """
    tmp = make_tmp_dir("log_records_full_snapshot")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_signal_analysis_detail(_full_payload_detail())
        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        actual_keys = frozenset(record.keys())
        # llm_routing_reason and pre_llm_gate_reason are explicitly None in the
        # full payload to test the None-omission contract holds regardless of
        # other fields.
        expected_keys = _FULL_SNAPSHOT_KEYS - {"llm_routing_reason", "pre_llm_gate_reason"}
        assert actual_keys == expected_keys, (
            f"Snapshot drift: missing={expected_keys - actual_keys}, "
            f"extra={actual_keys - expected_keys}"
        )
    finally:
        _cleanup(tmp)


def test_analysis_rejected_record_carries_counterfactual_eval_context():
    tmp = make_tmp_dir("analysis_rejected_eval_context")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_analysis_rejected(
            reason="no_keywords",
            rejection_category="post_llm_neutral_empty_keywords",
            signal_branch="empty_keywords_neutral_llm",
            method="llm",
            llm_direction="neutral",
            llm_magnitude="none",
            llm_confidence=0.82,
            keywords=[],
            ticker="KXVISITIRAN-26JUL01-JVAN",
            source="Reuters",
            headline="Talks continue before possible visit",
            match_score=0.42,
            retrieval_mode="source_hint",
            source_hint_domain="reuters.com",
            source_hint_query="site:reuters.com trump visit iran",
            source_class="news",
            rules_primary="Market resolves Yes if Trump visits Iran by July 1.",
            rules_secondary="Visits by other officials do not count.",
            settlement_source_names=["Reuters", "Associated Press"],
            settlement_source_urls=["https://reuters.com", "https://apnews.com"],
            contract_terms_url="https://kalshi.com/markets/KXVISITIRAN",
        )
        record = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert record["retrieval_mode"] == "source_hint"
        assert record["source_hint_domain"] == "reuters.com"
        assert record["source_class"] == "news"
        assert record["rules_primary"].startswith("Market resolves Yes")
        assert record["settlement_source_names"] == ["Reuters", "Associated Press"]
        assert record["contract_terms_url"].endswith("KXVISITIRAN")
    finally:
        _cleanup(tmp)


def test_analysis_rejected_record_carries_research_replay_fields():
    tmp = make_tmp_dir("analysis_rejected_research_replay_fields")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_analysis_rejected(
            reason="research_incomplete",
            rejection_category="research_continue",
            signal_branch="empty_keywords_research_continue",
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            source="Reuters",
            headline="Iran crude output rises",
            match_score=0.42,
            research_attempted=True,
            research_status="continue_researching",
            research_model_probability_yes=0.72,
            research_started_ts="2026-06-27T10:00:00+00:00",
            research_completed_ts="2026-06-27T10:00:02+00:00",
            research_duration_ms=2000.4,
            research_min_published_at="2026-06-27T09:45:00+00:00",
            research_max_published_at="2026-06-27T09:55:00+00:00",
            research_min_retrieved_at="2026-06-27T10:00:01+00:00",
            research_max_retrieved_at="2026-06-27T10:00:02+00:00",
            research_market_price=0.51,
            research_estimated_edge=0.12,
            research_decision_grade_reasons=[
                "price_present",
                "edge_recomputed",
            ],
            research_open_questions=["What official correction would change this?"],
            research_counterclaims=["Counter source says the opposite side remains possible."],
        )
        record = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert record["research_model_probability_yes"] == 0.72
        assert record["research_market_price"] == 0.51
        assert record["research_estimated_edge"] == 0.12
        assert record["research_decision_grade_reasons"] == [
            "price_present",
            "edge_recomputed",
        ]
        assert record["research_open_questions"] == [
            "What official correction would change this?"
        ]
        assert record["research_counterclaims"] == [
            "Counter source says the opposite side remains possible."
        ]
        assert record["research_duration_ms"] == 2000.4
        assert record["research_started_ts"] == "2026-06-27T10:00:00+00:00"
        assert record["research_completed_ts"] == "2026-06-27T10:00:02+00:00"
        assert record["research_min_published_at"] == "2026-06-27T09:45:00+00:00"
        assert record["research_max_retrieved_at"] == "2026-06-27T10:00:02+00:00"
    finally:
        _cleanup(tmp)


def test_opportunity_record_carries_source_hint_and_settlement_attribution():
    tmp = make_tmp_dir("opportunity_eval_context")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_opportunity(
            ticker="KXVISITIRAN-26JUL01-JVAN",
            market_title="Will JD Vance visit Iran before Jul 1, 2026?",
            entry_price_cents=1.0,
            estimated_probability=0.06,
            edge=0.05,
            kelly_fraction=0.01,
            kelly_dollars=1.0,
            capped_dollars=1.0,
            side="yes",
            reasoning="source-hint test",
            source="Reuters",
            headline="Vance visit reported",
            method="llm",
            llm_direction="yes",
            llm_magnitude="moderate",
            venue="kalshi",
            keywords=["vance", "iran"],
            source_class="news",
            retrieval_mode="source_hint",
            source_hint_domain="reuters.com",
            source_hint_query="site:reuters.com vance iran",
            evidence_id="ev-op-1",
            settlement_source_match=True,
            research_status="decision_grade_candidate",
            research_run_id="rr-decision",
            signal_type="research_decision_grade",
        )
        record = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert record["retrieval_mode"] == "source_hint"
        assert record["source_hint_domain"] == "reuters.com"
        assert record["source_hint_query"] == "site:reuters.com vance iran"
        assert record["evidence_id"] == "ev-op-1"
        assert record["settlement_source_match"] is True
        assert record["research_status"] == "decision_grade_candidate"
        assert record["research_run_id"] == "rr-decision"
        assert record["signal_type"] == "research_decision_grade"
    finally:
        _cleanup(tmp)


def test_logger_rounds_optional_floats_to_four_decimals():
    detail = SignalAnalysisDetail(
        ticker="KXTEST-25DEC31",
        source="AP",
        headline="Float rounding",
        method="llm",
        keywords=[],
        base_probability=0.5,
        final_probability=0.5,
        market_price=0.5,
        llm_confidence=0.123456789,
        pre_llm_semantic_overlap_ratio=0.987654321,
        pre_llm_keyword_signal_strength=0.111111111,
        llm_probability_movement=0.222222222,
        age_at_analysis_seconds=123.456789,
    )
    tmp = make_tmp_dir("log_records_rounding")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_signal_analysis_detail(detail)
        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["llm_confidence"] == 0.1235
        assert record["pre_llm_semantic_overlap_ratio"] == 0.9877
        assert record["pre_llm_keyword_signal_strength"] == 0.1111
        assert record["llm_probability_movement"] == 0.2222
        assert record["age_at_analysis_seconds"] == 123.4568
    finally:
        _cleanup(tmp)
