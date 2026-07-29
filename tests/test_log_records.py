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
from analysis.feedback_counterfactual import (
    FEEDBACK_ALGORITHM_VERSION,
    FeedbackDecisionRecord,
    FeedbackMultiplierReceipt,
)
from utils import logger as logger_module
from utils.log_records import SignalAnalysisDetail
from utils.logger import ShadowTradeLogger, TradeLogger


# Locked snapshot of every key the logger may emit for SIGNAL_ANALYSIS_DETAIL
# when EVERY optional field is populated. Includes the implicit "type" field.
# Drift here is intentional: any addition to SignalAnalysisDetail must be
# accompanied by an update to this snapshot, ensuring downstream consumers
# (governance audit, edge replay, dashboards) see the change.
_FULL_SNAPSHOT_KEYS = frozenset(
    {
        "type",
        "ts",
        "ticker",
        "source",
        "headline",
        "method",
        "keywords",
        "venue",
        "base_probability",
        "final_probability",
        "market_price",
        "publish_ts",
        "age_at_analysis_seconds",
        "analysis_threshold_seconds",
        "keyword_contributions",
        "llm_direction",
        "llm_magnitude",
        "llm_confidence",
        "llm_attempted",
        "llm_result_used",
        "llm_result_status",
        "llm_provider",
        "llm_latency_ms",
        "llm_total_stage_ms",
        "llm_queue_wait_ms",
        "llm_http_round_trip_ms",
        "llm_parse_ms",
        "llm_http_status",
        "llm_contention_observed",
        "llm_in_flight_at_entry",
        "llm_routing_passed",
        "llm_routing_reason",
        "pre_llm_quality_pass",
        "pre_llm_semantic_overlap_count",
        "pre_llm_semantic_overlap_ratio",
        "pre_llm_would_block",
        "pre_llm_keyword_override",
        "pre_llm_keyword_override_mode",
        "pre_llm_keyword_signal_strength",
        "pre_llm_gate_reason",
        "pre_llm_gate_enforced",
        "pre_llm_headline_token_count",
        "pre_llm_market_token_count",
        "pre_llm_filtered_stopword_count",
        "pre_llm_filtered_generic_count",
        "pre_llm_semantic_token_types",
        "llm_probability_movement",
        "llm_useful",
        "pre_llm_would_block_and_useful",
        "is_startup_probe",
        "is_synthetic_probe",
    }
)


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


def test_shadow_trade_logger_persists_immutable_runtime_cohort_context(tmp_path):
    log_path = tmp_path / "fresh_pass_assignment_shadow.jsonl"
    logger = ShadowTradeLogger(log_path)
    logger.bind_runtime_context(
        cohort_id="legacy-pending-20260729",
        cohort_kind="legacy_pending",
    )
    logger.log_fresh_pass_assignment_shadow(
        {
            "type": "FRESH_PASS_ASSIGNMENT_SHADOW",
            "assigned": True,
            "runtime_paper_cohort_id": "spoofed",
            "runtime_paper_cohort_kind": "active",
        }
    )

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["runtime_paper_cohort_id"] == "legacy-pending-20260729"
    assert record["runtime_paper_cohort_kind"] == "legacy_pending"

    logger.bind_runtime_context(
        cohort_id="legacy-pending-20260729",
        cohort_kind="legacy_pending",
    )
    with pytest.raises(RuntimeError, match="refusing to rebind"):
        logger.bind_runtime_context(
            cohort_id="other-pending-20260729",
            cohort_kind="legacy_pending",
        )


