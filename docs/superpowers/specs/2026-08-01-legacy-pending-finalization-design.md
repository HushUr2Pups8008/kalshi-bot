# Legacy-Pending Finalization Design

## Objective

Retire a settled `legacy_pending` cohort family only after every frozen baseline
trade has already been reconciled in the mutable legacy root with authoritative,
reviewed terminal receipts. Finalization is a separate, filesystem-only,
operator-approved bridge. It must never mutate any `paper_trades`,
`paper_settlement_*`, `bot_state`, or other settlement/accounting row.

## Scope

This design introduces a new three-artifact boundary:

- `plan_legacy_pending_finalization(...)`
- `apply_legacy_pending_finalization(...)`
- sealed finalization plan artifact

The boundary consumes the existing pending manifest family, the immutable frozen
baseline snapshot, and the already-mutated legacy root. It proves that the
pending family is fully retired, archives the family with a finalization
certificate, and removes it from live discovery so a normal active cutover can
be provisioned later.

It does not apply receipts, settle trades, rebuild P&L, or promote a pending
cohort directly into an active cohort.

## Existing Preconditions

- Pending provisioning already captures one immutable unresolved-legacy
  baseline:
  - `trading.paper_cohorts.initialize_legacy_pending_paper_cohort_manifest`
  - `trading.paper_cohorts.legacy_open_exposure_fingerprint`
- Runtime validation already keeps the pending family bound to that immutable
  baseline even after the mutable legacy root changes:
  - `trading.paper_cohorts.validate_legacy_pending_paper_cohort_manifest`
  - `trading.paper_cohorts.discover_legacy_pending_paper_risk_cohorts`
- Legacy root receipt application already exists and is the only allowed way to
  mutate historical legacy trade rows:
  - `scripts.reconcile_legacy_paper_receipts.apply_legacy_receipt_reconciliation`
  - `trading.settlement_store.SettlementStore._apply_legacy_directional_receipt`

The new finalization bridge depends on those invariants. It must refuse to run
unless the pending family still matches its frozen baseline and the legacy root
already contains authoritative terminal applications for every frozen trade.

## Hard Invariants

- No database row mutation. Finalization is filesystem-only.
- No network access in plan or apply. Fresh authoritative source re-fetch is a
  receipt-application concern, not a finalization concern.
- No runtime, launchd, `.env`, config, or live-trading changes.
- No mutation of pending manifests, pending snapshots, pending cohort DBs, or
  active cohort manifests in place.
- No implicit promotion. Finalization only retires the pending family from live
  discovery. A later operator step may run the existing active initializer.
- All mutable-state checks fail closed on symlink, hard-link, alias, missing
  file, hash drift, malformed manifest, duplicate certificate, or runtime-lock
  contention.

## Required Proof Inputs

`plan_legacy_pending_finalization(...)` must require exact operator-supplied
proof inputs:

- `db_path`: approved mutable legacy root `data/paper_trades.db`
- `pending_root`: approved `data/legacy_pending_paper_cohorts`
- `expected_root_sha256`
- `expected_pending_manifest_sha256s`: exact sorted manifest hash set for every
  pending cohort
- `expected_legacy_snapshot_sha256`: exact shared frozen snapshot hash
- `expected_baseline_open_rows_sha256`: exact shared unresolved baseline
  fingerprint
- `expected_baseline_trade_ids`: exact sorted frozen baseline trade IDs

`apply_legacy_pending_finalization(...)` must additionally require:

- `expected_finalization_plan_sha256`
- explicit `--write`
- explicit operator confirmation string bound to the finalization target

The operator must supply these values from a reviewed sealed finalization plan
artifact. Apply must never derive them implicitly from current disk state.

## Plan Semantics

Plan is read-only. It produces a canonical, sealed finalization plan artifact
and performs no file or database mutation.

### Sealed Finalization Plan Artifact

The planner must emit a deterministic JSON artifact, for example:

- `legacy_pending_finalization_plan.json`

This sealed plan is the reviewed control artifact for apply and replay. Its
body must contain only stable, read-only proof inputs and derived proof outputs.
Its SHA-256 is known before apply and is the only required operator-supplied
plan hash for apply:

- `finalization_plan_sha256 = sha256(canonical_plan_json)`

The sealed plan must not contain any apply-time-only fields whose value depends
on the final certificate body or other post-publish control files.

### 1. Discovery And Identity

- Resolve the pending family via
  `trading.paper_cohorts.discover_legacy_pending_paper_risk_cohorts`.
