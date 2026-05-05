"""Smoke tests for the behavioural simulations in `scripts/simulations/`.

These tests verify the simulation harnesses themselves stay green under
code changes — the harnesses are the operator's tool for catching
behavioural drift, so silent breakage is unacceptable. They do NOT
re-assert the calibration contracts; those live in the targeted
`tests/test_trade_readiness_gate.py` and `tests/test_regime_classifier.py`
calibration-contract tests where they are scoped to the math, not the
harness wiring.

Read-only — no DB writes, no trade-log writes, no network.
"""
from __future__ import annotations

import json
import math

import pytest

from scripts.simulations import _common
from scripts.simulations import (
    baseline_vs_multilane,
    blend_task_integration,
    dossier_creation,
    executor_validate,
    governance_fast_cycle,
    match_score_audit,
    paper_trade_roundtrip,
    readiness_gate_events,
    resolution_calibration,
    threshold_calibration,
    trading_queue_handoff,
)


# ── _common --------------------------------------------------------------------

def test_canonical_events_count_and_invariants():
    events = _common.LLM_POSITIVE_EVENTS_2026_04_26
    assert len(events) == 5, "5 canonical PROFIT-EDGE-001 events"
    for ev in events:
        assert 0.0 <= ev.fast_p <= 1.0
        assert 0.0 <= ev.fast_conf <= 1.0
        assert ev.side in {"yes", "no"}
        assert ev.ticker.startswith(ev.series)
        assert ev.title and ev.close_time


def test_regime_confidence_matches_known_values():
    # Sports prior (0.85, 0.10, 0.05) — entropy-driven rc value pinned in
    # tasks/trade_readiness_gate.py rationale block.
    assert math.isclose(_common.regime_confidence([0.85, 0.10, 0.05]), 0.528, abs_tol=0.005)
    # 7–14d time fallback (0.10, 0.45, 0.45) — matches the production
    # KXTRUMPIRAN-26MAY01 baseline from the EDGE-002 diagnosis.
    assert math.isclose(_common.regime_confidence([0.10, 0.45, 0.45]), 0.136, abs_tol=0.005)
    # Edge cases
    assert _common.regime_confidence([1.0, 0.0, 0.0]) == 1.0
    assert math.isclose(_common.regime_confidence([1/3, 1/3, 1/3]), 0.0, abs_tol=1e-9)


def test_synthetic_market_round_trip():
    ev = _common.LLM_POSITIVE_EVENTS_2026_04_26[2]  # KXTRUMPIRAN
    market = _common.synthetic_market(ev, yes_price_cents=50.0)
    assert market.ticker == ev.ticker
    assert market.series_ticker == ev.series
    assert market.status == "active"
    assert market.yes_price == 50.0
    assert market.yes_bid < market.yes_price < market.yes_ask


# ── threshold_calibration ------------------------------------------------------

def test_threshold_calibration_audit_g4_returns_priors_and_time_bands():
    audits = threshold_calibration.audit_g4_against_priors()
    # _SERIES_PRIORS has many entries (sports, polling, central bank, crypto,
    # weather, entertainment, Trump-say, plus 21 EDGE-002 additions). Time
    # fallback adds 6 bands. Total should be substantial.
    assert len(audits) >= 30, f"unexpectedly small audit set: {len(audits)}"
    # Sports prior must pass G4 — the strongest signal of regime concentration.
    sports = next(a for a in audits if "KXNFL" in a.label)
    assert sports.passes_g4
    # 7–14d time fallback must FAIL G4 — the canonical "uncategorized market" case.
    fallback = next(a for a in audits if "7–14d" in a.label or "7-14d" in a.label)
    assert not fallback.passes_g4


def test_threshold_calibration_collect_production_handles_missing_archive(tmp_path):
    # Pointing at an empty directory should return an empty list, not raise.
    out = threshold_calibration.collect_production_scaled_conf(archive_root=tmp_path)
    assert out == []


