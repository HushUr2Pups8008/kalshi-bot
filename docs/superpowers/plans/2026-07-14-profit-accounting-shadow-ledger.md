# Profit Accounting and Capital-Guard Shadow Ledger Implementation Plan

> Revised 2026-07-14 after independent financial-path review. Execute in order.

**Goal:** Produce venue-complete, fee-versioned paper accounting and an isolated
shadow ledger that measures G7-only counterfactual trades without silently
changing capital, sizing, admission, or live order behavior.

**Architecture:** Four T3 PRs. PR 1 establishes canonical identity and settlement
safety with routing cutover disabled. PR 2 adds pinned fill-level fee primitives
and report-only liquidation. PR 3 adds atomic fee-net accounting with canonical
cutover disabled. PR 4 adds disabled shadow capture, authoritative settlement,
and stateful replay. Every activation is a later protected operator action.

**Tech:** Python 3.14, `Decimal`, frozen dataclasses, SQLite, pytest, Ruff.

## Global Constraints

- Preserve `G7_MAX_OPEN_EXPOSURE_DRAWDOWN_PCT=0.20` and failure ordering.
- Never reset or rewrite `data/paper_trades.db`; never infer legacy fees.
- Use exact `Decimal` or fixed-point storage for money and quantity.
- Unknown venue, ID, book, fee schedule, or outcome fails closed.
- Treat every schema or runtime financial-path PR as T3. No downgrade exemption.
- Each activation needs an explicit flag, pre-change snapshot, classifier result,
  replay disposition, independent review, operator approval, protected restart,
  acceptance checks, and immediate false rollback.
- Runtime artifacts stay outside commits: `data/*.db`,
  `data/matcher_token_weights.json`, `logs/backups/`, and `logs/state/`.
- Do not enable weather trading or generic search enforcement in these PRs.

## PR 1: Venue-Qualified Settlement Safety

### Task 1: Store Canonical Market Identity in Memory

**Files**

- Modify: `trading/venue.py`
- Modify: `trading/portfolio.py`
- Test: `tests/test_portfolio.py`

**Contract**

- Frozen `MarketRef(venue, venue_market_id, alias)` rejects empty IDs.
- `Position` stores nullable `venue_market_id` for legacy compatibility.
- `Portfolio` remains keyed by display alias.
- `resolve(MarketRef)` selects the alias bucket, then exact venue and canonical
  ID. A legacy-null ID fails closed. `resolve(str)` remains read-compatible.

**TDD**

1. Add collision tests where two venues share an alias.
2. Add a regression where `alias != venue_market_id`.
3. Add legacy-null fail-closed coverage.
4. Run RED, implement minimal storage/filtering, run GREEN.

```bash
.venv/bin/python -m pytest tests/test_portfolio.py -q
.venv/bin/python -m pytest tests/test_portfolio.py \
  tests/test_paper_trader_venue.py \
  tests/polymarket/test_settlement_reconciler.py -q
.venv/bin/ruff check trading/venue.py trading/portfolio.py tests/test_portfolio.py
git diff --check
```

Commit: `fix: qualify portfolio positions by canonical market id`

### Task 2: Introduce SettlementObservation

**Files**

- Create: `trading/settlement.py`
- Modify: `kalshi/settlement.py` or the existing Kalshi settlement adapter
- Modify: `polymarket/settlement_reconciler.py`
- Create: `tests/test_settlement_observation.py`
- Modify: venue settlement tests

**Contract**

- Frozen observation contains `MarketRef`, market outcome `yes|no|void`, raw
  authoritative outcome, observed/effective timestamps, rules/source identity,
  payload SHA-256, explicit void refund contract, and optional supersession ID.
- Venue adapters normalize into the same contract.
- Malformed outcomes, mismatched identity, unsupported void, and correction
  conflicts are typed failures, never booleans.

**TDD**

1. Write RED adapter fixtures for yes, no, void, mismatch, malformed, and drift.
2. Implement validation and deterministic payload hashing.
3. Prove identical payloads hash identically and changed payloads conflict.

```bash
.venv/bin/python -m pytest tests/test_settlement_observation.py \
  tests/polymarket/test_settlement_reconciler.py -q
.venv/bin/ruff check trading/settlement.py polymarket/settlement_reconciler.py \
  tests/test_settlement_observation.py
git diff --check
```

