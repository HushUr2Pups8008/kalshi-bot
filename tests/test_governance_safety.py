"""Tests for governance.safety primitives (Phase 1 standalone tests).

These primitives are used by the governance agent in Phase 2+ but are
built and tested here so the safety layer is solid before any LLM
involvement begins.
"""

from __future__ import annotations

import pytest

from governance.safety import SafetyConfig


class TestSafetyConfig:
    def test_default_values(self):
        c = SafetyConfig()
        assert c.confidence_threshold == 0.7
        assert c.max_changes_per_run == 10
        assert c.blast_radius_max_source_disable_pct == 0.20
        assert c.blast_radius_max_source_disables_per_batch == 5
        assert c.blast_radius_max_keyword_changes_per_batch == 5
        assert c.blast_radius_max_threshold_tunings_per_batch == 3

    def test_custom_values(self):
        c = SafetyConfig(
            confidence_threshold=0.8,
            max_changes_per_run=20,
            blast_radius_max_source_disable_pct=0.10,
        )
        assert c.confidence_threshold == 0.8
        assert c.max_changes_per_run == 20
        assert c.blast_radius_max_source_disable_pct == 0.10

    def test_confidence_threshold_must_be_unit_interval(self):
        with pytest.raises(ValueError, match="confidence_threshold"):
            SafetyConfig(confidence_threshold=1.5)
        with pytest.raises(ValueError, match="confidence_threshold"):
            SafetyConfig(confidence_threshold=-0.1)

    def test_blast_radius_pct_must_be_unit_interval(self):
        with pytest.raises(ValueError, match="blast_radius_max_source_disable_pct"):
            SafetyConfig(blast_radius_max_source_disable_pct=1.5)

    def test_max_changes_must_be_positive(self):
        with pytest.raises(ValueError, match="max_changes_per_run"):
            SafetyConfig(max_changes_per_run=0)
        with pytest.raises(ValueError, match="max_changes_per_run"):
            SafetyConfig(max_changes_per_run=-5)
