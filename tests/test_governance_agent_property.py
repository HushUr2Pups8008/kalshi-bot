"""Hypothesis property tests for the governance agent's safety invariants.

Core invariant: no decision with confidence < safety.confidence_threshold
ever has applied=True in the audit log, regardless of mode or kill-switch
state. This is the load-bearing correctness property of Phase 2's
shadow→real promotion logic."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings, strategies as st



def _fixed_audit_data():
    return {"reddit": {"subs": [
        {"source": "r/Test", "ingestion": 408, "fresh_passes": 7,
         "matches": 0, "classification": "all_stale"}
    ]}}


@settings(
    max_examples=30,
    deadline=None,
    # tmp_path_factory + monkeypatch are function-scoped; each example
    # explicitly mktemp()s a fresh dir and clears the GOVERNANCE_*
    # env vars at the top, so non-reset is harmless here.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    threshold=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    mode=st.sampled_from(["shadow", "real"]),
)
def test_below_threshold_never_applies(tmp_path_factory, confidence, threshold, mode, monkeypatch):
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.agent import load_state, run_cycle
    from governance.evidence import Candidate, compose_evidence_for_candidate
    from governance.llm import FakeLLM, prompt_hash
    from governance.prompts import render_prompt
    from governance.safety import SafetyConfig
    from utils.runtime_overrides import OverridesState, atomic_write_state

    tmp_path = tmp_path_factory.mktemp("hyp")
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)

    overrides_path = tmp_path / "ovr.yaml"
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="t", mode=mode, applied_disabled_sources=[]),
        overrides_path,
    )
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")

    cand = Candidate(action="disable_source", target="r/Test",
                     evidence_pointer={"reddit_sub_index": 0})
    audit_data = _fixed_audit_data()
    adapter = KalshiGovernanceAdapter(
        trade_log_path=tmp_path / "trades.jsonl",
        paper_db_path=tmp_path / "paper.db",
        market_provider=lambda: [],
    )
    evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)
    response = json.dumps({
        "action": "disable_source",
        "target": "r/Test",
        "reasoning": "Hypothesis-generated.",
        "confidence": confidence,
        "predicted_effect": {
            "metric": "m", "baseline": 0.5,
            "predicted_post_change": 0.4, "evaluate_at_days": 7,
        },
    })
    fake = FakeLLM(canned={prompt_hash(sys_p, user_p): response})

    logs_dir = tmp_path / "logs"
    # AuditLogger uses keyword-only log_dir per Task 17 drift note.
    logger = AuditLogger(log_dir=logs_dir)
    # SafetyConfig: only the fields _evaluate_safety actually reads
    # (confidence_threshold + max_changes_per_run). The plan referenced
    # blast-radius fields with names that don't exist on the class
    # (max_disable_per_batch etc.); the real fields are
    # blast_radius_max_source_disables_per_batch etc., and Phase 2's
    # safety eval doesn't consult them yet.
    safety = SafetyConfig(
        confidence_threshold=threshold,
        max_changes_per_run=10,
    )

    run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter, llm=fake, audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand], audit_data_override=audit_data,
        safety_config=safety,
    )

    body = (logs_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    decisions = [json.loads(l) for l in body if json.loads(l).get("type") == "GOVERNANCE_DECISION"]
    for d in decisions:
        if d["confidence"] < threshold:
            assert d["applied"] is False, (
                f"INVARIANT VIOLATED: confidence={d['confidence']} < threshold={threshold} "
                f"but applied=True (mode={mode})"
            )
        if mode != "real":
            assert d["applied"] is False, (
                "INVARIANT VIOLATED: shadow mode produced applied=True"
            )