Commit: `feat: normalize authoritative settlement observations`

### Task 3: Add Canonical-ID Migration and Quarantine

**Files**

- Modify: `trading/paper_trader.py`
- Create: `scripts/migrate_paper_market_identity.py`
- Create: `tests/test_paper_identity_migration.py`
- Modify: `scripts/README.md`

**Contract**

- Add nullable `venue_market_id`, `identity_status`, and `quarantine_reason` to
  fresh-database DDL only; normal `PaperTrader` startup must not auto-ALTER an
  existing database.
- Default command is read-only dry run.
- The Polymarket adapter exposes all exact candidates so uniqueness is proven;
  the migration must not call its private HTTP method or trust a first match.
- Apply mode resolves all lookups first, then adds all columns and writes only
  unique venue-adapter mappings in one `BEGIN IMMEDIATE` transaction.
- Transport failure or a single clean miss remains unresolved and unwritten.
- Deterministic conflicts or repeated confirmed absence emit quarantine
  candidates. Only a separate reviewed `--apply-quarantine` mode writes them;
  exact-match repair is a separate reviewed operation.
- Settled legacy rows remain unchanged.
- Emit machine-readable counts and row identities for mapped, quarantined, and
  unresolved rows; never print secrets or mutate other databases.

**TDD**

1. RED tests: startup no-ALTER, unique match, transient/no match, multiple
   matches, alias/ID divergence, repeated absence, explicit quarantine, repair,
   existing conflicting ID, transaction drift, rollback, and idempotent retry.
2. Implement additive migration and planner.
3. Prove dry run leaves DB and sidecar files byte-for-byte unchanged; prove DDL
   and row updates roll back together after every injected failure.

```bash
.venv/bin/python -m pytest tests/test_paper_identity_migration.py \
  tests/test_paper_trader.py -q
.venv/bin/ruff check trading/paper_trader.py \
  scripts/migrate_paper_market_identity.py tests/test_paper_identity_migration.py
git diff --check
```

Commit: `feat: migrate and quarantine paper market identity`

### Task 4: Resolve by Venue and Canonical ID

**Files**

- Modify: `trading/paper_trader.py`
- Modify: `polymarket/settlement_reconciler.py`
- Modify: Kalshi settlement routing files
- Test: `tests/test_paper_trader_venue.py`
- Test: `tests/polymarket/test_settlement_reconciler.py`

**Contract**

- `PaperTrader` consumes `SettlementObservation`, not `resolved_yes: bool`.
- Financial update filters `venue`, `venue_market_id`, and `resolved=0`.
- Row-count mismatch rolls back and quarantines; no alias-only fallback.
- Portfolio closure receives the exact `MarketRef`.
- Legacy-null and quarantined rows cannot mutate bankroll.

**TDD**

1. RED tests for cross-venue alias collision, wrong ID, duplicate observation,
   two-row collision, void, and correction conflict.
2. Implement exact compare-and-set routing.
3. Run related settlement and paper suites.

```bash
.venv/bin/python -m pytest tests/test_paper_trader_venue.py \
  tests/polymarket/test_settlement_reconciler.py \
  tests/test_paper_trader.py -q
.venv/bin/ruff check trading/paper_trader.py \
  polymarket/settlement_reconciler.py tests/test_paper_trader_venue.py
git diff --check
```

Commit: `fix: settle paper rows by authoritative market id`

### Task 5: Decouple Persisted Settlement from Entry Flags

**Files**

- Modify: `config.py`
- Modify: `.env.example`
- Modify: `main.py`
- Modify: `scripts/botcheck.py`
- Test: `tests/test_main_pipeline.py`
- Test: `tests/test_botcheck.py`

**Contract**

- Add `ENABLE_PERSISTED_POSITION_SETTLEMENT_RECONCILIATION=false`.
- When false, current routing is unchanged.
- When true, unresolved mapped positions reconcile even when entry/discovery is
  disabled. Entry, discovery, and startup probes keep their existing flags.
- Botcheck reports flag state, mapped/quarantined backlog, and last observation.

**TDD**

