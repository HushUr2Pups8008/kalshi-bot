"""Tests for governance.safety primitives (Phase 1 standalone tests).

These primitives are used by the governance agent in Phase 2+ but are
built and tested here so the safety layer is solid before any LLM
involvement begins.
"""

from __future__ import annotations

import pytest

from governance.safety import KillSwitch
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


class TestKillSwitch:
    def test_default_state_is_active(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)
        ks = KillSwitch()
        assert ks.is_disabled() is False
        assert ks.is_readonly() is False
        assert ks.may_apply() is True

    def test_disabled_env_var_truthy_values(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)
        for truthy in ("true", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("GOVERNANCE_DISABLED", truthy)
            ks = KillSwitch()
            assert ks.is_disabled() is True, f"expected disabled for {truthy!r}"
            assert ks.may_apply() is False

    def test_disabled_env_var_falsy_values(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)
        for falsy in ("false", "FALSE", "0", "no", "off", ""):
            monkeypatch.setenv("GOVERNANCE_DISABLED", falsy)
            ks = KillSwitch()
            assert ks.is_disabled() is False, f"expected enabled for {falsy!r}"

    def test_readonly_blocks_apply_but_not_run(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        monkeypatch.setenv("GOVERNANCE_READONLY", "true")
        ks = KillSwitch()
        assert ks.is_disabled() is False
        assert ks.is_readonly() is True
        assert ks.may_apply() is False

    def test_disabled_takes_precedence_over_readonly(self, monkeypatch):
        monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
        monkeypatch.setenv("GOVERNANCE_READONLY", "true")
        ks = KillSwitch()
        assert ks.is_disabled() is True
        assert ks.may_apply() is False

    def test_re_check_picks_up_env_changes(self, monkeypatch):
        # KillSwitch reads env on each call -- not cached. This is
        # important so a sysadmin can flip the kill-switch on a running
        # agent process between cycles.
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        ks = KillSwitch()
        assert ks.is_disabled() is False
        monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
        assert ks.is_disabled() is True

    def test_readonly_env_var_truthy_values(self, monkeypatch):
        """GOVERNANCE_READONLY must accept the same truthy variants as GOVERNANCE_DISABLED."""
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        for truthy in ("true", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("GOVERNANCE_READONLY", truthy)
            ks = KillSwitch()
            assert ks.is_readonly() is True, f"expected readonly for {truthy!r}"
            assert ks.may_apply() is False

    def test_readonly_env_var_falsy_values(self, monkeypatch):
        """GOVERNANCE_READONLY must reject the same falsy variants as GOVERNANCE_DISABLED."""
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        for falsy in ("false", "FALSE", "0", "no", "off", ""):
            monkeypatch.setenv("GOVERNANCE_READONLY", falsy)
            ks = KillSwitch()
            assert ks.is_readonly() is False, f"expected enabled for {falsy!r}"
            assert ks.may_apply() is True

    def test_whitespace_around_values_handled(self, monkeypatch):
        """Operator may set GOVERNANCE_DISABLED='  true  ' from a shell with
        trailing space; the implementation strips and lowercases.
        """
        monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)
        monkeypatch.setenv("GOVERNANCE_DISABLED", "  true  ")
        assert KillSwitch().is_disabled() is True
        monkeypatch.setenv("GOVERNANCE_DISABLED", "\tTRUE\n")
        assert KillSwitch().is_disabled() is True
