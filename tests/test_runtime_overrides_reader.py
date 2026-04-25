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
