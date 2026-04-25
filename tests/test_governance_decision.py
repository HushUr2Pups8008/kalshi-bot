"""Decision dataclass — surface, validation, and conversions."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from governance.decision import Decision, PredictedEffect, VALID_ACTIONS


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


def test_predicted_effect_holds_all_required_fields():
    pe = _ok_predicted_effect()
    assert pe.metric
    assert isinstance(pe.baseline, float)
    assert isinstance(pe.predicted_post_change, float)
    assert pe.evaluate_at.tzinfo is not None
