"""Tests for the module-level helpers used by main.py / feeds/ to
consult the runtime overrides reader without holding a reader reference.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from utils.runtime_overrides import (
    DisabledKeyword,
    DisabledSource,
    OverridesState,
    PredictedEffect,
    ThresholdOverride,
    get_threshold_override,
    is_keyword_disabled,
    is_source_disabled,
    set_global_reader,
)
from utils import runtime_overrides as ro


_NOW = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)


def _pe() -> PredictedEffect:
    return PredictedEffect(metric="m", baseline=0, predicted_post_change=0, evaluate_at=_NOW)


@pytest.fixture(autouse=True)
def _reset_global_reader():
    """Always restore the global reader to whatever it was (likely None)
    before/after each test, so tests do not leak state."""
    original = ro._global_reader
    yield
    ro._global_reader = original


class FakeReader:
    """Stub reader with a fixed snapshot for tests that don't need disk."""

    def __init__(self, state: OverridesState):
        self._state = state

    def snapshot(self) -> OverridesState:
        return self._state


def _make_state_with_source(source_name: str) -> OverridesState:
    return OverridesState(
        version=1, updated_at=_NOW, updated_by="test", mode="real",
        applied_disabled_sources=[
            DisabledSource(
                source=source_name, reason="test", confidence=0.9,
                decided_at=_NOW, decided_by="test",
                decision_id="gd_2026-05-02_0001",
                expires_at=None, predicted_effect=_pe(),
            )
        ],
    )


def _make_state_with_keyword(keyword: str) -> OverridesState:
    return OverridesState(
        version=1, updated_at=_NOW, updated_by="test", mode="real",
        applied_disabled_keywords=[
            DisabledKeyword(
                keyword=keyword, reason="test", confidence=0.8,
                decided_at=_NOW, decided_by="test",
                decision_id="gd_2026-05-02_0002",
                expires_at=None, predicted_effect=_pe(),
            )
        ],
    )


def _make_state_with_threshold(path: str, value) -> OverridesState:
    return OverridesState(
        version=1, updated_at=_NOW, updated_by="test", mode="real",
        applied_threshold_overrides=[
            ThresholdOverride(
                path=path, value=value, reason="test", confidence=0.7,
                decided_at=_NOW, decided_by="test",
                decision_id="gd_2026-05-02_0003",
                expires_at=None, predicted_effect=_pe(),
            )
        ],
    )


class TestIsSourceDisabled:
    def test_no_global_reader_falls_back_to_static_only(self, monkeypatch):
        """Backward-compat: if main.py has not yet called set_global_reader,
        the helper still consults the static DISABLED_NEWS_SOURCES set and
        returns the same result as the pre-Phase-1 main.py helper."""
        monkeypatch.setattr(ro, "_static_disabled_sources",
                            lambda: frozenset({"static_only"}))
        ro._global_reader = None
        assert is_source_disabled("static_only") is True
        assert is_source_disabled("not_disabled") is False

    def test_runtime_only(self, monkeypatch):
        monkeypatch.setattr(ro, "_static_disabled_sources", lambda: frozenset())
        set_global_reader(FakeReader(_make_state_with_source("r/RuntimeOnly")))
        assert is_source_disabled("r/RuntimeOnly") is True
        assert is_source_disabled("r/Other") is False

    def test_static_and_runtime_union(self, monkeypatch):
        monkeypatch.setattr(ro, "_static_disabled_sources",
                            lambda: frozenset({"r/StaticOnly"}))
        set_global_reader(FakeReader(_make_state_with_source("r/RuntimeOnly")))
        assert is_source_disabled("r/StaticOnly") is True
        assert is_source_disabled("r/RuntimeOnly") is True
        assert is_source_disabled("r/Neither") is False

    def test_case_insensitive_match_against_static(self, monkeypatch):
        """Mirrors the existing main.py case-insensitive behavior. Static set
        contains 'r/Turkey' (per config.py); 'r/turkey' must also match."""
        monkeypatch.setattr(ro, "_static_disabled_sources",
                            lambda: frozenset({"r/Turkey"}))
        ro._global_reader = None
        assert is_source_disabled("r/Turkey") is True
        assert is_source_disabled("r/turkey") is True
        assert is_source_disabled("R/TURKEY") is True

    def test_case_insensitive_match_against_runtime(self, monkeypatch):
        """Same case-insensitive policy applies to runtime-disabled sources."""
        monkeypatch.setattr(ro, "_static_disabled_sources", lambda: frozenset())
        set_global_reader(FakeReader(_make_state_with_source("r/Turkey")))
        assert is_source_disabled("r/Turkey") is True
        assert is_source_disabled("r/turkey") is True


class TestIsKeywordDisabled:
    def test_no_global_reader_returns_false(self):
        ro._global_reader = None
        assert is_keyword_disabled("anything") is False

    def test_runtime_match(self, monkeypatch):
        set_global_reader(FakeReader(_make_state_with_keyword("trump may deadline")))
        assert is_keyword_disabled("trump may deadline") is True
        assert is_keyword_disabled("not in list") is False

    def test_keyword_match_case_sensitive(self, monkeypatch):
        """Keywords match against text body where capitalization is preserved.
        Spec preserves case-sensitivity for keywords."""
        set_global_reader(FakeReader(_make_state_with_keyword("ceasefire")))
        assert is_keyword_disabled("ceasefire") is True
        assert is_keyword_disabled("Ceasefire") is False  # different casing -> not disabled


class TestGetThresholdOverride:
    def test_no_global_reader_returns_none(self):
        ro._global_reader = None
        assert get_threshold_override("any.path") is None

    def test_returns_value_when_path_matches(self, monkeypatch):
        set_global_reader(FakeReader(
            _make_state_with_threshold("EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA", 21600)
        ))
        assert get_threshold_override("EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA") == 21600
        assert get_threshold_override("EARLY_MAX_NEWS_AGE_BY_SOURCE.OtherSrc") is None


class TestSetGlobalReader:
    def test_set_and_clear(self, monkeypatch):
        monkeypatch.setattr(ro, "_static_disabled_sources", lambda: frozenset())
        set_global_reader(FakeReader(_make_state_with_source("r/X")))
        assert is_source_disabled("r/X") is True
        set_global_reader(None)
        assert is_source_disabled("r/X") is False
