# Profit Accounting and Capital-Guard Shadow Ledger Design

**Status:** Revised after independent financial-path review on 2026-07-14.

## Problem

The current paper record does not establish a repeatable profitable strategy.
The latest verified state has 47 lifetime paper trades, 34 resolved, 8 wins, 26
losses, and gross realized P&L of -$16.96 before venue fees. Thirteen open
positions have $24.28 of cost and produce start-to-current drawdown near 24.1%.
The 20% G7 capital guard is therefore protecting capital correctly.

The accounting and evidence path is not yet decision-grade:

1. Settlement rows are selected with ticker aliases that can collide across
   venues and can differ from authoritative venue market IDs.
2. Paper P&L is gross of venue fees and has no explicit void/refund contract.
3. Kalshi fees depend on fill-level precision, signed revenue, and an order fee
   accumulator, not only quantity and price.
4. Open-position reporting and G7 do not share a reproducible, fee-aware
   executable liquidation model.
5. G7-blocked logs lack the complete state and later authoritative settlements
   required for stateful counterfactual replay.

## Goal

Build a venue-complete, versioned accounting and evidence system that can prove
or refute positive fee-net out-of-sample expectancy while the existing G7 guard
continues to protect paper capital. Implementation must not claim profitability
until realized, settled evidence meets the promotion standard below.

## Non-Goals

- Do not lower, bypass, reorder, or reset G7.
- Do not reset the paper bankroll or erase historical losses.
- Do not backfill legacy fees using a current schedule or assume zero fees.
- Do not change sizing, live order behavior, or enable weather trading.
- Do not activate settlement, accounting, G7-input, or shadow cutovers in the
  same commit that introduces them.

## Canonical Contracts

### Market Identity

`MarketRef` is the only settlement identity:

- `venue`: exhaustive `Venue` enum;
- `venue_market_id`: authoritative immutable venue identifier;
- `alias`: ticker or slug used only for display and legacy lookup.

`Position` and new paper rows persist `venue_market_id`. Portfolio storage may
remain keyed by alias for read compatibility, but settlement must select an
alias bucket and then match exact normalized venue plus exact canonical ID.
Ticker alone never authorizes a financial mutation.

Existing unresolved rows receive a nullable `venue_market_id`,
`identity_status`, and `quarantine_reason`. A read-only migration planner asks
the authoritative venue adapter to map each row. Apply mode writes only unique,
verified matches. Missing, conflicting, or drifting mappings are quarantined.
Settlement cutover is blocked until every unresolved row is mapped or
quarantined and the mapping report is independently reviewed.

### Settlement Observation

Venue adapters produce one frozen `SettlementObservation`:

- `market_ref`;
- terminal market outcome `yes`, `no`, or `void`;
- authoritative outcome payload;
- observed and effective timestamps;
- rules version and source identity;
- canonical payload hash;
- explicit refund contract for `void`;
- correction/supersession reference when supported.

`PaperTrader` derives position `won` or `lost` from the held side and the market
outcome. Malformed outcomes, identity drift, unsupported voids, or correction
conflicts quarantine before any bankroll write.

### Fee Context

Every fee calculation uses an immutable `FeeContext` containing:

- venue and pinned `FeeScheduleId(venue, effective_from, effective_to,
  artifact_sha256)`;
- fee type and multiplier/coefficient;
- maker/taker role;
- direct or non-direct account precision when relevant;
- fill quantity, price, and signed revenue;
- order identity and incoming fee-accumulator state;
- fill and schedule-effective timestamps.

Every `FeeQuote` persists base trade fee, rounding adjustment, rebate, net fee,
and outgoing accumulator state separately. Fractional quantities and subpenny
prices are supported. Unknown schedules, missing provenance, unsupported fee
types, non-finite values, and time gaps are explicitly unscorable.

