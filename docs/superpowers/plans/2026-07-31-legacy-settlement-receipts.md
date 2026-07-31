# Legacy Settlement Receipts Implementation Plan

## Global Constraints

- Default off and plan-only until both `--allow-network` and `--write` appear.
- Only official, freshly re-fetched, terminal directional observations can
  alter the legacy root.
- Do not mutate runtime, pending snapshot/manifest, active cohorts, feedback,
  sizing, launchd, config, or live mode.
- Legacy gross results remain excluded from fee-net and repeatable-profit
  readiness.

## Task Structure

### Task 1: Canonical Receipt Serialization

- Create: `trading/legacy_settlement_receipts.py`
- Modify: `scripts/audit_open_paper_settlements.py`
- Test: `tests/test_open_paper_settlement_audit.py`
- Consumes: `SettlementObservation`, `MarketRef`, `MarketOutcome`
- Produces: versioned receipt records which reconstruct and revalidate a
  `SettlementObservation`.

- [ ] **Step 1: Write the failing test**
  Add a terminal audit assertion that the row has a versioned receipt bundle;
  reconstruct it and assert its observation hash and canonical payload equal
  the source observation. Add a pending audit assertion that the bundle is
  absent.
- [ ] **Step 2: Run test to verify it fails**
  Run `CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_open_paper_settlement_audit.py -q` and expect the terminal
  bundle assertion to fail because `SettlementAuditRow` has no receipt field.
- [ ] **Step 3: Write minimal implementation**
  Add a frozen receipt dataclass plus parser in
  `trading/legacy_settlement_receipts.py`, serialize canonical observation
  semantics from `_MarketAudit`, and emit it only for exact terminal rows.
- [ ] **Step 4: Run test to verify it passes**
  Re-run the focused audit test file and require all tests to pass.
- [ ] **Step 5: Commit**
  Commit only the receipt serialization and its tests.

### Task 2: Legacy-Only Transaction Applier

- Modify: `trading/legacy_settlement_receipts.py`
- Test: `tests/test_legacy_settlement_receipts.py`
- Consumes: parsed receipt, root DB, runtime lock, exact source observation
- Produces: one atomic directional root settlement and an archival-only marker.

- [ ] **Step 1: Write the failing test**
  Create a legacy schema fixture and assert one exact receipt resolves one
  matching root trade, writes the canonical observation, writes no normal
  feedback outbox requirement, and remains excluded from profit evidence.
  Add root fingerprint drift, fresh-source hash mismatch, void, duplicate, and
  second-conflicting-receipt cases which leave tables unchanged.
- [ ] **Step 2: Run test to verify it fails**
  Run `CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_legacy_settlement_receipts.py -q` and expect import or missing
  applier failures.
- [ ] **Step 3: Write minimal implementation**
  Add a no-startup `LegacySettlementReceiptApplier` with immediate transaction,
  strict mapped identity checks, directional gross-payout computation, exact
  row CAS, canonical-observation insert, archival marker, and rollback on any
  failed condition. Do not import `PaperTrader` or `SettlementOutboxTask`.
- [ ] **Step 4: Run test to verify it passes**
  Re-run the focused legacy receipt tests and check both changed and unchanged
  database snapshots.
- [ ] **Step 5: Commit**
  Commit only the applier and its contract tests.

### Task 3: Explicit CLI Boundary

- Create: `scripts/reconcile_legacy_paper_receipts.py`
- Test: `tests/test_legacy_settlement_receipts.py`
- Consumes: hash-attested audit report, its externally supplied file hash,
  root/snapshot hashes, one trade ID, source adapter
- Produces: plan JSON by default; guarded apply only with explicit flags.

- [ ] **Step 1: Write the failing test**
  Assert no write without both flags, no write without runtime quiescence,
  successful apply writes an adjacent backup, and a fresh re-fetch must match
  the reviewed observation hash.
- [ ] **Step 2: Run test to verify it fails**
  Run the legacy receipt tests and expect missing command or missing guard
  failures.
- [ ] **Step 3: Write minimal implementation**
  Add parser validation, report-file hash validation, plan mode, project lock
  acquisition, writer-lock-bound backup creation, one exact
  `AuthoritativeSettlementSource` re-fetch, and delegation to the transaction
  applier. Keep network disabled unless `--allow-network` is set.
- [ ] **Step 4: Run test to verify it passes**
  Re-run the focused legacy receipt suite and check write behavior by hash.
- [ ] **Step 5: Commit**
  Commit only the CLI boundary and its tests.

### Task 4: Regression Isolation

- Modify: `tests/test_paper_cohorts.py`
- Modify: `tests/test_settlement_outbox_task.py`
- Modify: `tests/test_profit_evidence_report.py`
- Test: `tests/test_paper_canonical_settlement.py`, `tests/test_paper_cohorts.py`,
  `tests/test_settlement_outbox_task.py`, `tests/test_profit_evidence_report.py`
- Consumes: completed legacy receipt behavior
- Produces: proof that receipt application does not loosen cutover, feedback,
  or profit-readiness boundaries.

- [ ] **Step 1: Write the failing tests**
  Assert the pending manifest remains valid after a root receipt, no normal
  consumer receipt is enqueued, and an archival legacy receipt cannot make the
  profit-evidence verdict ready.
- [ ] **Step 2: Run tests to verify they fail**
  Run the exact four test files and require each failure to identify a missing
  isolation behavior.
- [ ] **Step 3: Write minimal implementation**
  Add only the archival exclusion/query predicates required by the failing
  tests. Do not alter generic outbox delivery behavior.
- [ ] **Step 4: Run tests to verify they pass**
  Run `CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_open_paper_settlement_audit.py tests/test_legacy_settlement_receipts.py tests/test_paper_canonical_settlement.py tests/test_paper_cohorts.py tests/test_settlement_outbox_task.py tests/test_profit_evidence_report.py -q`.
- [ ] **Step 5: Commit**
  Commit the isolation tests and minimal supporting code.

### Task 5: Review and Release

- Modify: `VERSION`, `README.md`, `CHANGELOG.md`
- Test: all files in Task 4 plus Ruff and `git diff --check`
- Consumes: all receipt-bound behavior and tests
- Produces: an independently reviewed, default-off release.

- [ ] **Step 1: Run final regression validation**
  Run the exact Task 4 command, Ruff on changed files, and
  `git diff --check origin/main...HEAD`; record the pass counts.
- [ ] **Step 2: Request independent review**
  Require review of write gating, source re-fetch equality, transaction
  rollback, backup/lock ordering, and feedback/profit isolation.
- [ ] **Step 3: Update release metadata**
  Record that this is a default-off, one-shot legacy-receipt boundary and not
  a profitability or live-trading change.
- [ ] **Step 4: Release only after checks pass**
  Push, open a PR, resolve review findings, and wait for CI. Do not restart the
  bot because the feature has no runtime wiring.
