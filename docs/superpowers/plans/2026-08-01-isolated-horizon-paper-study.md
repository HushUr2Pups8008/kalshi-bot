# Isolated Polymarket Horizon Paper Study Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manifest-bound, separate-process `(14.0, 30.0]` Polymarket
paper study without changing primary `0-14d` routing, primary paper lineage, or
the live-order boundary.

**Architecture:** The one study kind is `polymarket_horizon_15_30`; it lives
under `data/horizon_paper_studies/pm-horizon-15-30-20260805`. A dedicated launchd
process reads public data, writes explicit input/admission artifacts, records
simulated positions in a standalone `study_ledger.db`, and settles only those
ledger rows. It uses shared pure matching only, never `main.Bot`, primary
queues, `PaperTrader`, primary paper schemas, or primary accounting handlers.

**Tech Stack:** Python 3.11, SQLite, append-only canonical JSONL, existing
Polymarket public client/matching utilities, standalone study ledger/accounting,
launchd, pytest.

## Global Constraints

- Do not change `PAPER_ADMISSION_MAX_DAYS_TO_CLOSE` or primary Polymarket
  runtime behavior. The primary band remains `[0.0, 14.0]`.
- Never enable or add a live executor. `LIVE_TRADING_ENABLED=false` is a
  required study launch invariant, not merely a default.
- Do not share `data/paper_trades.db`, primary cohort directories, primary
  `logs/trades/live/trades.jsonl`, the primary settlement outbox, or the
  primary attestation path.
- Do not modify `trading/paper_trader.py`, `trading/paper_cohorts.py`,
  `trading/runtime_paper_cohort_attestation.py`, or a primary paper-trade DDL
  migration for this study. The study ledger/schema is separate.
- Do not write study outcomes to primary calibration, keyword, credibility,
  matcher-weight, bankroll, risk, readiness, or report surfaces.
- All manifests, policy/fee snapshots, artifacts, and receipts are canonical,
  hash validated, atomically written, and fail closed.
- A study may coexist with legacy-pending exposure. It must prove its root,
  ledger, state, journals, attestation, logs, launchd label, and runner are
  disjoint from all primary and pending-family paths. Never reset/reuse an
  aborted ID.
- TDD is mandatory. For every task, write/run the failing targeted test before
  implementation, then run the same test green.

## File Map

