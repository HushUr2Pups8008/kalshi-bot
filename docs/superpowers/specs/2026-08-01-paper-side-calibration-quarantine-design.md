# Paper Side Calibration Quarantine Design

## Objective

Contain further unvalidated paper admission while producing a fresh,
cohort-scoped record of every otherwise-ready paper candidate. The record must
support later side-specific, fee-net, out-of-sample calibration review. It
does not enable live trading, change sizing, relax a gate, or claim that the
strategy is profitable.

## Evidence

The historical paper ledger has 36 settled rows with gross P&L of -$17.74.
YES entries are 4 wins from 28 settled rows for -$18.34. Replaying 19
historical losers compatible with the current two-sided selector selects the
same side for 18 and rejects one; none flip sides. This is sufficient to deny
unvalidated new paper admission, but not to derive a fitted numeric haircut or
to release either side for trading.

The legacy ledger is not fee-net authoritative. It cannot release this
quarantine. The active legacy-pending cohort has unresolved positions and
remains isolated from live promotion.

## Chosen Boundary

Add a new isolated append-only store at
`data/side_calibration_quarantine.db`. Do not repurpose
`capital_guard_shadow`: that store represents G7/open-exposure drawdown
counterfactuals, and combining domains would corrupt its attribution and
historic meaning.

Phase A records every capture attempt, including incomplete input, and freezes
a candidate only when the following decision-time facts validate:

- lifecycle, decision, and capture timestamps;
- venue, ticker, native market ID, settlement alias, canonical contract
  question, market snapshot hash, and scheduled close/settlement metadata;
- selected side, model YES probability, selected-side probability, executable
  price, final derived gross edge, and reported upstream edge;
- sizing, decision-time order-book timestamp and canonical payload hash,
  evidence IDs, fee context, and provenance;
- run, dossier, and contract provenance with explicit state;
- runtime paper cohort ID, kind, identity, and manifest hash;
- a distinct quarantine policy ID, semantic version, schema version, payload
  hash, software version, and relevant config artifact hash.

Unavailable facts are explicit immutable evidence. A candidate is frozen only
when its required settlement, pricing, and provenance facts are sufficient;
otherwise the store retains a typed unscorable attempt. No later snapshot may
fill a frozen capture retroactively.

The store owns append-only lifecycle evidence for initial capture, candidate
freeze or unscorable disposition, identical replay, and conflict. It stores
conflicts without mutating an original attempt or candidate. Every evidence
table repeats cohort and policy provenance. Its exact schema contract is
versioned, DDL-hashed, immutable, and fails closed on drift; there is no
runtime migration path for this undeployed store.

## Runtime Behavior

`ENABLE_PAPER_SIDE_CALIBRATION_QUARANTINE` defaults to `false`.

When the flag is enabled and the bot is in paper mode:

1. A decision passes existing readiness and executable-price work unchanged.
2. A fresh read-only order-book provenance observation is obtained through a
   venue-neutral, quarantine-only path after readiness and before queue
   insertion. It is separate from G7 and cannot change readiness, sizing, or
   executable terms.
3. The quarantine sink records an immutable attempt and, when complete, a
   frozen candidate.
4. The result is terminally skipped with
   `paper_side_calibration_unvalidated`; it is not enqueued, does not reach
   `TradeExecutor`, and does not create a paper trade.
5. Capture, identity, order-book, storage, or idempotency failure produces
   `paper_side_calibration_capture_failed`. That failure is observable and
   fail-closed.

The hook lives in `BlendTask` after existing decision work and before queue
insertion. It covers normal fast-lane and research-backed paper routes without
mixing workflow policy into the stateless G1-G7 readiness gate.

Live mode never constructs the sink or order-book provenance provider, never
opens the database, and retains current enqueue/execution behavior. Existing
paper positions remain untouched.

## Settlement And Release

Phase A does not collect settlements. Phase B will use
`AuthoritativeSettlementSource.get_settlement_exact()` and a separate
append-only evidence store with a durable cross-process lease. It must never
write to `paper_trades` or paper-accounting settlement columns. Market close
is not settlement evidence: nonterminal or ambiguous observations remain
pending.

A later policy may release a side or market family only after an
operator-approved rule specifies:

- a predeclared out-of-sample cohort and recency window;
- complete decision-time fee provenance and authoritative settlement for every
  included row;
- complete, immutable candidate-to-settlement identity binding;
- coverage and minimum sample requirements;
- a positive fee-net performance confidence rule.

Unknown, missing, stale, mixed-identity, or non-authoritative evidence remains
unvalidated. A candidate cannot use its own later outcome to release itself.

## Safety Invariants

- The feature is default-off and paper-only; `LIVE_TRADING_ENABLED=false`
  remains unchanged.
- No order client, executor, queue, `PaperTrader`, or live-mode dependency
  is imported by the store or sink.
- Capture writes are append-only, canonicalized, deterministic for identical
  retries, and conflict-recording for differing payloads.
- A capture failure prevents enqueue rather than bypassing the quarantine.
- Existing capital-guard shadow tables, reports, and collector behavior stay
  unchanged.
- Phase A provides no profitability proof and no live-promotion eligibility.

## Verification

1. Test exact schema validation, immutable triggers, canonical hashing,
   timestamp canonicalization, retry idempotency, conflict persistence, and
   tamper detection.
2. Test valid Kalshi and Polymarket authoritative settlement identities and
   invalid aliases/IDs as unscorable.
3. Test a ready paper candidate: capture succeeds, `BLEND_DECISION` and
   `SKIPPED` are emitted, and the queue receives nothing.
4. Test malformed input, provenance failure, book-provenance failure, and a
   storage conflict: every path remains unqueued with an explicit reason.
5. Test research-backed routing inherits the same quarantine and live mode
   performs zero store/provider I/O.
6. Run focused store, provenance, blend, research-admission, main-pipeline,
   logger, and botcheck tests; then lint, diff checks, independent review, CI,
   and paper-only restart verification before enabling the flag.