1. RED tests for false parity, true persisted routing, and no entry-path leak.
2. Implement narrow routing condition.
3. Prove false mode produces the same task graph as baseline.

```bash
.venv/bin/python -m pytest tests/test_main_pipeline.py \
  tests/test_botcheck.py -k 'settlement or polymarket or persisted' -q
.venv/bin/ruff check config.py main.py scripts/botcheck.py \
  tests/test_main_pipeline.py tests/test_botcheck.py
git diff --check
```

Commit: `feat: gate persisted-position settlement routing`

### PR 1 Gate

1. Run focused suites plus full paper/settlement tests.
2. Run T3 classifier and replay disposition.
3. Obtain independent financial review.
4. Open protected PR; merge only after CI and explicit operator approval.
5. Sync main. Snapshot canonical DB and run identity dry run.
6. Review mapping report; apply migration only with zero unexplained rows.
7. Enable the settlement flag in a separate protected restart.
8. Roll back flag to false on mutation count, quarantine, or backlog regression.

## PR 2: Pinned Fees and Report-Only Liquidation

### Task 6: Pin Official Fee Artifacts and Implement Fill-Level Fees

**Files**

- Create: `trading/fees.py`
- Create: `tests/fixtures/fees/manifest.json`
- Add: immutable official fee fixtures permitted by source terms
- Create: `tests/test_fees.py`

**Contract**

- Manifest stores venue, effective interval, source URL, retrieval timestamp,
  SHA-256, and supported fee types.
- `FeeContext` includes account precision, role, quantity, price, signed revenue,
  order identity, accumulator state, multiplier/coefficient, and timestamp.
- `FeeQuote` separates base fee, rounding adjustment, rebate, net fee, and next
  accumulator state.
- Kalshi supports fractional/subpenny multi-fill examples and direct/non-direct
  precision. Polymarket supports taker/maker coefficients and half-even cents.
- Any schedule gap or provenance mismatch is unscorable.

**TDD**

1. Pin and hash the current official artifacts; verify effective dates manually.
2. Transcribe official examples as RED table tests, including fill splitting.
3. Implement pure calculators and an exhaustive venue dispatcher.
4. Test boundaries, negative values, NaN/infinity, and accumulator replay.

```bash
.venv/bin/python -m pytest tests/test_fees.py -q
.venv/bin/ruff check trading/fees.py tests/test_fees.py
git diff --check
```

Commit: `feat: add pinned fill-level venue fee accounting`

### Task 7: Preserve Venue Contract and Fill Provenance

**Files**

- Modify: Kalshi API/model normalization files discovered by `rg`
- Modify: `kalshi/series_metadata.py`
- Modify: `polymarket/models.py`
- Modify: `polymarket/normalizer.py`
- Test: `tests/test_series_metadata.py`
- Test: `tests/polymarket/test_normalizer.py`

**Contract**

- Both venues preserve canonical market ID, fee type/multiplier/coefficient,
  effective timestamp, quantity step, price tick, fill role, side/token IDs,
  source payload hash, and snapshot timestamp.
- Unsupported or absent fields remain explicit, not venue defaults.

**TDD**

1. RED normalized-fixture tests for every required field and missing provenance.
2. Implement additive fields and parsing.
3. Prove old fixtures remain readable with unscorable status.

```bash
.venv/bin/python -m pytest tests/test_series_metadata.py \
  tests/polymarket/test_normalizer.py -q
.venv/bin/ruff check kalshi polymarket tests/test_series_metadata.py \
  tests/polymarket/test_normalizer.py
git diff --check
```

Commit: `feat: retain venue fee and contract provenance`

### Task 8: Add Report-Only Executable Liquidation

**Files**

- Modify: `scripts/mark_open_positions.py`
- Modify: `scripts/paper_performance_drilldown.py`
- Modify: `scripts/daily_review.py`
- Test: related script tests

**Contract**

- Exhaustive venue dispatch; no heuristic fallback.
- Held YES uses YES bid; held NO uses NO bid.
- No midpoint, ask, or last fallback.
- Report keys include gross bid value, estimated exit fees, report-only net value,
  unscorable cost/reasons, schedule hashes, and `as_of`.
- Preserve the existing G7 `marked_value` input unchanged in this PR.
- Reports share one injected provider and timestamp.