- Require:
  - at least one discovered pending cohort plus the synthetic
    `legacy-pending-baseline`
  - one shared `legacy_db_path`
  - one shared `legacy_starting_bankroll`
  - one shared `legacy_baseline_attestation`
  - one shared `legacy_snapshot_sha256`
  - one shared `legacy_open_exposure`
- Recompute and match every pending manifest SHA-256 against the exact expected
  set supplied by the operator.
- Revalidate that all cohort database, snapshot, and manifest paths are plain
  files under the approved pending root with no symlinks or aliasing.

### 2. Frozen Baseline Attestation

- Open the immutable baseline snapshot DB read-only.
- Recompute:
  - file SHA-256
  - unresolved legacy open-row fingerprint
  - sorted frozen `trade_id` set
- Require exact equality with:
  - manifest-bound `legacy_snapshot_sha256`
  - manifest-bound `legacy_open_exposure.rows_sha256`
  - operator-supplied expected snapshot/open-row/trade-set inputs

### 3. Mutable Root Attestation

- Open the mutable legacy root read-only.
- Require exact root SHA-256 equality with `expected_root_sha256`.
- Require zero unresolved legacy rows in the mutable root.
  - This is the same unresolved-state gate used by
    `initialize_active_paper_cohort_manifest`.
- Require the root to remain the approved canonical legacy path, not a symlink,
  hard link, or aliased file.

### 4. Receipt-Coverage Proof

For every frozen baseline trade ID, and only for that frozen baseline trade ID
set:

- Require exactly one stored legacy receipt application row in
  `_LEGACY_RECEIPT_APPLICATION_TABLE`.
- Revalidate the stored row through
  `trading.settlement_store._validate_existing_legacy_receipt_application`.
- Require exactly one canonical observation linked from the corresponding root
  trade row.
- Require the trade row to be resolved and to match the stored receipt’s
  observation identity.
- Require no normal settlement outbox path for that archival application.
- Require no duplicate, conflicting, or missing receipt identities across:
  - `trade_id`
  - `observation_sha256`
  - `receipt_sha256`

Unrelated historical resolved rows in the mutable root are allowed. They must
not participate in the proof except to the extent that they would cause direct
identity conflicts with one of the frozen baseline trade IDs or receipt
identities.

The proof target is complete archival receipt coverage of the frozen baseline,
not generic “resolved count went to zero”.

### 5. Conservation And Completion

- Require `SettlementStore(...).conservation(...)` to remain OK at the current
  root state.
- Require every frozen baseline trade ID to be finalized in root with exact
  archival receipt coverage. No missing frozen trade can satisfy the proof.
- Do not require the mutable root’s entire resolved trade population to equal
  the frozen baseline trade set. Extra unrelated historical resolved rows are
  permitted.
- Produce a canonical plan payload with:
  - pending family identity
  - manifest SHA set
  - baseline snapshot hash
  - baseline open-row fingerprint
  - frozen trade ID set
  - applied receipt SHA set
  - root SHA-256
  - finalization target archive path
  - deterministic payload inventory
  - deterministic payload inventory SHA-256
- Compute `finalization_plan_sha256` from the canonical sealed plan body.

## Apply Semantics

Apply is filesystem-only. It has two explicit branches:

- first apply: live pending family still exists, so apply must acquire the
  runtime lock and re-run the sealed plan proof before publishing the archive
- replay apply: live pending family has already been removed, so apply must
  verify the sealed plan SHA, recomputed payload inventory, and published
  certificate consistency first and return idempotent success without requiring
  the live family to exist or re-running a live-family plan

### 1. Locking

- Acquire the same cooperative runtime lock used by provisioning and receipt
  reconciliation:
  - `data/bot_runtime.lock`
- Refuse to proceed if the lock path is a symlink, unreadable, or already held.
- While the runtime lock is held on first apply, re-run the sealed plan proof.
- No network access and no SQLite write transaction are allowed in this phase.

### 2. Certificate Staging

Create a new archival directory outside live discovery:

- `data/finalized_legacy_pending_paper_cohorts/<finalization-id>/`

The certificate must be a canonical JSON file, for example:

- `finalization_certificate.json`

Required certificate fields:

- `schema_version`
- `finalization_id`
- `created_at_utc`
- `pending_root_relative_to_storage_root`
- `archived_root_relative_to_storage_root`
- `legacy_db_relative_to_storage_root`
- `shared_legacy_snapshot_sha256`
- `shared_legacy_baseline_attestation`
- `shared_legacy_open_rows_sha256`
- `shared_legacy_open_trade_count`
- `pending_manifest_sha256s`
- `pending_cohort_ids`
- `frozen_trade_ids`
- `applied_receipt_sha256s`
- `finalization_plan_sha256`
- `root_db_sha256`
- `payload_inventory_sha256`
- `operator_confirmation`

