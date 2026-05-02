"""End-to-end Phase 2 cycle: real filesystem, FakeLLM, three candidates.

Asserts the load-bearing safety property — shadow mode never writes
applied — plus the audit-log fidelity to spec §6.2."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


@pytest.fixture
def tmp_overrides(tmp_path):
    from utils.runtime_overrides import OverridesState, atomic_write_state
    p = tmp_path / "overrides.yaml"
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test",
            mode="shadow",
            applied_disabled_sources=[],
        ),
        p,
    )
    return p


def test_full_shadow_cycle_three_reddit_candidates(tmp_path, tmp_overrides, monkeypatch):
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.agent import load_state, run_cycle
    from governance.evidence import (
        compose_evidence_for_candidate,
        select_candidates_for_cadence,
    )
    from governance.llm import FakeLLM, canned_response_for_action, prompt_hash
    from governance.prompts import render_prompt
    from utils.runtime_overrides import RuntimeOverridesReader

    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)

    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    paper_db = tmp_path / "paper.db"
    decisions_dir = tmp_path / "logs" / "governance"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=paper_db,
        market_provider=lambda: [],
    )

    audit_data = {
        "alignment": {"pairs": [], "overall_anchor_rate": 0.0, "overall_n": 0},
        "keywords": {"no_keyword_misses": 0, "candidate_phrases": []},
        "reddit": {"subs": [
            {"source": "r/Turkey", "ingestion": 408, "fresh_passes": 7,
             "matches": 0, "classification": "all_stale"},
            {"source": "r/pakistan", "ingestion": 80, "fresh_passes": 5,
             "matches": 0, "classification": "no_matches"},
            {"source": "r/Syria", "ingestion": 100, "fresh_passes": 0,
             "matches": 0, "classification": "all_stale"},
        ]},
        "freshness": {"sources": {}},
    }
    candidates = select_candidates_for_cadence(audit_data, cadence="deep")
    assert len(candidates) == 3, "test fixture should produce three Reddit candidates"

    canned: dict[str, str] = {}
    for cand in candidates:
        evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
        sys_p, user_p = render_prompt(cand.action, evidence)
        canned[prompt_hash(sys_p, user_p)] = canned_response_for_action(
            cand.action, target=cand.target,
        )
    fake = FakeLLM(canned=canned)
    # AuditLogger uses keyword-only log_dir per Task 17 drift note.
    logger = AuditLogger(log_dir=decisions_dir)

    rc = run_cycle(
        cadence="deep",
        loaded_state=load_state(overrides_path=tmp_overrides),
        adapter=adapter,
        llm=fake,
        audit_logger=logger,
        overrides_path=tmp_overrides,
        candidate_override=candidates,
        audit_data_override=audit_data,
    )
    assert rc == 0

    log_lines = (decisions_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in log_lines]
    decisions = [r for r in records if r["type"] == "GOVERNANCE_DECISION"]
    assert len(decisions) == 3
    assert all(r["shadow_mode"] is True for r in decisions)
    assert all(r["applied"] is False for r in decisions)
    assert all(r["action"] == "disable_source" for r in decisions)
    assert {r["target"] for r in decisions} == {"r/Turkey", "r/pakistan", "r/Syria"}
    # Spec §6.2 fidelity:
    for r in decisions:
        for k in ("decision_id", "batch_id", "decided_at", "decided_by",
                  "cadence", "action", "target", "proposed_change",
                  "model_used", "confidence", "reasoning",
                  "evidence_summary", "predicted_effect",
                  "outcome", "applied", "shadow_mode",
                  "safety_checks_passed"):
            assert k in r, f"spec §6.2 field {k} missing from audit record"

    # Load-bearing safety property: applied list still empty.
    # snapshot is a method on RuntimeOverridesReader (Task 16 drift note).
    after = RuntimeOverridesReader(path=tmp_overrides)
    after.reload()
    assert after.snapshot().applied_disabled_sources == []
