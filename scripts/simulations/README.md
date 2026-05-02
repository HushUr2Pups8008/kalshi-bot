# scripts/simulations/

Behavioural-simulation harnesses that exercise specific pipeline stages
end-to-end against either production state or curated synthetic fixtures.

These are **not** unit tests. Unit tests live in [`tests/`](../../tests/)
and verify isolated function correctness; simulations exercise integrated
production code paths and report on observed behaviour against documented
expectations. The two layers are complementary:

* a **regression test** says "function `_normalise_pem` parses this PEM
  correctly";
* a **simulation** says "the readiness gate, fed the five canonical
  LLM-positive events, accepts these specific markets at these specific
  scaled-confidence values".

When pipeline math is recalibrated (e.g. PROFIT-EDGE-002 lowered G4 from
0.40 → 0.20), the simulations are how the operator confirms the
recalibration produced the intended end-to-end behaviour without
deploying to production first. They also serve as durable regression
anchors: a future change to the blender that quietly drops
`scaled_confidence` for these markets will surface here.

## Read-only contract

Every simulation in this directory **must**:

1. Run without mutating any production state (`paper_trades.db`,
   `evidence_store.db`, the trade-log archive). Reads are fine.
2. Be deterministic for a given code revision and event-set fixture.
3. Print a structured summary suitable for archiving to a run log.
4. Be safe to run while the bot is active.

Smoke tests in [`tests/test_simulations_smoke.py`](../../tests/test_simulations_smoke.py)
verify the harnesses themselves stay green under code changes.

## Available simulations

| Script | Pipeline stage covered | Origin |
|---|---|---|
| [`threshold_calibration.py`](threshold_calibration.py) | G4 + G1 readiness-gate threshold calibration vs. existing categorical priors and production `BLEND_DECISION` distribution | PROFIT-EDGE-002 / EDGE-003 (v0.29.57 / v0.29.58) |
| [`readiness_gate_events.py`](readiness_gate_events.py) | Readiness gate (G1 / G3 / G4) end-to-end against the 5 canonical LLM-positive events from the 9-day no-edge investigation | PROFIT-EDGE-001 / EDGE-002 / EDGE-003 |
| [`executor_validate.py`](executor_validate.py) | Executor `_validate()` (E1–E12) against the same 5 canonical events, in independent + sequential passes | PROFIT-EDGE-002 (post-readiness-gate audit) |
| [`match_score_audit.py`](match_score_audit.py) | Match-score gate (`PAPER_MIN_MATCH_SCORE` — first kill point) against the production headlines + market titles for the 5 canonical events; threshold sweep + cross-contamination guard | PROFIT-EDGE-004 (Task A of pipeline simulation buildout) |
| [`blend_task_integration.py`](blend_task_integration.py) | Full `BlendTask.process_fast_lane_result` integration with seeded dossier + structural prior + recent evidence for KXTRUMPIRAN; surfaces accumulation-lane disagreement and any post-blend gate that wouldn't fire on the no-dossier readiness simulation | PROFIT-EDGE-004 (Task B of pipeline simulation buildout) |
| [`paper_trade_roundtrip.py`](paper_trade_roundtrip.py) | Paper-trade INSERT path against a real `PaperTrader` over a temp SQLite DB; per-event row write, bankroll debit, and source-credibility persistence | PROFIT-EDGE-004 (Task C of pipeline simulation buildout) |
| [`trading_queue_handoff.py`](trading_queue_handoff.py) | Replicates `main._trading_queue_consumer_task` wiring against an in-memory queue + recording executor stub; FIFO drain + back-pressure (no-drop) contracts | PROFIT-EDGE-004 (Task D of pipeline simulation buildout) |
| [`governance_fast_cycle.py`](governance_fast_cycle.py) | Drives `governance.agent.run_cycle` for one fast cadence with `FakeLLM` against a temp filesystem; pins shadow-mode invariant, audit JSONL append-only, and kill-switch (`GOVERNANCE_READONLY`) demotion of real → shadow | PROFIT-EDGE-004 (Task E of pipeline simulation buildout) |
| [`resolution_calibration.py`](resolution_calibration.py) | YES-wins / NO-wins resolution loop against a temp DB; pins `paper_trades` row mutation, bankroll credit math, source-credibility update, and per-lane `record_calibration_check` callback (PROFIT-CAL-001 wiring) | PROFIT-EDGE-004 (Task F of pipeline simulation buildout) |
| [`dossier_creation.py`](dossier_creation.py) | Streams synthetic `Evidence` records into `AccumulationTask` against a temp `EvidenceStore`; documents the empirical trigger (eager — dossier created at N=1) and pins the version + evidence-count contract step-by-step | PROFIT-EDGE-004 (Task G of pipeline simulation buildout) |