def test_feedback_decision_record_preserves_counterfactual_receipts():
    tmp = make_tmp_dir("feedback_decision_record")
    try:
        log_file = tmp / "trades.jsonl"
        receipt = FeedbackMultiplierReceipt(
            channel="source",
            key="Reuters",
            series_ticker=None,
            applied_multiplier=1.2,
            status="canonical",
            canonical_basis_sha256="a" * 64,
            delivered_event_count=12,
            effective_sample_count=10,
            algorithm_version=FEEDBACK_ALGORITHM_VERSION,
            as_of="2026-07-28T00:00:00+00:00",
        )
        record = FeedbackDecisionRecord(
            lifecycle_id="life-1",
            venue="kalshi",
            ticker="KXTEST-26DEC31",
            source="Reuters",
            series_ticker="KXTEST",
            decision_at="2026-07-28T00:00:01+00:00",
            source_receipt=receipt,
            keyword_receipts=(),
            probability_actual=0.62,
            probability_keyword_neutral=None,
            keyword_counterfactual_status="not_applicable_llm",
            sizing_inputs={"bankroll": 50.0, "source_multiplier": 1.2},
            actual={"capped_dollars": 12.0},
            source_neutral={"capped_dollars": 10.0},
            keyword_neutral=None,
            all_neutral=None,
            gate={"trade_blocked_reason": "G7_open_exposure_drawdown"},
        )

        TradeLogger(log_file).log_feedback_decision(record)
        event = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert event["type"] == "FEEDBACK_DECISION"
        assert event["lifecycle_id"] == "life-1"
        assert event["source_receipt"]["canonical_basis_sha256"] == "a" * 64
        assert event["source_neutral"]["capped_dollars"] == 10.0
        assert event["keyword_receipts"] == []
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


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


def test_trade_logger_binds_explicit_runtime_paper_cohort_fields(tmp_path: Path):
    log_file = tmp_path / "trades.jsonl"
    logger = TradeLogger(log_file)

    logger.bind_runtime_context(
        cohort_id="legacy-pending-20260729",
        cohort_kind="legacy_pending",
    )
    logger.log_signal(
        source="Reuters",
        headline="Example",
        url="https://example.test",
        signal_strength=0.5,
        keywords_matched=["example"],
    )

    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["runtime_paper_cohort_id"] == "legacy-pending-20260729"
    assert record["runtime_paper_cohort_kind"] == "legacy_pending"
    assert "cohort_id" not in record
    assert "cohort_kind" not in record


def test_trade_logger_allows_idempotent_runtime_paper_cohort_rebind(tmp_path: Path):
    log_file = tmp_path / "trades.jsonl"
    logger = TradeLogger(log_file)

    logger.bind_runtime_context(
        cohort_id="legacy-pending-20260729",
        cohort_kind="legacy_pending",
    )
    logger.bind_runtime_context(
        cohort_id="legacy-pending-20260729",
        cohort_kind="legacy_pending",
    )
    logger.log_signal(
        source="Reuters",
        headline="Example",
        url="https://example.test",
        signal_strength=0.5,
        keywords_matched=["example"],
    )

    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["runtime_paper_cohort_id"] == "legacy-pending-20260729"
    assert record["runtime_paper_cohort_kind"] == "legacy_pending"


def test_trade_logger_rejects_conflicting_runtime_paper_cohort_rebind(tmp_path: Path):
    log_file = tmp_path / "trades.jsonl"
    logger = TradeLogger(log_file)
    logger.bind_runtime_context(
        cohort_id="legacy-pending-20260729",
        cohort_kind="legacy_pending",
    )

    with pytest.raises(RuntimeError, match="runtime paper cohort is already bound"):
        logger.bind_runtime_context(
            cohort_id="new-cohort-20260730",
            cohort_kind="new_pending",
        )

    logger.log_signal(
        source="Reuters",
        headline="Example",
        url="https://example.test",
        signal_strength=0.5,
        keywords_matched=["example"],
    )
    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["runtime_paper_cohort_id"] == "legacy-pending-20260729"
    assert record["runtime_paper_cohort_kind"] == "legacy_pending"


def test_trade_logger_runtime_paper_context_wins_over_record_values(tmp_path: Path):
    log_file = tmp_path / "trades.jsonl"
    logger = TradeLogger(log_file)
    logger.bind_runtime_context(
        cohort_id="legacy-pending-20260729",
        cohort_kind="legacy_pending",
    )

    logger._write(
        {
            "type": "TEST_RECORD",
            "runtime_paper_cohort_id": "caller-provided-id",
            "runtime_paper_cohort_kind": "caller-provided-kind",
        }
    )

    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["runtime_paper_cohort_id"] == "legacy-pending-20260729"
    assert record["runtime_paper_cohort_kind"] == "legacy_pending"


