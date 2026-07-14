# Profit Accounting and Capital-Guard Shadow Ledger Design

Date: 2026-07-14

## Problem

The bot has not demonstrated repeatable profitability. The canonical paper
ledger currently contains 34 resolved trades, 8 wins, 26 losses, and gross
realized P&L of -$16.96. Thirteen open positions have $24.28 of entry cost and
currently mark to approximately $29.17, leaving paper equity near $37.93 and
start-to-current drawdown near 24.1%. The existing 20% G7 capital guard is
therefore correctly preventing new exposure.

Three measurement gaps prevent a defensible recovery decision:

1. Paper settlement P&L is gross of venue fees and has no explicit void state.
2. G7 marking is not uniformly executable liquidation accounting. Kalshi can
   use midpoint, ask, or last fallbacks, and venue routing treats every
   non-Kalshi row as Polymarket.
3. G7-blocked logs do not contain enough decision-time state to replay only the
   candidates that would otherwise have traded, net of fees and stateful risk
   constraints.

The daily review also uses a separate Kalshi-only snapshot helper and currently
labels all $23.50 of open Polymarket cost as unknown even though the G7 and
performance paths can price it. This is an operator decision-quality defect,
not the reason G7 is binding.

## Goal

Create one venue-complete, fee-versioned accounting surface and a disabled-by-
default append-only shadow ledger that can prove or refute positive
counterfactual expectancy while G7 continues to protect paper capital.

This design does not promise profitability. It creates the evidence and
accounting required before any capital, sizing, live-mode, or G7 policy change
can be considered.

## Non-Goals

- Do not lower, bypass, reset, or otherwise change G7.
- Do not reset or delete `data/paper_trades.db`.
- Do not change bankroll, sizing, paper/live mode, or order behavior.
- Do not backfill legacy fees as zero or with a current fee schedule.
- Do not enable shadow capture in the same PR that introduces it.
- Do not activate weather trading, generic search enforcement, or any live
  order path through this work.

## Authoritative Fee Contracts

Fee rules are versioned by venue and effective timestamp. Each captured or
newly recorded trade stores the exact schedule identity used for calculation.

### Kalshi

The July 7, 2026 official schedule defines the general taker fee as:

`round_up(M * 0.07 * C * P * (1 - P))`

where `M` is the series fee multiplier, `C` is contracts, and `P` is contract
price in dollars. The series metadata endpoint already exposes
`fee_multiplier` and `fee_type`; a missing, unsupported, or non-finite value is
unscorable. Fee and balance rounding follow the official API fee-rounding
contract. The bot's decision-time executable ask represents a taker fill.

Sources:

- `https://kalshi.com/docs/kalshi-fee-schedule.pdf`, effective 2026-07-07.
- `https://docs.kalshi.com/getting_started/fee_rounding`.

### Polymarket US

The schedule effective July 1, 2026 defines:

`Fee = theta * C * P * (1 - P)`

with taker `theta=0.06`, maker rebate `theta=-0.0125`, and banker's rounding to
the nearest cent. Shadow and paper buys at the executable ask are taker fills.
Volume rebates are not assumed; an actual known tier may be stored explicitly
in future versions.

Source: `https://docs.polymarket.us/fees`, effective 2026-07-01.

## Architecture

### 1. Canonical Money Types and Fee Registry

Add a small shared accounting module using `Decimal` and explicit cents. It
owns:

- `FeeScheduleId(venue, effective_at, version_hash)`;
- venue-native taker fee calculation and rounding;
- strict validation of price, contracts, multiplier, and schedule timestamp;
- immutable official-source metadata;
- an exhaustive `Venue` adapter registry.

Unknown venue, unknown schedule, missing Kalshi series multiplier, fractional
or non-finite values, and unsupported fee types return an explicit unscorable
result. They never silently become zero fees.

Add an immutable `MarketRef(venue, venue_market_id, alias)` and make it the
identity passed by valuation, portfolio, settlement, and shadow components.
Display tickers and slugs are aliases, not database keys. A same-text alias on
two venues must remain two distinct positions and settlements.

