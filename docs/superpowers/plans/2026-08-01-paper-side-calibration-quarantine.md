# Paper Side Calibration Quarantine Implementation Plan

## Goal

Stop unvalidated paper candidates before queue admission while preserving
immutable, cohort-scoped facts for later fee-net, out-of-sample side
calibration. This is containment and measurement work, not profitability proof.

## Constraints

- Keep `LIVE_TRADING_ENABLED=false`.
- Default off. Never construct the store or provider in live mode.
- Do not alter G1-G7 readiness, sizing, existing paper positions, or the
  order path.
- Do not derive a numeric release threshold from the legacy loss ledger.
- Capture failure must remain fail-closed once the paper-only flag is enabled.
- Phase B observes authoritative outcomes into a separate append-only store;
  it never settles or mutates `paper_trades`.

## Completed

- [x] Diagnose historical negative paper results and reject in-sample threshold
  fitting.
- [x] Build and independently review the evidence-grade isolated store.
- [x] Require canonical settlement identity for Kalshi and Polymarket,
  canonical UTC timestamps, explicit cohort/policy provenance, immutable
  lifecycle/conflict records, and future-settlement contract metadata.
- [x] Prove default data captures lack reliable pre-queue book provenance and
  select a quarantine-only, cross-venue observation path.
- [x] Define Phase B around `AuthoritativeSettlementSource`, durable lease,
  and separate fee-net evidence records.

## Remaining Work

### 1. Cross-Venue Pre-Queue Book Provenance

- [ ] Add a default-off, read-only, venue-neutral provider for canonical book
  timestamp and payload hash.
- [ ] Reuse the existing Kalshi and Polymarket public book clients.
- [ ] Invoke it only after readiness and before the quarantine sink.
- [ ] Keep it separate from G7 so baseline admission never changes.
- [ ] Treat unavailable provenance as a quarantine capture failure only while
  quarantine is enabled.

### 2. Paper-Only Sink And Runtime Context

- [ ] Add `ENABLE_PAPER_SIDE_CALIBRATION_QUARANTINE=false`.
- [ ] Build the sink only when paper mode and the flag are both true.
- [ ] Freeze cohort attestation, semantic quarantine-policy artifact, software
  version, and config artifact provenance in every envelope.
- [ ] Supply both normal and research-backed blend routes.

### 3. Pre-Queue Quarantine

- [ ] On successful complete capture, emit
  `paper_side_calibration_unvalidated`, skip, and do not enqueue.
- [ ] On incomplete, conflicting, provider, or storage failure, emit
  `paper_side_calibration_capture_failed`, skip, and do not enqueue.
- [ ] Preserve exact baseline behavior when the sink is absent.

### 4. Read-Only Status And Release Guard

- [ ] Add `botcheck` output that performs zero SQLite I/O when disabled and
  read-only integrity/count inspection when an existing store is enabled.
- [ ] Document the feature as containment, never as profitability proof.
- [ ] Keep the runtime flag disabled until independent review, CI, and an
  operator-approved paper-only activation.

### 5. Phase B

- [ ] Add a separate durable-lease authoritative settlement collector.
- [ ] Bind observations to frozen candidate identity and complete fee
  provenance.
- [ ] Evaluate only predeclared OOS cohorts and apply an operator-approved
  positive fee-net confidence rule.

## Verification

- [ ] Focused store, provider, sink, blend, research-route, main-pipeline, and
  botcheck tests.
- [ ] Ruff and `rtk proxy git diff --check`.
- [ ] Independent review of no live construction, zero queue insertion under
  quarantine, cross-venue identity/provenance, and append-only evidence.
- [ ] Normal CI before merge.
- [ ] Paper-only restart verification with `LIVE_ORDER=0`; do not enable
  collection until the required decision is explicitly made.