**TDD**

1. RED tests for held-side bids, fee subtraction, unknown=zero, API failure,
   snapshot sharing, venue exhaustiveness, and unchanged G7 input.
2. Implement report-only keys and shared provider.
3. Prove daily review no longer labels priced Polymarket cost as unknown.

```bash
.venv/bin/python -m pytest tests/test_mark_open_positions.py \
  tests/test_paper_performance_drilldown.py tests/test_daily_review.py \
  tests/test_trade_readiness_gate.py -q
.venv/bin/ruff check scripts/mark_open_positions.py \
  scripts/paper_performance_drilldown.py scripts/daily_review.py
git diff --check
```

Commit: `fix: report fee-net executable liquidation by venue`

### PR 2 Gate

Classify T3, run all fee/report/G7 tests, obtain independent financial review,
merge through protected CI, and restart with no G7-input cutover. Compare pre/post
G7 decisions byte-for-byte. Any future G7 mark cutover requires a new T3 design.

## PR 3: Atomic Fee-Net Paper Accounting

### Task 9: Add Disabled Additive Accounting Schema

**Files**

- Modify: `config.py`, `.env.example`, `trading/paper_trader.py`
- Create: `trading/paper_accounting.py`
- Create: `tests/test_paper_accounting.py`

**Contract**

- Add `ENABLE_FEE_NET_PAPER_ACCOUNTING=false`; it gates new entry admission.
- Add nullable fill, schedule, fee-component, gross/net, refund, terminal-state,
  settlement-receipt, observation-hash, and immutable `accounting_version`
  columns.
- False mode preserves canonical cash/P&L/Kelly behavior.
- Existing fee-net rows continue version-matched settlement when the flag is
  false; missing implementation or schedule quarantines without gross fallback.
- Legacy matrix from the design is enforced.

**TDD**

1. RED schema, legacy, false-parity, migration rollback, and persisted-version
   dispatch tests.
2. Implement additive schema and typed accounting records.
3. Prove resolved legacy rows and bankroll are unchanged.

Commit: `feat: add disabled fee-net paper accounting schema`

### Task 10: Make Entry Accounting Atomic

**Files**

- Modify: `trading/paper_trader.py`, `trading/paper_accounting.py`
- Modify: `tests/test_paper_accounting.py`, `tests/test_paper_trader.py`

**Contract**

- Under enabled mode, one `BEGIN IMMEDIATE` transaction commits trade row, cost
  debit, entry fee components, provenance, and bankroll-after.
- Faults before any commit roll back everything; retries are idempotent.
- Disabled mode blocks new fee-net entries but does not alter the settlement
  contract of already-persisted fee-net positions.

**TDD:** inject faults after every write and assert exact conservation. Add
enable-entry -> disable -> restart coverage before settlement tests.

Commit: `fix: commit paper entry fees and cash atomically`

### Task 11: Make Settlement and Feedback Atomic

**Files**

- Create: `trading/settlement_accounting.py`
- Modify: `trading/paper_trader.py`, settlement reconcilers
- Create: `tests/test_settlement_accounting.py`

**Contract**

- Unique settlement receipt and compare-and-set `resolved=0`.
- One transaction commits payout/refund, fee components, net P&L,
  bankroll-after, and immutable outbox event.
- Same-database consumers commit effect plus receipt atomically. External
  consumers must use `outbox_id` as their target-side idempotency key before the
  local append-only receipt is written. Non-idempotent external consumers are
  at-least-once and prohibited from financial effects.
- Crash before/after consumer effects is tested against those concrete
  idempotency contracts.
- Legacy open rows settle gross with null net P&L and no retroactive fee.
- Fee-net rows settle by persisted `accounting_version` even after admission is
  disabled or the process restarts; they never fall back to the legacy path.

**TDD:** won/lost/void/correction, duplicate/concurrent workers, every write
fault, same-DB atomic consumer, external idempotency key, consumer crash
boundaries, unsupported legacy fee, row-count mismatch, and
enable-entry -> disable -> restart -> settle.

Commit: `fix: settle fee-net paper accounting exactly once`

### PR 3 Gate