The exact Kalshi schedule artifact must be fetched, checked into test fixtures
or pinned by immutable content hash, and its effective interval verified before
implementation. The current fee-rounding documentation requires direct account
precision of $0.0001, non-direct precision of $0.01, trade fee rounding to
$0.0001, per-fill rounding adjustments, and an order-level accumulator/rebate.
No implementation may rely on the previously assumed July 7 effective date.

Polymarket US uses the official schedule effective 2026-07-01. Taker and maker
coefficients, fill role, and banker's rounding to the nearest cent are stored as
schedule data rather than hard-coded call-site assumptions.

## Architecture

### 1. Venue-Qualified Settlement Safety

PR 1 adds identity and observation contracts, persists canonical IDs, migrates
or quarantines legacy open rows, and resolves with:

```sql
UPDATE paper_trades
SET ...
WHERE venue = ? AND venue_market_id = ? AND resolved = 0
```

The update must affect exactly the expected row set. Settlement reconciliation
for persisted positions is independent of entry/discovery flags, but that new
routing is behind a separate default-false cutover and T3 restart gate.

### 2. Report-Only Executable Liquidation

One exhaustive venue dispatcher obtains a timestamped executable held-side bid:

- held YES uses YES bid;
- held NO uses NO bid;
- no midpoint, ask, or last-trade fallback;
- value is bid proceeds less the applicable exit fee;
- missing identity, bid, depth, or fee provenance contributes zero value and an
  explicit unscorable reason.

PR 2 exposes `report_net_liquidation_value` and fee provenance to reports only.
It does not overwrite the current `marked_value` key consumed by G7. A later T3
G7-input cutover requires its own replay, review, approval, restart, rollback,
and pre/post acceptance evidence.

### 3. Additive Fee-Net Paper Ledger

New schema is additive. A disabled accounting mode records proposed fee-net
entry and settlement rows beside canonical gross accounting without changing
canonical bankroll or Kelly inputs. Every position stores an immutable
`accounting_version`; the feature flag gates admission of new fee-net entries,
not settlement of positions already admitted under that version.

Entry accounting is one `BEGIN IMMEDIATE` transaction containing the trade row,
cost debit, entry fee debit, schedule/fill identity, and bankroll-after value.
Settlement is one `BEGIN IMMEDIATE` transaction containing a unique receipt,
compare-and-set `resolved=0`, exact row-count assertion, payout/refund, fee
components, net P&L, bankroll-after value, and immutable outbox event.

The outbox remains append-only. A same-database consumer commits its effect and
receipt atomically. Any external consumer must accept `outbox_id` as a durable
idempotency key and deduplicate before applying its effect; the local receipt is
an audit record, not the source of exactly-once semantics. A consumer without
either contract is explicitly at-least-once and cannot be used for a financial
effect.

Legacy matrix:

- resolved legacy rows remain unchanged with gross P&L and null net P&L;
- open legacy rows may settle authoritatively for contractual gross payout;
- legacy fees remain `unknown` and net P&L remains null;
- no estimated entry fee is ever debited retroactively;
- ambiguous legacy identity remains quarantined and uncredited.

Fee-net accounting changes canonical cash and sizing, so its cutover is a
separate T3 operator action after conservation replay and database snapshot. An
immediate false rollback stops new fee-net entry admission. Open rows retain
their immutable version and continue through the matching fee-net won/lost/void
settlement path after flag-off and restart; an unavailable version or schedule
quarantines instead of falling back to legacy gross settlement. Rollback never
rewrites already committed financial history.

### 4. Shared Reporting

Daily review and performance drilldown consume the same injected mark provider
and show, by venue:

- open cost;
- gross executable value;
- estimated exit fees;
- report-only net liquidation value;
- unrealized fee-net P&L;
- unknown/unscorable cost and reasons;
- fee schedule hashes and snapshot timestamp.

### 5. Isolated Capital-Guard Shadow Ledger

`data/capital_guard_shadow.db` is owned by one schema module. New candidate
admission is disabled unless `ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE=true`.
Settlement drainage has a separate
`ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION` flag so turning capture off
does not freeze already-admitted candidates. It never writes canonical paper,
evidence, calibration, or feedback databases.