def test_live_order_uses_explicit_runtime_paper_cohort_provenance(tmp_path: Path):
    log_file = tmp_path / "trades.jsonl"
    logger = TradeLogger(log_file)
    logger.bind_runtime_context(
        cohort_id="legacy-pending-20260729",
        cohort_kind="legacy_pending",
    )

    logger.log_live_order(
        order_id="order-123",
        ticker="KXTEST-25DEC31",
        side="yes",
        contracts=1,
        price_cents=50,
        cost_dollars=0.5,
        status="resting",
    )

    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["type"] == "LIVE_ORDER"
    assert record["runtime_paper_cohort_id"] == "legacy-pending-20260729"
    assert record["runtime_paper_cohort_kind"] == "legacy_pending"
    assert "cohort_id" not in record
    assert "cohort_kind" not in record


def test_trade_logger_omits_runtime_cohort_context_when_unbound(tmp_path: Path):
    log_file = tmp_path / "trades.jsonl"

    TradeLogger(log_file).log_signal(
        source="Reuters",
        headline="Example",
        url="https://example.test",
        signal_strength=0.5,
        keywords_matched=["example"],
    )

    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert "runtime_paper_cohort_id" not in record
    assert "runtime_paper_cohort_kind" not in record
    assert "cohort_id" not in record
    assert "cohort_kind" not in record


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


def test_trade_logger_omits_optional_no_candidate_pool_fields():
    tmp = make_tmp_dir("polymarket_no_candidate_legacy_schema")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=2,
            reason="no_match",
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert record["reason"] == "no_match"
        assert "candidate_pool_stage" not in record
        assert "pre_admission_matchable_market_count" not in record
        assert "within_admission_horizon_market_count" not in record
        assert "admission_horizon_days" not in record
        assert "post_admission_no_token_overlap_count" not in record
        assert "post_admission_below_min_post_weight_score_count" not in record
        assert "post_admission_weight_demoted_below_min_score_count" not in record
        assert "post_admission_min_match_score" not in record
        assert "post_admission_best_rejected_pre_weight_score" not in record
        assert "post_admission_best_rejected_post_weight_score" not in record
    finally:
        _cleanup(tmp)


def test_trade_logger_records_post_admission_rejection_fields():
    tmp = make_tmp_dir("polymarket_post_admission_rejection_schema")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=2,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            pre_admission_matchable_market_count=2,
            within_admission_horizon_market_count=2,
            admission_horizon_days=14.0,
            post_admission_no_token_overlap_count=1,
            post_admission_below_min_post_weight_score_count=1,
            post_admission_weight_demoted_below_min_score_count=1,
            post_admission_min_match_score=0.08,
            post_admission_best_rejected_pre_weight_score=0.12,
            post_admission_best_rejected_post_weight_score=0.012,
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert record == {
            "type": "MATCH_NO_CANDIDATE",
            "ts": record["ts"],
            "source": "Example Wire",
            "headline": "Example event gets more likely",
            "venue": "polymarket_us",
            "eligible_market_count": 2,
            "reason": "no_match",
            "candidate_pool_stage": "post_admission_no_match",
            "pre_admission_matchable_market_count": 2,
            "within_admission_horizon_market_count": 2,
            "admission_horizon_days": 14.0,
            "post_admission_no_token_overlap_count": 1,
            "post_admission_below_min_post_weight_score_count": 1,
            "post_admission_weight_demoted_below_min_score_count": 1,
            "post_admission_min_match_score": 0.08,
            "post_admission_best_rejected_pre_weight_score": 0.12,
            "post_admission_best_rejected_post_weight_score": 0.012,
        }
    finally:
        _cleanup(tmp)


def _post_admission_counterfactual_shadow() -> dict[str, object]:
    return {
        "schema_version": 1,
        "match_clock_utc": "2026-07-29T09:08:36+00:00",
        "news_headline_token_count": 8,
        "news_match_token_count": 14,
        "candidate_count_total": 2,
        "captured_market_count": 2,
        "omitted_market_count": 0,
        "truncated": False,
        "candidates": [
            {
                "ticker": "0xabc123",
                "market_title": "Will  Example   pass? ",
                "rejection_reason": "no_token_overlap",
                "market_token_count": 11,
                "matched_token_count": 0,
            },
            {
                "ticker": "0xdef456",
                "rejection_reason": "market_without_match_tokens",
                "market_token_count": 0,
                "matched_token_count": 0,
            },
        ],
    }