| File | Responsibility |
| --- | --- |
| `polymarket/horizon_selection.py` | Pure, side-effect-free `(lower, upper]` Polymarket market-band selection. |
| `polymarket/paper_runtime.py` | Retain primary behavior; delegate existing private shadow selection to the new pure helper only. |
| `polymarket/horizon_paper_study.py` | Study collector, admission engine, decision adapter, and paper-execution orchestration with no primary route callback. |
| `trading/horizon_paper_study_manifest.py` | `polymarket_horizon_15_30` manifest, coexistence proof, safe path/identity validation, and primary live-block discovery. |
| `trading/horizon_paper_study_ledger.py` | Separate `study_ledger.db` schema and unique admission-linked simulated position ledger. |
| `trading/horizon_paper_study_accounting.py` | Separate modeled fee schedule/provenance calculation with no primary fee-net imports. |
| `trading/horizon_paper_study_attestation.py` | Study-only, exact-path receipt writer/reader; cannot accept the primary state path. |
| `trading/horizon_paper_study_artifacts.py` | Canonical schema validation, append/recovery, SQLite index, lock, and abort receipts. |
| `tasks/horizon_paper_study_settlement_task.py` | Study-only settlement observation, provenance, and fee-accounting records. |
| `scripts/initialize_horizon_paper_study.py` | Explicit, confirmation-gated study provisioning CLI. |
| `scripts/run_horizon_paper_study.py` | Separate runtime entry point and fail-closed environment validation. |
| `scripts/horizon_paper_study_check.py` | Attestation, manifest, lock, artifact, and isolation checker. |
| `scripts/horizon_paper_study_report.py` | Study-local report writer; no primary report ingestion. |
| `scripts/horizon_paper_study_contamination_audit.py` | Explicit offline audit of selected primary artifacts; never a runtime or botcheck dependency. |
| `ops/launchd/com.jake.kalshi-horizon-paper-study.plist` | Dedicated service definition and distinct logs. |
| `tests/test_horizon_selection.py` | Pure horizon boundary and primary-compatibility coverage. |
| `tests/test_horizon_paper_study_manifest.py` | Manifest, coexistence proof, path, policy/fee snapshot, and primary live-block discovery coverage. |
| `tests/test_horizon_paper_study_ledger.py` | Separate ledger schema, unique admission linkage, and crash-recovery coverage. |
| `tests/test_horizon_paper_study_accounting.py` | Separate fee provenance/no-primary-fee-net-import coverage. |
| `tests/test_horizon_paper_study_artifacts.py` | JSONL validation, hash, recovery, lock, and deduplication coverage. |
| `tests/test_horizon_paper_study_runtime.py` | Non-routing collector/admission/decision/execution behavior. |
| `tests/test_horizon_paper_study_settlement.py` | Terminal observation and fee provenance coverage. |
| `tests/test_horizon_paper_study_attestation.py` | Dedicated exact-path receipt and process-binding coverage. |
| `tests/test_horizon_paper_study_scripts.py` | Initializer, runner, checker, and report CLI coverage. |
| `tests/test_horizon_paper_study_contamination_audit.py` | Explicit input/hash reporting and primary-artifact leakage detection coverage. |
| `tests/test_horizon_paper_study_launchd.py` | Plist label, interpreter, environment, and log-path coverage. |
| `tests/polymarket/test_paper_runtime.py` | Regression proving the primary runtime keeps current routing/telemetry behavior. |
| `tests/test_horizon_paper_study_primary_isolation.py` | Main live-block discovery and strict non-reading of ledger/attestation/report paths. |

## Task 1: Extract the Shared Pure Horizon Selector

**Files:**
- Create: `polymarket/horizon_selection.py`
- Modify: `polymarket/paper_runtime.py`
- Create: `tests/test_horizon_selection.py`
- Modify: `tests/polymarket/test_paper_runtime.py`

- [ ] **Step 1: Write failing boundary tests.**
  Test a valid pre-admission market at `14.0`, `14.000001`, `30.0`, and
  `30.000001` days to close. Assert the selector returns only
  `14.000001` and `30.0` for `(14.0, 30.0]`; invalid, closed, suppressed, or
  malformed markets are excluded. Add a regression test proving the existing
  primary shadow telemetry still partitions primary and shadow markets exactly
  as before.

- [ ] **Step 2: Run the focused tests and verify failure.**
  Run:
  ```bash
  CI=1 .venv/bin/python -m pytest -q tests/test_horizon_selection.py tests/polymarket/test_paper_runtime.py
  ```

- [ ] **Step 3: Implement the pure helper.**
  Define `select_polymarket_horizon_band(markets, *, now, lower_exclusive_days,
  upper_inclusive_days)` in `polymarket/horizon_selection.py`. It validates
  finite ordered bounds and returns only pre-admission-matchable markets in the
  exact band. Refactor `_horizon_shadow_market_sets` to call it while preserving
  the existing private API and primary behavior.

- [ ] **Step 4: Run the focused tests green.**
  Re-run the command from Step 2.

- [ ] **Step 5: Commit.**
  ```bash
  rtk proxy git add polymarket/horizon_selection.py polymarket/paper_runtime.py tests/test_horizon_selection.py tests/polymarket/test_paper_runtime.py
  rtk proxy git commit -m "feat: add pure Polymarket horizon selector"
  ```

## Task 2: Add the Immutable Study Manifest and Coexistence Proof

**Files:**
- Create: `trading/horizon_paper_study_manifest.py`
- Modify: `main.py`
- Create: `tests/test_horizon_paper_study_manifest.py`
- Create: `tests/test_horizon_paper_study_primary_isolation.py`