### 2. Executable Liquidation Marks

Replace heuristic venue branching with exhaustive `Venue` dispatch. Each
valuation run fetches one timestamped market snapshot per `(venue, market_id)`
and applies it to every open lot in that market.

- Held YES liquidation mark is the executable YES bid.
- Held NO liquidation mark is the executable NO bid.
- Crossed, empty, malformed, stale, or orientation-ambiguous books are
  unpriced.
- Liquidation value is gross bid proceeds minus the venue-native taker fee for
  selling at that bid.
- Missing price or fee provenance contributes zero marked value and records
  unknown cost, preserving fail-closed G7 behavior.
- Entry snapshots remain diagnostic only. They cannot inflate G7 equity.

G7 continues to compute equity as notional cash plus canonical net liquidation
value and continues to block above 20% drawdown.

### 3. Settlement Accounting

New paper rows store gross payout, entry fee, settlement or exit fee if any,
fee schedule identity, net P&L, and terminal state (`won`, `lost`, `void`).
Legacy rows retain explicit `fee_status=unknown`; their historical gross P&L is
never relabeled as net.

Settlement remains exactly once and atomic. A receipt binds venue, canonical
market identity, side orientation, authoritative outcome, and rules version.
Payload drift, identity mismatch, malformed outcomes, or an unsupported void
contract quarantines the settlement before any bankroll write.

The reconciler discovers work from persisted open positions, grouped by
`MarketRef`; entry/discovery feature flags may prevent new positions but may
not stop reconciliation of existing positions. `PaperTrader` and `Portfolio`
resolve by `(venue, venue_market_id)`, never by naked ticker. A schema migration
must reject ambiguous legacy identity rather than updating multiple venues.

The accounting transaction writes the terminal observation, gross payout,
fees/refunds, net P&L, bankroll credit, and a durable feedback outbox row before
commit. Keyword, source, calibration, and log consumers drain the outbox
idempotently, so a crash after financial commit cannot silently lose learning
feedback or double-credit the position.

Void/cancel/refund is not a win, loss, or unresolved row. It credits the
contractually correct refund and separately records any nonrefundable fee only
when the effective venue contract proves it.

### 4. Shared Reporting

`paper_performance_drilldown` consumes the canonical valuation result through
an injectable provider. Daily review and performance reporting render the same
venue-neutral fields:

- open entry cost;
- gross executable liquidation value;
- estimated exit fees;
- net liquidation value;
- unrealized net P&L;
- unknown/unscorable cost by venue and reason;
- fee-schedule versions and snapshot timestamp.

Old Kalshi-only keys may remain for one compatibility cycle but are derived
from the canonical result and labeled deprecated.

### 5. Capital-Guard Shadow Ledger

Add a dedicated SQLite database, `data/capital_guard_shadow.db`, owned by one
module and excluded from canonical paper accounting. Creation and writes are
disabled unless `ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE=true`.

At the BlendTask boundary, capture a row only when:

- paper mode is active;
- `G7_open_exposure_drawdown` is present;
- every other readiness failure is absent; and
- the candidate has a canonical side, executable ask, book depth, lifecycle
  identity, fee schedule, and complete sizing inputs.

The capture does not enqueue or call the executor. It records the complete
failure set even though only G7-only rows are evaluation eligible. Missing
depth, side, fee, or provenance is written as an unscorable diagnostic row,
never as an assumed fill.

Required immutable fields include:

- lifecycle ID, venue, canonical market ID, market family, and signal time;
- side, executed ask, displayed depth, raw snapshot hash, and price method;
- model probability for the executed side and correctly signed edge;
- readiness failure set and gate thresholds;
- Kelly inputs, proposed contracts, bankroll and open-exposure state;
- fee schedule identity, series multiplier, and estimated entry fee;
- code commit, config fingerprint, and model/capture provenance.

