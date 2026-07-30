# Runtime Paper Cohort Attestation

## Goal

Make the running process's selected paper cohort independently observable after
startup, without changing trading, sizing, live-mode, or paper-admission
behavior.

## Problem

The runtime validates the selected cohort manifest during startup, but discards
the returned binding. `scripts/botcheck.py` can validate pending-cohort
topology, not which cohort the current process actually bound. Configuration,
manifests, and log context are insufficient proof of a successful current boot.

## Selected Design

After runtime cohort validation and successful `PaperTrader` construction,
`main.py` writes an atomic nonsecret receipt at
`logs/state/runtime_paper_cohort_attestation.json`. The receipt includes:

- schema version, PID, and startup timestamp;
- cohort ID, kind, and database path relative to the storage root;
- manifest-bound flag; and
- for active or legacy-pending cohorts, cohort identity and manifest SHA-256.

The startup resolver carries the already-validated binding forward; it does not
repeat manifest validation. The receipt writer creates a sibling temporary
file, fsyncs it, atomically replaces the target, and fsyncs its parent
directory. It contains no credentials, bankroll, order, or dotenv data.

`botcheck` validates the receipt read-only. It rejects symlinks, malformed
payloads, stale or PID-mismatched receipts, invalid relative paths, and
manifest identity or cohort ID/kind mismatches. It reports a binding as
attested only for the current bot PID and a matching provisioned manifest.
Everything else remains unverified with a specific reason.

## Alternatives Rejected

1. Read process environment: can expose secrets and does not prove successful
   manifest validation.
2. Infer from structured logs: log rows can be absent and do not carry manifest
   identity.
3. Reopen the paper database in `botcheck`: violates read-only topology checks
   and adds unnecessary I/O/risk.

## Safety Invariants

- Receipt generation happens only after the selected cohort was validated and
  the runtime `PaperTrader` constructed.
- A failed startup must not create or refresh an attestation.
- Receipt validation never changes state or opens a paper database.
- Attestation cannot make a cohort eligible for live trading or active cutover.
- Existing legacy and active startup behavior remains unchanged when no
  attestation is present.

## Verification

Focused tests cover atomic payload validation, startup ordering, and botcheck
accept/reject conditions. The rollout check requires a restart, a fresh
attestation whose PID matches the new bot PID, and `LIVE_ORDER=0` with live
mode still disabled.
