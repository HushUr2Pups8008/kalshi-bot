"""Decision dataclass — surface, validation, and conversions."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from governance.decision import Decision, PredictedEffect, VALID_ACTIONS, VALID_CADENCES


_NOW = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 5, 9, 14, 30, 0, tzinfo=timezone.utc)


def _ok_predicted_effect() -> PredictedEffect:
    return PredictedEffect(
        metric="reddit_rate_limit_budget_consumed_daily",
        baseline=0.12,
        predicted_post_change=0.08,
        evaluate_at=_LATER,
    )


def _ok_decision(**overrides) -> Decision:
    defaults = dict(
        decision_id="gd_2026-05-02_0042",
        batch_id="gb_2026-05-02_0012",
        decided_at=_NOW,
        decided_by="governance-agent-v0.2.1",
        cadence="fast",
        action="disable_source",
        target="r/Turkey",
        proposed_change={"before": "source_active", "after": "source_disabled", "expires_at": None},
        confidence=0.94,
        reasoning="Test reasoning. Sufficient detail.",
        evidence_summary={"ingestion_events": 408, "match_count": 0},
        predicted_effect=_ok_predicted_effect(),
        model_used="qwen3-14b-instruct",
        escalated_to_claude=False,
        claude_response=None,
    )
    defaults.update(overrides)
    return Decision(**defaults)


def test_decision_constructs_with_valid_fields():
    d = _ok_decision()
    assert d.decision_id == "gd_2026-05-02_0042"
    assert d.action == "disable_source"
    assert d.confidence == 0.94
    assert d.predicted_effect.metric == "reddit_rate_limit_budget_consumed_daily"


def test_decision_is_frozen():
    d = _ok_decision()
    with pytest.raises(Exception):  # FrozenInstanceError on dataclasses; broad catch is fine
        d.confidence = 0.0  # type: ignore[misc]


def test_valid_actions_set_is_immutable_export():
    assert "disable_source" in VALID_ACTIONS
    assert "disable_keyword" in VALID_ACTIONS
    assert "tune_threshold" in VALID_ACTIONS
    assert "no_action" in VALID_ACTIONS


def test_valid_cadences_set_is_immutable_export():
    assert "fast" in VALID_CADENCES
    assert "deep" in VALID_CADENCES
    assert "weekly_review" in VALID_CADENCES
    # Sanity: no rogue cadences sneaked in.
    assert VALID_CADENCES == frozenset({"fast", "deep", "weekly_review"})


def test_predicted_effect_holds_all_required_fields():
    pe = _ok_predicted_effect()
    assert pe.metric
    assert isinstance(pe.baseline, float)
    assert isinstance(pe.predicted_post_change, float)
    assert pe.evaluate_at.tzinfo is not None


import re


def test_decision_rejects_confidence_above_one():
    with pytest.raises(ValueError, match="confidence"):
        _ok_decision(confidence=1.01)


def test_decision_rejects_confidence_below_zero():
    with pytest.raises(ValueError, match="confidence"):
        _ok_decision(confidence=-0.01)


def test_decision_rejects_unknown_action():
    with pytest.raises(ValueError, match="action"):
        _ok_decision(action="set_market_position")  # not in VALID_ACTIONS


def test_decision_rejects_unknown_cadence():
    with pytest.raises(ValueError, match="cadence"):
        _ok_decision(cadence="hourly")


def test_decision_rejects_malformed_decision_id():
    with pytest.raises(ValueError, match="decision_id"):
        _ok_decision(decision_id="not-a-valid-id")


def test_decision_rejects_malformed_batch_id():
    with pytest.raises(ValueError, match="batch_id"):
        _ok_decision(batch_id="batch_001")


def test_decision_requires_predicted_effect_for_action_decisions():
    """no_action decisions may have predicted_effect=None; action decisions
    must have one (per spec §10 / decision 10 — outcome-tracking is
    mandatory for any change the agent proposes)."""
    with pytest.raises(ValueError, match="predicted_effect"):
        _ok_decision(action="disable_source", predicted_effect=None)


def test_decision_allows_no_action_with_null_predicted_effect():
    d = _ok_decision(
        action="no_action",
        predicted_effect=None,
        proposed_change={},
        target="",
    )
    assert d.action == "no_action"
    assert d.predicted_effect is None


def test_decision_rejects_evaluate_at_at_or_before_decided_at():
    with pytest.raises(ValueError, match="evaluate_at"):
        bad_pe = PredictedEffect(
            metric="m",
            baseline=0.0,
            predicted_post_change=0.0,
            evaluate_at=_NOW,  # not strictly after decided_at
        )
        _ok_decision(predicted_effect=bad_pe)


def test_decision_rejects_naive_decided_at():
    naive = datetime(2026, 5, 2, 14, 30, 0)  # no tzinfo
    with pytest.raises(ValueError, match="decided_at"):
        _ok_decision(decided_at=naive)


def test_decision_rejects_naive_evaluate_at():
    naive = datetime(2026, 5, 9, 14, 30, 0)
    with pytest.raises(ValueError, match="evaluate_at"):
        bad_pe = PredictedEffect(
            metric="m", baseline=0.0, predicted_post_change=0.0, evaluate_at=naive,
        )
        _ok_decision(predicted_effect=bad_pe)


def test_decision_rejects_empty_reasoning():
    with pytest.raises(ValueError, match="reasoning"):
        _ok_decision(reasoning="")


def test_decision_rejects_target_empty_for_action_decisions():
    with pytest.raises(ValueError, match="target"):
        _ok_decision(action="disable_source", target="")
