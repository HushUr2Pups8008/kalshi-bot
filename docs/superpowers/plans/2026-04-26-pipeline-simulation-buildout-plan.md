# Pipeline Simulation Buildout Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the remaining behavioural-simulation harnesses identified during the 2026-04-26 PROFIT-EDGE-001/002/003 systematic-debugging investigation. The G1/G4 calibration audits, the readiness-gate end-to-end against the 5 canonical events, and the executor `_validate()` walkthrough were captured in v0.29.58 (commit pending). This plan covers the remaining seven pipeline stages that the operator + agents agreed should have permanent simulation harnesses before the bot reaches go-live.

**Why this matters:**

* Simulations are how we catch silent zero-trade regressions *before* deploying. The G4/G1 mis-calibrations were both invisible to the existing 1370-test pytest suite — only an empirical "let's measure what production scaled_confidence looks like" investigation surfaced them.
* When the bot is live, the simulations form the regression bedrock for "did this PR change behaviour for the markets we trade?" — independent of, and complementary to, unit tests.
* The 5 canonical LLM-positive events from the EDGE-001 diagnosis are immutable regression anchors. New simulations should reuse them where applicable so a single set of "what we know happened in production" fixtures drives every behavioural check.

**Existing scaffold (do not rebuild):**

* [`scripts/simulations/__init__.py`](../../../scripts/simulations/__init__.py)
* [`scripts/simulations/_common.py`](../../../scripts/simulations/_common.py) — `LLM_POSITIVE_EVENTS_2026_04_26`, `regime_confidence`, `synthetic_market`.
* [`scripts/simulations/threshold_calibration.py`](../../../scripts/simulations/threshold_calibration.py)
* [`scripts/simulations/readiness_gate_events.py`](../../../scripts/simulations/readiness_gate_events.py)
* [`scripts/simulations/executor_validate.py`](../../../scripts/simulations/executor_validate.py)
* [`scripts/simulations/README.md`](../../../scripts/simulations/README.md) — operator docs.
* [`tests/test_simulations_smoke.py`](../../../tests/test_simulations_smoke.py) — 13 smoke tests; pytest-runnable.

**Pattern each new simulation MUST follow:**

1. Live in `scripts/simulations/<name>.py`.
2. Expose at least one library function (`run() -> list[Report]`) the smoke test can import and call.
3. Provide a `main(argv) -> int` CLI entry-point with `argparse`. Default output text-readable; offer `--json` for archival.
4. Read-only: NEVER mutate `paper_trades.db`, `evidence_store.db`, or the trade-log archive.
5. Deterministic for a given code revision and event-set fixture.
6. Add a row to [`scripts/simulations/README.md`](../../../scripts/simulations/README.md)'s "Available simulations" table.
7. Add ≥3 smoke tests to [`tests/test_simulations_smoke.py`](../../../tests/test_simulations_smoke.py): one for `run()` library invariants, one for the CLI happy path, one for any pinned behavioural contract (e.g. "KXPSL must remain sport-blocked").
8. The git commit message references the originating debt-log item (e.g. `PROFIT-MATCH-001`) and the simulation file by relative path.

---

## Prerequisites

The PROFIT-EDGE-001/002/003 stack must be merged to `main` (v0.29.58 or later). Verify:

```bash
test "$(cat VERSION)" \> "0.29.57"           # version bump landed
test -d scripts/simulations                  # scaffold exists
.venv/bin/python -c "from scripts.simulations._common import LLM_POSITIVE_EVENTS_2026_04_26 as e; assert len(e) == 5"
.venv/bin/python -m pytest tests/test_simulations_smoke.py -q
```

If any of those fail, stop and reconcile state with `main` before proceeding.

---

## File Structure

Files this plan creates (NEW):

