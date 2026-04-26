"""Unit tests for governance.agent helpers (ID generators, load_state).
Integration tests live in test_governance_agent_integration.py."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from governance.agent import (
    AgentLoadedState,
    KillSwitchActive,
    generate_batch_id,
    generate_cycle_id,
    generate_decision_id,
    load_state,
)


def test_generate_decision_id_format():
    now = datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc)
    out = generate_decision_id(now=now, sequence=42)
    assert out == "gd_2026-05-02_0042"


def test_generate_batch_id_format():
    now = datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc)
    out = generate_batch_id(now=now, sequence=12)
    assert out == "gb_2026-05-02_0012"


def test_generate_cycle_id_uses_seconds_resolution():
    now = datetime(2026, 5, 2, 14, 30, 17, tzinfo=timezone.utc)
    out = generate_cycle_id(now=now)
    assert out == "gc_2026-05-02_143017"


def test_load_state_returns_loaded_state(tmp_path, monkeypatch):
    overrides_path = tmp_path / "overrides.yaml"
    # Write an empty (default) state. utils.runtime_overrides has helpers.
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test",
            mode="shadow",
            applied_disabled_sources=[],
        ),
        overrides_path,
    )
    # Ensure no kill switch
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)

    state = load_state(overrides_path=overrides_path)
    assert isinstance(state, AgentLoadedState)
    assert state.mode == "shadow"
    assert state.kill_switch_disabled is False
    assert state.kill_switch_readonly is False
    assert state.reader is not None


def test_load_state_raises_when_kill_switch_disabled(tmp_path, monkeypatch):
    overrides_path = tmp_path / "overrides.yaml"
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test", mode="shadow", applied_disabled_sources=[],
        ),
        overrides_path,
    )
    monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
    with pytest.raises(KillSwitchActive, match="DISABLED"):
        load_state(overrides_path=overrides_path)


def test_load_state_marks_readonly_without_raising(tmp_path, monkeypatch):
    overrides_path = tmp_path / "overrides.yaml"
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test", mode="shadow", applied_disabled_sources=[],
        ),
        overrides_path,
    )
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.setenv("GOVERNANCE_READONLY", "true")

    state = load_state(overrides_path=overrides_path)
    assert state.kill_switch_readonly is True
    assert state.kill_switch_disabled is False
