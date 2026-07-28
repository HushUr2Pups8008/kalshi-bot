# Active Paper Cohort and Horizon Guard Implementation Plan

> Execute each task test-first. Do not mutate the legacy database or enable
> live trading while implementing this plan.

## Task 1: Cohort Configuration and Paths

**Files:** `utils/output_paths.py`, `config.py`, `trading/paper_cohorts.py`,
`tests/test_paper_cohorts.py`

1. Add failing tests for default legacy resolution, valid active IDs, isolated
   paths, explicit active bankroll, and invalid configuration.
2. Add immutable cohort specs, path resolution, and an explicit manifest
   initializer/validator. Runtime must never create a manifest or adopt an
   existing unbound database.
3. Take the manifest's legacy source from a lock-protected immutable snapshot;
   require zero unresolved legacy trades and a repeated snapshot-bound operator
   baseline attestation before provisioning.
4. Require distinct regular single-link root, active, and snapshot database
   files; reject symlink and hard-link aliases before any runtime opens SQLite.
5. Validate active bankroll and horizon caps at config startup and against the
   manifest before `PaperTrader` opens SQLite.
6. Run `tests/test_paper_cohorts.py`.

## Task 2: Separate Active Admission From Aggregate Live Risk

**Files:** `trading/paper_cohorts.py`, `main.py`,
`tests/test_paper_cohorts.py`, `tests/test_main_startup.py`

1. Add failing tests for per-cohort provenance, summation, duplicate ID
   namespacing, read failures, and malformed marks.
2. Add read-only aggregate marking and persisted-bankroll reads; never
   instantiate a second runtime `PaperTrader`.
3. Keep BlendTask G7 on the active runtime cohort. Reserve aggregate state for
   live-readiness checks, where every cohort must independently clear the cap.
4. Use the immutable legacy snapshot as the aggregate legacy risk source and
   fail closed on root divergence pending explicit reconciliation.
5. Run targeted cohort and startup tests.

## Task 3: Runtime Wiring and Live Gate

**Files:** `main.py`, `trading/paper_trader.py`,
`tests/test_go_live_gates.py`, `tests/test_paper_accounting.py`

1. Add failing tests proving the active runtime DB is passed to `PaperTrader`,
   stats/discovery/WAL paths follow it, and live readiness still includes
   legacy.
2. Pass explicit active DB path and starting bankroll to the one runtime
   `PaperTrader`.
3. Keep aggregate checks fail-closed and independent-profit proof unchanged.
4. Run focused runtime, accounting, and gate tests.

## Task 4: Shared Horizon Guard

**Files:** `utils/market_horizon.py`, `config.py`,
`analysis/market_matcher.py`, `polymarket/paper_runtime.py`,
`tests/test_market_horizon.py`, `tests/test_market_matcher.py`,
`tests/polymarket/test_paper_runtime.py`

1. Add failing pure boundary tests: aware UTC/offset timestamps, exactly at
   cap, expired, too-far, missing, invalid, naive, invalid cap, invalid clock.
2. Add the evaluator with explicit rejection reasons.
3. Apply it at Kalshi/Polymarket ingress and candidate boundaries using the
   current cohort's paper horizon.
4. Run focused horizon and venue tests.

## Task 5: Review, Release, and Runtime Validation

**Files:** `VERSION`, `CHANGELOG.md`, affected tests and modules.

1. Run formatting/lint and all impacted test modules with the root virtualenv.
2. Obtain an independent diff review focused on persisted-state isolation,
   cross-cohort risk, and live gate bypasses.
3. Update version/changelog, commit exact paths, open/merge PR, and sync root
   without touching user-owned artifacts.
4. Restart only after merge. Verify process health, cohort identity, no live
   order, aggregate G7, and fresh decision receipt output.
5. Do not report profitability until settled fee-net active evidence exists.