## Canonical event fixtures

[`_common.py:LLM_POSITIVE_EVENTS_2026_04_26`](_common.py) defines the 5
events captured from the PROFIT-EDGE-001 systematic-debugging
investigation: across 9 days of v0.29.54 paper-mode operation, these
were the only signals where `llm_useful=True` with non-trivial
probability movement. They serve as the regression anchor for the
EDGE-001/002/003 fix stack and any future change that touches the
readiness gate, regime classifier, or executor.

When future investigations surface new diagnostic events (similar
"these specific markets, in this specific window, surfaced this
specific pathology"), append them as new constants in `_common.py` and
add a corresponding simulation harness — keep the fixture immutable
once committed.

## Running

```bash
# Default text report — readable
.venv/bin/python scripts/simulations/threshold_calibration.py
.venv/bin/python scripts/simulations/readiness_gate_events.py
.venv/bin/python scripts/simulations/executor_validate.py

# Threshold audit can scope the production-data window:
.venv/bin/python scripts/simulations/threshold_calibration.py --window 30
.venv/bin/python scripts/simulations/threshold_calibration.py --no-production

# Readiness simulation can emit JSONL for archival pipelines:
.venv/bin/python scripts/simulations/readiness_gate_events.py --json

# Executor simulation can run a single pass:
.venv/bin/python scripts/simulations/executor_validate.py --pass independent

# Match-score audit:
.venv/bin/python scripts/simulations/match_score_audit.py
.venv/bin/python scripts/simulations/match_score_audit.py --json

# Full BlendTask integration (with seeded dossier for KXTRUMPIRAN):
.venv/bin/python scripts/simulations/blend_task_integration.py
.venv/bin/python scripts/simulations/blend_task_integration.py --json

# Paper-trade INSERT path (real PaperTrader against a temp SQLite DB):
.venv/bin/python scripts/simulations/paper_trade_roundtrip.py
.venv/bin/python scripts/simulations/paper_trade_roundtrip.py --json

# Trading-queue → executor handoff (FIFO + back-pressure):
.venv/bin/python scripts/simulations/trading_queue_handoff.py
.venv/bin/python scripts/simulations/trading_queue_handoff.py --json

# Governance Phase 2 fast cycle (FakeLLM, temp filesystem):
.venv/bin/python scripts/simulations/governance_fast_cycle.py
.venv/bin/python scripts/simulations/governance_fast_cycle.py --json

# Resolution + calibration loop (YES-wins + NO-wins, temp DB):
.venv/bin/python scripts/simulations/resolution_calibration.py
.venv/bin/python scripts/simulations/resolution_calibration.py --json

# Evidence → dossier creation (eager, no threshold gate):
.venv/bin/python scripts/simulations/dossier_creation.py
.venv/bin/python scripts/simulations/dossier_creation.py --json
```

## When to run

* **Before opening a PR that touches** `analysis/regime_classifier.py`,
  `analysis/decision_blender.py`, `tasks/blend_task.py`,
  `tasks/trade_readiness_gate.py`, `trading/executor.py`, or
  `config.py:MARKET_SERIES_BLOCKLIST_PREFIXES`.
* **After deploying a calibration change** to confirm the post-deploy
  behaviour matches the intent captured in the PR description.
* **As part of incident response** when paper-trade volume drops to
  zero (rerun the threshold simulation; if it predicts trades but
  production produces none, the kill point has moved upstream of the
  readiness gate).

The smoke tests in [`tests/test_simulations_smoke.py`](../../tests/test_simulations_smoke.py)
also run as part of `scripts/run_tests.sh`, so calibration-shifting
changes that break the simulation harness (rather than the underlying
math) surface immediately at PR time.

## Adding new simulations

The remaining pipeline-stage simulations identified during the
2026-04-26 investigation are scoped in
[`docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md`](../../docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md).
Reach for that plan before adding ad-hoc simulations.

The two patterns each new simulation should follow:

1. **A library function** (`run() -> list[Report]`) that the smoke test
   in `tests/test_simulations_smoke.py` can import and call directly,
   asserting basic invariants.
2. **A `main()` CLI entry point** with `argparse` so an operator can
   run it interactively. Default output should be human-readable; offer
   `--json` for machine consumption.
