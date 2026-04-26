"""Chaos tests: malformed inputs and adversarial LLM outputs must
degrade gracefully, never corrupt state."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


def test_kill_switch_disabled_short_circuits_main(tmp_path, monkeypatch):
    from governance.agent import main
    monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
    monkeypatch.setenv("GOVERNANCE_OVERRIDES_PATH", str(tmp_path / "ovr.yaml"))
    monkeypatch.setenv("GOVERNANCE_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("GOVERNANCE_TRADE_LOG_PATH", str(tmp_path / "trades.jsonl"))
    monkeypatch.setenv("GOVERNANCE_PAPER_DB_PATH", str(tmp_path / "paper.db"))
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="t", mode="shadow", applied_disabled_sources=[]),
        tmp_path / "ovr.yaml",
    )
    rc = main(argv=["--cadence", "fast", "--llm", "fake"])
    assert rc == 2


def test_malformed_llm_json_logged_and_skipped(tmp_path, monkeypatch):
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.agent import load_state, run_cycle
    from governance.evidence import Candidate
    from governance.llm import FakeLLM, prompt_hash
    from governance.prompts import render_prompt
    from governance.evidence import compose_evidence_for_candidate
    from utils.runtime_overrides import OverridesState, atomic_write_state

    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    overrides_path = tmp_path / "ovr.yaml"
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="t", mode="shadow", applied_disabled_sources=[]),
        overrides_path,
    )
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    adapter = KalshiGovernanceAdapter(
        trade_log_path=tmp_path / "trades.jsonl",
        paper_db_path=tmp_path / "paper.db", market_provider=lambda: [],
    )
    cand = Candidate(action="disable_source", target="r/Turkey",
                     evidence_pointer={"reddit_sub_index": 0})
    audit_data = {"reddit": {"subs": [
        {"source": "r/Turkey", "ingestion": 408, "fresh_passes": 7,
         "matches": 0, "classification": "all_stale"}
    ]}}
    evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)
    fake = FakeLLM(canned={prompt_hash(sys_p, user_p): "this is not json at all"})
    logs_dir = tmp_path / "logs" / "governance"
    # AuditLogger uses keyword-only log_dir per Task 17 drift note.
    logger = AuditLogger(log_dir=logs_dir)

    rc = run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter, llm=fake, audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand], audit_data_override=audit_data,
    )
    assert rc == 0
    body = (logs_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(l)["type"] for l in body]
    assert "GOVERNANCE_DECISION_PARSE_ERROR" in types
    assert "GOVERNANCE_CYCLE_END" in types  # cycle completes despite the error


def test_validation_failure_caught_and_logged(tmp_path, monkeypatch):
    """LLM returns valid JSON but with confidence=2.0 — Decision.__post_init__
    raises ValueError, which the agent must catch and log as a validation
    error."""
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.agent import load_state, run_cycle
    from governance.evidence import Candidate, compose_evidence_for_candidate
    from governance.llm import FakeLLM, prompt_hash
    from governance.prompts import render_prompt
    from utils.runtime_overrides import OverridesState, atomic_write_state

    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    overrides_path = tmp_path / "ovr.yaml"
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="t", mode="shadow", applied_disabled_sources=[]),
        overrides_path,
    )
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    adapter = KalshiGovernanceAdapter(
        trade_log_path=tmp_path / "trades.jsonl",
        paper_db_path=tmp_path / "paper.db", market_provider=lambda: [],
    )
    cand = Candidate(action="disable_source", target="r/Turkey",
                     evidence_pointer={"reddit_sub_index": 0})
    audit_data = {"reddit": {"subs": [
        {"source": "r/Turkey", "ingestion": 408, "fresh_passes": 7,
         "matches": 0, "classification": "all_stale"}
    ]}}
    evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)
    bad_response = json.dumps({
        "action": "disable_source",
        "target": "r/Turkey",
        "reasoning": "x",
        "confidence": 2.0,  # invalid; will fail Decision.__post_init__
        "predicted_effect": {
            "metric": "m", "baseline": 0.0,
            "predicted_post_change": 0.0, "evaluate_at_days": 7,
        },
    })
    fake = FakeLLM(canned={prompt_hash(sys_p, user_p): bad_response})
    logs_dir = tmp_path / "logs" / "governance"
    logger = AuditLogger(log_dir=logs_dir)

    rc = run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter, llm=fake, audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand], audit_data_override=audit_data,
    )
    assert rc == 0
    body = (logs_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(l)["type"] for l in body]
    assert "GOVERNANCE_DECISION_VALIDATION_ERROR" in types
