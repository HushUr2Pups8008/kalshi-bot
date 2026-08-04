# Legacy-Pending Finalization Implementation Plan

## Global Constraints

- Finalization is a separate boundary from legacy receipt application.
- Plan is strictly read-only.
- Apply is strictly filesystem-only.
- Do not mutate any `paper_trades`, `paper_settlement_*`, `bot_state`, or other
  settlement/accounting row.
- Do not fetch network data.
- Do not change runtime config, launchd, live mode, sizing, fees, or research
  logic.
- Do not auto-provision an active cohort as part of finalization.
- Fail closed on any path alias, symlink, hard link, runtime-lock contention,
  hash drift, malformed manifest, duplicate certificate, or partial archive.

## Task Structure

### Task 1: Finalization Contract And Canonical Payloads

- Create: `trading/legacy_pending_finalization.py`
- Test: `tests/test_legacy_pending_finalization.py`
- Consumes: pending manifest family, immutable baseline snapshot, mutable legacy
  root, reviewed operator inputs
- Produces: canonical sealed finalization plan payload, canonical certificate
  payload, stable finalization-plan SHA

- [ ] **Step 1: Write the failing tests**
  Add RED tests that require:
  - exact canonical sealed-plan payload fields
  - exact canonical certificate payload fields
  - complete deterministic payload inventory entries with path plus SHA-256 for
    every archived payload file only
  - sealed plan and certificate are excluded from the payload inventory hashed
    domain
  - stable `payload_inventory_sha256`
  - stable `finalization_plan_sha256`
  - sorted hash/trade/receipt sets
  - rejection of empty pending families
  - rejection of malformed or duplicate manifest hash sets
  - rejection of noncanonical IDs, timestamps, or SHA-256 fields
- [ ] **Step 2: Run tests to verify they fail**
  Run `CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_legacy_pending_finalization.py -q`
  and expect missing-module or missing-contract failures.
- [ ] **Step 3: Implement the minimal contract**
  Add typed dataclasses/parsers for:
  - sealed finalization plan
  - finalization certificate
  - immutable pending family summary
  - archival publish target
  Keep canonical JSON encoding local to this module.
- [ ] **Step 4: Run tests to verify they pass**
  Re-run the focused file and require all contract tests to pass.
- [ ] **Step 5: Commit**
  Commit only the contract module and its tests.

### Task 2: Read-Only Plan Proof

- Modify: `trading/legacy_pending_finalization.py`
- Test: `tests/test_legacy_pending_finalization.py`
- Consumes:
  - `discover_legacy_pending_paper_risk_cohorts`
  - `validate_legacy_pending_paper_cohort_manifest`
  - immutable baseline snapshot
  - mutable root DB
  - legacy receipt application table
- Produces: `plan_legacy_pending_finalization(...)`

- [ ] **Step 1: Write the failing tests**
  Add RED tests for:
  - shared binding agreement across the pending family
  - exact manifest SHA-set matching
  - exact immutable snapshot SHA and open-row fingerprint matching
  - exact frozen trade ID set matching
  - zero unresolved rows required in the mutable root
  - one-and-only-one archival receipt application required per frozen trade
  - receipt coverage/equality is restricted to the frozen baseline trade ID set
    and does not reject unrelated historical resolved rows
  - duplicate/conflicting/missing receipt identities rejected
  - conservation failure rejected
- [ ] **Step 2: Run tests to verify they fail**
  Run the focused file and expect missing-plan or missing-proof failures.
- [ ] **Step 3: Implement the minimal proof path**
  Build one read-only planner that:
  - resolves the pending family
  - revalidates each manifest and recomputes each manifest SHA
  - loads frozen baseline trade IDs from the immutable snapshot
  - verifies mutable-root unresolved count is zero
  - verifies archival receipt coverage through stored legacy receipt
    applications and linked observations for the frozen baseline trade set only
  - computes the complete deterministic payload inventory expected after publish
  - emits a stable sealed-plan SHA
  Do not open any SQLite write transaction.
- [ ] **Step 4: Run tests to verify they pass**
  Re-run the focused plan tests and require exact payload equality.
- [ ] **Step 5: Commit**
  Commit only the planning proof and its tests.

### Task 3: Filesystem-Only Apply Boundary

- Create: `scripts/finalize_legacy_pending_paper_cohort.py`
- Modify: `trading/legacy_pending_finalization.py`
- Test: `tests/test_legacy_pending_finalization.py`
- Consumes: reviewed plan, runtime lock, staging/archive roots
- Consumes: reviewed sealed finalization plan SHA
- Produces: `apply_legacy_pending_finalization(...)` plus CLI plan/apply modes

