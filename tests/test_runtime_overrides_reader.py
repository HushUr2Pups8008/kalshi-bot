"""Tests for RuntimeOverridesReader: singleton, state-swap, diff detection."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from utils.runtime_overrides import (
    RuntimeOverridesReader,
    StateDiff,
)


VALID_YAML_NO_OVERRIDES = textwrap.dedent("""
    version: 1
    updated_at: "2026-05-02T14:30:00+00:00"
    updated_by: "test"
    mode: shadow
    applied:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
    proposed:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
""").lstrip()


def _yaml_with_disabled_source(source: str, decision_id: str) -> str:
    return textwrap.dedent(f"""
        version: 1
        updated_at: "2026-05-02T14:30:00+00:00"
        updated_by: "test"
        mode: real
        applied:
          disabled_sources:
            - source: "{source}"
              reason: "test"
              confidence: 0.9
              decided_at: "2026-05-02T14:30:00+00:00"
              decided_by: "test"
              decision_id: "{decision_id}"
              expires_at: null
              predicted_effect:
                metric: "m"
                baseline: 0
                predicted_post_change: 0
                evaluate_at: "2026-05-09T14:30:00+00:00"
          disabled_keywords: []
          threshold_overrides: []
        proposed:
          disabled_sources: []
          disabled_keywords: []
          threshold_overrides: []
    """).lstrip()


class TestRuntimeOverridesReader:
    def test_initial_state_is_empty_default(self, tmp_path: Path):
        r = RuntimeOverridesReader(path=tmp_path / "missing.yaml")
        # Before any reload, state is the default empty
        assert r.snapshot().applied_disabled_sources == []

    def test_reload_loads_state(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        diff = r.reload()
        assert len(r.snapshot().applied_disabled_sources) == 1
        assert isinstance(diff, StateDiff)

    def test_reload_failure_keeps_previous_state(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()  # load valid state
        assert len(r.snapshot().applied_disabled_sources) == 1

        # Corrupt the file
        p.write_text("not: valid: yaml: : :")
        with pytest.raises(ValueError):
            r.reload()
        # Previous state preserved
        assert len(r.snapshot().applied_disabled_sources) == 1

    def test_diff_detects_added_source(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML_NO_OVERRIDES)
        r = RuntimeOverridesReader(path=p)
        r.reload()  # initial empty applied state

        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        diff = r.reload()
        assert "r/Turkey" in diff.sources_added
        assert diff.sources_removed == set()

    def test_diff_detects_removed_source(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()

        p.write_text(VALID_YAML_NO_OVERRIDES)
        diff = r.reload()
        assert "r/Turkey" in diff.sources_removed
        assert diff.sources_added == set()

    def test_diff_no_changes_when_state_identical(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()

        diff = r.reload()  # reload same content
        assert diff.is_empty() is True


class TestReaderQueries:
    def test_is_source_disabled_runtime_only(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_source_disabled("r/Turkey") is True
        assert r.is_source_disabled("r/SomeOtherSub") is False

    def test_is_source_disabled_unioned_with_static_config(self, tmp_path: Path, monkeypatch):
        # Reader unions runtime overrides with static config for a single
        # source-of-truth answer. We patch the static set in config so we
        # don't accidentally exercise the real list.
        monkeypatch.setattr(
            "utils.runtime_overrides._static_disabled_sources",
            lambda: frozenset({"static_only_source"}),
        )
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("runtime_only_source", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_source_disabled("static_only_source") is True
        assert r.is_source_disabled("runtime_only_source") is True
        assert r.is_source_disabled("not_disabled_anywhere") is False

    def test_is_keyword_disabled(self, tmp_path: Path):
        yaml_with_kw = textwrap.dedent("""
            version: 1
            updated_at: "2026-05-02T14:30:00+00:00"
            updated_by: "test"
            mode: real
            applied:
              disabled_sources: []
              disabled_keywords:
                - keyword: "trump may deadline"
                  reason: "time-bounded"
                  confidence: 0.8
                  decided_at: "2026-05-02T14:30:00+00:00"
                  decided_by: "test"
                  decision_id: "gd_2026-05-02_0043"
                  expires_at: null
                  predicted_effect:
                    metric: "m"
                    baseline: 0
                    predicted_post_change: 0
                    evaluate_at: "2026-05-09T14:30:00+00:00"
              threshold_overrides: []
            proposed:
              disabled_sources: []
              disabled_keywords: []
              threshold_overrides: []
        """).lstrip()
        p = tmp_path / "overrides.yaml"
        p.write_text(yaml_with_kw)
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_keyword_disabled("trump may deadline") is True
        assert r.is_keyword_disabled("other keyword") is False

    def test_is_source_disabled_case_insensitive(self, tmp_path: Path):
        """Instance method matches case-insensitively (consistent with module helper)."""
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_source_disabled("r/Turkey") is True
        assert r.is_source_disabled("r/turkey") is True
        assert r.is_source_disabled("R/TURKEY") is True

    def test_get_threshold_override_returns_value_when_set(self, tmp_path: Path):
        yaml_with_threshold = textwrap.dedent("""
            version: 1
            updated_at: "2026-05-02T14:30:00+00:00"
            updated_by: "test"
            mode: real
            applied:
              disabled_sources: []
              disabled_keywords: []
              threshold_overrides:
                - path: "EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA"
                  value: 21600
                  reason: "slow"
                  confidence: 0.7
                  decided_at: "2026-05-02T14:30:00+00:00"
                  decided_by: "test"
                  decision_id: "gd_2026-05-02_0044"
                  expires_at: null
                  predicted_effect:
                    metric: "m"
                    baseline: 0
                    predicted_post_change: 0
                    evaluate_at: "2026-05-09T14:30:00+00:00"
            proposed:
              disabled_sources: []
              disabled_keywords: []
              threshold_overrides: []
        """).lstrip()
        p = tmp_path / "overrides.yaml"
        p.write_text(yaml_with_threshold)
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.get_threshold_override("EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA") == 21600
        assert r.get_threshold_override("EARLY_MAX_NEWS_AGE_BY_SOURCE.OtherSrc") is None

    def test_proposed_section_not_visible_to_queries(self, tmp_path: Path):
        # Bot must IGNORE proposed entirely. Querying a source that's
        # only in proposed must return False.
        yaml_proposed_only = textwrap.dedent("""
            version: 1
            updated_at: "2026-05-02T14:30:00+00:00"
            updated_by: "test"
            mode: shadow
            applied:
              disabled_sources: []
              disabled_keywords: []
              threshold_overrides: []
            proposed:
              disabled_sources:
                - source: "r/proposed_only"
                  reason: "shadow"
                  confidence: 0.9
                  decided_at: "2026-05-02T14:30:00+00:00"
                  decided_by: "test"
                  decision_id: "gd_2026-05-02_0099"
                  expires_at: null
                  predicted_effect:
                    metric: "m"
                    baseline: 0
                    predicted_post_change: 0
                    evaluate_at: "2026-05-09T14:30:00+00:00"
              disabled_keywords: []
              threshold_overrides: []
        """).lstrip()
        p = tmp_path / "overrides.yaml"
        p.write_text(yaml_proposed_only)
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_source_disabled("r/proposed_only") is False