- [ ] **Step 1: Write failing manifest tests.**
  Cover exactly one valid `polymarket_horizon_15_30` manifest under
  `data/horizon_paper_studies/pm-horizon-15-30-20260805`, a relative
  `study_ledger.db` path, `study_state.db`, unique initialized ledger identity,
  `(14.0,30.0]`, a positive explicit bankroll, hashes for both snapshots, and
  both fixed safety fields. Cover refusal of missing/rewritten/symlinked files,
  zero/negative bankroll, wrong venue/kind, changed hash, mutable source root,
  and non-empty ledger before initialization.

  Test coexistence with a live legacy-pending directory and unresolved rows:
  valid study initialization must succeed without opening or mutating either.
  Test refusal when the study root, ledger, state DB, journal, state receipt,
  log path, or launcher label overlaps a primary/legacy-pending path; when a
  target is symlinked; or when an existing study ID has any artifact. Add
  primary-isolation tests: a study manifest causes the permanent live block but
  `main.py` never opens the study ledger or study attestation path.

- [ ] **Step 2: Run the focused tests and verify failure.**
  ```bash
  CI=1 .venv/bin/python -m pytest -q tests/test_horizon_paper_study_manifest.py tests/test_horizon_paper_study_primary_isolation.py
  ```

- [ ] **Step 3: Implement manifest and primary-block support.**
  Implement `HorizonPaperStudyManifest` and
  `validate_horizon_paper_study_manifest()` in the new study module. The
  manifest has `study_kind="polymarket_horizon_15_30"`, never a cohort-kind
  field. Implement `validate_study_coexistence()` with explicit canonical path
  comparisons against the primary root DB, primary/pending cohort roots,
  primary journal/state/attestation paths, primary logs, and primary launchd
  label. It must not inspect row counts, await settlement, or mutate any
  legacy-pending file.

  Add the read-only manifest-only discovery call to the primary live-transition
  guard. A valid manifest produces
  `"horizon paper study remains permanently isolated from live trading"`; a
  malformed or changed manifest produces an invalid-study fail-closed block.
  Do not add the study kind to `config.py`, primary cohort resolution,
  `trading/paper_cohorts.py`, or primary attestation validation. Do not add any
  study record to primary P&L/report scopes.

- [ ] **Step 4: Run the focused tests green.**
  Re-run the command from Step 2.

- [ ] **Step 5: Commit.**
  ```bash
  rtk proxy git add main.py trading/horizon_paper_study_manifest.py tests/test_horizon_paper_study_manifest.py tests/test_horizon_paper_study_primary_isolation.py
  rtk proxy git commit -m "feat: add immutable horizon study manifest"
  ```

## Task 3: Build Canonical Artifacts, Locking, and Crash Recovery

**Files:**
- Create: `trading/horizon_paper_study_artifacts.py`
- Create: `tests/test_horizon_paper_study_artifacts.py`

- [ ] **Step 1: Write failing artifact tests.**
  Cover exact schemas for input, shadow admission, decision, execution,
  settlement, and abort records; canonical self-excluding SHA-256; manifest
  binding; `routing_prohibited=true`; duplicate same ID/same hash idempotency;
  duplicate same ID/different hash abort; input/admission uniqueness; lock
  collision; atomic state-transaction-before-JSONL-mirror persistence; restart
  regeneration of a missing mirror line; invalid JSON/hash abort; and ambiguous
  execution recovery abort.

- [ ] **Step 2: Run the focused test and verify failure.**
  ```bash
  CI=1 .venv/bin/python -m pytest -q tests/test_horizon_paper_study_artifacts.py
  ```

- [ ] **Step 3: Implement the writer.**
  Implement `HorizonStudyArtifactStore` with the four exact tables in the
  design, exclusive `runtime.lock`, canonical JSON serializer, state-first
  SQLite transaction, `O_APPEND` plus `fsync` audit mirror, and deterministic
  mirror reconstruction from committed state. Expose only
  `record_input`, `record_shadow_admission`, `record_decision`,
  `claim_execution`, `record_execution`, `record_settlement`, and `abort`.
  Every public method validates its caller-supplied manifest hash and writes
  only below the study root.