def test_trade_logger_persists_canonical_bounded_counterfactual_shadow():
    tmp = make_tmp_dir("polymarket_counterfactual_shadow_schema")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=1,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=2,
            post_admission_no_token_overlap_count=2,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=_post_admission_counterfactual_shadow(),
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert record["post_admission_counterfactual_shadow"] == {
            **_post_admission_counterfactual_shadow(),
            "candidates": [
                {
                    "ticker": "0xdef456",
                    "rejection_reason": "market_without_match_tokens",
                    "market_token_count": 0,
                    "matched_token_count": 0,
                },
                {
                    "ticker": "0xabc123",
                    "market_title": "Will Example pass?",
                    "rejection_reason": "no_token_overlap",
                    "market_token_count": 11,
                    "matched_token_count": 0,
                }
            ],
        }
        assert "post_admission_counterfactual_shadow_status" not in record
    finally:
        _cleanup(tmp)


@pytest.mark.parametrize(
    ("mutate", "expect_invalid", "expected_title", "forbidden_text"),
    [
        (
            lambda snapshot: snapshot["candidates"][0].update(
                {"matched_tokens": ["body_only_secret"]}
            ),
            True,
            None,
            "body_only_secret",
        ),
        (
            lambda snapshot: snapshot["candidates"][0].update({"market_title": "bad\x1btitle"}),
            False,
            "badtitle",
            "",
        ),
        (
            lambda snapshot: snapshot["candidates"][0].update(
                {"market_title": "title_redaction_sentinel" + "x" * 512}
            ),
            False,
            None,
            "title_redaction_sentinel",
        ),
        (
            lambda snapshot: snapshot["candidates"][0].update(
                {
                    "rejection_reason": "below_min_post_weight_score",
                    "matched_token_count": 1,
                    "pre_weight_score": float("nan"),
                    "post_weight_score": 0.01,
                }
            ),
            True,
            None,
            "",
        ),
    ],
    ids=["raw_tokens", "control_characters", "oversized_raw_title", "non_finite_score"],
)
def test_trade_logger_handles_unsafe_counterfactual_shadow_without_losing_base_event(
    mutate,
    expect_invalid,
    expected_title,
    forbidden_text,
):
    tmp = make_tmp_dir("polymarket_counterfactual_shadow_invalid")
    try:
        log_file = tmp / "trades.jsonl"
        shadow = _post_admission_counterfactual_shadow()
        mutate(shadow)

        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=1,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=2,
            post_admission_no_token_overlap_count=2,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=shadow,
        )

        line = log_file.read_text(encoding="utf-8").strip()
        record = json.loads(line)
        assert record["type"] == "MATCH_NO_CANDIDATE"
        if expect_invalid:
            assert record["post_admission_counterfactual_shadow_status"] == "invalid"
            assert "post_admission_counterfactual_shadow" not in record
        else:
            assert "post_admission_counterfactual_shadow_status" not in record
            candidate = next(
                candidate
                for candidate in record["post_admission_counterfactual_shadow"]["candidates"]
                if candidate["ticker"] == "0xabc123"
            )
            if expected_title is None:
                assert "market_title" not in candidate
            else:
                assert candidate["market_title"] == expected_title
        if forbidden_text:
            assert forbidden_text not in line
    finally:
        _cleanup(tmp)


@pytest.mark.parametrize(
    ("mutate", "forbidden_text"),
    [
        (
            lambda snapshot: snapshot.update({"unexpected_payload": "snapshot_secret_payload"}),
            "snapshot_secret_payload",
        ),
        (
            lambda snapshot: snapshot["candidates"][0].update(
                {"raw_payload": "candidate_secret_payload"}
            ),
            "candidate_secret_payload",
        ),
        (
            lambda snapshot: snapshot["candidates"].__setitem__(
                1,
                {**snapshot["candidates"][1], "ticker": "0xabc123"},
            ),
            "",
        ),
        (lambda snapshot: snapshot.update({"candidate_count_total": True}), ""),
        (lambda snapshot: snapshot.update({"candidates": "untrusted_candidate_list"}), "untrusted_candidate_list"),
        (
            lambda snapshot: snapshot.update(
                {
                    "candidate_count_total": 1,
                    "captured_market_count": 1,
                    "omitted_market_count": 0,
                    "candidates": [{"ticker": "0xmalformed", "raw_payload": "malformed_candidate_payload"}],
                }
            ),
            "malformed_candidate_payload",
        ),
        (
            lambda snapshot: snapshot["candidates"][0].update({"ticker": "bad\x1bticker"}),
            "bad",
        ),
    ],
    ids=[
        "unknown_snapshot_key",
        "unknown_candidate_key",
        "duplicate_ticker",
        "boolean_count",
        "candidates_nonlist",
        "malformed_candidate",
        "unsafe_ticker",
    ],
)
def test_trade_logger_rejects_malformed_counterfactual_shadow_without_payload_leak(
    mutate,
    forbidden_text,
):
    tmp = make_tmp_dir("polymarket_counterfactual_shadow_malformed")
    try:
        log_file = tmp / "trades.jsonl"
        shadow = _post_admission_counterfactual_shadow()
        mutate(shadow)

        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=1,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=2,
            post_admission_no_token_overlap_count=2,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=shadow,
        )

        line = log_file.read_text(encoding="utf-8").strip()
        record = json.loads(line)
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
        if forbidden_text:
            assert forbidden_text not in line
    finally:
        _cleanup(tmp)


