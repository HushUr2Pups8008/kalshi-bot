"""Tests for runtime_overrides schema dataclasses + validation.

The schema is the contract between the governance agent (writer) and
the kalshi-bot (reader). Bad schema = bad reload behavior, so tests are
parametrized against every required field, type, and range.

See docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md §6.1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils.runtime_overrides import (
    DisabledKeyword,
    DisabledSource,
    OverridesState,
    PredictedEffect,
    ThresholdOverride,
)


def _utc(years_offset: int = 0, hours_offset: int = 0) -> datetime:
    base = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
    return base + timedelta(days=365 * years_offset, hours=hours_offset)


class TestPredictedEffect:
    def test_construct_with_all_fields(self):
        pe = PredictedEffect(
            metric="anchor_rate",
            baseline=0.99,
            predicted_post_change=0.85,
            evaluate_at=_utc(),
        )
        assert pe.metric == "anchor_rate"
        assert pe.baseline == 0.99
        assert pe.predicted_post_change == 0.85
        assert pe.evaluate_at == _utc()

    def test_metric_required_non_empty(self):
        with pytest.raises(ValueError, match="metric"):
            PredictedEffect(
                metric="",
                baseline=0.0,
                predicted_post_change=0.0,
                evaluate_at=_utc(),
            )


class TestDisabledSource:
    def test_construct_with_required_fields(self):
        ds = DisabledSource(
            source="r/Turkey",
            reason="zero matches over 7d",
            confidence=0.94,
            decided_at=_utc(),
            decided_by="governance-agent-v0.2.1",
            decision_id="gd_2026-05-02_0042",
            expires_at=None,
            predicted_effect=PredictedEffect(
                metric="reddit_rate_limit_budget_consumed_daily",
                baseline=0.12,
                predicted_post_change=0.08,
                evaluate_at=_utc(hours_offset=168),
            ),
        )
        assert ds.source == "r/Turkey"
        assert ds.expires_at is None

    def test_confidence_must_be_unit_interval(self):
        with pytest.raises(ValueError, match="confidence"):
            DisabledSource(
                source="r/Turkey",
                reason="x",
                confidence=1.5,
                decided_at=_utc(),
                decided_by="agent",
                decision_id="gd_x",
                expires_at=None,
                predicted_effect=PredictedEffect(
                    metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
                ),
            )

    def test_decision_id_format(self):
        with pytest.raises(ValueError, match="decision_id"):
            DisabledSource(
                source="r/Turkey",
                reason="x",
                confidence=0.5,
                decided_at=_utc(),
                decided_by="agent",
                decision_id="not-a-valid-id",  # missing gd_ prefix
                expires_at=None,
                predicted_effect=PredictedEffect(
                    metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
                ),
            )


class TestDisabledKeyword:
    def test_construct(self):
        dk = DisabledKeyword(
            keyword="trump may deadline",
            reason="time-bounded",
            confidence=0.82,
            decided_at=_utc(),
            decided_by="agent",
            decision_id="gd_2026-05-02_0043",
            expires_at=_utc(hours_offset=24),
            predicted_effect=PredictedEffect(
                metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
            ),
        )
        assert dk.keyword == "trump may deadline"

    def test_keyword_required_non_empty(self):
        with pytest.raises(ValueError, match="keyword"):
            DisabledKeyword(
                keyword="",
                reason="x",
                confidence=0.5,
                decided_at=_utc(),
                decided_by="agent",
                decision_id="gd_2026-05-02_0001",
                expires_at=None,
                predicted_effect=PredictedEffect(
                    metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
                ),
            )


class TestThresholdOverride:
    def test_construct(self):
        to = ThresholdOverride(
            path="EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA",
            value=21600,
            reason="slow cadence",
            confidence=0.71,
            decided_at=_utc(),
            decided_by="agent",
            decision_id="gd_2026-05-02_0044",
            expires_at=None,
            predicted_effect=PredictedEffect(
                metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
            ),
        )
        assert to.path == "EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA"
        assert to.value == 21600

    def test_path_required_non_empty(self):
        with pytest.raises(ValueError, match="path"):
            ThresholdOverride(
                path="",
                value=10,
                reason="x",
                confidence=0.5,
                decided_at=_utc(),
                decided_by="agent",
                decision_id="gd_2026-05-02_0001",
                expires_at=None,
                predicted_effect=PredictedEffect(
                    metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
                ),
            )


class TestOverridesState:
    def test_empty_state(self):
        state = OverridesState(
            version=1,
            updated_at=_utc(),
            updated_by="agent",
            mode="shadow",
        )
        assert state.applied_disabled_sources == []
        assert state.applied_disabled_keywords == []
        assert state.applied_threshold_overrides == []
        assert state.proposed_disabled_sources == []

    def test_mode_must_be_valid(self):
        with pytest.raises(ValueError, match="mode"):
            OverridesState(
                version=1,
                updated_at=_utc(),
                updated_by="agent",
                mode="invalid",
            )

    def test_version_must_be_supported(self):
        with pytest.raises(ValueError, match="version"):
            OverridesState(
                version=99,  # future schema version we don't understand
                updated_at=_utc(),
                updated_by="agent",
                mode="shadow",
            )