- [ ] **Step 4: Run the focused test green.**
  Re-run the command from Step 2.

- [ ] **Step 5: Commit.**
  ```bash
  rtk proxy git add trading/horizon_paper_study_artifacts.py tests/test_horizon_paper_study_artifacts.py
  rtk proxy git commit -m "feat: add horizon study artifact lineage"
  ```

## Task 4: Build the Separate Ledger, Accounting, and Attestation Boundary

**Files:**
- Create: `trading/horizon_paper_study_ledger.py`
- Create: `trading/horizon_paper_study_accounting.py`
- Create: `trading/horizon_paper_study_attestation.py`
- Create: `tests/test_horizon_paper_study_ledger.py`
- Create: `tests/test_horizon_paper_study_accounting.py`
- Create: `tests/test_horizon_paper_study_attestation.py`

- [ ] **Step 1: Write failing ledger/accounting/attestation tests.**
  Assert the ledger creates only `study_trades` and its study-local auxiliary
  tables in `study_ledger.db`; it must never create or query `paper_trades`.
  Cover unique `(study_id, admission_id)` linkage, idempotent recovery of one
  inserted simulated position, and abort on ambiguous recovery. Assert the
  accounting module imports neither `PaperTrader`, `PaperAccountingHandlers`,
  `settlement_economics`, nor the fee-net runtime configuration. Cover absent,
  expired, partial, and fully covering manifest-pinned schedules.

  Cover path-scoped attestation with exact valid output
  `logs/state/horizon_paper_studies/pm-horizon-15-30-20260805/runtime_attestation.json`.
  Assert it rejects the primary attestation path, an arbitrary alternate path,
  a symlinked directory, a wrong service label, an incorrect study kind, a
  changed manifest digest, a nonrelative ledger path, and
  `live_trading_enabled=true`.

- [ ] **Step 2: Run the focused tests and verify failure.**
  ```bash
  CI=1 .venv/bin/python -m pytest -q tests/test_horizon_paper_study_ledger.py tests/test_horizon_paper_study_accounting.py tests/test_horizon_paper_study_attestation.py
  ```

- [ ] **Step 3: Implement the isolated boundary.**
  Implement `HorizonStudyLedger` with a separate schema and a single transaction
  that binds a claimed admission to exactly one simulated `study_trade_id`.
  Implement `HorizonStudyAccounting` as a pure schedule evaluator returning
  `unscorable` or `modeled_pinned_schedule`, never an authoritative receipt.
  Implement study-only attestation with an internal expected-path constructor
  derived from study ID and the fixed state root; public APIs receive the study
  manifest and service label, not an arbitrary path.

- [ ] **Step 4: Run the focused tests green.**
  Re-run the command from Step 2.

- [ ] **Step 5: Commit.**
  ```bash
  rtk proxy git add trading/horizon_paper_study_ledger.py trading/horizon_paper_study_accounting.py trading/horizon_paper_study_attestation.py tests/test_horizon_paper_study_ledger.py tests/test_horizon_paper_study_accounting.py tests/test_horizon_paper_study_attestation.py
  rtk proxy git commit -m "feat: add isolated horizon study ledger"
  ```

## Task 5: Implement the Non-Routing Study Runtime

**Files:**
- Create: `polymarket/horizon_paper_study.py`
- Create: `tests/test_horizon_paper_study_runtime.py`
- Modify: `tests/polymarket/test_paper_runtime.py`

- [ ] **Step 1: Write failing runtime tests.**
  Use fake feed, market, decision, and study-ledger adapters. Prove the
  collector records the input before a decision, selects only `(14.0,30.0]`,
  emits a shadow admission for every qualifying/rejected matchable market,
  preserves pinned policy hash, and uses a study decision adapter. Require a
  fresh study analysis and a fresh study research invocation after every
  qualified admission; assert the decision record binds
  `analysis_input_sha256`, `research_snapshot_sha256`,
  `counter_evidence_status`, `market_price_snapshot`, and `estimated_edge`.
  Assert
  `main.Bot._route_analysis`, `BlendTask`, primary queues, global trade logger,
  `PaperTrader`, primary accounting handlers, calibration task, and live
  executor are never called. Cover duplicate retry, crash after claim before
  receipt, a resumption that finds the same linked ledger row, and an ambiguous
  resumption that aborts.

