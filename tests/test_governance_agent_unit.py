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


import json


def test_run_cycle_emits_start_and_end_events_with_zero_candidates(tmp_path, monkeypatch):
    from governance.agent import run_cycle, AgentLoadedState
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger

    overrides_path = tmp_path / "overrides.yaml"
    decisions_dir = tmp_path / "logs" / "governance"
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")

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
    monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)

    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=tmp_path / "paper.db",
        market_provider=None,
    )
    # AuditLogger requires log_dir as a keyword arg (not positional) per
    # governance/audit.py:41 — Task 17 drift note in plan documents this.
    logger = AuditLogger(log_dir=decisions_dir)
    rc = run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter,
        llm=None,  # Task 18 wires LLM in
        audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[],  # force zero candidates for this test
    )
    assert rc == 0

    # Read the audit log: should have START and END
    log_files = sorted(decisions_dir.glob("decisions.jsonl*"))
    assert log_files, "audit logger did not write any decisions log file"
    body = log_files[-1].read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(line)["type"] for line in body]
    assert "GOVERNANCE_CYCLE_START" in types
    assert "GOVERNANCE_CYCLE_END" in types


def test_run_cycle_iterates_candidates_and_records_decisions(tmp_path, monkeypatch):
    from governance.agent import run_cycle
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.evidence import Candidate
    from governance.llm import FakeLLM, canned_response_for_action, prompt_hash
    from governance.prompts import render_prompt

    overrides_path = tmp_path / "overrides.yaml"
    decisions_dir = tmp_path / "logs" / "governance"
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")

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

    cand = Candidate(
        action="disable_source", target="r/Turkey",
        evidence_pointer={"reddit_sub_index": 0},
    )
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=tmp_path / "paper.db",
        market_provider=lambda: [],  # zero markets
    )
    # Pre-compute the prompt this candidate will produce so FakeLLM can match it.
    from governance.evidence import compose_evidence_for_candidate
    fake_audit = {
        "reddit": {"subs": [{"source": "r/Turkey", "ingestion": 408,
                              "fresh_passes": 7, "matches": 0,
                              "classification": "all_stale"}]},
    }
    evidence = compose_evidence_for_candidate(cand, fake_audit, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)

    fake = FakeLLM(canned={
        prompt_hash(sys_p, user_p): canned_response_for_action("disable_source", target="r/Turkey"),
    })
    logger = AuditLogger(log_dir=decisions_dir)

    rc = run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter,
        llm=fake,
        audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand],
        audit_data_override=fake_audit,  # injected for the test
    )
    assert rc == 0

    log_lines = (decisions_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(line)["type"] for line in log_lines]
    assert types.count("GOVERNANCE_DECISION") == 1
    decision_record = next(json.loads(l) for l in log_lines if json.loads(l)["type"] == "GOVERNANCE_DECISION")
    assert decision_record["action"] == "disable_source"
    assert decision_record["target"] == "r/Turkey"
    assert decision_record["shadow_mode"] is True
    assert decision_record["applied"] is False  # shadow mode never applies
