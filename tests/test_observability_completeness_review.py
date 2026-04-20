from scripts.observability_completeness_review import render_report, summarize
from utils.logger import BLEND_DECISION_REQUIRED_FIELDS


def _blend(**overrides):
    row = {
        "type": "BLEND_DECISION",
        "market_ticker": "KXOBS-26DEC31",
        "fast_lane_p": 0.60,
        "fast_lane_confidence": 0.70,
        "accumulation_p": 0.61,
        "accumulation_confidence": 0.40,
        "structural_p": 0.55,
        "structural_confidence": 0.30,
        "regime_weights": {"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
        "regime_confidence": 0.80,
        "blended_p": 0.60,
        "blended_confidence": 0.70,
        "disagreement_score": 0.02,
        "blend_mode": "weighted_blend",
        "trade_considered": False,
        "trade_blocked_reason": "not_considered",
        "evidence_ids_contributing": ["ev-1"],
        "ts": "2026-04-19T00:03:00+00:00",
    }
    row.update(overrides)
    return row


def test_blend_completeness_rates_and_threshold_failures_are_measured():
    summary = summarize(
        [
            _blend(),
            _blend(fast_lane_p=None),
        ]
    )

    assert summary["blend_decision_total"] == 2
    rates = {result.field: result.non_null_rate for result in summary["field_results"]}
    required_rates = {
        result.field: result.required_valid_rate
        for result in summary["field_results"]
    }
    assert rates["fast_lane_p"] == 0.5
    assert required_rates["fast_lane_p"] == 0.5
    assert "fast_lane_p" in summary["below_threshold"]
    assert summary["target_met"] is False


def test_semantically_nullable_lane_fields_do_not_fail_completeness():
    summary = summarize(
        [
            _blend(
                accumulation_p=None,
                accumulation_confidence=None,
                structural_p=None,
                structural_confidence=None,
                trade_blocked_reason=None,
            )
        ]
    )

    required_rates = {
        result.field: result.required_valid_rate
        for result in summary["field_results"]
    }

    assert required_rates["accumulation_p"] == 1.0
    assert required_rates["structural_p"] == 1.0
    assert required_rates["trade_blocked_reason"] == 1.0
    assert summary["below_threshold"] == []
    assert summary["target_met"] is True


def test_all_contract_required_blend_fields_are_reported():
    summary = summarize([_blend()])

    assert tuple(result.field for result in summary["field_results"]) == BLEND_DECISION_REQUIRED_FIELDS
    assert summary["target_met"] is True


def test_traceability_join_counts_find_intact_chain():
    rows = [
        {
            "type": "EVIDENCE_INGESTION",
            "market_ticker": "KXOBS-26DEC31",
            "evidence_id": "ev-1",
        },
        {
            "type": "DOSSIER_UPDATE",
            "market_ticker": "KXOBS-26DEC31",
            "evidence_ids_contributing": ["ev-1"],
        },
        {
            "type": "STRUCTURAL_PRIOR_RECOMPUTE",
            "market_ticker": "KXOBS-26DEC31",
        },
        _blend(trade_considered=True, trade_blocked_reason="G1_blended_confidence"),
        {"type": "SKIPPED", "ticker": "KXOBS-26DEC31"},
    ]

    trace = summarize(rows)["traceability"]

    assert trace["dossier_missing_evidence_links"] == 0
    assert trace["blend_missing_dossier_links"] == 0
    assert trace["blend_missing_structural_links"] == 0
    assert trace["outcome_missing_blend_links"] == 0
    assert trace["blocked_reason_gaps"] == 0


def test_traceability_join_counts_surface_gaps_and_render_warning_text():
    rows = [
        {
            "type": "DOSSIER_UPDATE",
            "market_ticker": "KXOBS-26DEC31",
            "evidence_ids_contributing": ["missing-ev"],
        },
        _blend(trade_considered=True, trade_blocked_reason=""),
        {"type": "SKIPPED", "ticker": "KXOTHER-26DEC31"},
    ]

    summary = summarize(rows)
    trace = summary["traceability"]
    report = render_report(summary)

    assert trace["dossier_missing_evidence_links"] == 1
    assert trace["blend_missing_dossier_links"] == 1
    assert trace["blend_missing_structural_links"] == 1
    assert trace["outcome_missing_blend_links"] == 1
    assert trace["blocked_reason_gaps"] == 1
    assert "Target met: True" in report
    assert "Blocked blend reason gaps:       1" in report