def test_trade_logger_rejects_counterfactual_shadow_over_serialized_byte_budget(monkeypatch):
    tmp = make_tmp_dir("polymarket_counterfactual_shadow_byte_budget")
    try:
        log_file = tmp / "trades.jsonl"
        monkeypatch.setattr(
            logger_module,
            "_POST_ADMISSION_COUNTERFACTUAL_SHADOW_MAX_BYTES",
            1,
        )

        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=1,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=2,
            post_admission_no_token_overlap_count=2,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=_post_admission_counterfactual_shadow(),
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
    finally:
        _cleanup(tmp)


def test_trade_logger_rejects_counterfactual_shadow_with_mismatched_within_horizon_total():
    tmp = make_tmp_dir("polymarket_counterfactual_shadow_total_mismatch")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=1,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=1,
            post_admission_no_token_overlap_count=1,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=_post_admission_counterfactual_shadow(),
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["within_admission_horizon_market_count"] == 1
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
    finally:
        _cleanup(tmp)


def test_trade_logger_rejects_counterfactual_shadow_for_non_polymarket_venue():
    tmp = make_tmp_dir("counterfactual_shadow_non_polymarket_venue")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="kalshi",
            eligible_market_count=2,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=2,
            post_admission_no_token_overlap_count=2,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=_post_admission_counterfactual_shadow(),
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["venue"] == "kalshi"
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
    finally:
        _cleanup(tmp)


def test_trade_logger_rejects_counterfactual_candidate_inconsistent_with_flat_counts():
    tmp = make_tmp_dir("counterfactual_shadow_flat_count_inconsistent")
    try:
        log_file = tmp / "trades.jsonl"
        shadow = _post_admission_counterfactual_shadow()
        shadow["candidates"][0].update(
            {
                "rejection_reason": "below_min_post_weight_score",
                "matched_token_count": 1,
                "pre_weight_score": 0.04,
                "post_weight_score": 0.01,
            }
        )
        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=2,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=2,
            post_admission_no_token_overlap_count=2,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=shadow,
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
    finally:
        _cleanup(tmp)


def test_trade_logger_rejects_counterfactual_candidate_at_or_above_flat_threshold():
    tmp = make_tmp_dir("counterfactual_shadow_threshold_inconsistent")
    try:
        log_file = tmp / "trades.jsonl"
        shadow = _post_admission_counterfactual_shadow()
        shadow["candidates"][0].update(
            {
                "rejection_reason": "below_min_post_weight_score",
                "matched_token_count": 1,
                "pre_weight_score": 0.04,
                "post_weight_score": 0.08,
            }
        )
        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=2,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=2,
            post_admission_no_token_overlap_count=1,
            post_admission_below_min_post_weight_score_count=1,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=shadow,
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
    finally:
        _cleanup(tmp)