Capture occurs only after canonical lifecycle telemetry has been emitted and
uses already available decision-time data. It performs no network calls. Store
lock, timeout, cancellation, logger, and capture failures cannot change queueing
or `BlendTaskResult`.

Every candidate records the complete readiness failure set. Only candidates
whose sole failure is `G7_open_exposure_drawdown` are evaluation eligible.
Incomplete rows are retained as unscorable diagnostics.

### 6. Authoritative Shadow Settlement and Replay

An idempotent shadow-settlement collector uses the same venue adapters and
`SettlementObservation` contract, but writes only the shadow database. It
records identity drift, voids, corrections, payload hashes, and unresolved
states without touching canonical rows. Capture can be false while collection
continues until the persisted unresolved backlog reaches zero.

Replay is chronological and stateful. It applies hypothetical entries,
fill-level fees, open exposure, executable marks, settlement, bankroll, and
drawdown in order. It never sums independent row P&L. Another-gate failures are
reported but never attributed to G7.

## Safety Invariants

- Every supported venue has one identity, fee, valuation, and settlement path.
- Money and quantity use `Decimal` or fixed-point storage; no float arithmetic.
- Missing fee, mark, outcome, identity, or provenance fails closed.
- Entry, settlement, and capture writes are transactional and idempotent.
- Each runtime-affecting or schema PR is T3. No safety-fix downgrade exception.
- Every T3 PR gets classifier evidence, full replay disposition, independent
  financial review, explicit operator approval, protected restart, acceptance
  checks, and rollback instructions.
- Runtime databases and state artifacts remain outside commits.

## Verification

Tests cover:

- alias/ID divergence and cross-venue collisions;
- legacy mapping, quarantine, and identity drift;
- `yes`, `no`, `void`, correction, malformed, and duplicate observations;
- official fractional/subpenny multi-fill fee examples and fill splitting;
- direct/non-direct precision and accumulator/rebate state;
- report-only marks versus unchanged G7 input;
- entry and settlement fault injection around every write;
- outbox crash before and after consumer effects;
- enable fee-net entry, disable admission, restart, then settle by persisted
  accounting version;
- shadow disabled state, SQLite lock/timeout/cancellation, and DB isolation;
- shadow settlement identity, drift, void, correction, and retry;
- capture enabled, candidate persisted, capture disabled, then settlement and
  restart backlog drainage;
- chronological replay conservation and unresolved worst case.

## Promotion Standard

Shadow capture never authorizes a G7 change by itself. A later alternative must
use a fixed preregistered out-of-sample window and satisfy all of:

- only G7-only candidates;
- stateful chronological replay with pinned historical fee schedules;
- authoritative settlements and no assumed outcomes;
- at least 30 independent resolved market families overall;
- at least 10 resolved families per affected venue;
- at least 95% scorable coverage by rows and stake overall;
- no affected venue below 90% coverage;
- positive lower 95% confidence bound on incremental fee-net P&L;
- stressed maximum liquidation drawdown at or below 20%;
- no venue with negative fee-net expectancy;
- independent financial review and a separate operator-approved T3 design.

Until these conditions are met, G7 remains unchanged and the bot must not be
described as repeatably profitable.

## Rollout

1. Land PR 1 identity/settlement safety with cutover disabled.
2. Dry-run and review canonical-ID mapping; snapshot the DB before apply mode.
3. Activate persisted-position reconciliation in a separate protected restart.
4. Land PR 2 fee primitives and report-only executable liquidation.
5. Land PR 3 additive fee-net ledger with canonical accounting cutover disabled.
6. Replay conservation, snapshot, then activate new fee-net entry admission;
   persisted fee-net rows remain version-routed after any later rollback.
7. Land PR 4 disabled shadow capture, settlement collector, and replay.
8. Activate shadow capture and settlement collection only after independent
   review; a capture rollback leaves collection draining existing candidates.
9. Collect the preregistered evidence window; keep G7 unchanged unless the full
   promotion standard passes.