The certificate is evidence only. It must not reference live config or runtime
state beyond the reviewed root and pending family identities.

## Deterministic Payload Inventory

The sealed plan must include a complete deterministic payload inventory. This
inventory is the post-publish idempotence source of truth after the live pending
discovery root has been removed.

Each inventory entry must include:

- `path_relative_to_archive_root`
- `file_type`
- `size_bytes`
- `sha256`

The payload inventory must contain every archived plain file required to
reconstruct the finalized pending family payload, including:

- every pending manifest
- every pending cohort DB
- every immutable baseline snapshot

The payload inventory must explicitly exclude apply-time control files:

- the sealed finalization plan artifact
- the finalization certificate

Those control files live outside the hashed payload domain, or are otherwise
handled as non-payload control artifacts by fixed path and direct SHA checks.

The payload inventory must be canonically sorted by relative path and hashed
into `payload_inventory_sha256`. Idempotence checks must compare the full
payload inventory, not just top-level archive paths.

### 3. Archival Copy/Move

Apply must preserve the full pending family evidence:

- every pending cohort directory
- every pending manifest
- every pending cohort DB
- every immutable baseline snapshot
- the sealed finalization plan artifact
- the finalization certificate

Safe sequence:

1. Stage the archival root in a hidden sibling directory.
2. Copy the pending family contents into the staged archive while preserving
   plain-file identities and refusing symlinks/hard links.
3. Materialize the deterministic payload inventory from the staged payload files
   only.
4. Verify staged file hashes against the reviewed plan, the live source, and
   the staged payload inventory.
5. Copy the sealed finalization plan artifact into the archive control path
   without placing it inside the payload inventory domain.
6. Write the canonical apply-time certificate referencing
   `finalization_plan_sha256` and `payload_inventory_sha256`.
7. Verify the staged sealed plan SHA directly against
   `expected_finalization_plan_sha256`.
8. Verify the staged certificate is internally consistent with the sealed plan
   and staged payload inventory.
9. `fsync` staged files and directories.
10. Atomically rename the staged archive into
   `finalized_legacy_pending_paper_cohorts/<finalization-id>`.
11. `fsync` the archive parent directory.
12. Re-read the published sealed plan, recomputed payload inventory, and
   certificate by archive path and require exact consistency.
13. Remove the live `legacy_pending_paper_cohorts` entries from discovery only
   after the archive is fully published.
14. `fsync` the live parent directory.

The live removal step may be implemented as renaming the whole pending root into
the archive or renaming each cohort directory and snapshot into the archive,
provided the result is atomic enough that discovery never observes a mixed
family.

## Idempotence

Apply must be idempotent by sealed finalization plan SHA.

- Replay branch:
  - If the exact archive root already exists, verify the archived sealed plan
    SHA first.
  - Then recompute the archived payload inventory from payload files only and
    require equality with the sealed plan’s `payload_inventory_sha256`.
  - Then verify the published certificate is consistent with the archived sealed
    plan SHA and recomputed payload inventory SHA.
  - If all checks pass, return success immediately without requiring the live
    pending family or re-running a live-family plan.
- First-apply branch:
  - If the live pending family still exists, acquire the runtime lock and re-run
    the sealed plan proof before any staging or publish.
- If any archival target already exists with different content, fail closed.
- If the live pending root is already absent but the archive is present and
  valid, return success.
- If the live pending root is absent and the archive is absent, fail closed:
  evidence has been lost or moved out of band.

## Rollback And Abort

- Before the final publish rename, any failure must remove only staging files.
- After the archive publish but before live discovery removal, apply may retry
  only if the published sealed plan SHA, recomputed payload inventory, and
  published certificate still match exactly.
- After successful discovery removal, rollback is not automatic. Recovery is an
  operator restore from the archived family, not a code-driven rewrite.
- Because finalization never mutates database rows, rollback scope is limited to
  filesystem evidence and discovery topology.

## Operator Boundary

The operator still owns:

- selection of the exact pending family to finalize
- approval of the reviewed plan artifact
- confirmation that every required legacy receipt has already been applied
- any later active cohort initialization
- any restart/config step needed to boot a new active cohort

The implementation must never collapse these steps into one command.

## Non-Goals

- No direct provisioning of an active cohort.
- No proof of profitability or live readiness.
- No conversion of archival legacy receipts into profit-attested evidence.
- No fee-net reconstruction for historical legacy trades.
- No deletion of archived evidence after finalization.
