"""Backward-compat test: behavior with NO overrides file present.

Bot must continue to operate identically to its pre-Phase-1 self when
no `data/runtime_overrides.yaml` file exists. This test exists to make
that an explicit invariant rather than an implicit assumption.
"""

from __future__ import annotations

from pathlib import Path

from utils.runtime_overrides import RuntimeOverridesReader


def test_no_file_yields_empty_overrides(tmp_path: Path):
    p = tmp_path / "does_not_exist.yaml"
    assert not p.exists()

    reader = RuntimeOverridesReader(path=p)
    reader.reload()

    state = reader.snapshot()
    assert state.applied_disabled_sources == []
    assert state.applied_disabled_keywords == []
    assert state.applied_threshold_overrides == []


def test_no_file_queries_return_static_only(tmp_path: Path, monkeypatch):
    from utils import runtime_overrides as ro
    monkeypatch.setattr(ro, "_static_disabled_sources", lambda: frozenset({"static_src"}))

    p = tmp_path / "does_not_exist.yaml"
    reader = RuntimeOverridesReader(path=p)
    reader.reload()

    # Static-only result -- no runtime overrides
    assert reader.is_source_disabled("static_src") is True
    assert reader.is_source_disabled("other") is False
    assert reader.is_keyword_disabled("any_keyword") is False
    assert reader.get_threshold_override("any.path") is None


def test_repeated_reload_with_no_file_idempotent(tmp_path: Path):
    p = tmp_path / "does_not_exist.yaml"
    reader = RuntimeOverridesReader(path=p)
    diff_first = reader.reload()
    diff_second = reader.reload()
    diff_third = reader.reload()
    # First reload may show changes from the default empty state to
    # a freshly-read empty state (none in practice). Subsequent reloads
    # must produce empty diffs.
    assert diff_second.is_empty()
    assert diff_third.is_empty()