| Path | Action | Responsibility |
|---|---|---|
| `scripts/simulations/match_score_audit.py` | NEW | Task A — match-score gate against curated headline-ticker pairs from canonical events. |
| `scripts/simulations/blend_task_integration.py` | NEW | Task B — `BlendTask.process_fast_lane_result` against KXTRUMPIRAN with its real dossier+structural prior; fixture pinned. |
| `scripts/simulations/paper_trade_roundtrip.py` | NEW | Task C — synthetic `SignalAnalysis` → `executor.execute()` → verify `paper_trades` row + bankroll math (in a temp DB). |
| `scripts/simulations/trading_queue_handoff.py` | NEW | Task D — produce a `TradeCandidate`, drain `_trading_queue_consumer_task`, verify executor invocation. |
| `scripts/simulations/governance_fast_cycle.py` | NEW | Task E — `python -m governance --cadence fast` once with `FakeLLM`; verify shadow-mode (no `applied` writes), audit JSONL produced. |
| `scripts/simulations/resolution_calibration.py` | NEW | Task F — synthetic resolved paper trade → calibration update path; verify PROFIT-CAL-001 wiring fires. |
| `scripts/simulations/dossier_creation.py` | NEW | Task G — synthetic evidence stream into the evidence queue; verify dossier-creation thresholds. |
| `tests/test_simulations_smoke.py` | MODIFY | Add ≥3 smoke tests per new simulation (~21 new cases). |
| `scripts/simulations/README.md` | MODIFY | Add table rows for each new simulation. |
| `scripts/README.md` | MODIFY | Reflect the same additions in the top-level operator index. |
| `docs/profit_path_debt_log.md` | MODIFY | Append a row to the simulation-harness reference and (if any task surfaces a bug) file the bug as a new HIGH item. |

Files this plan does NOT create:

* No new dependencies. All simulations use libraries already in `requirements.txt` / `requirements-dev.txt`.
* No CI hooks beyond `scripts/run_tests.sh`. The smoke tests exercise the harnesses; full simulation runs are an operator-invoked activity.

---

## Task A: `scripts/simulations/match_score_audit.py` — match-score gate audit

**Files:**
* Create: `scripts/simulations/match_score_audit.py`
* Test: extend `tests/test_simulations_smoke.py`

**Why this is high priority:** [`analysis/market_matcher.py`](../../../analysis/market_matcher.py) computes Jaccard-shaped similarity between news headlines and market titles; the gate at `score ≥ PAPER_MIN_MATCH_SCORE = 0.06` is the **first kill point** in the pipeline (before LLM call, before sports blocklist). The 2026-04-26 daily-review report flagged 228 of 269 matched candidates (85%) as low-quality. We have not directly audited what scores our target tickers (KXTRUMPIRAN, KXSBUDGETRES, KXMOCTRUMP25 …) actually surface for representative news headlines. If matches happen at score 0.04–0.05, they're killed before any of the EDGE-001/002/003 fixes apply.

### Scope

* Walk the 5 canonical LLM-positive events through `find_all_candidates` (or whatever the current matcher entry point is) using the actual headline text from the trade-log archive's `MATCH_DIAGNOSTIC` records.
* For each event, report: top-3 matched market candidates, their match scores, and whether the eventual matched ticker (per the diagnosis) cleared `PAPER_MIN_MATCH_SCORE`.
* Run the same audit against a sweep of `match_score` thresholds (0.03, 0.04, 0.05, 0.06, 0.08, 0.10) to surface where the cliff sits.
* Pin the contract: each canonical event MUST surface its anchor ticker in the top-3 matches at score ≥ 0.06.

### Implementation steps

- [ ] **A1** — Extract the headline strings from the trade-log archive for each of the 5 canonical events (look up by `MATCH_DIAGNOSTIC` records around the timestamps in `_common.LLM_POSITIVE_EVENTS_2026_04_26`). Add the headlines to `_common.py` as `LLM_POSITIVE_EVENT_HEADLINES_2026_04_26: dict[ticker, str]` (immutable fixture).

- [ ] **A2** — Write `match_score_audit.py:run()` returning `list[MatchAuditReport]` where each report contains: `event_name`, `headline`, `top_3_matches` (list of `(ticker, score)`), `target_ticker`, `target_in_top_3` (bool), `target_score` (float | None).

- [ ] **A3** — `main()` prints a per-event table and a threshold-sweep summary identical in structure to `threshold_calibration.py:_print_g1_report`.

- [ ] **A4** — Smoke tests (in `tests/test_simulations_smoke.py`):
  * `test_match_audit_finds_target_ticker_for_each_event` — `target_in_top_3` is `True` for all 5 events at default `PAPER_MIN_MATCH_SCORE`.
  * `test_match_audit_main_runs_clean` — CLI returns 0, output contains expected headers.
  * `test_match_audit_kxpsl_does_not_match_geo_news` — KXPSL cricket should NOT be a top-3 match for ICE-funding or Iran headlines (cross-contamination guard).