@pytest.mark.parametrize(
    "malformation",
    (
        "integer_subclass",
        "reason_subclass",
        "snapshot_dict_subclass",
        "candidate_dict_subclass",
        "score_subclass",
        "schema_version_subclass",
        "zero_totals",
    ),
)
def test_trade_logger_rejects_noncanonical_counterfactual_shadow_schema_values(malformation):
    tmp = make_tmp_dir(f"counterfactual_shadow_{malformation}")
    try:
        log_file = tmp / "trades.jsonl"
        shadow = _post_admission_counterfactual_shadow()
        within_horizon_market_count = 2
        no_token_overlap_count = 2
        below_min_count = 0
        if malformation == "integer_subclass":
            class IntegerSubclass(int):
                pass

            shadow["candidate_count_total"] = IntegerSubclass(2)
        elif malformation == "reason_subclass":
            class ReasonSubclass(str):
                pass

            shadow["candidates"][0]["rejection_reason"] = ReasonSubclass("no_token_overlap")
        elif malformation == "snapshot_dict_subclass":
            class SnapshotDictSubclass(dict):
                pass

            shadow = SnapshotDictSubclass(shadow)
        elif malformation == "candidate_dict_subclass":
            class CandidateDictSubclass(dict):
                pass

            shadow["candidates"][0] = CandidateDictSubclass(shadow["candidates"][0])
        elif malformation == "score_subclass":
            class ScoreSubclass(float):
                pass

            shadow["candidates"][0].update(
                {
                    "rejection_reason": "below_min_post_weight_score",
                    "matched_token_count": 1,
                    "pre_weight_score": ScoreSubclass(0.04),
                    "post_weight_score": 0.01,
                }
            )
            no_token_overlap_count = 1
            below_min_count = 1
        elif malformation == "schema_version_subclass":
            class SchemaVersionSubclass(int):
                pass

            shadow["schema_version"] = SchemaVersionSubclass(1)
        else:
            shadow.update(
                {
                    "candidate_count_total": 0,
                    "captured_market_count": 0,
                    "omitted_market_count": 0,
                    "candidates": [],
                }
            )
            within_horizon_market_count = 0
            no_token_overlap_count = 0

        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=2,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=within_horizon_market_count,
            post_admission_no_token_overlap_count=no_token_overlap_count,
            post_admission_below_min_post_weight_score_count=below_min_count,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=shadow,
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
    finally:
        _cleanup(tmp)


def test_trade_logger_rejects_counterfactual_candidate_list_len_cap_bypass():
    class LenSpoofingCandidateList(list):
        def __init__(self, values):
            super().__init__(values)
            self._length_calls = 0

        def __len__(self):
            self._length_calls += 1
            return 4 if self._length_calls == 1 else 5

    tmp = make_tmp_dir("counterfactual_shadow_len_cap_bypass")
    try:
        log_file = tmp / "trades.jsonl"
        shadow = _post_admission_counterfactual_shadow()
        shadow.update(
            {
                "candidate_count_total": 5,
                "captured_market_count": 5,
                "omitted_market_count": 0,
                "candidates": LenSpoofingCandidateList(
                    [
                        {
                            "ticker": f"0xspoof{index}",
                            "rejection_reason": "no_token_overlap",
                            "market_token_count": 1,
                            "matched_token_count": 0,
                        }
                        for index in range(5)
                    ]
                ),
            }
        )

        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=5,
            reason="no_match",
            candidate_pool_stage="post_admission_no_match",
            within_admission_horizon_market_count=5,
            post_admission_no_token_overlap_count=5,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=shadow,
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
    finally:
        _cleanup(tmp)


@pytest.mark.parametrize("scope_field", ("venue", "candidate_pool_stage", "reason"))
def test_trade_logger_rejects_counterfactual_shadow_with_spoofed_scope_input(scope_field):
    class ScopeStringSubclass(str):
        pass

    tmp = make_tmp_dir(f"counterfactual_shadow_spoofed_{scope_field}")
    try:
        log_file = tmp / "trades.jsonl"
        values = {
            "venue": "polymarket_us",
            "reason": "no_match",
            "candidate_pool_stage": "post_admission_no_match",
        }
        values[scope_field] = ScopeStringSubclass(values[scope_field])

        TradeLogger(log_file).log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue=values["venue"],
            eligible_market_count=2,
            reason=values["reason"],
            candidate_pool_stage=values["candidate_pool_stage"],
            within_admission_horizon_market_count=2,
            post_admission_no_token_overlap_count=2,
            post_admission_below_min_post_weight_score_count=0,
            post_admission_weight_demoted_below_min_score_count=0,
            post_admission_min_match_score=0.08,
            post_admission_counterfactual_shadow=_post_admission_counterfactual_shadow(),
        )

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["type"] == "MATCH_NO_CANDIDATE"
        assert record["post_admission_counterfactual_shadow_status"] == "invalid"
        assert "post_admission_counterfactual_shadow" not in record
    finally:
        _cleanup(tmp)


