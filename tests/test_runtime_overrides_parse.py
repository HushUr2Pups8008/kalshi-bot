"""Tests for parse_yaml_to_state — reading YAML dict into OverridesState."""

from __future__ import annotations

from datetime import timezone

import pytest

from utils.runtime_overrides import OverridesState, parse_yaml_to_state


_VALID_PREDICTED_EFFECT = {
    "metric": "anchor_rate",
    "baseline": 0.99,
    "predicted_post_change": 0.85,
    "evaluate_at": "2026-05-09T14:30:00+00:00",
}


def _valid_yaml() -> dict:
    return {
        "version": 1,
        "updated_at": "2026-05-02T14:30:00+00:00",
        "updated_by": "governance-agent-v0.2.1",
        "mode": "shadow",
        "applied": {
            "disabled_sources": [
                {
                    "source": "r/Turkey",
                    "reason": "0 matches over 7d",
                    "confidence": 0.94,
                    "decided_at": "2026-05-02T14:30:00+00:00",
                    "decided_by": "governance-agent-v0.2.1",
                    "decision_id": "gd_2026-05-02_0042",
                    "expires_at": None,
                    "predicted_effect": _VALID_PREDICTED_EFFECT,
                }
            ],
            "disabled_keywords": [],
            "threshold_overrides": [],
        },
        "proposed": {
            "disabled_sources": [],
            "disabled_keywords": [],
            "threshold_overrides": [],
        },
    }


class TestParseYamlToState:
    def test_minimal_valid_input(self):
        state = parse_yaml_to_state(_valid_yaml())
        assert isinstance(state, OverridesState)
        assert state.version == 1
        assert state.mode == "shadow"
        assert len(state.applied_disabled_sources) == 1
        assert state.applied_disabled_sources[0].source == "r/Turkey"

    def test_empty_applied_section(self):
        data = _valid_yaml()
        data["applied"]["disabled_sources"] = []
        state = parse_yaml_to_state(data)
        assert state.applied_disabled_sources == []

    def test_missing_version_raises(self):
        data = _valid_yaml()
        del data["version"]
        with pytest.raises(ValueError, match="version"):
            parse_yaml_to_state(data)

    def test_missing_mode_raises(self):
        data = _valid_yaml()
        del data["mode"]
        with pytest.raises(ValueError, match="mode"):
            parse_yaml_to_state(data)

    def test_unknown_top_level_section_ignored(self):
        # Forward-compat: agent might add a new section in v1+ that this
        # reader doesn't recognize. Don't crash; ignore.
        data = _valid_yaml()
        data["future_section"] = {"foo": "bar"}
        state = parse_yaml_to_state(data)
        assert state.version == 1

    def test_invalid_decision_id_in_entry_raises(self):
        data = _valid_yaml()
        data["applied"]["disabled_sources"][0]["decision_id"] = "not-valid"
        with pytest.raises(ValueError, match="decision_id"):
            parse_yaml_to_state(data)

    def test_proposed_validation_error_uses_proposed_prefix(self):
        """Regression: parse helpers used to hardcode 'applied.*' prefix even
        when called from the proposed-list path. The error context must
        accurately reflect where the bad data lives.
        """
        data = _valid_yaml()
        bad_entry = dict(data["applied"]["disabled_sources"][0])
        # Trigger an error inside the parse helper itself (not inside
        # DisabledSource.__post_init__): omit a required key so KeyError
        # is caught + re-raised with the section-prefixed ctx.
        del bad_entry["confidence"]
        data["applied"]["disabled_sources"] = []
        data["proposed"]["disabled_sources"] = [bad_entry]
        with pytest.raises(ValueError, match=r"proposed\.disabled_sources\[0\]"):
            parse_yaml_to_state(data)

    def test_iso8601_with_z_suffix_accepted(self):
        # Some YAML emitters use 'Z' instead of '+00:00' for UTC.
        # We accept both.
        data = _valid_yaml()
        data["updated_at"] = "2026-05-02T14:30:00Z"
        data["applied"]["disabled_sources"][0]["decided_at"] = "2026-05-02T14:30:00Z"
        data["applied"]["disabled_sources"][0]["predicted_effect"]["evaluate_at"] = (
            "2026-05-09T14:30:00Z"
        )
        state = parse_yaml_to_state(data)
        assert state.updated_at.tzinfo == timezone.utc

    def test_proposed_section_missing_treated_as_empty(self):
        data = _valid_yaml()
        del data["proposed"]
        state = parse_yaml_to_state(data)
        assert state.proposed_disabled_sources == []