- [ ] **A5** — Document in `scripts/simulations/README.md` "Available simulations" table.

**Acceptance:**
* Each canonical event surfaces its anchor ticker in the top-3 matches.
* Smoke tests added (≥ 3) and pass.
* CLI runs clean from a fresh shell with `.venv/bin/python scripts/simulations/match_score_audit.py`.

**Risk surfacing:** if the audit shows anchor tickers below the 0.06 threshold, file a new debt-log item (`PROFIT-MATCH-001`?) with the empirical score distribution and recommend either threshold recalibration or matcher improvements. Do NOT change `PAPER_MIN_MATCH_SCORE` in this task — that's a separate calibration decision.

---

## Task B: `scripts/simulations/blend_task_integration.py` — full BlendTask integration

**Files:**
* Create: `scripts/simulations/blend_task_integration.py`
* Test: extend `tests/test_simulations_smoke.py`

**Why this is high priority:** the existing readiness simulation calls `analysis.decision_blender.blend()` directly with synthetic `LaneInput` objects. The real path runs through [`tasks.blend_task.BlendTask.process_fast_lane_result`](../../../tasks/blend_task.py), which:

1. Reads dossier + structural prior + recent evidence from `evidence_store` (DB).
2. Computes regime weights + regime confidence.
3. Runs `_blend()` (combines fast lane with accumulation lane derived from dossier history).
4. Runs the readiness gate.
5. Emits a `BLEND_DECISION` event to the trade log.
6. Enqueues a `TradeCandidate` to the trading queue.

When a market has a dossier with recent evidence, the **accumulation lane** fires with non-0.5 `acc_p`. Our existing simulations all used `accumulation=None`. If the accumulation lane disagrees with the fast lane, `disagreement_score` may exceed G3 = 0.20 and silently block the trade.

### Scope

* Use a temp `EvidenceStore` (in `tmp_path`) seeded with synthetic dossier + structural prior + evidence records that mirror the real production state for KXTRUMPIRAN-26MAY01 (which has 10 evidence records and a structural prior in production).
* Construct a `BlendTask` with the temp store + a fake trade-log writer.
* Feed each canonical event's `SignalAnalysis` through `process_fast_lane_result()`.
* Capture the resulting `BlendTaskResult` (with `BLEND_DECISION` payload) and assert: regime weights match the new categorical priors; readiness outcome matches `readiness_gate_events.py`'s prediction; if any event's actual outcome diverges from the synthetic-only prediction, the simulation flags the divergence.

### Implementation steps

- [ ] **B1** — Build a `_seed_kxtrumpiran_state(store, market_ticker)` helper in the simulation module that creates a dossier with 5+ evidence records and a structural prior. Schema reference: [`tasks/evidence_store.py`](../../../tasks/evidence_store.py); test fixtures in [`tests/test_blend_task.py`](../../../tests/test_blend_task.py) and [`tests/test_structural_task.py`](../../../tests/test_structural_task.py) show the pattern.

- [ ] **B2** — Implement `run() -> list[BlendIntegrationReport]` returning, for each canonical event: `event_name`, `ticker`, `regime_weights`, `regime_confidence`, `blended_p`, `blended_confidence`, `disagreement_score`, `readiness_passed`, `trade_blocked_reason`.

- [ ] **B3** — Cross-check each result against `readiness_gate_events.py`'s prediction. Expect divergence on KXTRUMPIRAN (which has the dossier-driven accumulation lane in production). Surface the divergence as part of the report.

- [ ] **B4** — Smoke tests:
  * `test_blend_integration_produces_blend_decision_per_event` — one `BLEND_DECISION` per event.
  * `test_blend_integration_kxtrumpiran_with_dossier_changes_disagreement` — the dossier-backed KXTRUMPIRAN case has different `disagreement_score` from the no-dossier case.
  * `test_blend_integration_main_runs_clean`.

- [ ] **B5** — README + smoke-test row updates.

