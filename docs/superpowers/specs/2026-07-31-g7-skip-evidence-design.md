# G7 Skip Evidence Design

## Objective

Produce decision-time evidence for G7-blocked opportunities so future review can
distinguish observed insufficient executable liquidity from unavailable or
unqueried market data. This supports profit-oriented paper admission analysis;
it does not change G7, produce a trade candidate, send an order, or establish
profitability.

## Current Evidence Gap

The current report aggregates risk-gate skips. The latest structured logs contain
G7 momentum and zero-liquidity failures, but not a durable executable-liquidity
receipt with quantity/notional, source, observation time, or payload hash.
`CapitalGuardCandidate` cannot be reused because it is intentionally restricted
to open-exposure-drawdown captures and executable shadow candidates.

## Chosen Boundary

Add a separate, isolated append-only store at `data/g7_skip_evidence.db`.
It is never read by admission, execution, paper trading, settlement, sizing, or
feedback. Its only production writer is a default-off sink invoked after a
G7-blocked blend decision is already final.

The store records a canonical receipt with:

- decision identity: lifecycle, UTC decision/capture times, venue, market,
  selected side, ordered G7 failures, and final block reason;
- G7 threshold and observed executable quantity/notional when the existing
  order-book query completed;
- provenance: UTC `as_of`, source, and raw-payload SHA-256;
- a fail-closed evidence state: `observed`, `unavailable`, or `not_queried`.

`observed` requires the existing complete executable-liquidity metadata.
`unavailable` and `not_queried` carry a typed reason and cannot be interpreted
as zero liquidity. The current provider does not retain raw book levels, so this
slice stores its existing verified quantity/notional/time/hash contract rather
than fabricating a book snapshot.
Rows and schema are append-only. Exact replay is idempotent; a conflicting retry
is rejected.

## Shared Liquidity Contract

Reuse `BlendTask`'s existing `g7_execution_liquidity` metadata: source, side,
limit/best price, executable quantity/notional, UTC `as_of`, payload hash, and
the typed unavailable reason. Capture records the exact canonical projection of
that decision-time metadata. It does not refetch, infer a book from a later
snapshot, or change execution behavior.

For a G7 momentum block that occurs before the liquidity query, capture a
`not_queried` receipt. For a reader exception, preserve `unavailable`. The
existing fail-closed zero-liquidity result remains unchanged, but report output
will no longer conflate it with observed zero depth.

## Runtime Wiring

Add `ENABLE_G7_SKIP_EVIDENCE_CAPTURE`, default `false`. When enabled, `main.py`
constructs the isolated sink and passes it to `BlendTask`. The skipped-decision
hook invokes the sink only when at least one final non-drawdown failure starts
with `G7_`.
Capture errors are logged and never change a gate result, queue action, paper
trade, or live-order decision. No collector task exists for this store.

`botcheck` reports the flag and isolated-store integrity/counts read-only.

## Reporting

Add a read-only report that validates receipts and classifies each row as:

- `observed_insufficient_liquidity` when a complete receipt is below the G7
  liquidity threshold;
- `observed_sufficient_liquidity` when another G7 reason blocked an otherwise
  sufficient observed book;
- `unavailable_liquidity_evidence`;
- `not_queried_liquidity_evidence`.

The report must never label a receipt profitable, trade-ready, or replayable.

## Safety Invariants

- G7 constants and readiness evaluation are unchanged.
- The new store is default-off, isolated, append-only, and excluded from all
  trade, settlement, fee-net, feedback, and capital-allocation code paths.
- No live-mode, paper cohort, runtime database, credential, or service change
  is part of this implementation.
- A missing, malformed, stale, or tampered receipt is unavailable evidence, not
  support for a policy change.
- The result remains insufficient for repeatable-profit or live-trading claims
  until a separately isolated cohort yields enough fee-attributed, canonical,
  settled outcomes.

## Verification

1. Unit-test canonical receipt validation, append-only triggers, exact replay,
   conflicts, and tamper rejection.
2. Test observed zero/sufficient executable liquidity, unavailable reader, and not-queried
   momentum paths without changing readiness outcomes.
3. Test report classification and botcheck fail-closed store status.
4. Run focused blend, order-book, new-store, report, main, and botcheck suites;
   then lint and the appropriate CI gate.
