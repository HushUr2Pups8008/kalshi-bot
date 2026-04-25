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

    def test_all_integer_caps_reject_zero_and_negative(self):
        """All four integer caps must reject 0 and negative values, not just max_changes_per_run."""
        for field_name in (
            "max_changes_per_run",
            "blast_radius_max_source_disables_per_batch",
            "blast_radius_max_keyword_changes_per_batch",
            "blast_radius_max_threshold_tunings_per_batch",
        ):
            with pytest.raises(ValueError, match=field_name):
                SafetyConfig(**{field_name: 0})
            with pytest.raises(ValueError, match=field_name):
                SafetyConfig(**{field_name: -1})

    def test_blast_radius_pct_rejects_negative(self):
        """Symmetric coverage: lower bound, not just upper bound (1.5)."""
        with pytest.raises(ValueError, match="blast_radius_max_source_disable_pct"):
            SafetyConfig(blast_radius_max_source_disable_pct=-0.1)

    def test_unit_interval_boundary_values_accepted(self):
        """0.0 and 1.0 are valid operator settings:
        - confidence_threshold=0.0 → 'agent never auto-applies'
        - confidence_threshold=1.0 → 'only perfectly confident decisions apply'
        - blast_radius_max_source_disable_pct=0.0 → 'never auto-disable via percentage'
        - blast_radius_max_source_disable_pct=1.0 → 'no percentage cap, only absolute'
        """
        SafetyConfig(confidence_threshold=0.0)
        SafetyConfig(confidence_threshold=1.0)
        SafetyConfig(blast_radius_max_source_disable_pct=0.0)
        SafetyConfig(blast_radius_max_source_disable_pct=1.0)