**Acceptance:**
* `BlendTask.process_fast_lane_result` produces a `BlendTaskResult` for every canonical event.
* If accumulation-lane disagreement causes G3 to fail any event in production-shaped fixtures, the simulation surfaces the failure and a debt-log entry is filed.
* Smoke tests added (≥ 3) and pass.

**Risk surfacing:** if KXTRUMPIRAN's dossier-driven accumulation lane produces enough disagreement to fail G3 (0.20), file `PROFIT-G3-001` with the empirical disagreement distribution.

---

## Task C: `scripts/simulations/paper_trade_roundtrip.py` — paper-trade INSERT path

**Files:**
* Create: `scripts/simulations/paper_trade_roundtrip.py`
* Test: extend `tests/test_simulations_smoke.py`

**Why this is high priority:** `paper_trades` table has 0 rows in 8 days of v0.29.54 paper-mode operation. The INSERT path has never been executed in production. CLAUDE.md gotchas flag `resolve_market()` for transaction-atomicity issues, but the simpler `record_trade()` insert hasn't been audited recently. A bug here means silent zero-trade even after the readiness gate opens.

### Scope

* Use a temp `paper_trades.db` (`tmp_path` / `:memory:`) with the real schema from [`trading/paper_trader.py`](../../../trading/paper_trader.py).
* Construct a real `PaperTrader` against the temp DB. Construct a paper-mode `TradeExecutor`.
* Drive each canonical event's `SignalAnalysis` through `executor.execute()`.
* Verify: row inserted in `paper_trades` with correct values (ticker, side, contracts, cost, edge, estimated_prob, …); `bot_state.notional_bankroll` debited correctly; `source_stats` updated.

### Implementation steps

- [ ] **C1** — Build a `_make_real_paper_executor(db_path)` helper that constructs a real `PaperTrader` + temp DB rather than the mock used in `executor_validate.py`. Reuse fixture patterns from `tests/test_executor.py`.

- [ ] **C2** — Implement `run() -> list[RoundtripReport]` returning, for each canonical event that the executor accepts: `event_name`, `inserted_trade_id`, `inserted_row_dict`, `bankroll_before`, `bankroll_after`, `bankroll_delta`.

- [ ] **C3** — `main()` prints a per-event table showing every column of the inserted row and the bankroll delta.

- [ ] **C4** — Smoke tests:
  * `test_roundtrip_inserts_one_row_per_accepted_event` — count assertion against accepted events.
  * `test_roundtrip_bankroll_debit_matches_capped_dollars` — math contract.
  * `test_roundtrip_source_stats_updated` — `source_stats.signals` increments per event.

- [ ] **C5** — README + smoke-test row updates.

**Acceptance:**
* Every canonical event the executor accepts produces exactly one `paper_trades` row.
* Bankroll debit math matches `cost_dollars` for each row.
* Smoke tests added (≥ 3) and pass.

**Risk surfacing:** any column with `NULL` where the schema expects `NOT NULL`, any bankroll-math discrepancy, any `source_stats` mis-update — file as a new HIGH debt-log item (`PROFIT-PAPER-001`).

---

## Task D: `scripts/simulations/trading_queue_handoff.py` — queue → executor handoff

**Files:**
* Create: `scripts/simulations/trading_queue_handoff.py`
* Test: extend `tests/test_simulations_smoke.py`

**Why MEDIUM priority:** [`main.py:_trading_queue_consumer_task`](../../../main.py) drains candidates from an `asyncio.Queue` and calls `executor.execute()`. Backpressure / starvation behaviour is unverified. Probably fine in practice (low candidate rate) but it's the pipe between the readiness gate and the executor.

### Scope

* Construct an `asyncio.Queue` with a synthetic `TradeCandidate` per canonical event.
* Wire the consumer task with a mock executor that records call order + arguments.
* Drive the consumer for one drain cycle; assert each candidate was passed through to the executor in order, and the queue empties.
* Test backpressure: fill the queue beyond its `maxsize` and assert the producer (BlendTask) handles the back-pressure cleanly (no silent drop, no exception that takes down the loop).

### Implementation steps

- [ ] **D1** — Replicate the queue + consumer wiring from `main.py:_trading_queue_consumer_task` in the simulation. Reference: the real implementation reads `candidate = await self._trading_queue.get()` and calls `await self._executor.execute(candidate)`.