def test_live_submission_records_are_linked_and_sanitized():
    tmp = make_tmp_dir("live_submission_records")
    try:
        log_file = tmp / "trades.jsonl"
        logger = TradeLogger(log_file)
        summary = {
            "submission_id": "a" * 32,
            "ticker": "KXTEST-25DEC31",
            "side": "yes",
            "contracts": 20,
            "price_cents": 50,
            "cost_dollars": 10.0,
        }
        signal_meta = {"lifecycle_id": "lc-live-submission-record"}

        logger.log_live_submission_intent(
            **summary,
            venue="kalshi",
            signal_meta=signal_meta,
        )
        logger.log_live_submission_unknown(
            **summary,
            outcome="exception",
            venue="kalshi",
            signal_meta=signal_meta,
        )
        logger.log_live_submission_unknown(
            **summary,
            outcome="live_order_journal_failure",
            venue_order_id="venue-order-123",
            venue="kalshi",
            signal_meta=signal_meta,
        )
        logger.log_live_order(
            order_id="order-123",
            submission_id=summary["submission_id"],
            ticker=summary["ticker"],
            side=summary["side"],
            contracts=summary["contracts"],
            price_cents=summary["price_cents"],
            cost_dollars=summary["cost_dollars"],
            status="resting",
            venue="kalshi",
        )

        records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
        assert [record["type"] for record in records] == [
            "LIVE_SUBMISSION_INTENT",
            "LIVE_SUBMISSION_UNKNOWN",
            "LIVE_SUBMISSION_UNKNOWN",
            "LIVE_ORDER",
        ]
        assert all(record["submission_id"] == summary["submission_id"] for record in records)
        assert records[0]["venue"] == "kalshi"
        assert records[0]["lifecycle_id"] == signal_meta["lifecycle_id"]
        assert records[1]["outcome"] == "exception"
        assert records[1]["venue"] == "kalshi"
        assert records[1]["lifecycle_id"] == signal_meta["lifecycle_id"]
        assert "error" not in records[1]
        assert "venue_order_id" not in records[1]
        assert records[2]["outcome"] == "live_order_journal_failure"
        assert records[2]["venue_order_id"] == "venue-order-123"
        assert records[2]["venue"] == "kalshi"
        assert records[2]["lifecycle_id"] == signal_meta["lifecycle_id"]
        assert "error" not in records[2]
        assert records[3]["order_id"] == "order-123"
        assert records[3]["venue"] == "kalshi"
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
            f"Snapshot drift: missing={expected_keys - actual_keys}, extra={actual_keys - expected_keys}"
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
        assert record["research_open_questions"] == ["What official correction would change this?"]
        assert record["research_counterclaims"] == ["Counter source says the opposite side remains possible."]
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


def test_trade_logger_records_kalshi_cold_cache_event_schema():
    tmp = make_tmp_dir("kalshi_cold_cache_event")
    try:
        log_file = tmp / "trades.jsonl"
        TradeLogger(log_file).log_kalshi_cold_cache(
            action="replayed",
            cold_cache_id="kalshi-cold-cache-abc123",
            source="Example Wire",
            headline="Example event gets more likely",
            queue_depth=2,
            age_seconds=1.234,
            threshold_seconds=1800.0,
            wait_seconds=12.345,
            candidate_count=3,
        )
        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record == {
            "type": "KALSHI_COLD_CACHE",
            "ts": record["ts"],
            "action": "replayed",
            "cold_cache_id": "kalshi-cold-cache-abc123",
            "source": "Example Wire",
            "headline": "Example event gets more likely",
            "queue_depth": 2,
            "age_seconds": 1.23,
            "threshold_seconds": 1800.0,
            "wait_seconds": 12.35,
            "candidate_count": 3,
        }
    finally:
        _cleanup(tmp)


