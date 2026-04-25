"""Tests for load_from_disk: read YAML file -> OverridesState."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from utils.runtime_overrides import OverridesState, load_from_disk


VALID_YAML = textwrap.dedent("""
    version: 1
    updated_at: "2026-05-02T14:30:00+00:00"
    updated_by: "governance-agent-v0.2.1"
    mode: shadow
    applied:
      disabled_sources:
        - source: "r/Turkey"
          reason: "0 matches"
          confidence: 0.94
          decided_at: "2026-05-02T14:30:00+00:00"
          decided_by: "governance-agent-v0.2.1"
          decision_id: "gd_2026-05-02_0042"
          expires_at: null
          predicted_effect:
            metric: "anchor_rate"
            baseline: 0.99
            predicted_post_change: 0.85
            evaluate_at: "2026-05-09T14:30:00+00:00"
      disabled_keywords: []
      threshold_overrides: []
    proposed:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
""").lstrip()


class TestLoadFromDisk:
    def test_loads_valid_file(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML)
        state = load_from_disk(p)
        assert isinstance(state, OverridesState)
        assert len(state.applied_disabled_sources) == 1

    def test_missing_file_returns_default_empty_state(self, tmp_path: Path):
        p = tmp_path / "does_not_exist.yaml"
        state = load_from_disk(p)
        assert isinstance(state, OverridesState)
        assert state.applied_disabled_sources == []
        assert state.mode == "shadow"  # safest default for a missing file

    def test_empty_file_raises(self, tmp_path: Path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(ValueError, match="empty"):
            load_from_disk(p)

    def test_malformed_yaml_raises(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("not: valid: yaml: : :")
        with pytest.raises(ValueError, match="YAML"):
            load_from_disk(p)

    def test_schema_violation_raises_with_path(self, tmp_path: Path):
        # Confidence > 1 in a deeply nested entry; error should point to it.
        bad = VALID_YAML.replace("0.94", "1.94")
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="confidence"):
            load_from_disk(p)