- [ ] **D2** — Implement `run() -> list[HandoffReport]`: per-candidate, capture `enqueue_ts`, `dequeue_ts`, `executor_call_args`.

- [ ] **D3** — Backpressure scenario: enqueue `maxsize + 1` candidates; assert the (maxsize+1)th enqueue blocks and the consumer drains correctly.

- [ ] **D4** — Smoke tests:
  * `test_handoff_drains_in_order` — FIFO contract.
  * `test_handoff_no_candidate_lost_on_backpressure` — no silent drop.
  * `test_handoff_main_runs_clean`.

- [ ] **D5** — README + smoke-test row updates.

**Acceptance:**
* Each enqueued candidate reaches the executor exactly once.
* Backpressure behaviour matches the contract (block, don't drop).
* Smoke tests added (≥ 3) and pass.

---

## Task E: `scripts/simulations/governance_fast_cycle.py` — governance shadow-mode cycle

**Files:**
* Create: `scripts/simulations/governance_fast_cycle.py`
* Test: extend `tests/test_simulations_smoke.py`

**Why MEDIUM priority:** the governance agent shipped 2026-04-25 (v0.29.55). Tests pass but it has never executed against real production traces. If the user loads the launchd plists at any point, this fires every 2 h. First-cycle behaviour against real audit data is unverified.

### Scope

* Use a temp shadow-mode config (write all decisions to `proposed`, never `applied`).
* Use the `FakeLLM` from [`governance/llm.py`](../../../governance/llm.py) seeded with a deterministic response set.
* Run `agent.run_cycle()` once against the production trade-log archive (read-only).
* Verify: at least N `proposed` decisions emitted, audit JSONL append-only, kill-switch state preserved.

### Implementation steps

- [ ] **E1** — Inspect [`docs/governance/PHASE2_RUNBOOK.md`](../../../docs/governance/PHASE2_RUNBOOK.md) for the canonical fast-cycle invocation pattern.

- [ ] **E2** — Implement `run(*, fake_llm_responses: list[str], shadow_dir: Path) -> CycleReport` that drives one cycle and returns `proposed_count`, `applied_count`, `audit_records_emitted`, `kill_switch_tripped`.

- [ ] **E3** — `main()` accepts `--responses-file` to point at a deterministic JSON of FakeLLM responses; defaults to a built-in canonical fixture.

- [ ] **E4** — Smoke tests:
  * `test_governance_fast_cycle_writes_only_to_proposed`.
  * `test_governance_fast_cycle_audit_jsonl_append_only`.
  * `test_governance_fast_cycle_kill_switch_active_blocks_all_apply`.

- [ ] **E5** — README + smoke-test row updates.

**Acceptance:**
* Shadow-mode invariant holds: zero `applied` writes for any FakeLLM response.
* Audit JSONL is append-only (file size grows, existing records unchanged).
* Smoke tests added (≥ 3) and pass.

---

## Task F: `scripts/simulations/resolution_calibration.py` — resolution + calibration loop

**Files:**
* Create: `scripts/simulations/resolution_calibration.py`
* Test: extend `tests/test_simulations_smoke.py`

**Why MEDIUM priority:** [PROFIT-CAL-001](../../profit_path_debt_log.md) closed 2026-04-24 wired the calibration loop end-to-end but has never run against a real resolution. This won't matter until the first paper trade resolves (could be hours-days post-launch), so it's lower pre-launch priority — but worth verifying the wiring is sound before the first resolution event hits production.

### Scope

* In a temp `paper_trades.db` + `evidence_store.db`, insert a synthetic paper trade matching one of the canonical events, then trigger the resolution loop with a fixed market outcome (YES wins / NO wins).
* Verify: `paper_trades.resolved=1` and `pnl_dollars` set correctly; bankroll credited (not just debited); calibration callback fires; source-credibility multipliers updated; `resolved_ts` populated.

### Implementation steps

- [ ] **F1** — Locate the resolution-loop entry point (likely `main.py:_resolution_loop_task` or similar). Document the call signature in the simulation module docstring.

- [ ] **F2** — Implement `run(outcome: Literal["yes", "no"]) -> ResolutionReport` that drives one resolution against a temp DB seeded with one synthetic trade.

- [ ] **F3** — Smoke tests:
  * `test_resolution_yes_wins_credits_bankroll`.
  * `test_resolution_no_wins_zero_credit`.
  * `test_resolution_triggers_calibration_callback`.

- [ ] **F4** — README + smoke-test row updates.

**Acceptance:**
* Bankroll math correct for both YES-wins and NO-wins outcomes.
* Calibration callback fires exactly once per resolution.
* Smoke tests added (≥ 3) and pass.

---

## Task G: `scripts/simulations/dossier_creation.py` — evidence → dossier flow

**Files:**
* Create: `scripts/simulations/dossier_creation.py`
* Test: extend `tests/test_simulations_smoke.py`

**Why MEDIUM priority:** during the EDGE-002 investigation we found only 12 of 847 active markets had dossiers (~1.4%). The line-688 fix routes new evidence into the queue, but the threshold for dossier creation is unclear. Without dossier coverage, the structural lane is silently absent from BLEND_DECISIONs (`str_p=None`), which is exactly what we observed for KXTRUMPIRAN.

### Scope

* Stream a synthetic evidence sequence into the evidence queue (or directly into `EvidenceStore.add_evidence`) for a fresh ticker.
* Observe at what evidence count / source-class diversity / time elapsed a dossier is created.
* Audit the threshold against expectations.

### Implementation steps

- [ ] **G1** — Trace the dossier-creation logic: `EvidenceStore.add_evidence` → dossier-update or dossier-creation? Document the trigger condition.

- [ ] **G2** — Implement `run(*, ticker: str, evidence_count: int) -> DossierCreationReport` that streams N synthetic evidence records and returns: `dossier_created` (bool), `creation_threshold_reached_at_n` (int | None), `created_dossier_state`.

- [ ] **G3** — Smoke tests:
  * `test_dossier_not_created_below_threshold`.
  * `test_dossier_created_at_threshold`.
  * `test_dossier_evidence_records_attached`.

- [ ] **G4** — README + smoke-test row updates.

**Acceptance:**
* The threshold for dossier creation is documented (in the module docstring).
* Below-threshold evidence does not create a dossier.
* Smoke tests added (≥ 3) and pass.

**Risk surfacing:** if the threshold is "manually triggered" or otherwise unclear, file `PROFIT-DOSSIER-001` to track the dossier-coverage gap separately.

---

## Versioning + commit cadence

Each task ships as its own commit on the same feature branch (e.g. `feat/profit-edge-004-pipeline-sim-buildout`). After all tasks land:

* Bump `VERSION` to the next patch (`0.29.59` if pulled directly from `main` after EDGE-003).
* Append a `CHANGELOG.md` entry referencing each new simulation by file path and the contracts each pins.
* Append a row to the `docs/profit_path_debt_log.md` "Recommended Execution Order" if any task surfaces a new debt-log item (e.g. `PROFIT-MATCH-001`).
* Open a single MR titled `feat(sim): pipeline simulation buildout (PROFIT-EDGE-004, v0.29.59)`.
* In the MR description, list every new simulation + its smoke-test contract.

After merge, the operator runs each simulation manually once and confirms output matches the expected pattern. Any drift between expected and observed becomes the next debt-log entry.

---

## Out of scope for this plan

* CI integration beyond the existing smoke-test layer (the simulations are an operator-invoked tool by design; CI runs the smoke tests).
* New dependencies beyond what's already in `requirements*.txt`.
* Any change to production code paths (regime classifier, blender, gates, executor, governance). If a simulation surfaces a bug, that bug becomes a separate debt-log item with its own PR.
* Live-mode simulations. These are paper-mode only; live-mode `_validate()` paths can be added in a follow-up plan once paper trade volume is non-zero.

---

## Done definition

This plan is complete when:

- [ ] All seven tasks land on `main` as separate commits.
- [ ] [`tests/test_simulations_smoke.py`](../../../tests/test_simulations_smoke.py) has ≥ 13 + (~21 new) ≈ 34 smoke tests passing.
- [ ] [`scripts/simulations/README.md`](../../../scripts/simulations/README.md) lists all 10 simulations (3 captured + 7 new).
- [ ] Any bugs surfaced during buildout are filed as their own debt-log items and triaged.
- [ ] The operator can run the full simulation suite from a fresh shell and produce a report covering every documented pipeline stage.