- [ ] **Step 1: Write the failing tests**
  Add RED tests that require:
  - `--write` plus explicit confirmation for apply
  - apply requires `--expected-finalization-plan-sha`
  - replay apply verifies archived sealed-plan SHA, recomputed payload
    inventory, and certificate consistency first and returns idempotent success
    without a live pending family
  - runtime lock acquisition before any archive staging
  - full sealed-plan proof revalidation under the lock for first apply only
  - no SQLite write transaction during apply
  - replay verification happens before any demand for live-family planning
  - archive publish before live discovery removal
  - no staged-file leftovers on abort
  - no DB fingerprint change on success or failure
- [ ] **Step 2: Run tests to verify they fail**
  Run the focused file and expect missing-CLI or missing-apply failures.
- [ ] **Step 3: Implement the minimal apply path**
  Add a CLI that:
  - prints plan JSON by default
  - emits a sealed finalization plan artifact and its SHA in plan mode
  - requires `--expected-finalization-plan-sha` and operator confirmation for
    apply
  - checks for an already-published archive replay-success branch
    before any live-family planning requirement
  - stages an archive outside live discovery
  - copies the sealed plan into an archive control path outside the payload
    inventory domain
  - writes a canonical certificate
  - verifies staged hashes and payload inventory
  - publishes the archive
  - removes the live pending family from discovery
  - fsyncs files and parent directories
  Keep all operations filesystem-only.
- [ ] **Step 4: Run tests to verify they pass**
  Re-run focused finalization tests and verify exact on-disk outcomes.
- [ ] **Step 5: Commit**
  Commit only the CLI/apply boundary and its tests.

### Task 4: Idempotence, Recovery, And Discovery Regression

- Modify: `trading/paper_cohorts.py`
- Modify: `tests/test_paper_cohorts.py`
- Modify: `tests/test_go_live_gates.py`
- Test:
  - `tests/test_legacy_pending_finalization.py`
  - `tests/test_paper_cohorts.py`
  - `tests/test_go_live_gates.py`
- Consumes: completed finalization boundary
- Produces: proof that finalized pending families disappear from live discovery
  without weakening any live block or active-cutover precondition

- [ ] **Step 1: Write the failing tests**
  Add RED tests that require:
  - a valid archived finalization is idempotent
  - idempotent replay succeeds from archived sealed-plan SHA, recomputed
    payload inventory, and certificate consistency even after the live pending
    discovery root is gone
  - mismatched preexisting archive content fails closed
  - finalized pending families are no longer returned by
    `discover_legacy_pending_paper_risk_cohorts`
  - `initialize_active_paper_cohort_manifest` can proceed only after pending
    discovery is cleared and unresolved root rows are zero
  - archived legacy receipts remain excluded from profit-attested evidence and
    live readiness
- [ ] **Step 2: Run tests to verify they fail**
  Run the exact regression set and require each failure to identify a missing
  topology or idempotence behavior.
- [ ] **Step 3: Implement the minimal support code**
  Add only the discovery/archive handling required by the failing tests. Do not
  change the existing active or pending settlement semantics.
- [ ] **Step 4: Run tests to verify they pass**
  Run `CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_legacy_pending_finalization.py tests/test_paper_cohorts.py tests/test_go_live_gates.py -q`
  and require all selected tests to pass.
- [ ] **Step 5: Commit**
  Commit only the regression isolation changes and tests.

### Task 5: Verification And Review

- Test:
  - `tests/test_legacy_pending_finalization.py`
  - `tests/test_legacy_settlement_receipt_reconciler.py`
  - `tests/test_legacy_settlement_receipts.py`
  - `tests/test_paper_cohorts.py`
  - `tests/test_go_live_gates.py`
  - Ruff on changed files
  - `git diff --check`
- Consumes: all finalization behavior
- Produces: a reviewable, docs-backed, default-off implementation boundary

- [ ] **Step 1: Run final focused verification**
  Run the exact test suite above, Ruff on changed files, and `git diff --check`.
  Record pass/fail counts and any residual gaps.
- [ ] **Step 2: Request independent review**
  Require review of:
  - proof completeness
  - receipt-coverage validation
  - lock ordering
  - archive publish/remove ordering
  - idempotence and abort cleanup
  - no-row-mutation guarantee
- [ ] **Step 3: Verify operator boundary language**
  Confirm the CLI/help text and docs state that:
  - receipt application happens first
  - finalization is archive/discovery only
  - active provisioning is still a later operator step
- [ ] **Step 4: Release only after checks pass**
  Merge only after focused verification and review complete. Runtime restart is
  out of scope because this boundary is not wired into the live daemon path.