1. Run full paper, settlement, accounting, sizing, and G7 suites.
2. Replay a production DB copy in disabled and enabled modes; prove conservation.
3. Classify T3 and obtain independent financial review.
4. Merge protected with accounting disabled; restart and prove false parity.
5. Snapshot DB, then enable accounting in a separate approved restart.
6. Roll back entry admission to false on any balance, sizing, fee, or outbox
   mismatch; verify all existing fee-net rows remain on version-matched
   settlement and quarantine any unsupported version.

## PR 4: Disabled Capital-Guard Shadow Evidence

### Task 12: Add Isolated Append-Only Shadow Store

**Files**

- Create: `trading/capital_guard_shadow.py`
- Modify: `config.py`, `.env.example`, `scripts/botcheck.py`
- Create: `tests/test_capital_guard_shadow.py`

Add `ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE=false` and
`ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION=false`; candidate, conflict,
observation, settlement, and evaluation tables live only in
`data/capital_guard_shadow.db`. Both flags false and no existing backlog creates
no DB. Collection may open an existing DB while capture is false. Test idempotency,
concurrent writers, transaction faults, and canonical DB byte hashes.

Commit: `feat: add disabled capital guard shadow store`

### Task 13: Capture Fully Specified G7-Only Candidates

**Files**

- Modify: `tasks/blend_task.py`
- Modify: `models/analysis.py`
- Modify: shadow and blend tests

Emit canonical lifecycle telemetry first. Then perform bounded, fail-isolated
capture from already available data only; no network calls. Capture the complete
failure set, side, depth, edge, identity, fee/fill provenance, sizing inputs, and
decision timestamp. Only G7-only rows are eligible. Test flag off, other-gate
failure, missing provenance, duplicate lifecycle, SQLite lock/timeout,
cancellation, logger failure, and capture exception with identical queue/result.

Commit: `feat: capture isolated G7-only decision evidence`

### Task 14: Collect Authoritative Shadow Settlements

**Files**

- Create: `tasks/capital_guard_shadow_settlement.py`
- Modify: `trading/capital_guard_shadow.py`, `main.py`
- Create: `tests/test_capital_guard_shadow_settlement.py`

Use the shared venue adapters and `SettlementObservation`. Write only shadow DB.
Test identity mismatch/drift, yes/no/void, correction, 404/unresolved, duplicate,
restart, and canonical DB byte hashes. Collection is independently gated and
continues for an existing DB when capture admission is false. Test capture true
-> persist candidate -> capture false -> yes/no/void settle -> restart -> zero
unresolved backlog.

Commit: `feat: collect authoritative shadow settlements`

### Task 15: Add Stateful Fee-Net Replay

**Files**

- Create: `scripts/capital_guard_shadow_replay.py`
- Create: `tests/test_capital_guard_shadow_replay.py`
- Modify: `scripts/README.md`

Chronologically apply eligible entries, fill-level fees, open exposure, marks,
settlements, bankroll, and drawdown. Emit coverage by rows/stake/venue, resolved
families, gross/net P&L, fees, turnover, risk, unresolved worst case,
family/day block-bootstrap CI, and explicit promotion failures. Another-gate
rows are diagnostics only. Test ordering, fee-version change, void/correction,
exposure cap, unresolved worst case, and non-independent family grouping.

Commit: `feat: replay settled capital guard counterfactuals`

### PR 4 and Runtime Gate

1. Run shadow, blend, settlement, replay, botcheck, and isolation suites.
2. Classify T3; obtain independent financial review; merge through protected CI.
3. Restart with both flags false and prove no DB creation/writes.
4. In a separate approved restart enable settlement collection, then capture.
5. Verify append-only isolation, natural G7-only capture, and collector progress.
6. On capture-path failure set capture false while collection keeps draining
   persisted candidates. Disable collection only for collector integrity or
   canonical-isolation failure. Record both flag states in acceptance evidence.

## Evidence Gate

Do not propose a G7 change until a preregistered fixed window has 30 independent
resolved families overall, 10 per affected venue, at least 95% scorable rows and
stake overall, no venue below 90%, positive lower 95% confidence bound on
incremental fee-net P&L, stressed liquidation drawdown at or below 20%, and no
venue with negative fee-net expectancy. Failure keeps G7 unchanged and records
the hypothesis as rejected or insufficient.