def test_trade_logger_records_polymarket_funnel_event_schemas():
    tmp = make_tmp_dir("polymarket_funnel_events")
    try:
        log_file = tmp / "trades.jsonl"
        logger = TradeLogger(log_file)

        logger.log_match_no_candidate(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            eligible_market_count=2,
            reason="market_fetch_failed",
            candidate_pool_stage="provider_fetch_failed",
            pre_admission_matchable_market_count=0,
            within_admission_horizon_market_count=0,
            admission_horizon_days=14.0,
        )
        logger.log_polymarket_market_cache(
            raw_fetched=3,
            raw_unique=3,
            pages_fetched=1,
            cursor_present=True,
            pagination_exhausted=True,
            pagination_stop_reason="short_page",
            eligible_30d=2,
            candidate_within_admission_horizon=1,
            admission_horizon_days=14.0,
            market_limit=10,
        )
        logger.log_polymarket_horizon_shadow(
            source="Example Wire",
            headline="Example event gets more likely",
            venue="polymarket_us",
            production_horizon_days=14.0,
            shadow_horizon_start_days=14.0,
            shadow_horizon_end_days=30.0,
            production_candidate_count=2,
            shadow_candidate_count=3,
            production_qualifying_match_count=0,
            shadow_qualifying_match_count=1,
            production_no_token_overlap_count=2,
            production_below_min_post_weight_score_count=0,
            production_weight_demoted_below_min_score_count=0,
            production_min_match_score=0.08,
            shadow_no_token_overlap_count=2,
            shadow_below_min_post_weight_score_count=1,
            shadow_weight_demoted_below_min_score_count=0,
            shadow_min_match_score=0.08,
            shadow_analysis_status="not_evaluated_shadow_only",
            production_best_rejected_pre_weight_score=0.12,
            production_best_rejected_post_weight_score=0.012,
            shadow_best_rejected_pre_weight_score=0.09,
            shadow_best_rejected_post_weight_score=0.07,
            production_counterfactual_shadow=_post_admission_counterfactual_shadow(),
        )

        no_candidate, cache, horizon_shadow = [
            json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()
        ]

        assert no_candidate == {
            "type": "MATCH_NO_CANDIDATE",
            "ts": no_candidate["ts"],
            "source": "Example Wire",
            "headline": "Example event gets more likely",
            "venue": "polymarket_us",
            "eligible_market_count": 2,
            "reason": "market_fetch_failed",
            "candidate_pool_stage": "provider_fetch_failed",
            "pre_admission_matchable_market_count": 0,
            "within_admission_horizon_market_count": 0,
            "admission_horizon_days": 14.0,
        }
        assert cache == {
            "type": "POLYMARKET_MARKET_CACHE",
            "ts": cache["ts"],
            "raw_fetched": 3,
            "raw_unique": 3,
            "pages_fetched": 1,
            "cursor_present": True,
            "pagination_exhausted": True,
            "pagination_stop_reason": "short_page",
            "eligible_30d": 2,
            "candidate_within_admission_horizon": 1,
            "admission_horizon_days": 14.0,
            "market_limit": 10,
        }
        assert horizon_shadow == {
            "type": "POLYMARKET_HORIZON_SHADOW",
            "ts": horizon_shadow["ts"],
            "source": "Example Wire",
            "headline": "Example event gets more likely",
            "venue": "polymarket_us",
            "production_horizon_days": 14.0,
            "shadow_horizon_start_days": 14.0,
            "shadow_horizon_end_days": 30.0,
            "production_candidate_count": 2,
            "shadow_candidate_count": 3,
            "production_qualifying_match_count": 0,
            "shadow_qualifying_match_count": 1,
            "production_no_token_overlap_count": 2,
            "production_below_min_post_weight_score_count": 0,
            "production_weight_demoted_below_min_score_count": 0,
            "production_min_match_score": 0.08,
            "shadow_no_token_overlap_count": 2,
            "shadow_below_min_post_weight_score_count": 1,
            "shadow_weight_demoted_below_min_score_count": 0,
            "shadow_min_match_score": 0.08,
            "shadow_analysis_status": "not_evaluated_shadow_only",
            "production_best_rejected_pre_weight_score": 0.12,
            "production_best_rejected_post_weight_score": 0.012,
            "shadow_best_rejected_pre_weight_score": 0.09,
            "shadow_best_rejected_post_weight_score": 0.07,
            "production_counterfactual_shadow": {
                **_post_admission_counterfactual_shadow(),
                "candidates": [
                    {
                        "ticker": "0xdef456",
                        "rejection_reason": "market_without_match_tokens",
                        "market_token_count": 0,
                        "matched_token_count": 0,
                    },
                    {
                        "ticker": "0xabc123",
                        "market_title": "Will Example pass?",
                        "rejection_reason": "no_token_overlap",
                        "market_token_count": 11,
                        "matched_token_count": 0,
                    },
                ],
            },
        }
    finally:
        _cleanup(tmp)