- [ ] **Step 2: Run the focused tests and verify failure.**
  ```bash
  CI=1 .venv/bin/python -m pytest -q tests/test_horizon_paper_study_runtime.py tests/polymarket/test_paper_runtime.py
  ```

- [ ] **Step 3: Implement the study runtime.**
  Implement `HorizonPaperStudyRuntime` with injected read-only feed/public
  market clients, policy snapshot, artifact store, decision adapter, and study
  ledger executor. Its public cycle is `collect -> record_input -> select ->
  record_shadow_admission -> independently_analyze -> independently_research ->
  record_decision -> claim -> simulated_ledger_entry -> record_execution`.
  It rejects a qualified admission without all five decision evidence fields.
  Its constructor rejects a callback/object
  shaped like a primary route, queue, live executor, or primary event sink.
  It never imports `main.py`.

- [ ] **Step 4: Run the focused tests green.**
  Re-run the command from Step 2.

- [ ] **Step 5: Commit.**
  ```bash
  rtk proxy git add polymarket/horizon_paper_study.py tests/test_horizon_paper_study_runtime.py tests/polymarket/test_paper_runtime.py
  rtk proxy git commit -m "feat: add non-routing horizon study runtime"
  ```

## Task 6: Add Study-Only Settlement and Fee Provenance

**Files:**
- Create: `tasks/horizon_paper_study_settlement_task.py`
- Create: `tests/test_horizon_paper_study_settlement.py`

- [ ] **Step 1: Write failing settlement tests.**
  Cover a valid terminal public-market observation with immutable payload hash,
  study/trade binding, and one settlement receipt. Cover nonterminal/malformed
  data as no settlement. Cover absent/expired/partial schedule as
  `unscorable`, a complete pinned schedule as `modeled_pinned_schedule`, and
  assert all study-ledger rows retain `profit_receipt_attested=false` and
  `live_readiness_eligible=false`. Assert no call reaches the primary settlement
  outbox, its consumer registry, `PaperAccountingHandlers`, or a primary
  fee-net runtime rule.

- [ ] **Step 2: Run the focused tests and verify failure.**
  ```bash
  CI=1 .venv/bin/python -m pytest -q tests/test_horizon_paper_study_settlement.py tests/test_horizon_paper_study_accounting.py
  ```

- [ ] **Step 3: Implement the settlement task.**
  Add a study-only task that accepts only a validated study binding and public
  terminal observations. It writes `artifacts/settlements.jsonl` via the
  artifact store. Compute gross P&L separately. Compute modeled fee net only
  when the hash-bound schedule fully covers both functions, otherwise emit
  `null`; never infer a zero fee, import Kalshi settlement economics, or reuse
  the primary fee-net accounting contract.

- [ ] **Step 4: Run the focused tests green.**
  Re-run the command from Step 2.

- [ ] **Step 5: Commit.**
  ```bash
  rtk proxy git add tasks/horizon_paper_study_settlement_task.py tests/test_horizon_paper_study_settlement.py
  rtk proxy git commit -m "feat: add horizon study settlement provenance"
  ```

## Task 7: Add Runner, Checker, Report, and Dedicated LaunchAgent

**Files:**
- Create: `scripts/initialize_horizon_paper_study.py`
- Create: `scripts/run_horizon_paper_study.py`
- Create: `scripts/horizon_paper_study_check.py`
- Create: `scripts/horizon_paper_study_report.py`
- Create: `scripts/horizon_paper_study_contamination_audit.py`
- Create: `ops/launchd/com.jake.kalshi-horizon-paper-study.plist`
- Create: `tests/test_horizon_paper_study_scripts.py`
- Create: `tests/test_horizon_paper_study_contamination_audit.py`
- Create: `tests/test_horizon_paper_study_launchd.py`
- Modify: `scripts/botcheck.py`
- Modify: `tests/test_botcheck.py`
- Modify: `tests/test_decision_funnel_summary.py`
- Modify: `tests/test_daily_review.py`

