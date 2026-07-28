# Active Paper Cohort and Horizon Guard Design

## Objective

Resume decision-useful paper collection without rewriting the legacy portfolio,
while preventing a new cohort from hiding legacy exposure or qualifying a live
transition. This creates evidence infrastructure, not a profitability claim.

## Current State

- `data/paper_trades.db` is the legacy source portfolio. Cutover copies it into
  a hash-verified immutable snapshot owned by the active cohort; the source has
  unresolved exposure and incomplete fee-net settlement proof.
- The runtime currently owns one writable `PaperTrader`; constructing a second
  runtime trader is rejected and would also attempt schema/bootstrap writes.
- Kalshi applies a permissive 30-day filter. Polymarket has no equivalent
  admission horizon, and malformed Kalshi close times can pass through.

## Decisions

### Cohort Identity and Storage

- `legacy` is the default cohort. Its database remains
  `data/paper_trades.db`; existing deployments keep their current behavior.
- An explicit non-legacy paper cohort resolves to a separate database below
  `data/paper_cohorts/<cohort-id>/paper_trades.db`.
- Cohort identifiers are validated as stable filesystem-safe tokens. The
  runtime can write only its selected cohort database. Once an active cohort is
  provisioned, legacy runtime and CLI construction are rejected rather than
  letting a stale config mutate the predecessor.
- A cohort receives an explicit immutable starting bankroll. A new cohort may
  not silently reuse `BANKROLL`, because that would create two full paper
  accounts and make aggregate risk meaningless.
- A non-legacy cohort requires a pre-created immutable manifest before runtime
  opens SQLite. It binds cohort ID, database path, starting bankroll, horizon,
  legacy snapshot fingerprint, and database identity. Runtime never creates or
  rewrites that manifest, and an initialized cohort refuses a missing core
  schema instead of rebootstrapping it.
- The legacy bankroll baseline is an explicitly repeated operator attestation,
  canonicalized and hashed with the cutover snapshot. It is marked
  `operator_attested_unverified`: current `BANKROLL` is never used as proof of
  historical capital and the attestation cannot satisfy a live-release gate.
- Provisioning refuses a legacy snapshot with unresolved paper trades. Legacy
  settlement must complete through the existing reviewed path before cutover,
  rather than freezing a new active cohort behind an unreconciled predecessor.
- Legacy roots, active databases, and cutover snapshots must be distinct,
  regular, single-link files. Symlinks, hard links, and symlinked cohort
  directories fail closed before a runtime opens SQLite.
- Once an active cohort exists, runtime and CLI callers must use a
  manifest-bound active database. An arbitrary database path cannot become a
  parallel paper or go-live route.

### Risk and Live Readiness

- Active paper G7 evaluates the active cohort only, so legacy unresolved
  exposure cannot permanently suppress fresh evidence collection.
- Live readiness evaluates every cohort independently, preserves per-cohort
  provenance, and fails closed on unreadable or malformed data. A weighted
  aggregate may be reported, but may never dilute a cohort that breaches its
  own drawdown threshold.
- Live-readiness checks retain the independent realized-profit-evidence gate.
  A non-legacy active cohort cannot qualify live trading by itself; legacy
  exposure and settlement state remain part of the hard gate.
- No database rows are moved, reset, settled synthetically, or merged across
  cohorts. Row identifiers are namespaced by cohort in aggregate views.
- The immutable legacy snapshot, not mutable `data/paper_trades.db`, is the
  legacy risk source. Every active manifest must agree on its snapshot and
  baseline attestation. Any legacy-root divergence is reconciliation-required
  and fails closed rather than hiding pre-cutover exposure.

### Admission Horizon

- A pure horizon evaluator accepts only aware close timestamps and a valid,
  positive cap. Missing, invalid, expired, and too-far markets are rejected.
- The shared universe cap remains 30 days. A non-legacy active paper cohort
  uses a tighter explicit cap, defaulting to 14 days and never exceeding the
  universe cap.
- Both Kalshi and Polymarket enforce the evaluator at cache ingress and again
  at candidate selection, so stale caches and alternate code paths cannot
  bypass it.

## Non-Goals

- No live-trading enablement, Kalshi order placement, or position resizing.
- No legacy settlement mutation, manual resolution, database reset, or balance
  transfer.
- No claim of profit, repeatability, or go-live readiness. Those require enough
  settled, fee-net, independently corroborated active evidence.

## Acceptance Criteria

1. Default legacy configuration leaves the existing database path unchanged.
2. An explicit active cohort gets an isolated path and explicit bankroll.
3. Active G7 uses the active cohort only; live readiness includes the immutable
   legacy snapshot plus every active cohort and fails closed when any cohort or
   root-reconciliation check cannot be evaluated.
4. Both venues reject invalid, expired, and beyond-horizon markets before
   routing.
5. Focused unit and integration tests cover storage resolution, aggregate risk,
   go-live failure, and horizon boundaries.
6. Provisioning fails when the legacy source still has unresolved trades or its
   runtime lock is held.

## Operational Cutover

After merge and validation, reconcile legacy to zero unresolved trades through
the reviewed settlement path, then stop the legacy runtime and explicitly
initialize the cohort with a repeated operator-attested legacy baseline. Make
the matching operator configuration change that names the cohort and allocates
its paper bankroll. Observe fresh paper trades and terminal settlements. A
legacy-root change requires explicit reconciliation before restart. Keep live
mode disabled until the independent evidence gate passes with enough fee-net
data.
