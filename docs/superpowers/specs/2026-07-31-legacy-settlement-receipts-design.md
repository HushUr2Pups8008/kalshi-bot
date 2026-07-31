# Legacy Settlement Receipts Design

## Purpose

Allow one explicitly approved, officially terminal legacy paper settlement to be
recorded without treating historical gross results as fee-net or repeatable
profit evidence. The normal runtime, active-cohort provisioning, live mode,
sizing, and feedback consumers remain unchanged.

## Current Evidence

- The legacy root has 47 rows: 36 historical gross resolutions and 11 open
  rows. The open rows are the frozen exposure of `legacy-pending-20260729`.
- No terminal official receipt is currently available for any open row. An old
  close timestamp is not a terminal receipt.
- The legacy root lacks fee-net accounting evidence, so any correct legacy
  result must stay excluded from profit-readiness calculations.

## Considered Approaches

1. Use `PaperTrader.resolve_observation` directly. Rejected: constructing a
   `PaperTrader` can initialize or migrate the legacy database, and its normal
   outbox path can feed historical outcomes into calibration and source scoring.
2. Enable the generic runtime settlement reconciler. Rejected: this creates a
   background mutation path and cannot prove that an operator reviewed the
   exact receipt before applying it.
3. Add a default-off, one-shot receipt bundle and legacy-only applier.
   Selected: this creates an explicit review boundary and has no startup,
   runtime, or feedback side effect.

## Receipt Bundle

`scripts/audit_open_paper_settlements.py` remains read-only. For every
`authoritative_terminal` row it emits a versioned canonical observation bundle
containing the full semantic `SettlementObservation`, not only derived hashes.
The bundle binds:

- source snapshot artifact hashes and source database path;
- open-row fingerprint;
- trade ID, venue, market ID, and alias;
- canonical payload and authoritative outcome JSON;
- source, rules version, observed/effective timestamps, and observation hash.

The serialization and parser reconstruct a `SettlementObservation` and reject
non-canonical JSON, malformed fields, duplicated receipt targets, a changed
hash, nonterminal records, a supersession, or a void outcome. A receipt is
therefore inspectable but never trusted just because it is locally stored.

## Apply Boundary

`scripts/reconcile_legacy_paper_receipts.py` defaults to plan-only. It requires
both `--allow-network` and `--write` before it can alter a database. Apply also
requires the exact legacy root path, an explicit trade ID, the complete
hash-attested read-only audit report, an externally supplied SHA-256 of the
complete report file, the reviewed snapshot SHA-256, the current root SHA-256,
and the current open-row fingerprint. The report, snapshot, and root are
rehash-validated at plan time, apply time, and again before mutation. Symlinked
or hard-linked artifacts, including paths that traverse a symlinked parent, are
rejected.

Before mutation the command requires runtime quiescence through the project
runtime lock, reads the source record again through
`AuthoritativeSettlementSource`, and requires the fresh observation hash to
equal the reviewed receipt. It then acquires the immediate SQLite writer lock,
re-attests the immutable root identity, root hash, and open-row fingerprint
through that locked connection, and creates a durable no-clobber preimage
backup from a sibling read-only connection. The backup is integrity-checked,
fsynced, published without overwrite, and semantically checked against the
reviewed target before any database mutation. It applies exactly one
directional market observation in that same writer transaction and rejects any
new conservation failure, normal outbox link, or receipt inconsistency before
commit. Pending, void, duplicate, conflicting, identity-drifted,
source-drifted, or fingerprint-drifted records fail without changing the root.

## Persistence and Isolation

The legacy applier is a new transaction-only module. It does not construct
`PaperTrader`, call `SettlementOutboxTask`, alter the pending snapshot or its
manifest, write an active cohort, or enqueue normal feedback consumers. It
persists the canonical observation and trade resolution using the existing
durable settlement schema, but records an archival-only marker which the
profit-evidence report excludes. The old and newly applied legacy gross results
remain non-fee-net and non-repeatable-profit evidence.

## Error Handling

All parse, lock, identity, source, and SQLite failures are explicit errors.
No failure triggers a retrying background task or a synthetic resolution. The
database transaction rolls back on every failed row update or inconsistency;
the backup remains for operator inspection.

## Validation

- Audit tests prove full receipt serialization is stable and read-only.
- Applier tests cover plan-only gating, root/snapshot/open-row drift, re-fetch
  mismatch, terminal identity mismatch, duplicate/idempotent receipt handling,
  conflicting receipt rejection, void rejection, strict report parsing,
  writer-lock handling, durable backup publication, normal-outbox prevention,
  conservation postconditions, and one-market atomic rollback.
- Cohort tests prove later root settlement preserves the pending manifest.
- Outbox and profit-report tests prove archival legacy results never enter
  feedback consumers or fee-net/repeatable-profit readiness.

## Non-Goals

This work does not manufacture a settlement, make a profit claim, switch
cohorts, enable live trading, change stake sizing, or enable a periodic runtime
collector. It only makes a future official terminal result safely actionable.