- [ ] **Step 1: Write failing operator/isolation tests.**
  Cover initializer refusal for a path/label overlapping primary or
  legacy-pending state, a symlinked/nonempty target root, mismatched
  confirmation, or wrong study kind; cover successful atomic creation beside a
  live legacy-pending directory of the manifest, standalone ledger/state DBs,
  policy snapshot, and fee schedule. Cover runner
  refusal for every wrong/missing environment field, true live setting, any
  manifest/ledger/state/journal/attestation/log path outside the exact study
  roots, lock collision, and invalid primary label. Test the checker validates only the study receipt and reports no
  primary mutation. Test report output has all required counts and fixed
  `cannot_change_primary_horizon_or_live_readiness=true`. Test the plist has
  only the study label, study interpreter/entry point, study environment, and
  distinct logs. Add regression fixtures proving botcheck, decision funnel, and
  daily review exclude all study event types and paths. Add a botcheck test
  that study-manifest discovery produces the exact manifest-only summary
  `polymarket_horizon_15_30_manifests=1, valid=1, invalid=0,
  live_transition_blocked=true`; it must never open a ledger, journal, study
  attestation, primary log, primary DB, or primary report, or emit primary
  performance values. Add offline-audit tests that pass explicit fixture paths,
  record each input hash, reject a study ID/manifest hash/artifact type/path in
  a selected primary artifact, and prove the audit module is never imported by
  botcheck.

- [ ] **Step 2: Run the focused tests and verify failure.**
  ```bash
  CI=1 .venv/bin/python -m pytest -q tests/test_horizon_paper_study_scripts.py tests/test_horizon_paper_study_contamination_audit.py tests/test_horizon_paper_study_launchd.py tests/test_botcheck.py tests/test_decision_funnel_summary.py tests/test_daily_review.py
  ```

- [ ] **Step 3: Implement operator surfaces.**
  The initializer validates coexistence with any legacy-pending family before
  creating a study directory, then atomically copies reviewed inputs, initializes
  the standalone ledger/state DBs, and writes the manifest once. The runner
  validates its environment against the manifest and derives every path from
  the fixed study roots before opening a database, builds only
  `HorizonPaperStudyRuntime`, writes the dedicated
  attestation, and aborts on fatal boundary violation. The checker verifies
  process PID/start, manifest hash, ledger identity, journal index, and the
  exact derived study-attestation path. The report writes only below `reports/`.
  Add manifest-only botcheck discovery that emits the exact summary from Step 1
  for valid manifests and fails closed for malformed manifests. It must not
  call the study attestation reader or open `study_ledger.db` or any primary
  artifact. Implement the separately invoked contamination audit with explicit
  file arguments and a study-local output report; it must not be imported by
  the runner, primary runtime, or botcheck. Create the separate plist without
  editing `com.jake.kalshi-bot.plist`.

- [ ] **Step 4: Run the focused tests green.**
  Re-run the command from Step 2.

- [ ] **Step 5: Commit.**
  ```bash
  rtk proxy git add scripts/initialize_horizon_paper_study.py scripts/run_horizon_paper_study.py scripts/horizon_paper_study_check.py scripts/horizon_paper_study_report.py scripts/horizon_paper_study_contamination_audit.py ops/launchd/com.jake.kalshi-horizon-paper-study.plist scripts/botcheck.py tests/test_horizon_paper_study_scripts.py tests/test_horizon_paper_study_contamination_audit.py tests/test_horizon_paper_study_launchd.py tests/test_botcheck.py tests/test_decision_funnel_summary.py tests/test_daily_review.py
  rtk proxy git commit -m "feat: add isolated horizon study operator surfaces"
  ```

## Task 8: Full Verification and Review

**Files:**
- Modify only if verification exposes a scoped defect.

