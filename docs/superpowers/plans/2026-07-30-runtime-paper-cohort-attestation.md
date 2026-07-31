# Runtime Paper Cohort Attestation Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Global Constraints

- Work only in this isolated worktree; leave main's runtime artifacts untouched.
- Attestation is observability-only: no trade, sizing, live-mode, cohort-cutover,
  database, credential, or environment mutation.
- Keep the receipt nonsecret and fail closed on malformed or stale data.
- Use test-driven development. Run focused tests under `CI=1`.
- Preserve `LIVE_TRADING_ENABLED=false` throughout implementation and rollout.

## File Structure

- `trading/runtime_paper_cohort_attestation.py`: receipt serialization,
  atomic writer, and strict read-only validation helpers.
- `main.py`: retain the validated binding and write the receipt after
  `PaperTrader` initialization.
- `scripts/botcheck.py`: validate and display runtime binding status from the
  receipt using the current process identity.
- `tests/test_runtime_paper_cohort_attestation.py`: writer and parser behavior.
- `tests/test_main_startup.py`: startup ordering and no-receipt-on-failure.
- `tests/test_botcheck.py`: positive and fail-closed botcheck status paths.
- `VERSION`, `CHANGELOG.md`, `README.md`: release metadata only after behavior
  and verification are complete.

### Task 1: Add Fail-Closed Receipt Contract
- Test: `tests/test_runtime_paper_cohort_attestation.py`
- [ ] **Step 1: Write failing tests** for a manifest-bound pending receipt,
  atomic replacement, malformed payload rejection, symlink rejection, and
  current-PID validation.
- [ ] **Step 2: Run the focused test file** and confirm the tests fail because
  the module or behavior is absent.
- [ ] **Step 3: Add `trading/runtime_paper_cohort_attestation.py`** with a
  versioned payload, storage-root-relative path validation, secure lstat checks,
  atomic temp-file/fsync/replace write, and a read-only parser.
- [ ] **Step 4: Re-run the focused test file** and confirm it passes.

### Task 2: Bind Successful Startup to Receipt Creation
- Test: `tests/test_main_startup.py`
- [ ] **Step 1: Write failing tests** proving the runtime resolver returns the
  already-validated manifest binding and startup writes a receipt only after
  `PaperTrader` construction succeeds.
- [ ] **Step 2: Run the focused selected tests** and confirm expected failure.
- [ ] **Step 3: Update `main.py`** to carry the binding with the runtime cohort
  result and invoke the receipt writer at the successful startup boundary.
- [ ] **Step 4: Re-run the focused selected tests** and confirm they pass.

### Task 3: Make Botcheck Verify the Current Binding
- Test: `tests/test_botcheck.py`
- [ ] **Step 1: Write failing tests** for valid current-PID pending binding,
  stale/mismatched PID, malformed JSON, symlink, and manifest identity mismatch.
- [ ] **Step 2: Run the focused selected tests** and confirm expected failure.
- [ ] **Step 3: Update `scripts/botcheck.py`** to inspect only the receipt and
  manifest, match it to the selected process, and render attested or unverified
  status with a reason.
- [ ] **Step 4: Re-run the focused selected tests** and confirm they pass.

### Task 4: Review, Release, and Rollout
- Test: `tests/test_runtime_paper_cohort_attestation.py`,
  `tests/test_main_startup.py`, `tests/test_botcheck.py`
- [ ] **Step 1: Run ruff and the complete focused test group** under `CI=1`.
- [ ] **Step 2: Obtain an independent diff review** focused on startup ordering,
  receipt atomicity, secret exposure, and false-positive binding verification.
- [ ] **Step 3: Update patch release metadata** and verify the final committed
  tree with focused tests and `git diff --check`.
- [ ] **Step 4: Open/merge only after required CI and review pass, then restart
  paper/shadow mode and verify a new PID, attested binding, fresh heartbeat,
  `LIVE_ORDER=0`, and disabled live mode.**