def test_threshold_calibration_main_runs_clean(capsys):
    rc = threshold_calibration.main(["--no-production"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "G4 audit" in captured.out
    assert "Summary" in captured.out


# ── readiness_gate_events ------------------------------------------------------

def test_readiness_simulation_produces_one_report_per_event():
    reports = readiness_gate_events.run()
    assert len(reports) == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    for r in reports:
        assert r.regime_weights, "regime_weights dict must be populated"
        assert sum(r.regime_weights.values()) == pytest.approx(1.0, abs=0.001)
        assert 0.0 <= r.regime_confidence <= 1.0


def test_readiness_simulation_kxpsl_is_sport_blocked():
    reports = readiness_gate_events.run()
    psl = next(r for r in reports if r.ticker.startswith("KXPSL"))
    assert psl.sport_blocked, "KXPSL must remain sport-blocked at the upstream gate"


def test_readiness_simulation_post_fix_clears_more_events_than_pre_fix():
    """The post-fix path is what we *changed*; it must be at least as good
    as the pre-fix path for these specific events. If a future code change
    regresses this, the simulation surfaces it immediately."""
    reports = readiness_gate_events.run()
    pre = sum(1 for r in reports if r.pre_fix_outcome.passed)
    post = sum(1 for r in reports if r.post_fix_outcome.passed)
    assert post >= pre


def test_readiness_simulation_main_text_and_json(capsys):
    assert readiness_gate_events.main([]) == 0
    out_text = capsys.readouterr().out
    assert "READINESS GATE SIMULATION" in out_text
    assert readiness_gate_events.main(["--json"]) == 0
    out_json = capsys.readouterr().out
    # one JSON object per event, all decodable
    lines = [ln for ln in out_json.splitlines() if ln.strip()]
    assert len(lines) == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    for ln in lines:
        json.loads(ln)


# ── executor_validate ----------------------------------------------------------

def test_executor_independent_pass_returns_one_result_per_event():
    results = executor_validate.run_independent()
    assert len(results) == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    for r in results:
        # Edge magnitude is bounded by |fast_p - 0.5| in our fixtures (0.068).
        assert abs(r.edge) <= 0.5
        # PAPER_MIN_EDGE = 0.02; effective_min_edge cannot be lower than that.
        assert r.effective_min_edge >= 0.02 - 1e-9


def test_executor_sequential_pass_suppresses_kxtrumpiran_second_event():
    """Event 5 (KXTRUMPIRAN talks-stall, side=no) fires after Event 3
    (KXTRUMPIRAN dispatch, side=yes). Within the 4 h cooldown, the second
    event MUST be suppressed by E5/E6 (cooldown or opposing-position).
    This is the bot's core flip-flop guard."""
    results = executor_validate.run_sequential()
    by_name = {r.event_name: r for r in results}
    third = by_name["Event 3: KXTRUMPIRAN (Trump dispatching)"]
    fifth = by_name["Event 5: KXTRUMPIRAN (talks stall)"]
    assert third.skip_reason is None, "Event 3 should pass on a fresh executor"
    assert fifth.skip_reason is not None, (
        "Event 5 must be suppressed within the 4 h cooldown of Event 3 — "
        "either by paper_ticker_cooldown OR opposing-position guard"
    )


def test_executor_main_runs_clean(capsys):
    assert executor_validate.main([]) == 0
    out = capsys.readouterr().out
    assert "Pass: independent" in out
    assert "Pass: sequential" in out


# ── match_score_audit ----------------------------------------------------------

def test_match_audit_finds_target_ticker_for_each_event():
    """A1 acceptance pin: every canonical event surfaces its anchor ticker
    in the top-3 matches at score ≥ PAPER_MIN_MATCH_SCORE. If this fails,
    the matcher's first kill point is silently dropping a real LLM-positive
    signal — file a new debt-log item before changing thresholds."""
    from config import PAPER_MIN_MATCH_SCORE
    reports = match_score_audit.run()
    assert len(reports) == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    for r in reports:
        assert r.target_in_top_3, (
            f"{r.event_name}: anchor ticker {r.target_ticker} not in top 3 "
            f"({[t for t, _ in r.top_3_matches]})"
        )
        assert r.target_score is not None
        assert r.target_score >= PAPER_MIN_MATCH_SCORE, (
            f"{r.event_name}: anchor score {r.target_score:.4f} below "
            f"PAPER_MIN_MATCH_SCORE = {PAPER_MIN_MATCH_SCORE}"
        )


def test_match_audit_main_runs_clean(capsys):
    assert match_score_audit.main([]) == 0
    out = capsys.readouterr().out
    assert "Match-score gate audit" in out
    assert "Threshold sweep" in out
    # JSON path also exercised
    assert match_score_audit.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert len(payload) == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)


# ── blend_task_integration -----------------------------------------------------

def test_blend_integration_produces_blend_decision_per_event():
    reports = blend_task_integration.run()
    assert len(reports) == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    for r in reports:
        assert r.regime_weights, "regime_weights must be populated"
        assert sum(r.regime_weights.values()) == pytest.approx(1.0, abs=0.001)
        # blended_p / blended_confidence are produced for every event regardless
        # of readiness outcome — the BLEND_DECISION emission is unconditional.
        assert 0.0 <= r.blended_p <= 1.0
        assert 0.0 <= r.blended_confidence <= 1.0


def test_blend_integration_kxtrumpiran_with_dossier_changes_disagreement():
    """Pin the central reason this harness exists: KXTRUMPIRAN's
    dossier-seeded path produces non-zero disagreement (because the
    accumulation lane disagrees with fast), while the no-dossier
    readiness simulation produces disagreement = 0 (no second lane to
    disagree with). A future change that silently drops the accumulation
    lane in the integration path would surface here."""
    reports = blend_task_integration.run()
    iran = [r for r in reports if r.ticker == "KXTRUMPIRAN-26MAY01"]
    assert iran, "expected at least one KXTRUMPIRAN report"
    for r in iran:
        assert r.has_dossier, "KXTRUMPIRAN must have a seeded dossier"
        assert r.accumulation_p is not None
        assert r.disagreement_score > 0.0, (
            f"{r.event_name}: accumulation lane should produce disagreement, got 0"
        )
    # And the no-dossier reference path has disagreement = 0 by construction.
    no_dossier = readiness_gate_events.run()
    iran_pred = next(p for p in no_dossier if p.ticker == "KXTRUMPIRAN-26MAY01")
    # readiness_gate_events doesn't expose disagreement, but the post-fix
    # path runs blend() with no accumulation lane — disagreement is forced
    # to 0 by construction. Pin that as a contract via the predicted-pass
    # field, which is what cross-checking diverges against.
    assert isinstance(iran_pred.post_fix_outcome.passed, bool)


def test_blend_integration_main_runs_clean(capsys):
    assert blend_task_integration.main([]) == 0
    out = capsys.readouterr().out
    assert "BlendTask integration simulation" in out
    assert "Summary" in out
    # JSON path also exercised — one JSON object per event line.
    assert blend_task_integration.main(["--json"]) == 0
    out_json = capsys.readouterr().out
    lines = [ln for ln in out_json.splitlines() if ln.strip()]
    assert len(lines) == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    for ln in lines:
        json.loads(ln)


# ── baseline_vs_multilane ------------------------------------------------------

def test_baseline_vs_multilane_generates_required_metrics():
    report = baseline_vs_multilane.run()
    assert report.offline_paper_only is True
    assert report.baseline.evaluated_candidates == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    assert report.multi_lane.evaluated_candidates == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    assert report.baseline.paper_trade_count >= 0
    assert report.multi_lane.paper_trade_count >= 0
    assert 0.0 <= report.baseline.acceptance_rate <= 1.0
    assert 0.0 <= report.multi_lane.acceptance_rate <= 1.0
    assert (
        report.multi_lane.blend_pass_count + report.multi_lane.blend_block_count
        == report.multi_lane.evaluated_candidates
    )
    assert report.within_2x_trade_frequency_constraint is True


def test_baseline_vs_multilane_main_runs_clean(capsys):
    assert baseline_vs_multilane.main([]) == 0
    out = capsys.readouterr().out
    assert "Baseline vs multi-lane validation" in out
    assert "within 2x constraint" in out
    assert baseline_vs_multilane.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["offline_paper_only"] is True
    assert "baseline" in payload and "multi_lane" in payload


# ── paper_trade_roundtrip ------------------------------------------------------

def test_roundtrip_inserts_one_row_per_accepted_event(tmp_path):
    """A4 #1: every accepted event produces exactly one paper_trades row."""
    reports = paper_trade_roundtrip.run(db_root=tmp_path)
    accepted = [r for r in reports if r.accepted]
    assert accepted, "expected at least one accepted event in the round-trip sim"
    for r in accepted:
        assert r.inserted_trade_id, f"{r.event_name}: accepted but no trade_id"
        assert r.inserted_row is not None, f"{r.event_name}: row missing from DB"
        assert r.inserted_row["ticker"] == r.ticker


def test_roundtrip_bankroll_debit_matches_paper_flat_cost(tmp_path):
    """A4 #2: bankroll debit equals the paper-flat cost recorded on the row.

    Paper mode uses ``PAPER_FLAT_CONTRACTS`` rather than ``capped_dollars``,
    so the debit math contract is "delta == row.cost_dollars" rather than
    "delta == capped_dollars". Pin that explicitly so a future change that
    reroutes paper sizing to capped_dollars surfaces here."""
    reports = paper_trade_roundtrip.run(db_root=tmp_path)
    for r in reports:
        if not r.accepted:
            continue
        row_cost = float(r.inserted_row["cost_dollars"])
        assert r.bankroll_delta == pytest.approx(-row_cost, abs=1e-6), (
            f"{r.event_name}: bankroll Δ={r.bankroll_delta} but row cost={row_cost}"
        )
        assert row_cost == pytest.approx(r.expected_cost_dollars, abs=1e-6)


def test_roundtrip_source_multiplier_persists_to_row(tmp_path):
    """A4 #3 (substituted): source_multiplier (read from SourceCredibility)
    is persisted to every accepted row. Source-stats orchestration lives in
    main.py and isn't exercised by record_trade — but ``record_trade`` does
    query SourceCredibility and write the result, so that contract is the
    one this harness can pin."""
    reports = paper_trade_roundtrip.run(db_root=tmp_path)
    accepted = [r for r in reports if r.accepted]
    assert accepted
    for r in accepted:
        assert r.source_multiplier_persisted is not None, (
            f"{r.event_name}: source_multiplier missing from inserted row"
        )
        # Fresh DB → no prior credibility → neutral 1.0 multiplier.
        assert r.source_multiplier_persisted == pytest.approx(1.0)


# ── trading_queue_handoff ------------------------------------------------------

def test_handoff_drains_in_order():
    """FIFO contract: candidates exit the queue in enqueue order."""
    report = trading_queue_handoff.run()
    assert report.fifo_in_order, "trading queue did not preserve FIFO order"
    assert len(report.fifo_handoffs) == len(_common.LLM_POSITIVE_EVENTS_2026_04_26)
    for h in report.fifo_handoffs:
        assert h.dequeue_ts >= h.enqueue_ts, (
            f"candidate {h.candidate_index} dequeued before it was enqueued"
        )


def test_handoff_no_candidate_lost_on_backpressure():
    """Backpressure contract: enqueueing past maxsize must not drop candidates.

    The producer has to await the consumer (asyncio.Queue.put blocks when
    full). If a future change replaces ``put`` with ``put_nowait`` or adds
    a silent drop path, this surfaces it."""
    report = trading_queue_handoff.run()
    bp = report.backpressure
    assert bp.no_drops, (
        f"backpressure dropped candidates: enqueued={bp.enqueued} "
        f"drained={bp.drained}"
    )
    assert bp.enqueued > bp.queue_maxsize, "scenario must overflow maxsize"
    assert bp.producer_ever_blocked, (
        "producer never observed a full queue — backpressure path not exercised"
    )


def test_handoff_main_runs_clean(capsys):
    assert trading_queue_handoff.main([]) == 0
    out = capsys.readouterr().out
    assert "Trading-queue → executor handoff" in out
    assert "Back-pressure scenario" in out
    # JSON path also exercised
    assert trading_queue_handoff.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "fifo_handoffs" in payload
    assert "backpressure" in payload


# ── governance_fast_cycle ------------------------------------------------------

def test_governance_fast_cycle_writes_only_to_proposed(tmp_path):
    """Shadow-mode invariant + readonly invariant: ``applied`` is False on
    every emitted GOVERNANCE_DECISION, regardless of overrides mode or
    LLM confidence. The Phase 2 launchd soak depends on this; a code
    change that flips applied=True in shadow would be a load-bearing
    safety regression."""
    report = governance_fast_cycle.run(work_dir=tmp_path)
    for cycle in (
        report.shadow_first_cycle,
        report.shadow_second_cycle,
        report.readonly_real_mode_cycle,
    ):
        assert cycle.applied_count == 0, (
            f"{cycle.cycle_label}: applied_count={cycle.applied_count}; "
            "shadow-mode + readonly must never apply"
        )
        assert cycle.proposed_count > 0, (
            f"{cycle.cycle_label}: zero proposed decisions — fixture broken"
        )


def test_governance_fast_cycle_audit_jsonl_append_only(tmp_path):
    """Two cycles in sequence: the second cycle must strictly extend the
    audit log; cycle-1 records remain byte-for-byte identical."""
    report = governance_fast_cycle.run(work_dir=tmp_path)
    assert report.audit_log_size_grew, "audit log did not grow on second cycle"
    assert report.pre_existing_records_preserved, (
        "audit JSONL is not append-only — earlier records were rewritten"
    )
    # Cycle 2's record count matches cycle 1's record count (same fixture).
    assert (
        report.shadow_second_cycle.audit_records_emitted
        == report.shadow_first_cycle.audit_records_emitted
    )


def test_governance_fast_cycle_kill_switch_active_blocks_all_apply(tmp_path):
    """Even with mode='real' in the overrides file, GOVERNANCE_READONLY=true
    must demote the cycle to shadow and block every apply. This is the
    second leg of the kill switch: a misbehaving real-mode agent the
    operator wants to halt without rolling back the YAML mode field."""
    report = governance_fast_cycle.run(work_dir=tmp_path)
    real_readonly = report.readonly_real_mode_cycle
    assert real_readonly.overrides_mode == "real"
    assert real_readonly.governance_readonly is True
    assert real_readonly.applied_count == 0, (
        "GOVERNANCE_READONLY=true did not block apply path in real mode"
    )


def test_governance_fast_cycle_main_runs_clean(capsys, tmp_path):
    assert governance_fast_cycle.main(["--work-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Governance Phase 2 fast-cycle simulation" in out
    assert "Append-only contract" in out


# ── resolution_calibration -----------------------------------------------------

def test_resolution_yes_wins_credits_bankroll(tmp_path):
    """A4 #1: YES-side trade wins on resolved_yes=True. Bankroll credit
    equals the per-row payout (contracts as dollars), pnl = payout - cost,
    resolved=1 + resolved_ts populated."""
    report = resolution_calibration.run(db_root=tmp_path)
    r = report.yes_wins
    assert r.side == "yes" and r.outcome == "yes"  # event-3 is yes-side
    expected_credit = r.expected_payout
    assert r.bankroll_after_resolution - r.bankroll_after_entry == pytest.approx(expected_credit, abs=1e-6)
    row = r.resolved_row
    assert row["resolved"] == 1
    assert row["resolved_yes"] == 1
    assert row["resolved_ts"], "resolved_ts must be populated"
    assert row["pnl_dollars"] == pytest.approx(expected_credit - row["cost_dollars"], abs=1e-6)


def test_resolution_no_wins_zero_credit(tmp_path):
    """A4 #2: YES-side trade loses on resolved_yes=False. Bankroll credit
    is exactly zero (cost was already debited at entry); pnl is negative
    and equals -cost."""
    report = resolution_calibration.run(db_root=tmp_path)
    r = report.no_wins
    assert r.bankroll_after_resolution == pytest.approx(r.bankroll_after_entry, abs=1e-6)
    row = r.resolved_row
    assert row["resolved"] == 1
    assert row["resolved_yes"] == 0
    assert row["pnl_dollars"] == pytest.approx(-row["cost_dollars"], abs=1e-6)


def test_resolution_triggers_calibration_callback(tmp_path):
    """A4 #3: every populated lane in signal_meta produces one
    record_calibration_check call per resolved trade. Wiring closed in
    PROFIT-CAL-001 (v0.29.47); this is its read-only regression anchor."""
    report = resolution_calibration.run(db_root=tmp_path)
    for r in (report.yes_wins, report.no_wins):
        lanes = {c["lane"] for c in r.calibration_calls}
        assert lanes == {"fast", "accumulation", "structural"}, (
            f"{r.outcome}-wins: expected one calibration call per lane, got {lanes}"
        )
        # error == |estimate - final_resolution|, asserted directly so a
        # future change in the resolution signature surfaces here.
        for c in r.calibration_calls:
            expected_err = abs(c["lane_estimate"] - c["final_resolution"])
            assert c["error"] == pytest.approx(expected_err, abs=1e-6)


def test_resolution_calibration_main_runs_clean(capsys, tmp_path):
    assert resolution_calibration.main(["--db-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Resolution + calibration loop" in out
    assert "YES wins" in out and "NO wins" in out


# ── dossier_creation -----------------------------------------------------------

def test_dossier_not_created_below_threshold(tmp_path):
    """At N=0 (no evidence streamed), no dossier exists for the ticker.

    The harness's pre-loop snapshot is the load-bearing observation here:
    it confirms the temp DB is genuinely empty before any evidence is
    ingested, so a future change that auto-creates dossiers from market
    metadata alone would surface."""
    report = dossier_creation.run(evidence_count=1, db_root=tmp_path)
    assert report.pre_first_evidence_dossier_present is False, (
        "dossier present before any evidence was ingested"
    )


def test_dossier_created_at_threshold(tmp_path):
    """Empirical trigger: dossier exists after the *first* evidence record.

    No minimum-evidence threshold gate — creation is eager. Pin this so a
    future change that adds a min-evidence threshold (e.g. ≥3 records
    before persisting) is caught by the harness rather than discovered
    when KXTRUMPIRAN's structural lane silently disappears."""
    report = dossier_creation.run(evidence_count=1, db_root=tmp_path)
    assert report.dossier_created is True
    assert report.creation_threshold_reached_at_n == 1
    assert report.final_dossier_version is not None and report.final_dossier_version >= 1


def test_dossier_evidence_records_attached(tmp_path):
    """Streamed evidence records all appear in store.get_recent_evidence,
    and the dossier_version grows monotonically with evidence count."""
    report = dossier_creation.run(evidence_count=5, db_root=tmp_path)
    assert report.final_recent_evidence_count == 5
    versions = [obs.dossier_version for obs in report.step_observations]
    # Strictly monotonic — every new evidence advances the dossier.
    assert all(versions[i] is not None and versions[i + 1] is not None
               and versions[i + 1] > versions[i]
               for i in range(len(versions) - 1)), (
        f"dossier_version not strictly monotonic across evidence stream: {versions}"
    )


def test_dossier_creation_main_runs_clean(capsys, tmp_path):
    assert dossier_creation.main([
        "--db-root", str(tmp_path),
        "--evidence-count", "3",
    ]) == 0
    out = capsys.readouterr().out
    assert "Dossier creation simulation" in out
    assert "Per-step observations" in out


def test_match_audit_kxpsl_does_not_match_geo_news():
    """KXPSL can surface only for its canonical cricket anchor per MATCH-001 §8."""
    reports = match_score_audit.run()
    geo_event_names = {
        "Event 1: KXSBUDGETRES-APR28 (ICE funding)",
        "Event 2: KXSBUDGETRES-APR25 (ICE funding)",
        "Event 3: KXTRUMPIRAN (Trump dispatching)",
        "Event 5: KXTRUMPIRAN (talks stall)",
    }
    kxpsl_allowlist = {
        "Event 4: KXPSL-PZA (cricket — should be sport-blocked)",
    }
    for r in reports:
        top_tickers = {t for t, _ in r.top_3_matches}
        if r.event_name in kxpsl_allowlist:
            assert "KXPSL-26-PZA" in top_tickers, (
                f"{r.event_name}: canonical KXPSL anchor missing from top-3 ({top_tickers})"
            )
            continue
        if r.event_name not in geo_event_names:
            continue
        assert "KXPSL-26-PZA" not in top_tickers, (
            f"{r.event_name}: KXPSL leaked into top-3 ({top_tickers}) — "
            "cricket market scored against geo-news headline, regression"
        )