Tables are append-only. Corrections create new versioned rows. Unique keys
prevent duplicate lifecycle capture. Canonical paper and evidence databases are
never read-write dependencies of the capture transaction.

### 6. Stateful Settlement Replay

An offline read-only evaluator processes a predeclared window chronologically.
It re-applies cooldown, duplicate, family concentration, bankroll, sizing,
depth, and exposure state after every simulated fill and authoritative
settlement. It never sums independent hypothetical row P&L.

The report separates:

- all G7-blocked decisions;
- G7-only candidates;
- scorable/no-fill/unscorable candidates;
- gross and fee-net P&L;
- fees, turnover, capital at risk, maximum liquidation drawdown, and unresolved
  worst case;
- results by venue, family, source, and signal type;
- block-bootstrap 95% confidence intervals by market family and day.

Candidate rows that fail another gate are not attributed to G7.

## Safety Invariants

- Every supported `Venue` has exactly one valuation, fee, and settlement
  adapter; unknown venue fails closed.
- All lots in one market share one valuation snapshot per run.
- Money reconciliation uses exact decimal/cents arithmetic.
- Cash, entry debit, fees, payout, realized net P&L, open cost, marked value,
  and maximum remaining loss reconcile exactly.
- Missing fees, marks, outcomes, identity, or provenance are explicit and
  fail closed.
- Persisted open positions reconcile even when their venue's entry/discovery
  feature flag is disabled.
- Settlement and capture writes are transactional, idempotent, append-only,
  and safe under retry, crash, concurrent workers, and outbox replay.
- No code in this design can place or enqueue a live or paper trade.
- Runtime activation is a separate operator-reviewed step with an immediate
  false rollback.

## Verification

### Unit and Integration Coverage

- Fee matrices for both venues across prices, sides, quantities, rounding
  boundaries, effective dates, and invalid schedules.
- YES/NO liquidation with asymmetric, crossed, empty, stale, and malformed
  books.
- Exhaustive venue dispatch and mixed-venue portfolio conservation.
- Multi-lot snapshot consistency and API fetch deduplication.
- Settlement outcomes for won, lost, unresolved, void, 404, wrong identity,
  malformed/nonbinary values, and corrections.
- Same alias on two venues resolves only the exact `MarketRef`; existing
  Polymarket positions continue reconciling when new Polymarket entry is off.
- Transaction fault injection around every settlement and shadow write,
  duplicate delivery, concurrent reconcilers, outbox replay, and restart
  recovery.
- End-to-end mixed-venue accounting through G7 and both report surfaces.
- Disabled-by-default proof: no shadow database or writes when the flag is
  false.
- Isolation proof: shadow writes do not alter canonical paper, evidence, or
  matcher state.

### Promotion Standard

Shadow capture is observational and does not authorize G7 changes. Any later
G7 alternative requires a separate design and all of:

- a fixed, preregistered evaluation window;
- 100% candidate-universe classification;
- at least 95% scorable coverage by rows and proposed stake, with no venue
  below 90%;
- at least 30 independent resolved market families overall and 10 per affected
  venue;
- historically effective fee schedules and authoritative settlements;
- positive lower 95% confidence bound on incremental fee-net P&L;
- observed and stressed maximum liquidation drawdown no greater than 20%;
- no result dependent on unresolved mark-to-market value;
- independent financial-path review and explicit operator approval.

## Rollout

1. Land fee/accounting primitives and canonical reporting with no runtime flag.
2. Land disabled shadow schema, capture, and evaluator in a separate PR.
3. Run focused and full suites, replay-as-CI classification, and independent
   review.
4. Merge and sync without enabling capture.
5. In a separate authorized restart, enable shadow capture only; verify DB
   isolation, append-only behavior, and natural G7-only captures.
6. Continue settlement collection until the promotion standard is met or the
   hypothesis is rejected.

Any accounting mismatch, unsupported fee schedule, settlement backlog,
unpriced open cost, venue omission, or optimistic fallback blocks promotion and
keeps G7 unchanged.
