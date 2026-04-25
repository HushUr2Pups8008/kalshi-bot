"""Tests for TTL-expiry filtering on OverridesState."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from utils.runtime_overrides import (
    DisabledSource,
    OverridesState,
    PredictedEffect,
    filter_expired,
)


def _utc(hours_offset: int = 0) -> datetime:
    return datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc) + timedelta(hours=hours_offset)


def _make_disabled_source(decision_id: str, expires_at):
    return DisabledSource(
        source=f"src_{decision_id}",
        reason="x",
        confidence=0.5,
        decided_at=_utc(),
        decided_by="agent",
        decision_id=decision_id,
        expires_at=expires_at,
        predicted_effect=PredictedEffect(
            metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc(),
        ),
    )


class TestFilterExpired:
    def test_indefinite_never_expires(self):
        s = _make_disabled_source("gd_2026-05-02_0001", expires_at=None)
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            applied_disabled_sources=[s],
        )
        result = filter_expired(state, now=_utc(hours_offset=10000))
        assert len(result.applied_disabled_sources) == 1

    def test_expired_in_past_filtered(self):
        s = _make_disabled_source("gd_2026-05-02_0001", expires_at=_utc(hours_offset=-1))
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            applied_disabled_sources=[s],
        )
        result = filter_expired(state, now=_utc())
        assert result.applied_disabled_sources == []

    def test_not_yet_expired_kept(self):
        s = _make_disabled_source("gd_2026-05-02_0001", expires_at=_utc(hours_offset=24))
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            applied_disabled_sources=[s],
        )
        result = filter_expired(state, now=_utc(hours_offset=12))
        assert len(result.applied_disabled_sources) == 1

    def test_filter_does_not_mutate_input(self):
        s_kept = _make_disabled_source("gd_2026-05-02_0001", expires_at=None)
        s_expired = _make_disabled_source("gd_2026-05-02_0002", expires_at=_utc(hours_offset=-1))
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            applied_disabled_sources=[s_kept, s_expired],
        )
        original_len = len(state.applied_disabled_sources)
        filter_expired(state, now=_utc())
        assert len(state.applied_disabled_sources) == original_len  # input untouched

    def test_proposed_section_also_filtered(self):
        s = _make_disabled_source("gd_2026-05-02_0001", expires_at=_utc(hours_offset=-1))
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            proposed_disabled_sources=[s],
        )
        result = filter_expired(state, now=_utc())
        assert result.proposed_disabled_sources == []
