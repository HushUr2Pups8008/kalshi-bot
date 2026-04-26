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

import math

import pytest

from scripts.simulations import _common
from scripts.simulations import (
    executor_validate,
    readiness_gate_events,
    threshold_calibration,
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
    import json
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