- [ ] **Step 1: Run the complete focused study suite.**
  ```bash
  CI=1 .venv/bin/python -m pytest -q \
    tests/test_horizon_selection.py \
    tests/test_horizon_paper_study_manifest.py \
    tests/test_horizon_paper_study_primary_isolation.py \
    tests/test_horizon_paper_study_artifacts.py \
    tests/test_horizon_paper_study_ledger.py \
    tests/test_horizon_paper_study_accounting.py \
    tests/test_horizon_paper_study_runtime.py \
    tests/test_horizon_paper_study_settlement.py \
    tests/test_horizon_paper_study_attestation.py \
    tests/test_horizon_paper_study_scripts.py \
    tests/test_horizon_paper_study_contamination_audit.py \
    tests/test_horizon_paper_study_launchd.py \
    tests/polymarket/test_paper_runtime.py \
    tests/test_botcheck.py \
    tests/test_decision_funnel_summary.py \
    tests/test_daily_review.py
  ```

- [ ] **Step 2: Run static and primary-boundary checks.**
  ```bash
  make lint
  CI=1 .venv/bin/python scripts/botcheck.py
  rg -n "HORIZON_STUDY|polymarket_horizon_15_30|study_ledger" main.py scripts/botcheck.py polymarket/horizon_paper_study.py trading/horizon_paper_study_manifest.py trading/horizon_paper_study_ledger.py trading/horizon_paper_study_accounting.py trading/horizon_paper_study_attestation.py
  rtk proxy git diff --name-only origin/main...HEAD
  ```
  Inspect every hit to confirm `main.py` only discovers manifest blocks and
  the primary runtime retains its existing horizon, paper schema, and
  attestation path. Fail verification if the changed-path list contains
  `trading/paper_trader.py`, `trading/paper_cohorts.py`,
  `trading/runtime_paper_cohort_attestation.py`, `data/paper_trades.db`,
  `logs/trades/live/trades.jsonl`, or an existing primary launchd plist.

- [ ] **Step 3: Obtain independent review.**
  Review for accidental primary routing, live-executor imports, shared state
  paths, fee overclaim, mutable policy reads, primary-schema migration, and
  duplicate simulated execution.
  Resolve each finding with a targeted test before merge.

## Deployment Order

1. Keep the current legacy-pending family running unchanged. Before provisioning,
   verify the study root is `data/horizon_paper_studies/`, its launcher label is
   `com.jake.kalshi-horizon-paper-study`, and every planned ledger/state/journal/
   log/attestation path is disjoint from the primary and legacy-pending roots.
2. Merge verified implementation changes. Sync the deployment checkout to the
   reviewed main commit. Preserve unrelated runtime artifacts.
3. Without changing primary configuration or restarting the primary service,
   create explicit reviewed `policy_snapshot.json` and `fee_schedule.json`.
   The fee schedule may intentionally make fee net unscorable; it may not
   assume zero fees.
4. Run the initializer once with a new study ID and exact confirmation arguments.
   Verify manifest, standalone ledger, policy, fee, and coexistence-path hashes
   and ensure all paths are under the study root while the legacy-pending family
   remains unchanged.
5. Install and bootstrap only
   `com.jake.kalshi-horizon-paper-study.plist`. Do not bootout or kickstart
   `com.jake.kalshi-bot`.
6. Run `scripts/horizon_paper_study_check.py` and `scripts/botcheck.py`.
   Require a valid path-scoped study attestation, a primary runtime attestation
   unchanged from before installation, `LIVE_ORDER: 0`, the exact manifest-only
   botcheck study summary, and no claim by botcheck about primary data/report
   leakage. Run `scripts/horizon_paper_study_contamination_audit.py` separately
   with explicit selected primary log, DB, and report paths; retain its
   study-local input-hash report as the distinct lineage-exclusion evidence.
7. Let the study collect a prospective cohort. Inspect only study-local report
   artifacts for inputs, claims, terminal observations, and provenance. Do not
   alter primary horizon, sizing, weights, or live settings in response.
8. On boundary violation, run the confirmed study abort operation, preserve its
   artifacts for review, and leave the primary service running unchanged.
